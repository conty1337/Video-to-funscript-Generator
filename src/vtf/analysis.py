"""Motion analysis: dense optical flow -> per-frame dominant motion vector.

The expensive step (reading the video) produces :class:`MotionData`. All
user-tunable parameters (sensitivity, smoothing, ...) are applied afterwards in
:mod:`vtf.generator`, so they can be changed instantly without re-analysis.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from . import gpu
from .video import VideoInfo, iter_gray_frames, probe, scaled_size

ProgressCb = Callable[[float], None]
CancelCb = Callable[[], bool]

MOTION_FORMAT_VERSION = 1


class AnalysisCancelled(Exception):
    pass


@dataclass
class AnalysisSettings:
    width: int = 320                      # analysis resolution (px)
    roi: tuple[float, float, float, float] | None = None  # normalized x, y, w, h
    frame_step: int = 1                   # analyse every n-th frame
    compensate_camera: bool = True        # subtract global (camera) motion
    use_opencl: bool = False              # OpenCV T-API (GPU)
    hwaccel: str = "auto"                 # ffmpeg -hwaccel value ("none", "auto", "vaapi")

    def cache_key(self) -> str:
        d = asdict(self)
        d.pop("use_opencl")
        d.pop("hwaccel")
        return repr(sorted(d.items()))


@dataclass
class MotionData:
    video: str
    fps: float                 # sample rate of dx/dy (video fps / frame_step)
    dx: np.ndarray             # horizontal motion per sample (px of analysis frame)
    dy: np.ndarray             # vertical motion per sample (positive = down)
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.dx)

    @property
    def duration_ms(self) -> float:
        return len(self.dx) / self.fps * 1000.0

    def save(self, path: str | Path) -> None:
        tmp = f"{path}.tmp.npz"
        np.savez_compressed(tmp, dx=self.dx, dy=self.dy, fps=self.fps,
                            version=MOTION_FORMAT_VERSION)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str | Path, video: str = "") -> "MotionData":
        with np.load(path) as d:
            if int(d["version"]) != MOTION_FORMAT_VERSION:
                raise ValueError("incompatible motion cache")
            return cls(video, float(d["fps"]), d["dx"].astype(np.float32), d["dy"].astype(np.float32))


def _roi_slices(shape: tuple[int, int], roi) -> tuple[slice, slice]:
    h, w = shape
    if not roi:
        return slice(0, h), slice(0, w)
    x, y, rw, rh = roi
    x0, y0 = int(np.clip(x, 0, 1) * w), int(np.clip(y, 0, 1) * h)
    x1, y1 = int(np.clip(x + rw, 0, 1) * w), int(np.clip(y + rh, 0, 1) * h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return slice(0, h), slice(0, w)
    return slice(y0, y1), slice(x0, x1)


def frame_motion(flow: np.ndarray, compensate_camera: bool) -> tuple[float, float]:
    """Reduce a dense flow field to one dominant motion vector."""
    fx = flow[..., 0]
    fy = flow[..., 1]
    if compensate_camera:
        fx = fx - np.median(fx)
        fy = fy - np.median(fy)
    mag = np.hypot(fx, fy)
    # focus on the strongest-moving pixels (the actual action, not noise)
    thr = max(float(np.percentile(mag, 80)), 0.05)
    mask = mag >= thr
    if not mask.any():
        return 0.0, 0.0
    wgt = mag[mask]
    s = float(wgt.sum())
    if s <= 1e-9:
        return 0.0, 0.0
    return float((fx[mask] * wgt).sum() / s), float((fy[mask] * wgt).sum() / s)


def analyze_motion(path: str | Path, settings: AnalysisSettings | None = None,
                   progress: ProgressCb | None = None, cancel: CancelCb | None = None,
                   info: VideoInfo | None = None) -> MotionData:
    settings = settings or AnalysisSettings()
    info = info or probe(path)
    using_ocl = gpu.set_opencl(settings.use_opencl)
    step = max(1, settings.frame_step)
    total = max(1, info.frame_count // step)
    w, h = scaled_size(info, settings.width)
    ys, xs = _roi_slices((h, w), settings.roi)

    dx: list[float] = [0.0]
    dy: list[float] = [0.0]
    prev = None
    flow_buf = None
    for i, frame in enumerate(iter_gray_frames(info, settings.width, settings.hwaccel, step)):
        if cancel and cancel():
            raise AnalysisCancelled()
        cur = np.ascontiguousarray(frame[ys, xs])
        cur = cv2.GaussianBlur(cur, (3, 3), 0)
        if using_ocl:
            cur = cv2.UMat(cur)
        if prev is not None:
            flow_buf = cv2.calcOpticalFlowFarneback(
                prev, cur, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            flow = flow_buf.get() if isinstance(flow_buf, cv2.UMat) else flow_buf
            mx, my = frame_motion(flow, settings.compensate_camera)
            dx.append(mx)
            dy.append(my)
        prev = cur
        if progress and i % 25 == 0:
            progress(min(1.0, i / total))
    if prev is None:
        raise RuntimeError(f"Keine Frames gelesen: {path}")
    if progress:
        progress(1.0)
    return MotionData(str(path), info.fps / step, np.asarray(dx, np.float32),
                      np.asarray(dy, np.float32),
                      {"opencl": using_ocl, "width": w, "height": h})


# ---------------------------------------------------------------- cache

def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    d = Path(base) / "video-to-funscript"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_file(path: str | Path, settings: AnalysisSettings) -> Path:
    p = Path(path).resolve()
    st = p.stat()
    key = f"{p}|{st.st_size}|{int(st.st_mtime)}|{settings.cache_key()}"
    return cache_dir() / (hashlib.sha1(key.encode()).hexdigest() + ".npz")


def analyze_motion_cached(path: str | Path, settings: AnalysisSettings | None = None,
                          progress: ProgressCb | None = None, cancel: CancelCb | None = None,
                          use_cache: bool = True) -> MotionData:
    settings = settings or AnalysisSettings()
    cf = _cache_file(path, settings)
    if use_cache and cf.is_file():
        try:
            data = MotionData.load(cf, str(path))
            if progress:
                progress(1.0)
            return data
        except (OSError, ValueError, KeyError):
            pass
    data = analyze_motion(path, settings, progress, cancel)
    try:
        data.save(cf)
    except OSError:
        pass
    return data
