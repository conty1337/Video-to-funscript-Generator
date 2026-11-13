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
from .i18n import tr
from .video import VideoInfo, iter_gray_frames, probe, scaled_size

ProgressCb = Callable[[float], None]
CancelCb = Callable[[], bool]

MOTION_FORMAT_VERSION = 4
READABLE_VERSIONS = (1, 2, 3, 4)


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

    Area 1 is the part that moves (toy, machine), areas 2 and 3 are what it is
    measured against (the body, which moves little). ``outlines`` holds, per
    area, an optional polygon drawn around the thing, in the area's own
    coordinates (-0.5..0.5 of its width and height, turned with it); empty = the
    whole rectangle. ``stroke`` is the stroke direction drawn as an arrow
    (x0, y0, x1, y1, normalized; the head points "out" = position 100); it only
    affects the script.
    """
    start_ms: int
    boxes: tuple = ()                      # normalized (x, y, w, h), up to 3
    end_ms: int | None = None
    calib_in_ms: int | None = None
    calib_out_ms: int | None = None
    keys: tuple = ()                       # corrections: (ms, area index, box) - "nachjustieren"
    outlines: tuple = ()                   # per area: ((u, v), ...) or () = rectangle
    stroke: tuple | None = None            # arrow (x0, y0, x1, y1): stroke direction, head = out

    def outline(self, area: int) -> tuple:
        return tuple(self.outlines[area]) if area < len(self.outlines) and self.outlines[area] else ()

    @property
    def calibrated(self) -> bool:
        return self.calib_in_ms is not None and self.calib_out_ms is not None

    def analysis_key(self) -> tuple:
        key = (int(self.start_ms), None if self.end_ms is None else int(self.end_ms),
               tuple(tuple(round(v, 4) for v in b) for b in self.boxes),
               tuple((int(ms), int(a), tuple(round(v, 4) for v in b)) for ms, a, b in self.keys))
        lines = tuple(tuple((round(u, 4), round(v, 4)) for u, v in self.outline(i))
                      for i in range(len(self.boxes)))
        if any(lines):                    # without outlines: the same key as before they existed
            key += (lines,)
        return key


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
    hwaccel: str = "auto"                 # ffmpeg -hwaccel value ("none", "auto", "vaapi", "cuda")
    mode: str = "flow"                    # "flow" = motion in the image, "track" = marked areas
    track_boxes: tuple = ()               # shortcut for one section: boxes marked at track_start_ms
    track_start_ms: int = 0
    track_sections: tuple = ()            # TrackSection, ... (mode "track"); wins over track_boxes
    track_turn: bool = True               # areas follow turning and size, not only moving
    track_anchor: bool = False            # areas 2 and 3 stay put (only follow the camera)
    ai_check: bool = False                # body recognition checks the areas now and then (pose.py)

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
            d.pop("track_turn")
            d.pop("track_anchor")
            d.pop("ai_check")
        else:
            if not d["ai_check"]:             # same key as before the AI check existed
                d.pop("ai_check")
            d.pop("roi")
            # calibration is applied when generating, it does not need a new analysis
            d["sections"] = tuple(s.analysis_key() for s in self.sections())
            d["tracker"] = TRACKER_VERSION     # results change with the tracker
        return repr(sorted(d.items()))


#: bumped when tracking results change (new cache entries for tracked videos)
TRACKER_VERSION = 10
#: frames kept (compressed) for following an area backwards from a correction,
#: as image pixels: ~10 min at 320 px width
BACK_BUDGET_PX = 18000 * 320 * 180
AI_EVERY_S = 1.0            # AI check: this often the body points are looked at
AI_PULL = 0.3               # box this far off its body part (part of its size): pull it back
BRIDGE_MAX_S = 2.5          # an area out of the frame for longer is not bridged (unknown path)
BRIDGE_PLAIN_S = 1.0        # covered or lost: bridged up to this long (or between AI sightings)
AI_REACH = 0.5              # covered and placed by the AI: looked for this far around (box sizes)
AI_SURE_LOOK = 0.8          # its look agrees this well at the AI's place: moved there at once
AI_TRUST = 3                # AI looks in a row that put an area elsewhere: it counts as covered
AI_STUCK_S = 0.5            # AI moves a covered or lost area when it stays so this long
AI_UNSURE_EVERY = 3         # frames between AI looks while an area is covered, lost or unsure


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
    track_geom: np.ndarray | None = None     # (n, areas, 2) scale and angle (degrees) per area
    track_lost: np.ndarray | None = None     # (n, areas) True where an area could not be followed

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
            if self.track_geom is not None:
                extra["track_geom"] = self.track_geom
            if self.track_lost is not None:
                extra["track_lost"] = self.track_lost
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
            geom = d["track_geom"].astype(np.float32) if "track_geom" in d.files else None
            lost = d["track_lost"].astype(bool) if "track_lost" in d.files else None
            return cls(video, float(d["fps"]), d["dx"].astype(np.float32), d["dy"].astype(np.float32),
                       tracks=tracks, track_sizes=sizes, track_section=sect, track_geom=geom,
                       track_lost=lost)


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
        raise RuntimeError(tr("Keine Frames gelesen: {0}", path))
    if progress:
        progress(1.0)
    return MotionData(str(path), info.fps / step, np.asarray(dx, np.float32),
                      np.asarray(dy, np.float32),
                      {"opencl": using_ocl, "width": w, "height": h,
                       "decoder": report.get("decoder"), "decoder_errors": report.get("errors", [])})


def _spread_corrections(events, centers, owner, plan, dx, dy, k) -> None:
    """A correction moves an area at once; the drift that made it necessary built
    up since the previous anchor (section start or earlier correction). Without a
    backward track the offset is spread back linearly over that stretch. With one
    (the area followed backwards from the correction) both directions are blended:
    near the anchor the forward track counts, near the correction the backward one."""
    last: dict[tuple[int, int], int] = {}
    starts = {sec: a for sec, a, _b in plan}
    for i, sec, area, off, back in sorted(events, key=lambda e: e[0]):
        j0 = last.get((sec, area), starts.get(sec, i))
        n = i - j0
        last[(sec, area)] = i
        if n <= 0:
            continue
        if back is not None and len(back) != n + 1:
            back = None
        deltas = np.zeros((n + 1, 2))
        deltas[n] = off
        for j in range(j0 + 1, i):
            t = (j - j0) / n
            d = off * t
            c = centers[j]
            if back is not None and c is not None and owner[j] == sec:
                b = back[j - j0]
                if not (np.isnan(b[0]) or np.isnan(c[area][0])):
                    d = t * (b - c[area])
            deltas[j - j0] = d
        for j in range(j0 + 1, i):
            if centers[j] is not None and owner[j] == sec:
                centers[j][area] = centers[j][area] + deltas[j - j0]
        n_areas = sum(1 for c in (centers[i] if centers[i] is not None else [])
                      if not np.isnan(c[0]))
        weight = 1.0 if n_areas <= 1 or area == 0 else -1.0 / (n_areas - 1)
        steps = np.diff(deltas, axis=0) * weight
        dx[j0 + 1:i + 1] += steps[:, 0]
        dy[j0 + 1:i + 1] += steps[:, 1]


def _hermite(p0, v0, p1, v1, n: int) -> np.ndarray:
    """Points 1..n-1 of a cubic from p0 (slope v0 per step) to p1 (slope v1) over n steps."""
    t = (np.arange(1, n) / n)[:, None]
    h00, h10 = 2 * t ** 3 - 3 * t ** 2 + 1, t ** 3 - 2 * t ** 2 + t
    h01, h11 = -2 * t ** 3 + 3 * t ** 2, t ** 3 - t ** 2
    return h00 * p0 + h10 * n * v0 + h01 * p1 + h11 * n * v1


def _path_through(knots: list[tuple[int, np.ndarray]], v0, v1) -> dict[int, np.ndarray]:
    """Smooth path through (frame, point) knots, slopes v0/v1 (per frame) at the ends,
    Catmull-Rom in between. Returns the points of the frames strictly between the ends."""
    ts = [t for t, _p in knots]
    ps = [np.asarray(p, np.float64) for _t, p in knots]
    m = len(knots)
    vs = [np.asarray(v0, np.float64)]
    for q in range(1, m - 1):
        vs.append((ps[q + 1] - ps[q - 1]) / (ts[q + 1] - ts[q - 1]))
    vs.append(np.asarray(v1, np.float64))
    out = {}
    for q in range(m - 1):
        n = ts[q + 1] - ts[q]
        if n <= 0:
            continue
        seg = _hermite(ps[q], vs[q], ps[q + 1], vs[q + 1], n)
        for j, c in enumerate(seg, start=ts[q] + 1):
            out[j] = c
        if q + 1 < m - 1:
            out[ts[q + 1]] = ps[q + 1]
    return out


def _smooth_knots(knots: list[tuple[int, np.ndarray]]) -> list[tuple[int, np.ndarray]]:
    """Under a cover the body recognition sometimes guesses wildly. A place far off the
    line between its neighbours (much more than the others are) is replaced by that
    line; all others stay as they are, so strokes keep their height."""
    if len(knots) < 3:
        return knots
    ts = np.array([t for t, _c in knots], np.float64)
    ps = np.array([c for _t, c in knots], np.float64)
    w = (ts[1:-1] - ts[:-2]) / np.maximum(ts[2:] - ts[:-2], 1e-9)
    line = ps[:-2] + w[:, None] * (ps[2:] - ps[:-2])
    resid = np.hypot(*(ps[1:-1] - line).T)
    wild = resid > max(4.0, 3 * float(np.median(resid)))
    out = ps.copy()
    out[1:-1][wild] = line[wild]
    return [(t, out[q]) for q, (t, _c) in enumerate(knots)]


def _bridge_gaps(centers, gaps, outs, losts, owner, dx, dy, k: int, fps: float,
                 knots: dict | None = None) -> None:
    """Fill short stretches where an area was not followed (covered, lost or out of the
    frame) with a smooth path from how it left to how it came back, through the places
    the body recognition saw it meanwhile (`knots`: area -> {frame: centre}). Without
    such places only up to BRIDGE_PLAIN_S (BRIDGE_MAX_S out of the frame): a cubic
    cannot know strokes in a longer gap. Both ends must be followed, same section."""
    n = len(centers)
    knots = knots or {}

    def ok(j, area):
        return (0 <= j < n and centers[j] is not None and not losts[j][area]
                and not np.isnan(centers[j][area][0]))

    filled: set[int] = set()
    for area in range(k):
        seen = knots.get(area, {})
        j = 0
        while j < n:
            if gaps[j] is None or not gaps[j][area]:
                j += 1
                continue
            a = j
            while j < n and gaps[j] is not None and gaps[j][area]:
                j += 1
            b = j
            last = a - 1
            if not ok(last, area) or not ok(b, area) or owner[last] != owner[b] or owner[last] < 0:
                continue
            # a short gap is bridged best by a smooth curve; the places the body recognition
            # saw (a few px off under a cover) only help when the gap is too long for that
            inner = (_smooth_knots(sorted((t, c) for t, c in seen.items() if a <= t < b))
                     if b - last > 0.5 * BRIDGE_PLAIN_S * fps else [])
            pts = [(last, centers[last][area])] + inner + [(b, centers[b][area])]
            longest = max(t1 - t0 for (t0, _c0), (t1, _c1) in zip(pts, pts[1:]))
            out = all(outs[t] is not None and outs[t][area] for t in range(a, b))
            limit = BRIDGE_MAX_S if (out or inner) else BRIDGE_PLAIN_S
            if longest > limit * fps:
                continue
            v0 = centers[last][area] - centers[last - 1][area] if ok(last - 1, area) else np.zeros(2)
            v1 = centers[b + 1][area] - centers[b][area] if ok(b + 1, area) else np.zeros(2)
            for t, c in _path_through(pts, v0, v1).items():
                centers[t][area] = c
                losts[t][area] = False
            filled.update(range(a, b + 1))
    # the motion for the script over the bridges (camera motion is not known there)
    for i in sorted(filled):
        cur = [c for c in range(k) if ok(i, c) and ok(i - 1, c)]
        if 0 not in cur:
            continue
        d = centers[i][0] - centers[i - 1][0]
        rest = [centers[i][c] - centers[i - 1][c] for c in cur if c != 0]
        if rest:
            d = d - np.mean(rest, axis=0)
        dx[i], dy[i] = float(d[0]), float(d[1])


def _track_back(frames: dict, a: int, b: int, box_px, shape, cancel=None, turn: bool = True,
                outline=None) -> np.ndarray:
    """Follow one area backwards from frame b (where the user put it) to frame a.
    Returns its centre per frame a..b (NaN where it was lost)."""
    from .tracking import AreaTracker

    def dec(j):
        return cv2.imdecode(frames[j], cv2.IMREAD_GRAYSCALE)

    nxt = dec(b)
    t = AreaTracker(box_px, shape, nxt, turn, outline or None)
    out = np.full((b - a + 1, 2), np.nan)
    out[-1] = t.center
    for j in range(b - 1, a - 1, -1):
        if cancel and j % 50 == 0 and cancel():
            raise AnalysisCancelled()
        cur = dec(j)
        t.step(nxt, cur)
        if not t.lost:
            out[j - a] = t.center
        nxt = cur
    return out


def _looks_right_there(t, c, cur, sure: float = 0.0) -> bool:
    """Does the area look at least as much like itself at c as where it is now (and at
    least `sure`)? Only the looks the user marked (or corrected) count: a look taken
    lately while tracking may already show the wrong place."""
    tpls = t.templates
    if not tpls:
        return sure <= 0
    here = t._best(cur, tpls, 0.1)[0]
    old = t.center
    t.center = np.asarray(c, np.float64)
    there = t._best(cur, tpls, 0.1)[0]
    t.center = old
    return there >= max(here - 0.05, 0.3, sure)


def _unsure(t) -> bool:
    return t.lost or t.covered or t.unsure_checks > 0


def _move_area(t, c, cur) -> None:
    """Put an area where the body recognition sees it. Turn and size as when it last surely
    looked right (whatever dragged it away may have bent the box too); then its look
    fine-tunes the place where it can."""
    t.center = np.asarray(c, np.float64)
    t.scale, t.angle = t.good[1], t.good[2]
    score, off = t._best(cur, t.templates, 0.3) if t.templates else (0.0, np.zeros(2))
    if score >= t.TEMPLATE_MIN_SCORE:
        t.center = t.center + off
    t.covered = t.outside = t.lost = False
    t.lost_frames = t.bad_checks = t.unsure_checks = 0
    t.vel = np.zeros(2)
    t._set_good()
    t.pts = None
    t._refill(cur)


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
    geoms: list[np.ndarray | None] = []
    losts: list[np.ndarray | None] = []
    outs: list[np.ndarray | None] = []
    owner: list[int] = []
    # corrections by frame index: {i: [(area, box), ...]}
    key_at: dict[int, list] = {}
    for sec, a, b in plan:
        for ms, area, bx in sections[sec].keys:
            ki = to_i(ms)
            if a < ki < b:
                key_at.setdefault(ki, []).append((int(area), bx))
    # stretches followed backwards too: (section, area, correction frame) -> (anchor frame, box)
    back_segs: dict[tuple[int, int, int], tuple[int, tuple]] = {}
    for sec, a, b in plan:
        last_at: dict[int, int] = {}
        for ki in sorted(key_at):
            if not a < ki < b:
                continue
            for area, bx in key_at[ki]:
                j0 = last_at.get(area, a)
                last_at[area] = ki
                if area < len(sections[sec].boxes[:MAX_AREAS]) and (ki - j0) * w * h <= BACK_BUDGET_PX:
                    back_segs[(sec, area, ki)] = (j0, bx)
    keep_until: dict[int, int] = {}              # frame -> last correction frame that needs it
    for (_s, _ar, ki), (j0, _bx) in back_segs.items():
        for j in range(j0, ki + 1):
            keep_until[j] = max(keep_until.get(j, -1), ki)
    free_at: dict[int, list[int]] = {}
    for j, ki in keep_until.items():
        free_at.setdefault(ki, []).append(j)
    frames: dict[int, np.ndarray] = {}            # kept frames, JPEG
    events: list[tuple] = []                      # (frame, section, area, offset px, backward track)
    tracker = None
    active = -1
    prev = None
    estimator = None
    if settings.ai_check:
        from . import pose
        if pose.installed():
            estimator = pose.PoseEstimator()
        else:
            report.setdefault("notes", []).append(tr("KI-Kontrolle aus: Modelle nicht geladen"))
    ai_every = max(1, round(AI_EVERY_S * info.fps / step))

    def ai_people(img, reuse=False) -> list:
        """People in the frame; if the AI fails, the analysis goes on without it."""
        nonlocal estimator, anchors
        try:
            return estimator.people(img, reuse=reuse)
        except Exception as e:             # a model / OpenCV problem must not end the analysis
            estimator, anchors = None, []
            report.setdefault("notes", []).append(tr("KI-Kontrolle ausgefallen, ohne KI weiter: {0}", e))
            return []
    anchors: list = []
    knots: dict[int, dict[int, np.ndarray]] = {}     # area -> {frame: centre} seen by the AI
    sec_start = 0
    ai_moves = 0
    agree_at: list[int] = []
    disagree: list[int] = []
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
            geoms.append(None)
            losts.append(None)
            outs.append(None)
            owner.append(-1)
        else:
            cur = cv2.GaussianBlur(np.ascontiguousarray(frame), (3, 3), 0)
            if i in keep_until:
                frames[i] = cv2.imencode(".jpg", cur, [cv2.IMWRITE_JPEG_QUALITY, 95])[1]
            if sec != active:                         # section starts: areas marked on this frame
                boxes = sections[sec].boxes[:MAX_AREAS]
                boxes_px = [(b[0] * w, b[1] * h, b[2] * w, b[3] * h, *b[4:5]) for b in boxes]
                tracker = MultiTracker(boxes_px, cur.shape, settings.compensate_camera, first=cur,
                                       turn=settings.track_turn, anchor=settings.track_anchor,
                                       outlines=[sections[sec].outline(q) for q in range(len(boxes))])
                active = sec
                sec_start = i
                d = np.zeros(2)
                anchors = []
                if estimator is not None:
                    from .pose import Anchor
                    people = ai_people(cur)
                    anchors = [Anchor.from_box(people, a.center, a.size * a.scale) for a in tracker.areas]
                agree_at = [i] * len(tracker.areas)
                disagree = [0] * len(tracker.areas)
            else:
                d = tracker.step(prev, cur)
                # correction: the area is really here in this frame (after following it)
                for area, bx in key_at.get(i, ()):
                    if area < len(tracker.areas):
                        before = tracker.areas[area].center.copy()
                        box_px = (bx[0] * w, bx[1] * h, bx[2] * w, bx[3] * h, *bx[4:5])
                        tracker.reset_area(area, box_px, cur)
                        back = None
                        seg = back_segs.get((sec, area, i))
                        if seg is not None and all(j in frames for j in range(seg[0], i + 1)):
                            back = _track_back(frames, seg[0], i, box_px, cur.shape, cancel,
                                               settings.track_turn, sections[sec].outline(area))
                        events.append((i, sec, area, tracker.areas[area].center - before, back))
                        if estimator is not None and area < len(anchors):
                            from .pose import Anchor
                            t = tracker.areas[area]
                            people = ai_people(cur)
                            if estimator is not None:
                                anchors[area] = Anchor.from_box(people, t.center, t.size * t.scale)
                if anchors and any(anchors):
                    routine = (i - sec_start) % ai_every == 0
                    fixed = {area for area, _bx in key_at.get(i, ())}       # the user's word counts
                    due = [area for area, anc in enumerate(anchors)
                           if anc is not None and area not in fixed and not (tracker.anchor and area > 0)
                           and (routine or (i % AI_UNSURE_EVERY == 0
                                            and (disagree[area] or _unsure(tracker.areas[area]))))]
                    # in between the regular checks the people are looked for where they were
                    # a moment ago (no new search for people: that is two thirds of the time)
                    people = ai_people(cur, reuse=not routine) if due else []
                    if estimator is None:
                        due = []
                    long_lost = AI_STUCK_S * info.fps / step
                    for area in due:
                        t = tracker.areas[area]
                        stuck = t.lost or t.covered
                        loc = anchors[area].locate(people, t.center)
                        if loc is None or t.outside:
                            continue
                        c = loc[0]
                        if not (0 <= c[0] < t.w and 0 <= c[1] < t.h):
                            continue
                        knots.setdefault(area, {})[i] = c   # a place for a bridge, if needed
                        off = float(np.hypot(*(c - t.center))) / float(np.max(t.size * t.scale))
                        if stuck:
                            # covered or lost: wait (and look for it) where the body points
                            # say it is now; after a while follow from there even if its look
                            # does not show (still covered, e.g. a hand resting on it)
                            if t.covered:
                                t.center = np.asarray(c, np.float64)
                                t.search_reach = AI_REACH
                            if t.lost_frames < long_lost:
                                continue
                        elif off <= AI_PULL:
                            agree_at[area], disagree[area] = i, 0
                            continue
                        else:
                            disagree[area] += 1
                        if not stuck and not _looks_right_there(t, c, cur, AI_SURE_LOOK):
                            if disagree[area] >= AI_TRUST:
                                # the body points keep saying it is elsewhere, its look does not
                                # clearly tell: something dragged it. Covered: it waits where the
                                # body points say, the tracker looks for it there, the stretch
                                # since it last surely looked right is bridged
                                t.cover()
                                t.center = np.asarray(c, np.float64)
                                t.recent = None             # may show what dragged it
                                t.search_reach = AI_REACH   # it is near where the body points say
                            continue
                        before = t.center.copy()
                        _move_area(t, c, cur)
                        ai_moves += 1
                        gap = i - agree_at[area] - 1        # frames since the AI last agreed
                        if stuck:
                            pass                            # the lost stretch is bridged
                        elif gap <= BRIDGE_PLAIN_S * info.fps / step:
                            t.slipped = gap                 # dragged lately: bridged too
                        else:
                            # drifted slowly: spread the offset back over that stretch like a
                            # correction (keeps the strokes in it)
                            events.append((agree_at[area], sec, area, np.zeros(2), None))
                            events.append((i, sec, area, t.center - before, None))
                        agree_at[area], disagree[area] = i, 0
            # areas just found covered: the frames before were already following the cover
            if tracker is not None and sec >= 0:
                for area, t in enumerate(tracker.areas):
                    if t.slipped:
                        for j in range(max(sec_start + 1, i - t.slipped), i):
                            if losts[j] is not None and owner[j] == sec:
                                losts[j][area] = True
                        t.slipped = 0
            dx.append(float(d[0]))
            dy.append(float(d[1]))
            c = np.full((k, 2), np.nan)
            cs = tracker.centers()
            c[:len(cs)] = cs
            centers.append(c)
            g = np.full((k, 2), np.nan)
            gs = tracker.geometry()
            g[:len(gs)] = gs
            geoms.append(g)
            lo = np.zeros(k, bool)
            ls = tracker.lost()
            lo[:len(ls)] = ls
            losts.append(lo)
            ou = np.zeros(k, bool)
            os_ = [a.outside for a in tracker.areas]
            ou[:len(os_)] = os_
            outs.append(ou)
            owner.append(sec)
            prev = cur
        for j in free_at.pop(i, ()):
            frames.pop(j, None)
        if progress and i % 25 == 0:
            progress(min(1.0, i / total))
    if not dx:
        raise RuntimeError(tr("Keine Frames gelesen: {0}", path))
    if all(o < 0 for o in owner):
        raise RuntimeError(tr("Kein Tracking-Abschnitt liegt im Video"))
    dx_a = np.asarray(dx, np.float64)
    dy_a = np.asarray(dy, np.float64)
    _spread_corrections(events, centers, owner, plan, dx_a, dy_a, k)
    gaps = [None if lo is None else lo.copy() for lo in losts]
    _bridge_gaps(centers, gaps, outs, losts, owner, dx_a, dy_a, k, info.fps / step, knots)
    dx, dy = dx_a, dy_a
    nan = np.full((k, 2), np.nan)
    tracks = np.array([nan if c is None else c for c in centers], np.float32) / np.array([w, h], np.float32)
    sizes = np.full((len(sections), k, 2), np.nan, np.float32)
    for si, s in enumerate(sections):
        for ai, b in enumerate(s.boxes[:k]):
            sizes[si, ai] = (b[2], b[3])
    nan2 = np.full((k, 2), np.nan)
    geom = np.array([nan2 if g is None else g for g in geoms], np.float32)
    lost = np.array([np.zeros(k, bool) if lo is None else lo for lo in losts], bool)
    if progress:
        progress(1.0)
    return MotionData(str(path), info.fps / step, np.asarray(dx, np.float32), np.asarray(dy, np.float32),
                      {"opencl": False, "width": w, "height": h, "mode": "track",
                       "decoder": report.get("decoder"), "decoder_errors": report.get("errors", []),
                       "ai_moves": ai_moves, "notes": report.get("notes", [])},
                      tracks=tracks, track_sizes=sizes, track_section=np.asarray(owner, np.int16),
                      track_geom=geom, track_lost=lost)


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
