"""Further axes: file names, loading, roll from tracking."""
import numpy as np

from vtf.analysis import MotionData
from vtf.funscript import Action, Funscript, funscript_path_for, load_axes
from vtf.generator import GeneratorSettings, generate_axes, roll_signal


def test_axis_file_names_and_loading(tmp_path):
    v = tmp_path / "clip.mp4"
    assert funscript_path_for(v) == tmp_path / "clip.funscript"
    assert funscript_path_for(v, "R1") == tmp_path / "clip.roll.funscript"
    assert funscript_path_for(v, "R0") == tmp_path / "clip.twist.funscript"
    Funscript([Action(0, 0), Action(500, 100)]).save(funscript_path_for(v))
    Funscript([Action(0, 50), Action(400, 80)]).save(funscript_path_for(v, "R1"))
    (tmp_path / "clip.pitch.funscript").write_text("kaputt")
    axes = load_axes(v)
    assert set(axes) == {"L0", "R1"}
    assert axes["R1"][1].pos == 80


def _rotating_motion(n=300, fps=30.0):
    t = np.arange(n) / fps
    ang = np.radians(20) * np.sin(2 * np.pi * 0.5 * t)       # rocks +-20 deg at 0.5 Hz
    c0 = np.full((n, 2), 0.5)
    c1 = c0 + 0.2 * np.stack([np.cos(ang), np.sin(ang)], axis=1)
    tracks = np.stack([c0, c1], axis=1).astype(np.float32)
    m = MotionData("x", fps, np.zeros(n, np.float32), np.zeros(n, np.float32),
                   {"width": 320, "height": 320}, tracks=tracks,
                   track_sizes=np.full((2, 2), 0.1, np.float32))
    return m, t


def test_roll_follows_the_angle_between_areas():
    m, _t = _rotating_motion()
    t_ms, sig = roll_signal(m, GeneratorSettings(smoothing_ms=50))
    # highest roll where the angle is largest (t = 0.5 s + 2k)
    i = int(np.argmax(sig[:60]))
    assert abs(t_ms[i] - 500) < 120
    assert sig.max() > 0.85 and sig.min() < 0.15
    axes = generate_axes(m, GeneratorSettings())
    assert "R1" in axes and len(axes["R1"]) >= 8 and "L2" in axes


def test_no_roll_without_two_areas():
    m = MotionData("x", 30.0, np.zeros(90, np.float32), np.zeros(90, np.float32))
    assert roll_signal(m, GeneratorSettings()) is None
    assert "R1" not in generate_axes(m)
