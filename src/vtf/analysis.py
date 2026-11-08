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

MOTION_FORMAT_VERSION = 3
READABLE_VERSIONS = (1, 2, 3)


class AnalysisCancelled(Exception):
    pass


@dataclass(frozen=True)
class TrackSection:
    """One tracked stretch of the video (mode "track").

    The areas are marked on the frame at ``start_ms``; tracking runs until
    ``end_ms`` (None = until the next section or the end of the video). A new
    section after a scene cut simply starts over with newly marked areas.
    ``calib_in_ms`` / ``calib_out_ms`` are the moments that are "fully in"
    (position 0) and "fully out" (100); they only affect the script, not the
    analysis.
    """
    start_ms: int
    boxes: tuple = ()                      # normalized (x, y, w, h), up to 3
    end_ms: int | None = None
    calib_in_ms: int | None = None
    calib_out_ms: int | None = None

    @property
    def calibrated(self) -> bool:
        return self.calib_in_ms is not None and self.calib_out_ms is not None

    def analysis_key(self) -> tuple:
        return (int(self.start_ms), None if self.end_ms is None else int(self.end_ms),
                tuple(tuple(round(v, 4) for v in b) for b in self.boxes))


def section_ranges(sections, duration_ms: float) -> list[tuple[int, float, float]]:
    """(index, start, end) per section with boxes, sorted by start; an open end
    runs to the next section or the end of the video."""
    secs = sorted(((i, s) for i, s in enumerate(sections) if s.boxes), key=lambda t: t[1].start_ms)
    out = []
    for k, (i, s) in enumerate(secs):
        nxt = secs[k + 1][1].start_ms if k + 1 < len(secs) else duration_ms
        end = nxt if s.end_ms is None else min(s.end_ms, nxt)
        if end > s.start_ms:
            out.append((i, float(s.start_ms), float(end)))
    return out


@dataclass
class AnalysisSettings:
    width: int = 320                      # analysis resolution (px)
    roi: tuple[float, float, float, float] | None = None  # normalized x, y, w, h
    frame_step: int = 1                   # analyse every n-th frame
    compensate_camera: bool = True        # subtract global (camera) motion
    use_opencl: bool = False              # OpenCV T-API (GPU)
    hwaccel: str = "auto"                 # ffmpeg -hwaccel value ("none", "auto", "vaapi")
    mode: str = "flow"                    # "flow" = motion in the image, "track" = marked areas
    track_boxes: tuple = ()               # shortcut for one section: boxes marked at track_start_ms
    track_start_ms: int = 0
    track_sections: tuple = ()            # TrackSection, ... (mode "track"); wins over track_boxes

    def sections(self) -> tuple:
        if self.track_sections:
            return tuple(self.track_sections)
        if self.track_boxes:
            return (TrackSection(int(self.track_start_ms), tuple(self.track_boxes)),)
        return ()

    def cache_key(self) -> str:
        d = asdict(self)
        d.pop("use_opencl")
        d.pop("hwaccel")
        for k in ("track_boxes", "track_start_ms", "track_sections"):
            d.pop(k)
        if d["mode"] == "flow":       # same key as before tracking existed: old caches stay valid
            d.pop("mode")
        else:
            d.pop("roi")
            # calibration is applied when generating, it does not need a new analysis
            d["sections"] = tuple(s.analysis_key() for s in self.sections())
        return repr(sorted(d.items()))


@dataclass
class MotionData:
    video: str
    fps: float                 # sample rate of dx/dy (video fps / frame_step)
    dx: np.ndarray             # horizontal motion per sample (px of analysis frame)
    dy: np.ndarray             # vertical motion per sample (positive = down)
    meta: dict = field(default_factory=dict)
    tracks: np.ndarray | None = None       # (n, areas, 2) normalized area centres, NaN = not tracked
    track_sizes: np.ndarray | None = None  # (sections, areas, 2) normalized width/height, NaN-padded
    track_section: np.ndarray | None = None  # (n,) index into the sections, -1 = not tracked

    def __len__(self) -> int:
        return len(self.dx)

    @property
    def duration_ms(self) -> float:
        return len(self.dx) / self.fps * 1000.0

    def save(self, path: str | Path) -> None:
        tmp = f"{path}.tmp.npz"
        extra = {}
        if self.tracks is not None:
            extra = {"tracks": self.tracks, "track_sizes": self.track_sizes,
                     "track_section": self.track_section}
        np.savez_compressed(tmp, dx=self.dx, dy=self.dy, fps=self.fps,
                            version=MOTION_FORMAT_VERSION, **extra)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str | Path, video: str = "") -> "MotionData":
        with np.load(path) as d:
            if int(d["version"]) not in READABLE_VERSIONS:
                raise ValueError("incompatible motion cache")
            tracks = d["tracks"].astype(np.float32) if "tracks" in d.files else None
            sizes = d["track_sizes"].astype(np.float32) if "track_sizes" in d.files else None
            sect = d["track_section"].astype(np.int16) if "track_section" in d.files else None
            if tracks is not None and sect is None:          # format 2: one section, whole video
                sizes = sizes[None] if sizes is not None and sizes.ndim == 2 else sizes
                sect = np.zeros(len(tracks), np.int16)
            return cls(video, float(d["fps"]), d["dx"].astype(np.float32), d["dy"].astype(np.float32),
                       tracks=tracks, track_sizes=sizes, track_section=sect)


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
    if settings.mode == "track" and settings.sections():
        return analyze_tracking(path, settings, progress, cancel, info)
    using_ocl = gpu.set_opencl(settings.use_opencl)
    report: dict = {}
    step = max(1, settings.frame_step)
    total = max(1, info.frame_count // step)
    w, h = scaled_size(info, settings.width)
    ys, xs = _roi_slices((h, w), settings.roi)

    dx: list[float] = [0.0]
    dy: list[float] = [0.0]
    prev = None
    flow_buf = None
    for i, frame in enumerate(iter_gray_frames(info, settings.width, settings.hwaccel, step,
                                                    report=report)):
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
                      {"opencl": using_ocl, "width": w, "height": h,
                       "decoder": report.get("decoder"), "decoder_errors": report.get("errors", [])})


