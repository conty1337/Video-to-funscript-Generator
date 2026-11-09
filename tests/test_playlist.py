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
