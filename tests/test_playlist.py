"""Playlist model: order, skipping, repeat, shuffle, M3U."""
import random
from pathlib import Path

from vtf.playlist import Playlist


def _videos(tmp_path, names, scripts=()):
    paths = []
    for n in names:
        p = tmp_path / f"{n}.mp4"
        p.write_bytes(b"x")
        if n in scripts:
            p.with_suffix(".funscript").write_text('{"actions": []}')
        paths.append(p)
    return paths


def test_order_and_skip_without_script(tmp_path):
    pl = Playlist()
    assert pl.add(_videos(tmp_path, "abcd", scripts="acd")) == 4
    assert pl.add([tmp_path / "a.mp4"]) == 0                 # no duplicates
    assert pl.first_playable() == 0
    pl.current = 0
    assert pl.next_index() == 2                              # b has no script
    pl.current = 3
    assert pl.next_index() is None                           # end, no repeat
    pl.repeat = True
    assert pl.next_index() == 0
    pl.current = 2
    assert pl.prev_index() == 0
    pl.skip_without_script = False
    assert pl.prev_index() == 1


def test_remove_and_move_keep_current(tmp_path):
    pl = Playlist()
    pl.add(_videos(tmp_path, "abcd"))
    pl.current = 2                                           # c
    pl.remove(0)
    assert Path(pl.items[pl.current]).stem == "c"
    pl.move(pl.current, 0)
    assert pl.current == 0 and Path(pl.items[0]).stem == "c"
    pl.set_items(list(reversed(pl.items)))
    assert Path(pl.items[pl.current]).stem == "c"
    pl.remove(pl.current)
    assert 0 <= pl.current < len(pl.items)
    pl.clear()
    assert pl.current == -1 and pl.next_index() is None


def test_shuffle_plays_each_once_per_round(tmp_path):
    pl = Playlist(shuffle=True, repeat=True, skip_without_script=False)
    pl._rng = random.Random(1)
    pl.add(_videos(tmp_path, "abcdef"))
    seen = []
    pl.current = -1
    for _ in range(6):
        pl.current = pl.next_index()
        seen.append(pl.current)
    assert sorted(seen) == list(range(6))
    nxt = pl.next_index()                                    # next round starts
    assert nxt is not None and nxt != pl.current


def test_m3u_roundtrip(tmp_path):
    vids = _videos(tmp_path, "ab")
    pl = Playlist()
    pl.add(vids)
    m3u = tmp_path / "list.m3u"
    pl.save_m3u(m3u)
    assert m3u.read_text().startswith("#EXTM3U")
    other = Playlist()
    assert other.load_m3u(m3u) == 2
    assert other.items == pl.items
    rel = tmp_path / "rel.m3u"
    rel.write_text("#EXTM3U\n# Kommentar\nb.mp4\n")          # relative to the file
    third = Playlist()
    third.load_m3u(rel)
    assert third.items == [str((tmp_path / "b.mp4").resolve())]


def test_script_start_and_intro_skip():
    from vtf.funscript import Action
    from vtf.playlist import intro_skip_ms, script_start_ms

    quiet = [Action(0, 50), Action(60_000, 52), Action(120_000, 50)]
    acts = quiet + [Action(120_400, 95), Action(120_800, 5)]
    assert script_start_ms(acts) == 120_000
    assert intro_skip_ms(acts) == 110_000                     # 10 s before it starts
    assert script_start_ms(quiet) is None and intro_skip_ms(quiet) == 0
    early = [Action(0, 50), Action(8_000, 50), Action(8_400, 90)]
    assert intro_skip_ms(early) == 0                          # starts soon anyway
    assert intro_skip_ms([]) == 0


def test_quiet_stretches_and_skip_target():
    from vtf.funscript import Action
    from vtf.playlist import quiet_stretches, skip_target

    acts = [Action(0, 0), Action(500, 90), Action(1000, 0),          # strokes
            Action(5000, 0), Action(25000, 2),                       # 24 s nothing (2 is no stroke)
            Action(30000, 0), Action(30500, 90), Action(31000, 0)]
    q = quiet_stretches(acts, duration_ms=60000, lead_ms=5000)
    assert q == [(1000, 30000, 25000), (31000, 60000, 60000)]
    assert skip_target(1500, (), q) is None                          # the last stroke ends first
    assert skip_target(2500, (), q) == 25000
    assert skip_target(10000, (), q) == 25000
    assert skip_target(26000, (), q) is None                         # already near the next stroke
    assert skip_target(40000, (), q) == 60000                        # after the last: to the end
    cuts = [(2000, 4000)]
    assert skip_target(2500, cuts) == 4000 and skip_target(4000, cuts) is None
    assert quiet_stretches(acts[:3]) == [] and quiet_stretches([]) == []


def test_countdown_also_after_a_pause():
    from vtf.funscript import Action
    from vtf.playlist import resume_ms

    acts = [Action(20000, 0), Action(20500, 90), Action(21000, 0),     # starts at 20 s
            Action(22000, 90), Action(23000, 0),                       # goes on: no countdown
            Action(40000, 0), Action(40500, 90)]                       # after 17 s still
    assert resume_ms(15000, acts) == 20000
    assert resume_ms(21500, acts) is None
    assert resume_ms(33000, acts) == 40000
    assert resume_ms(41000, acts) is None


def test_remember_and_resume_spot():
    from vtf.playlist import RESUME_BACK_MS, RESUME_MAX, remember_spot, resume_spot
    spots = {}
    remember_spot(spots, "a", 120_000, 600_000)
    assert resume_spot(spots, "a") == 120_000 - RESUME_BACK_MS
    remember_spot(spots, "a", 10_000, 600_000)                       # back at the start: forgotten
    assert resume_spot(spots, "a") is None
    remember_spot(spots, "a", 120_000, 600_000)
    remember_spot(spots, "a", 590_000, 600_000)                      # watched to the end: forgotten
    assert "a" not in spots and resume_spot({"b": "x"}, "b") is None
    for k in range(RESUME_MAX + 5):
        remember_spot(spots, str(k), 100_000, 600_000)
    assert len(spots) == RESUME_MAX and "0" not in spots and str(RESUME_MAX + 4) in spots


def test_countdown_time_skips_cuts():
    """The countdown counts playing time: a cut on the way does not count, and skipping the
    quiet intro starts early enough for the whole countdown."""
    from vtf.funscript import Action
    from vtf.playlist import before_ms, intro_skip_ms, play_ms
    cuts = [(20_000, 24_000)]
    assert play_ms(18_000, 30_000, cuts) == 8_000 and play_ms(25_000, 30_000, cuts) == 5_000
    assert play_ms(21_000, 30_000, cuts) == 6_000                     # inside the cut: from its end
    assert before_ms(30_000, 10_000, cuts) == 16_000                  # 4 s cut -> 4 s earlier
    assert before_ms(30_000, 5_000, cuts) == 25_000                   # cut not in the way
    assert before_ms(30_000, 10_000, [(17_000, 18_000), (20_000, 24_000)]) == 15_000
    acts = [Action(0, 50), Action(30_000, 50), Action(30_400, 90)]
    assert intro_skip_ms(acts, 10_000) == 20_000 and intro_skip_ms(acts, 10_000, cuts) == 16_000