def analyze_tracking(path: str | Path, settings: AnalysisSettings, progress: ProgressCb | None = None,
                     cancel: CancelCb | None = None, info: VideoInfo | None = None) -> MotionData:
    """Mode "track": follow the marked areas section by section. Each section
    starts on the frame its areas were marked on; outside the sections nothing
    is tracked (no motion)."""
    from .tracking import MAX_AREAS, MultiTracker

    info = info or probe(path)
    report: dict = {}
    step = max(1, settings.frame_step)
    total = max(1, info.frame_count // step)
    w, h = scaled_size(info, settings.width)
    sections = settings.sections()
    dur_ms = max(info.duration * 1000, info.frame_count / max(info.fps, 1e-6) * 1000)
    ranges = section_ranges(sections, dur_ms + 1000)
    def to_i(ms: float) -> int:
        return round(ms / 1000 * info.fps / step)

    plan = [(sec, to_i(a), to_i(b)) for sec, a, b in ranges]
    k = max((len(sections[sec].boxes[:MAX_AREAS]) for sec, _a, _b in plan), default=1)

    dx: list[float] = []
    dy: list[float] = []
    centers: list[np.ndarray | None] = []
    owner: list[int] = []
    tracker = None
    active = -1
    prev = None
    for i, frame in enumerate(iter_gray_frames(info, settings.width, settings.hwaccel, step,
                                                    report=report)):
        if cancel and cancel():
            raise AnalysisCancelled()
        sec = next((s for s, a, b in plan if a <= i < b), -1)
        if sec < 0:
            tracker, active = None, -1
            dx.append(0.0)
            dy.append(0.0)
            centers.append(None)
            owner.append(-1)
        else:
            cur = cv2.GaussianBlur(np.ascontiguousarray(frame), (3, 3), 0)
            if sec != active:                         # section starts: areas marked on this frame
                boxes = sections[sec].boxes[:MAX_AREAS]
                boxes_px = [(x * w, y * h, bw * w, bh * h) for x, y, bw, bh in boxes]
                tracker = MultiTracker(boxes_px, cur.shape, settings.compensate_camera)
                active = sec
                d = np.zeros(2)
            else:
                d = tracker.step(prev, cur)
            dx.append(float(d[0]))
            dy.append(float(d[1]))
            c = np.full((k, 2), np.nan)
            cs = tracker.centers()
            c[:len(cs)] = cs
            centers.append(c)
            owner.append(sec)
            prev = cur
        if progress and i % 25 == 0:
            progress(min(1.0, i / total))
    if not dx:
        raise RuntimeError(f"Keine Frames gelesen: {path}")
    if all(o < 0 for o in owner):
        raise RuntimeError("Kein Tracking-Abschnitt liegt im Video")
    nan = np.full((k, 2), np.nan)
    tracks = np.array([nan if c is None else c for c in centers], np.float32) / np.array([w, h], np.float32)
    sizes = np.full((len(sections), k, 2), np.nan, np.float32)
    for si, s in enumerate(sections):
        for ai, (_x, _y, bw, bh) in enumerate(s.boxes[:k]):
            sizes[si, ai] = (bw, bh)
    if progress:
        progress(1.0)
    return MotionData(str(path), info.fps / step, np.asarray(dx, np.float32), np.asarray(dy, np.float32),
                      {"opencl": False, "width": w, "height": h, "mode": "track",
                       "decoder": report.get("decoder"), "decoder_errors": report.get("errors", [])},
                      tracks=tracks, track_sizes=sizes, track_section=np.asarray(owner, np.int16))


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
