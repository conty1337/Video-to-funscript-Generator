"""Body recognition: geometry, anchors, download and the AI check during tracking.
The real models are not needed: a stand-in finds the bright object in the frame."""
import hashlib
import io
import urllib.error
import zipfile

import cv2
import numpy as np
import pytest

from vtf import pose
from vtf.analysis import AnalysisSettings, TrackSection, analyze_motion


def _person(center=(100.0, 120.0), height=200.0, conf=5.0):
    pts = np.zeros((pose.N_POINTS, 2))
    cf = np.zeros(pose.N_POINTS)
    cx, cy = center
    pts[pose.L_HIP], pts[pose.R_HIP] = (cx + 10, cy), (cx - 10, cy)
    pts[pose.L_KNEE], pts[pose.R_KNEE] = (cx + 10, cy + 50), (cx - 10, cy + 50)
    pts[pose.L_SHOULDER], pts[pose.R_SHOULDER] = (cx + 15, cy - 70), (cx - 15, cy - 70)
    for k, idx in enumerate(pose.R_HAND):
        pts[idx] = (cx - 40 + k % 5, cy - 20 + k // 5)
    cf[[pose.L_HIP, pose.R_HIP, pose.L_KNEE, pose.R_KNEE, pose.L_SHOULDER, pose.R_SHOULDER]] = conf
    cf[list(pose.R_HAND)] = conf
    box = np.array([cx - 50, cy - 120, cx + 50, cy - 120 + height])
    return pose.Person(box, 0.9, pts, cf)


def test_region_boxes_sit_on_the_body_part():
    p = _person()
    x, y, w, h = pose.region_box(p, "pelvis")
    assert abs(x + w / 2 - 100) < 1 and 120 < y + h / 2 < 135      # a bit below the hip line
    x, y, w, h = pose.region_box(p, "hand_r")
    assert 58 < x + w / 2 < 64
    assert pose.region_box(p, "mouth") is None                      # face not seen
    labels = [r.label for _i, r, _b in pose.suggestions([p])]
    assert "Becken / Schritt" in labels and "Hand rechts" in labels and "Mund" not in labels


def test_anchor_follows_the_body_and_ignores_hidden_points():
    a = pose.Anchor.from_box([_person()], (100, 125), (40, 40))
    assert set(a.idx) == {pose.L_HIP, pose.R_HIP}
    moved = _person(center=(130, 90))
    c, n = a.locate([moved], (100, 125))
    np.testing.assert_allclose(c, (130, 95), atol=1e-6)
    assert n == 2
    hidden = _person(center=(130, 90))
    hidden.conf[[pose.L_HIP, pose.R_HIP]] = 0.2
    assert a.locate([hidden], (100, 125)) is None
    # two people: the one nearest to the area counts
    c, _n = a.locate([_person(center=(300, 90)), moved], (120, 100))
    assert abs(c[0] - 130) < 1e-6


def test_anchor_needs_body_points_in_the_box():
    assert pose.Anchor.from_box([_person()], (300, 300), (40, 40)) is None


def _zip(data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("some/dir/end2end.onnx", data)
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status


class _Server:
    """Stand-in for the network: url -> bytes, honours Range, can fail."""
    def __init__(self, files, fail_after=None, missing=()):
        self.files, self.fail_after, self.missing = files, dict(fail_after or {}), set(missing)
        self.calls = []

    def __call__(self, req):
        url = req.full_url
        self.calls.append((url, req.headers.get("Range")))
        if url in self.missing or url not in self.files:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        data = self.files[url]
        start = 0
        rng = req.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
        n = self.fail_after.pop(url, None)
        if n is not None:                      # deliver n bytes, then time out
            class Broken(_Resp):
                def read(self, size=-1):
                    if self.tell() >= n:
                        raise TimeoutError("The read operation timed out")
                    return super().read(min(size, n - self.tell()))
            return Broken(data[start:], 206 if start else 200)
        return _Resp(data[start:], 206 if start else 200)


def _model(name, data, mb=1):
    return pose.ModelFile(name, f"https://mm.invalid/{name}.zip", "end2end.onnx",
                          hashlib.sha256(data).hexdigest(), mb)


def test_download_from_mirror_checks_and_keeps_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    good = b"onnx" * 100
    m = _model("a.onnx", good)
    monkeypatch.setattr(pose, "MODELS", (m,))
    server = _Server({m.mirror_url: good})
    seen = []
    pose.download(lambda p, n: seen.append(p), opener=server)
    assert (pose.models_dir() / "a.onnx").read_bytes() == good and seen[-1] == 1.0
    assert pose.installed() and server.calls == [(m.mirror_url, None)]
    assert not list(pose.models_dir().glob("*.part"))


def test_download_falls_back_to_openmmlab_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    good = b"onnx" * 100
    m = _model("a.onnx", good)
    monkeypatch.setattr(pose, "MODELS", (m,))
    pose.download(opener=_Server({m.url: _zip(good)}))
    assert (pose.models_dir() / "a.onnx").read_bytes() == good
    assert not list(pose.models_dir().glob("*.part"))


def test_download_continues_after_a_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    good = bytes(range(256)) * 4000
    m = _model("a.onnx", good)
    monkeypatch.setattr(pose, "MODELS", (m,))
    monkeypatch.setattr(pose, "CHUNK", 1000)
    server = _Server({m.mirror_url: good}, fail_after={m.mirror_url: 300_000})
    pose.download(opener=server)
    assert (pose.models_dir() / "a.onnx").read_bytes() == good
    assert server.calls == [(m.mirror_url, None), (m.mirror_url, "bytes=300000-")]


def test_cancelled_download_continues_next_time(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    good = bytes(range(256)) * 4000
    m = _model("a.onnx", good)
    monkeypatch.setattr(pose, "MODELS", (m,))
    monkeypatch.setattr(pose, "CHUNK", 1000)
    reads = []
    with pytest.raises(pose.DownloadCancelled):
        pose.download(cancel=lambda: reads.append(1) or len(reads) > 50,
                      opener=_Server({m.mirror_url: good}))
    assert (pose.models_dir() / "a.onnx.part").stat().st_size == 50_000
    server = _Server({m.mirror_url: good})
    pose.download(opener=server)
    assert (pose.models_dir() / "a.onnx").read_bytes() == good
    assert server.calls == [(m.mirror_url, "bytes=50000-")]


def test_bad_checksum_everywhere_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    good = b"onnx" * 100
    bad = pose.ModelFile("b.onnx", "https://mm.invalid/b.zip", "end2end.onnx", "0" * 64, 1)
    monkeypatch.setattr(pose, "MODELS", (bad,))
    with pytest.raises(RuntimeError, match="Prüfsumme"):
        pose.download(opener=_Server({bad.mirror_url: good, bad.url: _zip(good)}))
    assert not (pose.models_dir() / "b.onnx").exists()
    assert not list(pose.models_dir().glob("*.part"))


def test_new_model_version_replaces_the_old_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    folder = pose.models_dir()
    folder.mkdir(parents=True)
    (folder / "old-model.onnx").write_bytes(b"old")
    new = b"new" * 10
    m = _model("new-model.onnx", new)
    monkeypatch.setattr(pose, "MODELS", (m,))
    assert not pose.installed()
    pose.download(opener=_Server({m.mirror_url: new}))
    assert pose.installed() and not (folder / "old-model.onnx").exists()


# ---------------------------------------------------------------- AI check in the analysis
W, H, OBJ, N = 320, 240, 60, 150


def _cover_video(path):
    """A bright textured square moving up and down; for 1.4 s a 'hand' (other texture)
    rests on it and moves along. Returns the true centre y per frame."""
    rng = np.random.default_rng(3)

    def tex(h, w, lo, span):
        t = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), 1.5)
        return (lo + span * (t - t.min()) / np.ptp(t)).astype(np.uint8)

    bg, obj, hand = tex(H, W, 20, 60), tex(OBJ, OBJ, 150, 100), tex(34, 70, 90, 50)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (W, H))
    ys = []
    for i in range(N):
        y = int(round(120 + 40 * np.sin(2 * np.pi * 0.9 * i / 30)))
        f = bg.copy()
        f[y - OBJ // 2:y + OBJ // 2, W // 2 - OBJ // 2:W // 2 + OBJ // 2] = obj
        if 40 <= i < 82:
            hx = W // 2 - 35 + int(12 * np.sin(i * 0.4))
            f[y - 17:y + 17, hx:hx + 70] = hand
        vw.write(cv2.cvtColor(f, cv2.COLOR_GRAY2BGR))
        ys.append(y)
    vw.release()
    return np.array(ys, float)


class _SeesTheSquare:
    """Stand-in for the pose models: the hips are where the bright square is (its rows
    left and right of the hand still show it)."""
    calls = 0

    def people(self, img, reuse=False):
        _SeesTheSquare.calls += 1
        g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ys, xs = np.nonzero(g > 145)
        if len(ys) < 50:
            return []
        return [_person(center=(float(np.median(xs)), float((ys.min() + ys.max()) / 2)), height=200.0)]


def test_ai_check_keeps_the_area_on_its_body_part(tmp_path, monkeypatch):
    path = tmp_path / "cover.avi"
    ys = _cover_video(path)
    box = ((W / 2 - 25) / W, (ys[0] - 25) / H, 50 / W, 50 / H)
    sec = TrackSection(0, (box,))
    monkeypatch.setattr(pose, "installed", lambda: True)
    monkeypatch.setattr(pose, "PoseEstimator", _SeesTheSquare)
    errs = {}
    for ai in (False, True):
        m = analyze_motion(path, AnalysisSettings(mode="track", track_sections=(sec,), width=W,
                                                  hwaccel="none", ai_check=ai))
        tr = m.tracks[:, 0, 1] * H
        errs[ai] = float(np.sqrt(np.nanmean((tr - ys[:len(tr)]) ** 2)))
    assert _SeesTheSquare.calls > 5
    assert errs[True] < 4.0
    assert errs[True] < errs[False]


def test_ai_check_is_part_of_the_cache_key_only_when_on():
    a = AnalysisSettings(mode="track", track_boxes=((0.1, 0.1, 0.2, 0.2),))
    assert "ai_check" not in a.cache_key()
    assert AnalysisSettings(mode="track", track_boxes=((0.1, 0.1, 0.2, 0.2),), ai_check=True).cache_key() != a.cache_key()


def test_knots_lose_outliers_but_keep_strokes():
    from vtf.analysis import _smooth_knots
    ts = list(range(0, 90, 3))
    ys = 40 * np.sin(np.array(ts) / 30 * 2 * np.pi)
    noisy = ys.copy()
    noisy[10] += 30                                   # one wild guess under the cover
    out = np.array([c[1] for _t, c in _smooth_knots([(t, np.array([0.0, y])) for t, y in zip(ts, noisy)])])
    assert abs(out[10] - ys[10]) < 6
    assert out.max() > 33 and out.min() < -33         # strokes keep most of their height


def test_people_search_reuses_last_boxes():
    """Between regular checks the people are not searched again: their points are looked
    for where they were."""
    est = pose.PoseEstimator(det_net=object(), pose_net=object())
    calls = {"detect": 0}
    p = _person(center=(160, 140))

    def detect(img):
        calls["detect"] += 1
        return [(p.box, 0.9)]

    est.detect = detect
    est.keypoints = lambda img, box: (p.pts, p.conf)
    img = np.zeros((240, 320), np.uint8)
    est.people(img)
    est.people(img, reuse=True)
    est.people(img, reuse=True)
    assert calls["detect"] == 1
    est.keypoints = lambda img, box: (p.pts, np.zeros(pose.N_POINTS))     # nobody there now
    est.people(img, reuse=True)
    assert calls["detect"] == 2                       # then a full search


def test_pick_box_for_point_and_line():
    p = _person(center=(100.0, 120.0), height=200.0)
    x, y, w, h, ang = pose.pick_box(p, pose.L_HIP)
    assert abs(x + w / 2 - 110) < 1e-6 and abs(y + h / 2 - 120) < 1e-6 and w == h and ang == 0
    # thigh: hip (110, 120) -> knee (110, 170): turned box along it, mostly upright
    x, y, w, h, ang = pose.pick_box(p, (pose.L_HIP, pose.L_KNEE))
    assert abs(x + w / 2 - 110) < 1e-6 and abs(y + h / 2 - 145) < 1e-6
    assert abs(abs(ang) - 90) < 1e-6 and w > 50 and h < w
    assert pose.pick_box(p, (pose.L_HIP, pose.L_HIP)) is None
    assert pose.point_name(pose.L_KNEE) == "Knie links"
    assert pose.line_name(pose.L_HIP, pose.L_KNEE) == "Oberschenkel links"
    assert all(0 <= a < pose.N_POINTS and 0 <= b < pose.N_POINTS for a, b in pose.LINKS)
