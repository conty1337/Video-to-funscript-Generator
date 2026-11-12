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
import os
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .i18n import tr

_BASE = "https://download.openmmlab.com/mmpose/v1/projects"
# Copies of the ONNX files on this project's GitHub (release "ki-modelle", uploaded by
# .github/workflows/models.yml). Much faster than OpenMMLab from Europe; OpenMMLab
# stays as the second source.
MIRROR = "https://github.com/conty1337/Video-to-funscript-Generator/releases/download/ki-modelle"


@dataclass(frozen=True)
class ModelFile:
    """One model. When a newer model is used, give it a new *name*: an installed app
    then sees the model as missing and offers the download again (old files are
    removed after it)."""
    name: str            # file name in the models folder (and on the mirror)
    url: str             # OpenMMLab zip
    member: str          # path of the ONNX file inside the zip (suffix)
    sha256: str          # of the ONNX file
    mb: int              # size of the ONNX file

    @property
    def mirror_url(self) -> str:
        return f"{MIRROR}/{self.name}"


DETECTOR = ModelFile(
    "yolox-m-humanart.onnx",
    f"{_BASE}/rtmposev1/onnx_sdk/yolox_m_8xb8-300e_humanart-c2c7a14a.zip",
    "end2end.onnx",
    "3dea6513388889f0fff4b77bf7a26013600321b9eb9ceb0e9a400a82572f5f23", 101)
POSE = ModelFile(
    "rtmw-l-wholebody-384x288.onnx",
    f"{_BASE}/rtmw/onnx_sdk/rtmw-dw-x-l_simcc-cocktail14_270e-384x288_20231122.zip",
    "end2end.onnx",
    "bd033156e5104c4f5d2edfe0453e02661e30a2f3da453ec93c8764d561b83054", 229)
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


TRIES = 4                # per source; an interrupted download continues where it stopped
CHUNK = 1 << 18


