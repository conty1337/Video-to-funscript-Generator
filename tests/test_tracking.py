"""Tracking of marked areas: camera-independent strokes, caching, overlay data."""
import itertools

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


def _rot_video(path, seconds=2.0, cover=None):
    """A textured square turns by 30 degrees and grows by 20 % while moving right.
    `cover` = (t0, t1): the square is hidden (grey) in that time."""
    rng = np.random.default_rng(1)
    bg = (rng.random((H, W)) * 60).astype(np.uint8)
    tex = (rng.random((60, 60)) * 255).astype(np.uint8)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    n = int(seconds * FPS)
    for i in range(n):
        t = i / n
        f = bg.copy()
        if not (cover and cover[0] <= i / FPS < cover[1]):
            ang, sc = 30 * t, 1 + 0.2 * t
            cx, cy = 150 + 120 * t, 135
            m = cv2.getRotationMatrix2D((30, 30), -ang, sc)
            m[:, 2] += (cx - 30, cy - 30)
            patch = cv2.warpAffine(tex, m, (W, H), flags=cv2.INTER_LINEAR, borderValue=0)
            mask = cv2.warpAffine(np.full((60, 60), 255, np.uint8), m, (W, H)) > 128
            f[mask] = patch[mask]
        else:
            f[100:170, 100:300] = 90
        vw.write(cv2.cvtColor(f, cv2.COLOR_GRAY2BGR))
    vw.release()
    return path, (120 / W, 105 / H, 60 / W, 60 / H)


def test_area_turns_and_grows_with_the_object(tmp_path):
    from vtf.analysis import TrackSection

    path, box = _rot_video(tmp_path / "rot.avi")
    m = analyze_motion(path, AnalysisSettings(mode="track", track_sections=(TrackSection(0, (box,)),),
                                              width=W, hwaccel="none", compensate_camera=False))
    scale, angle = m.track_geom[-1, 0]
    assert 20 <= angle <= 40, angle                 # turned along (30 degrees)
    assert 1.08 <= scale <= 1.35, scale             # and grew (20 %)
    cx = m.tracks[-1, 0, 0] * W
    assert abs(cx - 266) < 15, cx                   # followed to the right


def test_hidden_area_is_lost_not_thrown_away(tmp_path):
    from vtf.analysis import TrackSection

    path, box = _rot_video(tmp_path / "cover.avi", seconds=2.0, cover=(0.6, 0.9))
    m = analyze_motion(path, AnalysisSettings(mode="track", track_sections=(TrackSection(0, (box,)),),
                                              width=W, hwaccel="none", compensate_camera=False))
    t = np.arange(len(m)) / m.fps
    hidden = (t > 0.65) & (t < 0.85)
    assert m.track_lost[hidden, 0].any()            # noticed
    xs = m.tracks[:, 0, 0] * W
    assert np.abs(np.diff(xs)).max() < 60           # no jump across the picture
    assert not m.track_lost[t > 1.5, 0].all()       # found again afterwards


def test_jump_is_rejected():
    """The object teleports across the picture: the area must not follow."""
    from vtf.tracking import AreaTracker

    rng = np.random.default_rng(2)
    bg = (rng.random((H, W)) * 60).astype(np.uint8)
    tex = (rng.random((40, 40)) * 255).astype(np.uint8)
    prev, cur = bg.copy(), bg.copy()
    prev[100:140, 100:140] = tex
    cur[150:190, 380:420] = tex                     # 290 px away
    a = AreaTracker((100, 100, 40, 40), (H, W), first=prev)
    a.step(prev, cur)
    assert np.hypot(*(a.center - (120, 120))) < a.max_jump()
    assert a.center[0] < 250


def test_rotated_corners():
    from vtf.tracking import box_corners

    c = box_corners(0, 0, 20, 10, 90)
    assert np.allclose(sorted(np.round(c[:, 0])), [-5, -5, 5, 5])
    assert np.allclose(sorted(np.round(c[:, 1])), [-10, -10, 10, 10])


def test_correction_moves_area_and_spreads_drift(tmp_path):
    """A correction ("nachjustieren") puts the area where it really is; the error is
    spread back so the positions do not jump."""
    from vtf.analysis import TrackSection

    path, a, b = _video(tmp_path / "k.avi", seconds=2.0, pan=False)
    # deliberately wrong second area at the start (30 px too far left) ...
    wrong_b = (b[0] - 30 / W, b[1], b[2], b[3])
    # ... corrected at 1 s to the real place
    sec = TrackSection(0, (a, wrong_b), keys=((1000, 1, b),))
    m = analyze_motion(path, AnalysisSettings(mode="track", track_sections=(sec,), width=W, hwaccel="none"))
    i = int(round(1.0 * m.fps))
    real_cx = (b[0] + b[2] / 2) * W
    assert abs(m.tracks[i, 1, 0] * W - real_cx) < 3              # corrected at the key
    xs = m.tracks[:i + 1, 1, 0] * W
    assert np.abs(np.diff(xs)).max() < 5                        # spread back: no jump
    assert abs(m.tracks[0, 1, 0] * W - (real_cx - 30)) < 3       # start unchanged
    # a correction is part of the analysis (new cache entry)
    plain = AnalysisSettings(mode="track", track_sections=(TrackSection(0, (a, wrong_b)),))
    assert plain.cache_key() != AnalysisSettings(mode="track", track_sections=(sec,)).cache_key()


