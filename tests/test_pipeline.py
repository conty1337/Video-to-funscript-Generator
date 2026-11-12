import json

import numpy as np

from vtf.analysis import AnalysisSettings, MotionData, analyze_motion
from vtf.batch import process_video, run_batch
from vtf.funscript import Action, Funscript, funscript_path_for, position_at
from vtf.generator import (GeneratorSettings, enforce_min_interval, find_extrema,
                           generate)
from vtf.video import probe


def test_probe(sine_video):
    info = probe(sine_video)
    assert (info.width, info.height) == (320, 240)
    assert abs(info.fps - 30) < 0.1
    assert abs(info.frame_count - 180) <= 1


def test_analysis_detects_strokes(sine_video):
    motion = analyze_motion(sine_video, AnalysisSettings(hwaccel="none"))
    assert len(motion) >= 179
    script = generate(motion, GeneratorSettings())
    acts = script.actions
    # 6 s at 1 Hz -> ~6 peaks and ~6 valleys
    assert 9 <= len(acts) <= 14, [(a.at, a.pos) for a in acts]
    highs = [a for a in acts if a.pos > 70]
    lows = [a for a in acts if a.pos < 30]
    assert len(highs) >= 4 and len(lows) >= 4
    # the square is at the top at t = 0.25 s + k
    for a in highs:
        phase = (a.at / 1000.0 - 0.25) % 1.0
        assert min(phase, 1 - phase) < 0.15, a


def test_invert(sine_video):
    motion = analyze_motion(sine_video, AnalysisSettings(hwaccel="none"))
    a = generate(motion, GeneratorSettings()).signal
    b = generate(motion, GeneratorSettings(invert=True)).signal
    assert np.corrcoef(a, b)[0, 1] < -0.9


def test_extrema_alternate():
    t = np.linspace(0, 10, 1000)
    sig = 0.5 + 0.4 * np.sin(2 * np.pi * t) + 0.02 * np.sin(2 * np.pi * 13 * t)
    idx = find_extrema(sig, 0.1)
    assert 19 <= len(idx) <= 22  # 10 periods + start/end point
    kinds = [sig[idx[i]] > sig[idx[i + 1]] for i in range(len(idx) - 1)]
    assert all(kinds[i] != kinds[i + 1] for i in range(len(kinds) - 1))


def test_min_interval_keeps_alternation():
    t_ms = np.arange(10) * 50.0
    sig = np.array([0, 1, 0.9, 1.0, 0, 0.1, 0, 1, 0, 1])
    idx = enforce_min_interval(list(range(10)), t_ms, sig, 120)
    for a, b in zip(idx, idx[1:]):
        assert t_ms[b] - t_ms[a] >= 120


def test_funscript_roundtrip(tmp_path):
    fs = Funscript([Action(500, 90), Action(0, 10)], metadata={"title": "x"})
    p = tmp_path / "a.funscript"
    fs.save(p)
    d = json.loads(p.read_text())
    assert d["actions"] == [{"at": 0, "pos": 10}, {"at": 500, "pos": 90}]
    assert Funscript.load(p).actions[1].pos == 90


def test_batch_skips_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from videogen import make_video
    folder = tmp_path / "videos"
    (folder / "sub").mkdir(parents=True)
    v1 = make_video(folder / "a.avi", seconds=2)
    v2 = make_video(folder / "sub" / "b.avi", seconds=2)
    an = AnalysisSettings(hwaccel="none")
    gen = GeneratorSettings()
    r = run_batch(folder, an, gen)
    assert len(r.done) == 2 and not r.failed
    assert funscript_path_for(v1).is_file() and funscript_path_for(v2).is_file()
    r = run_batch(folder, an, gen)
    assert len(r.skipped) == 2 and not r.done
    r = run_batch(folder, an, gen, overwrite=True, recursive=False)
    assert len(r.done) == 1


