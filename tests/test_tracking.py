"""Tracking of marked areas: camera-independent strokes, caching, overlay data."""
import cv2
import numpy as np
import pytest

from vtf.analysis import AnalysisSettings, MotionData, analyze_motion
from vtf.generator import GeneratorSettings, generate

W, H, FPS = 480, 270, 30


def _video(path, seconds=4.0, pan=True):
    """Area A strokes up/down at 1 Hz, area B is still; the camera pans in x and y."""
    rng = np.random.default_rng(0)
    bg = (rng.random((H * 2, W * 2)) * 80).astype(np.uint8)
    tex_a = (rng.random((44, 44)) * 255).astype(np.uint8)
    tex_b = (rng.random((44, 44)) * 255).astype(np.uint8)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    for i in range(int(seconds * FPS)):
        t = i / FPS
        cx = int(45 * np.sin(2 * np.pi * 0.3 * t)) if pan else 0
        cy = int(20 * np.sin(2 * np.pi * 0.2 * t)) if pan else 0
        f = bg[80 + cy:80 + cy + H, 150 + cx:150 + cx + W].copy()
        ya = int(110 + 38 * np.sin(2 * np.pi * t))
        f[ya - cy:ya - cy + 44, 150 - cx:194 - cx] = tex_a
        f[190 - cy:234 - cy, 290 - cx:334 - cx] = tex_b
        vw.write(cv2.cvtColor(f, cv2.COLOR_GRAY2BGR))
    vw.release()
    box_a = (150 / W, 110 / H, 44 / W, 44 / H)
    box_b = (290 / W, 190 / H, 44 / W, 44 / H)
    return path, box_a, box_b


def _peaks(motion):
    g = generate(motion, GeneratorSettings(axis="vertical", normalize_window_ms=0))
    return [a.at for a in g.actions if a.pos > 70]


def test_two_areas_ignore_camera(tmp_path):
    path, a, b = _video(tmp_path / "pan.avi")
    m = analyze_motion(path, AnalysisSettings(mode="track", track_boxes=(a, b), width=W, hwaccel="none"))
    assert m.tracks.shape == (len(m), 2, 2) and m.track_sizes.shape == (2, 2)
    peaks = _peaks(m)
    # area A is highest (smallest y) at t = 0.75 s + k
    expected = [750, 1750, 2750]
    assert all(any(abs(p - e) <= 120 for p in peaks) for e in expected), peaks
    # the overlay follows area A: its y centre moves by ~2*38 px
    ys = m.tracks[:, 0, 1] * H
    assert ys.max() - ys.min() > 60


def test_one_area_with_camera_compensation(tmp_path):
    path, a, _b = _video(tmp_path / "pan1.avi")
    m = analyze_motion(path, AnalysisSettings(mode="track", track_boxes=(a,), width=W, hwaccel="none",
                                              compensate_camera=True))
    peaks = _peaks(m)
    assert all(any(abs(p - e) <= 150 for p in peaks) for e in (750, 1750, 2750)), peaks


def test_start_frame_and_cache_roundtrip(tmp_path):
    path, a, b = _video(tmp_path / "s.avi", seconds=2.0, pan=False)
    s = AnalysisSettings(mode="track", track_boxes=(a, b), width=W, hwaccel="none", track_start_ms=0)
    m = analyze_motion(path, s)
    f = tmp_path / "m.npz"
    m.save(f)
    m2 = MotionData.load(f)
    assert np.allclose(m2.tracks, m.tracks) and np.allclose(m2.track_sizes, m.track_sizes)


def test_cache_key_unchanged_for_flow():
    # caches from before tracking existed stay valid
    s = AnalysisSettings()
    assert "track" not in s.cache_key() and "mode" not in s.cache_key()
    t = AnalysisSettings(mode="track", track_boxes=((0.1, 0.1, 0.2, 0.2),))
    assert t.cache_key() != s.cache_key()


def test_tracking_needs_area():
    from vtf.tracking import MultiTracker
    with pytest.raises(ValueError):
        MultiTracker([], (100, 100))