def test_area_is_found_again_after_a_loss():
    """The texture vanishes for some frames and comes back a bit further on."""
    from vtf.tracking import AreaTracker

    rng = np.random.default_rng(3)
    tex = (rng.random((40, 40)) * 255).astype(np.uint8)
    flat = np.full((H, W), 40, np.uint8)
    f0 = flat.copy()
    f0[100:140, 100:140] = tex
    back = flat.copy()
    back[100:140, 125:165] = tex                     # reappears 25 px to the right
    a = AreaTracker((100, 100, 40, 40), (H, W), first=f0)
    seq = [f0, flat, flat, flat, flat, back, back]
    lost = []
    for prev, cur in itertools.pairwise(seq):
        a.step(prev, cur)
        lost.append(a.lost)
    assert any(lost[:4])                             # noticed the loss
    assert abs(a.center[0] - 145) < 4 and abs(a.center[1] - 120) < 4   # and found it again


def _scene(angle, shift=(0, 0), scale=1.0, seed=3):
    """Textured 60x40 patch on noise, turned by `angle` around (200, 140) and moved."""
    rng = np.random.default_rng(seed)
    img = (rng.random((280, 400)) * 60).astype(np.uint8)
    tex = cv2.GaussianBlur((rng.random((40, 60)) * 255).astype(np.uint8), (3, 3), 0)
    big = np.zeros_like(img)
    m = np.zeros_like(img)
    big[120:160, 170:230] = tex
    m[120:160, 170:230] = 255
    rot = cv2.getRotationMatrix2D((200, 140), -angle, scale)
    rot[:, 2] += shift
    big = cv2.warpAffine(big, rot, (400, 280))
    m = cv2.warpAffine(m, rot, (400, 280))
    img[m > 128] = big[m > 128]
    return img


def test_drift_correction_turns_with_the_area():
    """Marked upright; later turned by 35 deg and a bit off: the comparison still pulls it back."""
    from vtf.tracking import AreaTracker

    t = AreaTracker((170, 120, 60, 40, 0.0), (280, 400), _scene(0))
    later = _scene(35, shift=(6, 4))
    t.angle = 35.0
    t.center = np.array([200.0, 140.0])                       # tracking lagged 6/4 px behind
    for _ in range(4):
        t._drift_correction(later)
    assert np.hypot(*(t.center - [206, 144])) < 2.0, t.center


def test_correction_keeps_earlier_appearances():
    """After a correction the area knows both views and matches the better one."""
    from vtf.tracking import MultiTracker

    a = _scene(0)
    mt = MultiTracker([(170, 120, 60, 40)], (280, 400), first=a)
    b = _scene(0, seed=9)                                      # a different look, same place
    mt.reset_area(0, (170, 120, 60, 40, 0.0), b)
    area = mt.areas[0]
    assert len(area.templates) == 2
    # in a frame that looks like the first view, the inherited template is the one that fits
    score_old = area._match(a, area.templates[1], 0.5)[0]
    score_new = area._match(a, area.templates[0], 0.5)[0]
    assert score_old > 0.9 > score_new


def test_correction_blends_backward_track(tmp_path):
    """Area 1 is marked beside the moving object (so the forward track stays put) and
    corrected onto it at 1 s. Following it backwards from there catches its real
    motion shortly before the correction - a linear spread would not."""
    from vtf.analysis import TrackSection

    path, a, b = _video(tmp_path / "bk.avi", seconds=1.5, pan=False)
    wrong_a = (a[0] - 60 / W, a[1], a[2], a[3])
    sec = TrackSection(0, (wrong_a, b), keys=((1000, 0, a),))
    m = analyze_motion(path, AnalysisSettings(mode="track", track_sections=(sec,), width=W, hwaccel="none"))
    i = int(round(1.0 * m.fps))
    j = int(round(0.8 * m.fps))
    t = j / i
    y_true = 110 + 38 * np.sin(2 * np.pi * 0.8) + 22            # centre of area A at 0.8 s
    y_wrong = 110 + 22                                           # where the forward track stayed
    expected = (1 - t) * y_wrong + t * y_true
    assert abs(m.tracks[j, 0, 1] * H - expected) < 6, (m.tracks[j, 0, 1] * H, expected)
    assert abs(m.tracks[i, 0, 1] * H - (110 + 22)) < 3


def _covering_frames():
    """A still textured object; another texture slides over it, stays ~40 frames,
    then slides away to the right and leaves the object visible again."""
    rng = np.random.default_rng(5)
    bg = (rng.random((H, W)) * 50).astype(np.uint8)
    obj = (rng.random((40, 40)) * 255).astype(np.uint8)
    cover = cv2.GaussianBlur((rng.random((70, 70)) * 255).astype(np.uint8), (5, 5), 0)
    frames = []
    for i in range(110):
        f = bg.copy()
        f[100:140, 200:240] = obj
        if 10 <= i < 80:
            if i < 20:
                cx = 100 + (i - 10) * 8           # slides in from the left
            elif i < 60:
                cx = 185                          # covers the object
            else:
                cx = 185 + (i - 60) * 10          # slides away to the right
            f[85:155, cx:cx + 70] = cover
        frames.append(f)
    return frames