def _fetch(url: str, dest: Path, opener, cancel, on_bytes) -> None:
    """Download *url* into *dest*, continuing a partial *dest* (HTTP Range)."""
    have = dest.stat().st_size if dest.exists() else 0
    headers = {"User-Agent": "video-to-funscript"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = opener(req)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:              # already complete
            return
        raise
    with resp:
        status = getattr(resp, "status", None) or getattr(resp, "code", None) or 200
        if have and status != 206:              # server ignored the range: start over
            have = 0
        on_bytes(have)
        with open(dest, "ab" if have else "wb") as f:
            while True:
                if cancel and cancel():
                    raise DownloadCancelled()
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                on_bytes(len(chunk))


def _from_zip(zip_path: Path, member: str, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        name = next((n for n in z.namelist() if n.endswith(member)), None)
        if name is None:
            raise RuntimeError(tr("Modell nicht im Archiv gefunden"))
        with z.open(name) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)


def download(progress: Callable[[float, str], None] | None = None,
             cancel: Callable[[], bool] | None = None, opener=None,
             mirror: bool = True, folder: Path | None = None) -> None:
    """Fetch the missing models: first from the GitHub copy, else from OpenMMLab (zip).
    Each file is checked against its sha256. Dropped connections are retried and
    continue where they stopped; a cancelled download continues on the next call."""
    from .updater import ssl_context

    if opener is None:
        def opener(req):
            return urllib.request.urlopen(req, timeout=60, context=ssl_context())
    folder = folder or models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    todo = [m for m in MODELS if not (folder / m.name).is_file()]
    total = sum(m.mb for m in todo) * 1e6 or 1.0
    done = 0.0
    for m in todo:
        start = done
        sources = ([(m.mirror_url, folder / (m.name + ".part"), False)] if mirror else []) + \
                  [(m.url, folder / (m.name + ".zip.part"), True)]
        errors: list[str] = []
        for url, part, is_zip in sources:
            def on_bytes(_n, _part=part, _start=start, _name=m.name):
                done = _start + (_part.stat().st_size if _part.exists() else 0)
                if progress:
                    progress(min(0.99, done / total), _name)
            ok = False
            for _ in range(TRIES):
                try:
                    _fetch(url, part, opener, cancel, on_bytes)
                    ok = True
                    break
                except DownloadCancelled:
                    raise
                except urllib.error.HTTPError as e:
                    errors.append(f"{url.split('/')[2]}: HTTP {e.code}")
                    part.unlink(missing_ok=True)
                    break                       # not there: next source
                except (OSError, urllib.error.URLError) as e:    # timeout, reset, ...
                    errors.append(f"{url.split('/')[2]}: {getattr(e, 'reason', e)}")
            if not ok:
                continue
            onnx = part
            if is_zip:
                onnx = folder / (m.name + ".part")
                try:
                    _from_zip(part, m.member, onnx)
                except (zipfile.BadZipFile, RuntimeError) as e:
                    errors.append(f"{m.name}: {e}")
                    part.unlink(missing_ok=True)
                    onnx.unlink(missing_ok=True)
                    continue
                part.unlink(missing_ok=True)
            if _sha256(onnx) != m.sha256:
                onnx.unlink(missing_ok=True)
                errors.append(tr("{0}: Prüfsumme stimmt nicht, Download beschädigt", m.name))
                continue
            onnx.replace(folder / m.name)
            break
        else:
            raise RuntimeError(errors[-1] if errors else tr("{0}: Laden fehlgeschlagen", m.name))
        done = start + m.mb * 1e6
    _remove_old(folder)
    if progress:
        progress(1.0, "")


def _remove_old(folder: Path) -> None:
    """Delete models from earlier app versions that are no longer used."""
    keep = {m.name for m in MODELS}
    for f in folder.glob("*.onnx"):
        if f.name not in keep:
            f.unlink(missing_ok=True)


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


# ---------------------------------------------------------------- detector without NMS
# The YOLOX export ends in NonMaxSuppression + Gather. OpenCV's DNN (5.0) fails there
# as soon as more than one person is in the frame ("indices are outside of range in
# function 'gather'"). So the graph is cut before it, in memory (the file on disk stays
# as downloaded and checked): outputs are the decoded boxes and class scores of all
# 8400 anchors, and the NMS runs in Python with cv2.dnn.NMSBoxes.
DET_BOXES, DET_SCORES = "1436", "1438"         # tensor names in the export
DET_ANCHORS = (DET_SIZE // 8) ** 2 + (DET_SIZE // 16) ** 2 + (DET_SIZE // 32) ** 2
DET_CLASSES = 80
NMS_IOU = 0.65


def _varint(b: bytes, i: int) -> tuple[int, int]:
    v = shift = 0
    while True:
        c = b[i]
        i += 1
        v |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return v, i


def _enc(v: int) -> bytes:
    out = bytearray()
    while True:
        c, v = v & 0x7F, v >> 7
        out.append(c | 0x80 if v else c)
        if not v:
            return bytes(out)


def _fields(b: bytes):
    """Top-level protobuf fields of *b*: (number, raw bytes incl. key, payload or None)."""
    i = 0
    while i < len(b):
        start = i
        key, i = _varint(b, i)
        wt = key & 7
        data = None
        if wt == 0:
            _, i = _varint(b, i)
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        elif wt == 2:
            n, i = _varint(b, i)
            data = b[i:i + n]
            i += n
        else:
            raise ValueError(tr("ONNX-Datei nicht lesbar"))
        yield key >> 3, b[start:i], data


def _ld(field: int, data: bytes) -> bytes:
    return _enc(field << 3 | 2) + _enc(len(data)) + data


def _cut_onnx(model: bytes, outputs: list[tuple[str, tuple[int, ...]]]) -> bytes:
    """ONNX model whose outputs are the given float tensors; nodes not needed for them
    are dropped. Plain protobuf editing, no onnx package needed."""
    out = bytearray()
    found = False
    for f, raw, data in _fields(model):
        if f != 7 or data is None:              # ModelProto.graph = 7
            out += raw
            continue
        found = True
        nodes, rest = [], bytearray()
        for gf, graw, gdata in _fields(data):
            if gf == 1:                         # GraphProto.node
                ins = [d.decode() for nf, _r, d in _fields(gdata) if nf == 1]
                outs = [d.decode() for nf, _r, d in _fields(gdata) if nf == 2]
                nodes.append((ins, outs, graw))
            elif gf != 12:                      # GraphProto.output: replaced below
                rest += graw
        need = {name for name, _dims in outputs}
        keep = []
        for ins, outs, graw in reversed(nodes):  # nodes are in topological order
            if need & set(outs):
                keep.append(graw)
                need |= set(ins)
        missing = {name for name, _d in outputs} - {o for _i, outs, _g in nodes for o in outs}
        if missing:
            raise ValueError(tr("Modell passt nicht (fehlt: {0})", ', '.join(sorted(missing))))
        g = bytearray(b"".join(reversed(keep))) + rest
        for name, dims in outputs:              # ValueInfoProto: name, float tensor + shape
            shape = b"".join(_ld(1, b"\x08" + _enc(d)) for d in dims)
            g += _ld(12, _ld(1, name.encode()) + _ld(2, _ld(1, b"\x08\x01" + _ld(2, shape))))
        out += _ld(7, bytes(g))
    if not found:
        raise ValueError(tr("ONNX-Datei ohne Graph"))
    return bytes(out)


class PoseEstimator:
    """Finds people and their 133 body points. Nets are loaded on first use."""

    def __init__(self, folder: Path | None = None, det_net=None, pose_net=None):
        self.folder = folder or models_dir()
        self._det = det_net
        self._pose = pose_net
        self._last: list[Person] = []

    def _nets(self):
        if self._det is None:
            model = (self.folder / DETECTOR.name).read_bytes()
            cut = _cut_onnx(model, [(DET_BOXES, (1, DET_ANCHORS, 4)),
                                    (DET_SCORES, (1, DET_ANCHORS, DET_CLASSES))])
            self._det = cv2.dnn.readNetFromONNX(np.frombuffer(cut, np.uint8))
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
        boxes, scores = self._forward(det, lb.transpose(2, 0, 1)[None].astype(np.float32))
        boxes = np.asarray(boxes, np.float32).reshape(-1, 4)
        scores = np.asarray(scores, np.float32).reshape(len(boxes), -1)[:, 0]   # class 0 = person
        sel = np.flatnonzero(scores >= PERSON_SCORE)
        d = np.zeros((0, 5), np.float32)
        if len(sel):
            b = boxes[sel]
            xywh = np.c_[b[:, :2], b[:, 2:] - b[:, :2]]
            keep = np.asarray(cv2.dnn.NMSBoxes(xywh.tolist(), scores[sel].tolist(),
                                               PERSON_SCORE, NMS_IOU), int).ravel()
            d = np.c_[b[keep], scores[sel][keep]]
            d = d[np.argsort(-d[:, 4])]
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
    Region("pelvis", tr("Becken / Schritt")),
    Region("mouth", tr("Mund")),
    Region("head", tr("Kopf")),
    Region("hand_r", tr("Hand rechts")),
    Region("hand_l", tr("Hand links")),
    Region("chest", tr("Brust")),
    Region("hips", tr("Hüfte (breit)")),
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


# ---------------------------------------------------------------- skeleton (pick it yourself)
_BODY_LINKS = ((5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12),
               (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4),
               (15, 17), (15, 18), (15, 19), (16, 20), (16, 21), (16, 22))
_HAND_LINKS = tuple((a, b) for f in range(5) for a, b in
                    zip((0, 1 + 4 * f, 2 + 4 * f, 3 + 4 * f), (1 + 4 * f, 2 + 4 * f, 3 + 4 * f, 4 + 4 * f)))
LINKS = (_BODY_LINKS + ((9, 91), (10, 112))
         + tuple((91 + a, 91 + b) for a, b in _HAND_LINKS)
         + tuple((112 + a, 112 + b) for a, b in _HAND_LINKS))

_BODY_NAMES = (tr("Nase"), tr("Auge links"), tr("Auge rechts"), tr("Ohr links"), tr("Ohr rechts"), tr("Schulter links"),
               tr("Schulter rechts"), tr("Ellbogen links"), tr("Ellbogen rechts"), tr("Handgelenk links"),
               tr("Handgelenk rechts"), tr("Hüfte links"), tr("Hüfte rechts"), tr("Knie links"), tr("Knie rechts"),
               tr("Knöchel links"), tr("Knöchel rechts"), tr("großer Zeh links"), tr("kleiner Zeh links"),
               tr("Ferse links"), tr("großer Zeh rechts"), tr("kleiner Zeh rechts"), tr("Ferse rechts"))
_FINGERS = (tr("Daumen"), tr("Zeigefinger"), tr("Mittelfinger"), tr("Ringfinger"), tr("kleiner Finger"))


def point_name(i: int) -> str:
    """German name of a body point (COCO-WholeBody numbering)."""
    if i < len(_BODY_NAMES):
        return _BODY_NAMES[i]
    if i in MOUTH:
        return tr("Mund")
    if i in FACE:
        return tr("Gesicht")
    side, base = ("links", 91) if i in L_HAND else ("rechts", 112)
    k = i - base
    return f"Hand {side}" if k == 0 else f"{_FINGERS[(k - 1) // 4]} {side}"


def line_name(a: int, b: int) -> str:
    names = {(5, 7): tr("Oberarm links"), (6, 8): tr("Oberarm rechts"), (7, 9): tr("Unterarm links"),
             (8, 10): tr("Unterarm rechts"), (11, 13): tr("Oberschenkel links"), (12, 14): tr("Oberschenkel rechts"),
             (13, 15): tr("Unterschenkel links"), (14, 16): tr("Unterschenkel rechts"), (5, 6): tr("Schultern"),
             (11, 12): tr("Becken"), (5, 11): tr("Seite links"), (6, 12): tr("Seite rechts")}
    return names.get((a, b)) or f"{point_name(a)} – {point_name(b)}"


def pick_box(p: Person, target) -> tuple[float, float, float, float, float] | None:
    """Box (x, y, w, h, angle; px) for a clicked point (int) or line ((a, b)) of person p.
    A point gets a square around it; a line a turned box along it."""
    small = p.height * (0.035 if (isinstance(target, int) and target >= 91) else 0.07)
    side = max(small, 16.0)
    if isinstance(target, int):
        cx, cy = p.pts[target]
        return (cx - side / 2, cy - side / 2, side, side, 0.0)
    a, b = target
    pa, pb = p.pts[a], p.pts[b]
    d = pb - pa
    length = float(np.hypot(*d))
    if length < 4:
        return None
    w = length * 1.1
    h = max(0.35 * length, side * 0.8)
    cx, cy = (pa + pb) / 2
    ang = float(np.degrees(np.arctan2(d[1], d[0])))
    # keep boxes mostly upright: a box along a leg reads better turned by -90 than by +90
    if ang > 90:
        ang -= 180
    elif ang < -90:
        ang += 180
    return (cx - w / 2, cy - h / 2, w, h, round(ang, 1))