def test_motion_cache_roundtrip(tmp_path):
    m = MotionData("v", 30.0, np.arange(5, dtype=np.float32), np.ones(5, np.float32))
    m.save(tmp_path / "m.npz")
    m2 = MotionData.load(tmp_path / "m.npz")
    assert m2.fps == 30.0 and np.allclose(m2.dx, m.dx)


def test_position_at():
    acts = [Action(0, 0), Action(1000, 100), Action(2000, 50)]
    assert position_at([], 5) is None
    assert position_at(acts, -10) == 0
    assert position_at(acts, 500) == 50
    assert position_at(acts, 1500) == 75
    assert position_at(acts, 9999) == 50


def test_turn_time_between_frames():
    from vtf.generator import turn_time
    t = np.arange(10) * 33.3
    true_t = 4.3 * 33.3                                  # a peak between frame 4 and 5
    sig = 1 - ((t - true_t) / 100) ** 2
    i = int(np.argmax(sig))
    assert abs(turn_time(t, sig, i) - true_t) < 1.0       # within 1 ms, a frame is 33 ms
    assert turn_time(t, sig, 0) == t[0]                   # at the edge: the frame itself


def test_small_strokes_right_after_big_ones_are_found():
    """The curve is scaled to the big strokes around; small strokes next to them still count."""
    from vtf.generator import extract_actions
    fps = 30
    t = np.arange(int(8 * fps)) / fps
    amp = np.where(t < 4, 1.0, 0.2)
    sig = 0.5 + 0.5 * amp * np.sin(2 * np.pi * 1.4 * t)    # big, then strokes a fifth as big
    acts = extract_actions(t * 1000, sig, GeneratorSettings())
    small = [a for a in acts if a.at > 4300]
    assert len(small) >= 9                                 # ~2.8 strokes/s * 3.7 s, both turns
    # still: tiny wobble on a quiet curve is not taken for strokes
    quiet = 0.5 + 0.01 * np.sin(2 * np.pi * 7 * t)
    assert len(extract_actions(t * 1000, quiet, GeneratorSettings())) <= 2


def _plateau_signal(kind: str):
    """1 stroke per second at 50 fps, the deep end held 0.4 s: flat ('flat'), with a small
    dip in the middle ('dip'), or a real double stroke ('double')."""
    fps = 50
    t = np.arange(0, 10, 1 / fps)
    ph = t % 1.0
    up = np.clip(ph / 0.3, 0, 1)
    down = np.clip((1.0 - ph) / 0.3, 0, 1)
    sig = np.minimum(up, down)                              # 0 -> 1 (held 0.3..0.7) -> 0
    held = (ph > 0.3) & (ph < 0.7)
    dip = {"flat": 0.0, "dip": 0.12, "double": 0.6}[kind]
    sig = np.where(held, 1.0 - dip * np.sin(np.pi * (ph - 0.3) / 0.4), sig)
    return t * 1000, sig


def test_plateau_gets_one_point_in_its_middle():
    from vtf.generator import extract_actions

    for kind in ("flat", "dip"):
        t, sig = _plateau_signal(kind)
        acts = extract_actions(t, sig, GeneratorSettings(sensitivity=70, min_interval_ms=60))
        tops = [a for a in acts if a.pos > 70]
        assert 8 <= len(tops) <= 10, (kind, [(a.at, a.pos) for a in acts])
        for a in tops[1:-1]:
            assert abs(a.at % 1000 - 500) <= 40, (kind, a)          # middle of the held stretch
            assert a.pos >= 95
        # switched off: as before (a dip gives two tops, flat the start of the stretch)
        old = extract_actions(t, sig, GeneratorSettings(sensitivity=70, min_interval_ms=60, plateaus=False))
        old_tops = [a for a in old if a.pos > 70]
        assert len(old_tops) > len(tops) or any(abs(a.at % 1000 - 500) > 100 for a in old_tops[1:-1])
    # a real double stroke at the deep end stays two strokes
    t, sig = _plateau_signal("double")
    acts = extract_actions(t, sig, GeneratorSettings(sensitivity=70, min_interval_ms=60))
    assert len([a for a in acts if a.pos > 70]) >= 16