def test_area_comes_back_after_being_covered():
    from vtf.tracking import AreaTracker

    frames = _covering_frames()
    t = AreaTracker((200, 100, 40, 40), (H, W), first=frames[0])
    lost = []
    for prev, cur in itertools.pairwise(frames):
        t.step(prev, cur)
        lost.append(t.lost)
    assert any(lost[25:60]), "covering should show as lost"
    assert not any(lost[-10:])
    assert np.hypot(*(t.center - [220, 120])) < 4, t.center     # back on the object


def test_area_only_moves_when_turning_is_off(tmp_path):
    """Machines: with turning off the area keeps its angle and size and still follows."""
    path, a, _b = _video(tmp_path / "rig.avi", pan=False)
    m = analyze_motion(path, AnalysisSettings(mode="track", track_boxes=(a,), width=W, hwaccel="none",
                                              compensate_camera=False, track_turn=False))
    assert (m.track_geom[:, 0, 1] == 0.0).all() and (m.track_geom[:, 0, 0] == 1.0).all()
    ys = m.tracks[:, 0, 1] * H
    assert ys.max() - ys.min() > 60                      # followed the strokes (2 x 38 px)
    peaks = _peaks(m)
    assert all(any(abs(p - e) <= 120 for p in peaks) for e in (750, 1750, 2750)), peaks
    s1 = AnalysisSettings(mode="track", track_boxes=(a,))
    assert s1.cache_key() != AnalysisSettings(mode="track", track_boxes=(a,), track_turn=False).cache_key()


def test_area_dragged_away_counts_as_covered():
    """A hand partly in front drags the area slowly away while it only half looks
    like itself: after a few such checks it goes back and waits (covered)."""
    from vtf.tracking import AreaTracker

    frames = _covering_frames()
    t = AreaTracker((200, 100, 40, 40), (H, W), first=frames[0])
    good = t.center.copy()
    t.center = good + [6.0, 0.0]                     # still close: half-good looks are fine
    assert not any(t._check_covered(0.5) for _ in range(6)) and not t.covered
    t.unsure_checks = 0
    t.center = good + [25.0, 0.0]                    # pulled well away and not looking right
    hits = [t._check_covered(0.5) for _ in range(4)]
    assert hits[-1] and t.covered and np.allclose(t.center, good)


def test_box_does_not_grow_from_a_small_cluster():
    """Only a small textured spot inside a big area: the area follows it but does
    not start to grow or tilt from the wobbly few points."""
    from vtf.tracking import AreaTracker

    rng = np.random.default_rng(11)
    spot = (rng.random((18, 18)) * 255).astype(np.uint8)
    frames = []
    for i in range(60):
        f = np.full((H, W), 60, np.uint8)
        x = 200 + int(20 * np.sin(i / 6))
        f[120:138, x:x + 18] = spot
        frames.append(cv2.GaussianBlur(f, (3, 3), 0))
    t = AreaTracker((170, 90, 80, 80), (H, W), first=frames[0])
    for prev, cur in itertools.pairwise(frames):
        t.step(prev, cur)
    assert 0.9 < t.scale < 1.1 and abs(t.angle) < 5, (t.scale, t.angle)


def test_anchored_areas_stay_put(tmp_path):
    """Areas 2/3 anchored: they do not move (fixed camera), or move only with the
    camera (pan, compensated); the strokes of area 1 are still found."""
    path, a, b = _video(tmp_path / "anc.avi", pan=False)
    m = analyze_motion(path, AnalysisSettings(mode="track", track_boxes=(a, b), width=W, hwaccel="none",
                                              track_anchor=True))
    xs, ys = m.tracks[:, 1, 0] * W, m.tracks[:, 1, 1] * H
    assert np.ptp(xs) < 1.5 and np.ptp(ys) < 1.5               # area 2 did not move at all
    peaks = _peaks(m)
    assert all(any(abs(p - e) <= 120 for p in peaks) for e in (750, 1750, 2750)), peaks
    # with a panning camera the anchored area moves along with it
    path, a, b = _video(tmp_path / "anc_pan.avi", pan=True)
    m = analyze_motion(path, AnalysisSettings(mode="track", track_boxes=(a, b), width=W, hwaccel="none",
                                              track_anchor=True, compensate_camera=True))
    assert np.ptp(m.tracks[:, 1, 0] * W) > 40                  # followed the pan (±45 px)
    peaks = _peaks(m)
    assert all(any(abs(p - e) <= 150 for p in peaks) for e in (750, 1750, 2750)), peaks
    s = AnalysisSettings(mode="track", track_boxes=(a, b))
    assert s.cache_key() != AnalysisSettings(mode="track", track_boxes=(a, b), track_anchor=True).cache_key()
