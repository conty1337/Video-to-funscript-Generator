"""Tracking of marked areas: camera-independent strokes, caching, overlay data."""
import cv2
import numpy as np
import pytest

from vtf.analysis import AnalysisSettings, MotionData, analyze_motion
from vtf.generator import GeneratorSettings, generate

W, H, FPS = 480, 270, 30


def _video(path, seconds=4.0, pan=True, amp=lambda t: 38):
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
        ya = int(110 + amp(t) * np.sin(2 * np.pi * t))
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
    assert m.tracks.shape == (len(m), 2, 2) and m.track_sizes.shape == (1, 2, 2)
    assert (m.track_section == 0).all()
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


def test_sections_with_end_and_gap(tmp_path):
    from vtf.analysis import TrackSection
    from vtf.generator import generate

    path, a, b = _video(tmp_path / "sec.avi", seconds=4.0, pan=False)
    sections = (TrackSection(0, (a, b), end_ms=1500), TrackSection(2500, (a, b)))
    m = analyze_motion(path, AnalysisSettings(mode="track", track_sections=sections, width=W, hwaccel="none"))
    t = np.arange(len(m)) / m.fps * 1000
    assert (m.track_section[t < 1450] == 0).all()
    gap = (t > 1550) & (t < 2450)
    assert (m.track_section[gap] == -1).all() and np.isnan(m.tracks[gap]).all()
    assert (m.track_section[t > 2550] == 1).all()
    acts = generate(m, GeneratorSettings(axis="vertical"), sections).actions
    assert acts and not any(1500 <= x.at < 2500 for x in acts)


def test_depth_calibration_keeps_small_strokes_small(tmp_path):
    from vtf.analysis import TrackSection
    from vtf.generator import generate

    # big strokes (+-38 px) for 2 s, then small ones (+-10 px)
    path, a, b = _video(tmp_path / "cal.avi", seconds=4.0, pan=False, amp=lambda t: 38 if t < 2 else 10)
    sec = TrackSection(0, (a, b))
    s = AnalysisSettings(mode="track", track_sections=(sec,), width=W, hwaccel="none")
    m = analyze_motion(path, s)
    g = GeneratorSettings(axis="vertical", smoothing_ms=50)
    # area A lowest = closest to B = "fully in" at 0.25 s, highest = "fully out" at 0.75 s
    cal = (TrackSection(0, (a, b), calib_in_ms=250, calib_out_ms=750),)
    acts = generate(m, g, cal).actions
    big = [x.pos for x in acts if 300 < x.at < 1900]
    small = [x.pos for x in acts if 2300 < x.at < 3900]
    assert max(big) >= 90 and min(big) <= 10
    assert small and 20 <= min(small) and max(small) <= 80          # real depth, not stretched
    assert max(small) - min(small) >= 10                            # but still detected
    # without calibration the small strokes would be stretched to the full range
    plain = generate(m, g, (sec,)).actions
    small_plain = [x.pos for x in plain if 2300 < x.at < 3900]
    assert max(small_plain) - min(small_plain) > max(small) - min(small)
    # the calibration does not change the analysis cache key
    assert s.cache_key() == AnalysisSettings(mode="track", track_sections=cal, width=W,
                                             hwaccel="none").cache_key()
