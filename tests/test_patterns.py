"""Stroke patterns: library, rendering, saving, live playback."""
import pytest

from vtf import patterns as P
from vtf.device.simulated import SimulatedDevice
from vtf.device.sync import ScriptSync, SyncSettings


def test_library_renders():
    assert len(P.BUILTIN) >= 30
    names = [p.name for p in P.BUILTIN]
    assert len(names) == len(set(names))
    for p in P.BUILTIN:
        acts = P.actions(p, 60, 10, 90, 0, 20_000)
        assert len(acts) > 4, p.name
        assert all(10 <= a.pos <= 90 for a in acts), p.name
        assert all(b.at > a.at for a, b in zip(acts, acts[1:])), p.name


def test_tempo_and_range():
    even = P.BUILTIN[0]
    assert P.cycle_ms(even, 60) == pytest.approx(1000)
    assert P.cycle_ms(even, 120) == pytest.approx(500)
    acts = P.actions(even, 60, 20, 80, 0, 3000)
    assert {a.pos for a in acts} == {20, 80}


def test_ramp_and_hold():
    ramp = P.Pattern("r", [P.Block(repeat=5, speed=1, speed_to=2, lo=60, lo_to=0)])
    segs = P.segments(ramp, 60)
    downs = [f for _d, f in segs[::2]]
    assert downs[0] == pytest.approx(0.6) and downs[-1] == pytest.approx(0.0)
    assert segs[0][0] == pytest.approx(500) and segs[-1][0] == pytest.approx(250)
    hold = P.Pattern("h", [P.Block(repeat=1), P.Block("hold", hi=100, length=2)])
    segs = P.segments(hold, 60)
    assert segs[-1] == (pytest.approx(2000), 1.0)          # already at the top: just stays
    move = P.Pattern("m", [P.Block("move", hi=0, speed=0.5), P.Block("move", hi=100, speed=2)])
    assert [d for d, _ in P.segments(move, 60)] == [pytest.approx(1000), pytest.approx(250)]
    assert len(P.block_ranges(hold)) == 2


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "p.json"
    mine = P.BUILTIN[20].copy("Meins")
    mine.blocks[0].speed_to = 3.0
    P.save_user([mine], path)
    back = P.load_user(path)
    assert back[0].name == "Meins" and not back[0].builtin
    assert back[0].to_dict() == mine.to_dict()
    path.write_text("kaputt")
    assert P.load_user(path) == []
    # bad values are clamped
    b = P.Block.from_dict({"kind": "x", "repeat": 999, "speed": 50, "lo": -5, "hi": 300})
    assert (b.kind, b.repeat, b.speed, b.lo, b.hi) == ("stroke", 64, P.MAX_SPEED, 0, 100)


def test_unique_name():
    assert P.unique_name("Welle", ["welle", "Welle 2"]) == "Welle 3"
    assert P.unique_name("  ", []) == "Mein Muster"


def test_runner_plays_and_changes_live():
    dev = SimulatedDevice()
    dev.connect()
    sync = ScriptSync(dev, SyncSettings(max_speed=0))
    r = P.PatternRunner(sync)
    r.configure(0, pattern=P.BUILTIN[0], bpm=60, lo=0, hi=100)
    r.start(1000)
    for t in range(1000, 4000, 20):
        r.tick(t)
    assert r.running and r.phase(4000) is not None
    before = r.position(4250)
    r.configure(4250, bpm=120, lo=40, hi=60)           # no jump: continues from where it is
    assert r.position(4250) == pytest.approx(before, abs=1)
    assert P.cycle_ms(r.pattern, r.bpm) == pytest.approx(500)
    assert all(a.pos <= 60 for a in r.acts[1:])
    # keeps going beyond the first look-ahead
    for t in range(4250, 4250 + 2 * P.PatternRunner.AHEAD_MS, 100):
        r.tick(t)
    assert r.acts[-1].at > 4250 + 2 * P.PatternRunner.AHEAD_MS
    r.stop()
    assert not r.running and r.acts == []


def test_rhythm_with_pattern():
    from vtf.editing import rhythm
    p = next(x for x in P.BUILTIN if x.name == "Doppelschlag")
    out, sel = rhythm([], 0, 6000, 60, 10, 90, p)
    assert len(sel) == len(out) > 8
    assert out[0].pos == 90 and min(a.pos for a in out) == 10


def test_gamepad_parse_and_reader(tmp_path):
    import struct
    import threading

    from vtf import gamepad as G

    ev = struct.pack("<IhBB", 10, 1, G.JS_BUTTON, 0) + struct.pack("<IhBB", 11, -32767, G.JS_AXIS | G.JS_INIT, 1)
    out = G.parse(ev + b"\x01\x02")                      # a cut-off event at the end is ignored
    assert [(e.kind, e.number, e.value, e.init) for e in out] == [("button", 0, 1.0, False),
                                                              ("axis", 1, -1.0, True)]
    dev = tmp_path / "js0"                               # a file: its end counts as unplugged
    dev.write_bytes(ev)
    got, states, done = [], [], threading.Event()

    def state(name):
        states.append(name)
        if name is None:
            done.set()

    r = G.GamepadReader(got.append, state, paths=lambda: [str(dev)])
    r.start()
    assert done.wait(3)                                  # writer closed = unplugged
    r.stop()
    assert states[0] and states[-1] is None
    assert [e.number for e in got] == [0, 1]
