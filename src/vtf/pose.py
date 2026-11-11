"""Body pose recognition (local, no internet after the download).

Two ONNX models from OpenMMLab (Apache-2.0), run with OpenCV's DNN module that the
app already contains:

* YOLOX-m (Human-Art) finds people in the frame,
* RTMW-l (whole body, 133 points: body, feet, face, both hands with fingers) finds the
  points of each person.

Together about 330 MB, downloaded once on first use. Used for two things:

* suggesting boxes for body parts (pelvis, hand, mouth, ...) when marking areas,
* checking during the analysis whether a box still sits on the body part it was
  put on (an *anchor*: the body points inside the box at marking time), and pulling
  it back when it has slipped away.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

_BASE = "https://download.openmmlab.com/mmpose/v1/projects"


@dataclass(frozen=True)
class ModelFile:
    name: str            # file name in the models folder
    url: str
    member: str          # path of the ONNX file inside the zip (suffix)
    sha256: str
    mb: int              # download size


DETECTOR = ModelFile(
    "yolox-m-humanart.onnx",
    f"{_BASE}/rtmposev1/onnx_sdk/yolox_m_8xb8-300e_humanart-c2c7a14a.zip",
    "end2end.onnx",
    "3dea6513388889f0fff4b77bf7a26013600321b9eb9ceb0e9a400a82572f5f23", 94)
POSE = ModelFile(
    "rtmw-l-wholebody-384x288.onnx",
    f"{_BASE}/rtmw/onnx_sdk/rtmw-dw-x-l_simcc-cocktail14_270e-384x288_20231122.zip",
    "end2end.onnx",
    "bd033156e5104c4f5d2edfe0453e02661e30a2f3da453ec93c8764d561b83054", 213)
MODELS = (DETECTOR, POSE)

DET_SIZE = 640
POSE_W, POSE_H = 288, 384
SIMCC_SPLIT = 2.0
MEAN = np.array([123.675, 116.28, 103.53], np.float32)
STD = np.array([58.395, 57.12, 57.375], np.float32)
PERSON_SCORE = 0.45      # detector: this sure it is a person
SURE = 1.5               # point score (SimCC peak) from which a point counts as seen
N_POINTS = 133

# COCO-WholeBody point numbers
NOSE, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 0, 5, 6, 11, 12
L_WRIST, R_WRIST, L_KNEE, R_KNEE = 9, 10, 13, 14
FACE = range(23, 91)
MOUTH = range(71, 91)
L_HAND = range(91, 112)
R_HAND = range(112, 133)
FEET = range(17, 23)


# ---------------------------------------------------------------- download
def models_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(Path.home(), ".local", "share")
    return Path(base) / "video-to-funscript" / "models"


def installed() -> bool:
    return all((models_dir() / m.name).is_file() for m in MODELS)


def download_mb() -> int:
    return sum(m.mb for m in MODELS if not (models_dir() / m.name).is_file())


class DownloadCancelled(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(progress: Callable[[float, str], None] | None = None,
             cancel: Callable[[], bool] | None = None, opener=None) -> None:
    """Fetch the missing models (zip from OpenMMLab, the ONNX file is kept and checked)."""
    from .updater import ssl_context

    folder = models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    todo = [m for m in MODELS if not (folder / m.name).is_file()]
    total = sum(m.mb for m in todo) * 1e6 or 1.0
    done = 0.0
    for m in todo:
        req = urllib.request.Request(m.url, headers={"User-Agent": "video-to-funscript"})
        if opener is None:
            resp = urllib.request.urlopen(req, timeout=60, context=ssl_context())
        else:
            resp = opener(req)
        with resp, tempfile.TemporaryFile() as tmp:
            while True:
                if cancel and cancel():
                    raise DownloadCancelled()
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                tmp.write(chunk)
                done += len(chunk)
                if progress:
                    progress(min(0.99, done / total), m.name)
            tmp.seek(0)
            with zipfile.ZipFile(io.BufferedReader(tmp)) as z:
                member = next((n for n in z.namelist() if n.endswith(m.member)), None)
                if member is None:
                    raise RuntimeError(f"{m.name}: Modell nicht im Archiv gefunden")
                part = folder / (m.name + ".part")
                with z.open(member) as src, open(part, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
        if _sha256(part) != m.sha256:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{m.name}: Prüfsumme stimmt nicht, Download beschädigt")
        part.replace(folder / m.name)
    if progress:
        progress(1.0, "")


# ---------------------------------------------------------------- recognition
@dataclass
class Person:
    box: np.ndarray                 # x0, y0, x1, y1 (px)
    score: float
    pts: np.ndarray                 # (133, 2) px
    conf: np.ndarray                # (133,) SimCC peak, >= SURE: seen

    def seen(self, idx) -> np.ndarray:
        idx = np.asarray(list(idx))
        return idx[self.conf[idx] >= SURE]

    @property
    def height(self) -> float:
        return float(max(1.0, self.box[3] - self.box[1]))


def _bgr(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img


def _letterbox(img: np.ndarray, size: int) -> tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    r = min(size / h, size / w)
    out = np.full((size, size, 3), 114, np.uint8)
    nh, nw = max(1, int(round(h * r))), max(1, int(round(w * r)))
    out[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return out, r


def _crop_affine(box, w: int = POSE_W, h: int = POSE_H, padding: float = 1.25) -> np.ndarray:
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    bw, bh = (x1 - x0) * padding, (y1 - y0) * padding
    if bw / max(bh, 1e-6) > w / h:
        bh = bw * h / w
    else:
        bw = bh * w / h
    src = np.float32([[cx - bw / 2, cy - bh / 2], [cx + bw / 2, cy - bh / 2], [cx - bw / 2, cy + bh / 2]])
    dst = np.float32([[0, 0], [w, 0], [0, h]])
    return cv2.getAffineTransform(src, dst)


class PoseEstimator:
    """Finds people and their 133 body points. Nets are loaded on first use."""

    def __init__(self, folder: Path | None = None, det_net=None, pose_net=None):
        self.folder = folder or models_dir()
        self._det = det_net
        self._pose = pose_net
        self._last: list[Person] = []

    def _nets(self):
        if self._det is None:
            self._det = cv2.dnn.readNetFromONNX(str(self.folder / DETECTOR.name))
        if self._pose is None:
            self._pose = cv2.dnn.readNetFromONNX(str(self.folder / POSE.name))
        return self._det, self._pose

    @staticmethod
    def _forward(net, blob):
        net.setInput(blob)
        return net.forward(net.getUnconnectedOutLayersNames())

    def detect(self, img: np.ndarray) -> list[tuple[np.ndarray, float]]:
        det, _pose = self._nets()
        lb, r = _letterbox(_bgr(img), DET_SIZE)
        dets, _labels = self._forward(det, lb.transpose(2, 0, 1)[None].astype(np.float32))
        d = np.asarray(dets).reshape(-1, 5)
        d = d[d[:, 4] >= PERSON_SCORE]
        h, w = img.shape[:2]
        out = []
        for x0, y0, x1, y1, s in d:
            b = np.array([x0, y0, x1, y1]) / r
            b = np.clip(b, 0, [w, h, w, h])
            if b[2] - b[0] >= 8 and b[3] - b[1] >= 8:
                out.append((b, float(s)))
        return out

    def keypoints(self, img: np.ndarray, box) -> tuple[np.ndarray, np.ndarray]:
        _det, pose = self._nets()
        a = _crop_affine(box)
        crop = cv2.warpAffine(_bgr(img), a, (POSE_W, POSE_H), flags=cv2.INTER_LINEAR)
        x = (cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) - MEAN) / STD
        sx, sy = self._forward(pose, x.transpose(2, 0, 1)[None].astype(np.float32))
        sx, sy = np.asarray(sx)[0], np.asarray(sy)[0]
        kx = sx.argmax(1) / SIMCC_SPLIT
        ky = sy.argmax(1) / SIMCC_SPLIT
        conf = np.minimum(sx.max(1), sy.max(1)).astype(np.float64)
        inv = cv2.invertAffineTransform(a)
        pts = np.c_[kx, ky, np.ones_like(kx)] @ inv.T
        return pts.astype(np.float64), conf

    def people(self, img: np.ndarray, reuse: bool = False) -> list[Person]:
        """All people with their points. reuse: take the people found last time and look
        for their points around where they were (no new search for people, about three
        times faster); falls back to a full search when that finds nobody."""
        boxes = []
        if reuse and self._last:
            h, w = img.shape[:2]
            for p in self._last:
                seen = p.pts[p.conf >= SURE]
                if len(seen) < 4:
                    continue
                (x0, y0), (x1, y1) = seen.min(axis=0), seen.max(axis=0)
                gx, gy = 0.15 * (x1 - x0) + 8, 0.15 * (y1 - y0) + 8
                b = np.clip([x0 - gx, y0 - gy, x1 + gx, y1 + gy], 0, [w, h, w, h])
                boxes.append((b, p.score))
        out = []
        for box, score in boxes:
            pts, conf = self.keypoints(img, box)
            if (conf >= SURE).sum() >= 4:
                out.append(Person(box, score, pts, conf))
        if not out:
            out = [Person(box, score, *self.keypoints(img, box)) for box, score in self.detect(img)]
        self._last = out
        return out


# ---------------------------------------------------------------- body parts
@dataclass(frozen=True)
class Region:
    key: str
    label: str


REGIONS = (
    Region("pelvis", "Becken / Schritt"),
    Region("mouth", "Mund"),
    Region("head", "Kopf"),
    Region("hand_r", "Hand rechts"),
    Region("hand_l", "Hand links"),
    Region("chest", "Brust"),
    Region("hips", "Hüfte (breit)"),
)


def _square(cx: float, cy: float, side: float) -> tuple[float, float, float, float]:
    return (cx - side / 2, cy - side / 2, side, side)


def _from_points(p: Person, idx, grow: float, min_side: float):
    seen = p.seen(idx)
    if len(seen) < 3:
        return None
    pts = p.pts[seen]
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = max((x1 - x0) * grow, min_side)
    h = max((y1 - y0) * grow, min_side)
    return (cx - w / 2, cy - h / 2, w, h)


def region_box(p: Person, key: str):
    """Box (x, y, w, h px) for a body part of person p, None when it is not visible."""
    torso = p.height * 0.08
    if key in ("pelvis", "hips"):
        hips = p.seen((L_HIP, R_HIP))
        if len(hips) < 2:
            return None
        a, b = p.pts[L_HIP], p.pts[R_HIP]
        width = float(np.hypot(*(a - b)))
        side = max(width * (0.8 if key == "pelvis" else 1.6), torso)
        cx, cy = (a + b) / 2
        if key == "pelvis":
            # the crotch lies a bit below the line between the hip joints, towards the knees
            knees = p.seen((L_KNEE, R_KNEE))
            if len(knees):
                down = p.pts[knees].mean(axis=0) - (a + b) / 2
                cx, cy = np.array([cx, cy]) + 0.2 * down
        return _square(cx, cy, side)
    if key == "mouth":
        return _from_points(p, MOUTH, 1.8, torso * 0.6)
    if key == "head":
        return _from_points(p, FACE, 1.3, torso)
    if key == "hand_r":
        return _from_points(p, R_HAND, 1.3, torso * 0.8)
    if key == "hand_l":
        return _from_points(p, L_HAND, 1.3, torso * 0.8)
    if key == "chest":
        sh = p.seen((L_SHOULDER, R_SHOULDER))
        hips = p.seen((L_HIP, R_HIP))
        if len(sh) < 2:
            return None
        a, b = p.pts[L_SHOULDER], p.pts[R_SHOULDER]
        width = float(np.hypot(*(a - b)))
        c = (a + b) / 2
        if len(hips) == 2:
            c = c + 0.3 * ((p.pts[L_HIP] + p.pts[R_HIP]) / 2 - c)
        else:
            c = c + np.array([0, 0.3 * width])
        return _square(c[0], c[1], max(width * 0.9, torso))
    raise KeyError(key)


def suggestions(people: list[Person]) -> list[tuple[int, Region, tuple]]:
    """All visible body parts: (person number, region, box px)."""
    out = []
    for i, p in enumerate(people):
        for r in REGIONS:
            b = region_box(p, r.key)
            if b is not None:
                out.append((i, r, b))
    return out


# ---------------------------------------------------------------- anchors
@dataclass
class Anchor:
    """Which body points a box covers, and where the box centre lies seen from each of
    them (in person heights, so it fits when the person comes closer)."""
    idx: np.ndarray                 # point numbers
    offsets: np.ndarray             # (len(idx), 2): centre - point, / person height

    MIN_POINTS = 2

    @classmethod
    def from_box(cls, people: list[Person], center, size) -> Anchor | None:
        center = np.asarray(center, np.float64)
        half = np.asarray(size, np.float64) / 2 * 1.2
        best = None
        for p in people:
            seen = np.nonzero(p.conf >= SURE)[0]
            if not len(seen):
                continue
            inside = seen[np.all(np.abs(p.pts[seen] - center) <= half, axis=1)]
            if len(inside) < cls.MIN_POINTS:
                continue
            if best is None or len(inside) > len(best[1]):
                best = (p, inside)
        if best is None:
            return None
        p, idx = best
        return cls(idx, (center - p.pts[idx]) / p.height)

    def locate(self, people: list[Person], near) -> tuple[np.ndarray, int] | None:
        """Where the box centre should be now: (centre px, points seen), from the person
        whose estimate lies nearest to `near`. None if too few of the points are seen."""
        near = np.asarray(near, np.float64)
        need = max(self.MIN_POINTS, (len(self.idx) + 1) // 2)
        best = None
        for p in people:
            ok = p.conf[self.idx] >= SURE
            if ok.sum() < need:
                continue
            est = p.pts[self.idx[ok]] + self.offsets[ok] * p.height
            c = np.median(est, axis=0)
            d = float(np.hypot(*(c - near)))
            if best is None or d < best[2]:
                best = (c, int(ok.sum()), d)
        return None if best is None else (best[0], best[1])
