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


def _runner():
    dev = SimulatedDevice()
    dev.connect()
    lib = {p.name: p for p in P.BUILTIN}
    return P.ProgramRunner(ScriptSync(dev, SyncSettings(max_speed=0)), lib.get)


def test_program_steps_smooth_offsets():
    prog = P.Program("t", [P.Step("Gleichmäßig", 1, 60, 50), P.Step("Kurz vor Schluss", 1, 120, 100, 1, 1)])
    r = _runner()
    r.start(0, prog)
    assert r.index == 0 and r.runner.bpm == 60 and r.runner.lo == 50
    for t in range(0, 60_000, 50):
        r.tick(t)
    r.tick(60_000 + 4000)                                # halfway through the glide
    assert r.index == 1 and r.runner.bpm == 90 and r.runner.lo == 25
    assert len(r.runner.pattern.blocks) == 1             # only the first block of the pattern
    r.tick(60_000 + 9000)
    assert r.runner.bpm == 120 and r.runner.lo == 0
    r.offset_bpm, r.offset_depth = 10, -30
    r.tick(60_000 + 9500)
    assert r.runner.bpm == 130 and r.runner.lo == 30
    assert r.current(60_000 + 9500) == (130, 70)
    r.tick(120_001)                                      # end: stops
    assert not r.running and r.finished


def test_program_loop_skip_and_hard_switch():
    prog = P.Program("t", [P.Step("Gleichmäßig", 1, 60, 50), P.Step("Schnell", 1, 100, 100)],
                     loop=True, smooth=False)
    r = _runner()
    r.start(1000, prog)
    r.tick(1000 + 60_000)
    assert r.index == 1 and r.runner.bpm == 100          # no glide
    r.tick(1000 + 120_000 + 10)                          # starts again
    assert r.running and r.index == 0 and r.loops == 1
    r.skip(200_000, 1)
    assert r.index == 1 and r.elapsed(200_000) == pytest.approx(60_000)
    r.skip(200_100, 1)                                   # wraps around
    assert r.index == 0
    r.stop()
    assert not r.running


def test_program_save_load_and_missing_pattern(tmp_path):
    path = tmp_path / "programs.json"
    prog = P.BUILTIN_PROGRAMS[0].copy("Meins")
    prog.steps[0].first, prog.steps[0].last = 2, 3
    P.save_programs([prog], path)
    back = P.load_programs(path)
    assert back[0].to_dict() == prog.to_dict() and not back[0].builtin
    for pr in P.BUILTIN_PROGRAMS:                        # presets only use existing patterns
        assert all(any(p.name == st.pattern for p in P.BUILTIN) for st in pr.steps)
    r = _runner()
    r.start(0, P.Program("x", [P.Step("Gibt es nicht", 1, 60, 100)]))
    assert r.running and r.runner.pattern is P.BUILTIN[0]


def test_random_program():
    a = P.random_program("ruhig", seed=1, total_minutes=60)
    b = P.random_program("ruhig", seed=1, total_minutes=60)
    assert a.to_dict() == b.to_dict() and a.loop and a.smooth
    assert 60 <= a.total_ms / 60_000 < 64
    names = {p.name: p for p in P.BUILTIN}
    for st in a.steps:
        assert names[st.pattern].category in ("Grundlagen", "Necken", "Steigerung")
        assert 30 <= st.bpm <= 60 and 40 <= st.depth <= 80 and 1 <= st.minutes <= 3
    assert all(x.pattern != y.pattern for x, y in zip(a.steps, a.steps[1:]))
    c = P.random_program("intensiv", bpm=(100, 110), depth=(90, 95), minutes=(0.5, 0.5), seed=2, total_minutes=5)
    assert all(100 <= st.bpm <= 110 and 90 <= st.depth <= 95 and st.minutes == 0.5 for st in c.steps)
    own = P.Pattern("Meins", [P.Block()])
    d = P.random_program("gemischt", seed=3, total_minutes=600, extra=[own])
    assert any(st.pattern == "Meins" for st in d.steps)


def test_random_step_and_moods():
    import random

    rng = random.Random(5)
    every = {st.pattern for st in (P.random_step(rng, "alles", last=None) for _ in range(400))}
    intense = {p.name for p in P.BUILTIN if p.category == "Intensiv"}
    assert every & intense                               # "Alles" includes everything
    calm = {P.random_step(rng, "ruhig").pattern for _ in range(200)}
    assert not calm & intense
    st = P.random_step(rng, "gemischt", bpm=(90, 90), depth=(70, 70), minutes=(2, 2), last="Welle")
    assert (st.bpm, st.depth, st.minutes) == (90, 70, 2) and st.pattern != "Welle"


def test_gamepad_evdev_and_filter():
    import os
    import struct
    import threading

    from vtf import gamepad as G

    pad = G.Pad(-1, "8BitDo Ultimate", "evdev", {0x00: (-32768, 32767), 0x04: (0, 255), 0x11: (-1, 1)})

    def ev(t, c, v):
        return G.EV_EVENT.pack(0, 0, t, c, v)

    data = (ev(G.EV_KEY, 0x130, 1) + ev(G.EV_KEY, 0x131, 0) + ev(G.EV_KEY, 0x137, 1) +
            ev(G.EV_ABS, 0x00, 32767) + ev(G.EV_ABS, 0x04, 0) + ev(G.EV_ABS, 0x11, -1) +
            ev(G.EV_KEY, 0x220, 1) + ev(G.EV_KEY, 0x130, 2) + ev(0, 0, 0))
    out = [(e.kind, e.number, round(e.value, 2)) for e in pad.events(data[:30]) + pad.events(data[30:])]
    assert out == [("button", G.BTN_A, 1.0), ("button", G.BTN_B, 0.0), ("button", G.BTN_RB, 1.0),
                   ("axis", G.AX_LX, 1.0), ("axis", G.AX_RY, -1.0), ("axis", G.AX_DY, -1.0),
                   ("axis", G.AX_DY, -1.0)]               # key repeat (2) and sync are ignored
    assert not G.looks_like_pad("Corsair CORSAIR K100 RGB Optical-Mechanical Gaming Keyboard")
    assert G.looks_like_pad("8BitDo Ultimate Wireless / Pro 2 Wired Controller")

    r, w = os.pipe()
    got, states, done = [], [], threading.Event()

    def state(name):
        states.append(name)
        if name is None:
            done.set()

    reader = G.GamepadReader(got.append, state, finder=lambda: [G.Pad(r, "8BitDo", "evdev")] if not states else [])
    reader.start()
    os.write(w, ev(G.EV_KEY, 0x130, 1))
    os.close(w)                                            # unplugged
    assert done.wait(3)
    reader.stop()
    assert states[0] == "8BitDo" and states[-1] is None
    assert [(e.kind, e.number) for e in got] == [("button", G.BTN_A)]
    assert struct.calcsize("llHHi") == G.EV_EVENT.size
