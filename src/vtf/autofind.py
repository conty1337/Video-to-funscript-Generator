"""Find the areas to track by themselves: "Automatisch finden".

A few seconds from the playhead, many corner points are followed. Points that move
far, in a steady rhythm and together are the toy (or machine); their hull becomes its
outline. A patterned spot close to it that moves much less becomes the reference
(the body). The stroke direction is the main direction of the toy's motion, its head
pointing away from the reference ("out"). No Qt.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

SECONDS = 4.0            # how long is looked at
WIDTH = 320              # analysis width (px)
MAX_POINTS = 500
MIN_TRACKED = 0.9        # a point must be followed through this part of the frames
FREQ_RANGE = (0.4, 5.0)  # strokes per second that count as a rhythm
TOGETHER = 0.8           # correlation with the strongest point: moves with the toy
REF_REACH = 2.5          # the reference is looked for this many toy sizes around it
REF_MOTION = 0.6         # ... among points moving less than this part of the toy


@dataclass
class Found:
    toy: tuple                       # (x, y, w, h) normalized
    outline: tuple                   # toy outline in the box's own coordinates
    body: tuple | None               # reference (x, y, w, h) normalized, or None
    stroke: tuple | None             # arrow (x0, y0, x1, y1) normalized, head = out
    rate: float                      # strokes per second
    sure: float                      # 0..1: how clearly one thing moves in a rhythm


def read_frames(path: str, start_ms: float, seconds: float = SECONDS, width: int = WIDTH):
    """Gray frames (blurred a little) from start_ms, about 25 per second, and their rate."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return [], 25.0
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(fps / 25.0)))
    cap.set(cv2.CAP_PROP_POS_MSEC, float(start_ms))
    frames = []
    n = int(seconds * fps)
    for k in range(n):
        ok, img = cap.read()
        if not ok:
            break
        if k % step:
            continue
        h, w = img.shape[:2]
        g = cv2.cvtColor(cv2.resize(img, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY)
        frames.append(cv2.GaussianBlur(g, (3, 3), 0))
    cap.release()
    return frames, fps / step


def _tracks(frames) -> np.ndarray:
    """(points, frames, 2) positions; NaN after a point was lost."""
    p0 = cv2.goodFeaturesToTrack(frames[0], MAX_POINTS, 0.01, 4)
    if p0 is None:
        return np.zeros((0, len(frames), 2))
    pts = p0.reshape(-1, 1, 2).astype(np.float32)
    out = np.full((len(pts), len(frames), 2), np.nan)
    out[:, 0] = pts.reshape(-1, 2)
    alive = np.ones(len(pts), bool)
    lk = {"winSize": (21, 21), "maxLevel": 3,
          "criteria": (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03)}
    for t in range(1, len(frames)):
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(frames[t - 1], frames[t], pts, None, **lk)
        back, st2, _ = cv2.calcOpticalFlowPyrLK(frames[t], frames[t - 1], nxt, None, **lk)
        err = np.linalg.norm((pts - back).reshape(-1, 2), axis=1)
        alive &= (st.ravel() == 1) & (st2.ravel() == 1) & (err < 1.5)
        pts = nxt
        out[alive, t] = pts.reshape(-1, 2)[alive]
    return out


def _rhythm(series: np.ndarray, rate: float) -> tuple[float, float]:
    """(share of the motion in the strongest rhythm, strokes per second) of a 1D series."""
    x = series - np.linspace(series[0], series[-1], len(series))      # remove a slow drift
    x = x - x.mean()
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    band = (freqs >= FREQ_RANGE[0]) & (freqs <= FREQ_RANGE[1])
    total = float(spec[1:].sum())
    if not band.any() or total <= 0:
        return 0.0, 0.0
    k = int(np.argmax(np.where(band, spec, 0)))
    peak = float(spec[max(1, k - 1):k + 2].sum())
    return peak / total, float(freqs[k])


def _box(points: np.ndarray, pad: float, w: int, h: int, min_px: float = 12) -> tuple:
    x0, y0 = points.min(axis=0)
    x1, y1 = points.max(axis=0)
    bw, bh = max(x1 - x0, min_px), max(y1 - y0, min_px)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    bw, bh = bw * (1 + 2 * pad), bh * (1 + 2 * pad)
    x0, y0 = max(0.0, cx - bw / 2), max(0.0, cy - bh / 2)
    x1, y1 = min(float(w), cx + bw / 2), min(float(h), cy + bh / 2)
    return (x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h)


def find(frames, rate: float) -> Found | None:
    """The toy, the reference and the stroke direction in these frames (None: no rhythm)."""
    if len(frames) < 2 * rate:                   # at least two seconds
        return None
    h, w = frames[0].shape
    tr = _tracks(frames)
    keep = np.mean(~np.isnan(tr[:, :, 0]), axis=1) >= MIN_TRACKED
    tr = tr[keep]
    if len(tr) < 8:
        return None
    # fill the few missing frames at the end with the last position
    for p in tr:
        bad = np.isnan(p[:, 0])
        if bad.any():
            last = np.where(~bad)[0][-1]
            p[last + 1:] = p[last]
    # camera: the median motion of the calmest half of the points
    disp = tr - tr[:, :1]
    spread = np.linalg.norm(disp, axis=2).max(axis=1)
    calm = spread <= np.median(spread)
    cam = np.median(disp[calm], axis=0)
    disp = disp - cam
    # per point: main direction, how far, how rhythmic
    amps = np.zeros(len(tr))
    shares = np.zeros(len(tr))
    rates = np.zeros(len(tr))
    proj = np.zeros((len(tr), tr.shape[1]))
    axes = np.zeros((len(tr), 2))
    for i, d in enumerate(disp):
        c = d - d.mean(axis=0)
        _u, _s, vt = np.linalg.svd(c, full_matrices=False)
        axes[i] = vt[0]
        proj[i] = c @ vt[0]
        amps[i] = float(np.percentile(proj[i], 95) - np.percentile(proj[i], 5))
        shares[i], rates[i] = _rhythm(proj[i], rate)
    score = amps * shares
    best = int(np.argmax(score))
    if amps[best] < 0.03 * w or shares[best] < 0.25:
        return None
    # the toy: points moving with the strongest one
    ref = proj[best] - proj[best].mean()
    corr = np.array([abs(np.corrcoef(ref, p)[0, 1]) if np.std(p) > 1e-6 else 0.0 for p in proj])
    toy = (corr >= TOGETHER) & (amps >= 0.5 * amps[best])
    toy_pts = tr[toy, 0]
    toy_box = _box(toy_pts, 0.15, w, h)
    hull = cv2.convexHull(toy_pts.astype(np.float32)).reshape(-1, 2)
    bx, by, bw, bh = toy_box[0] * w, toy_box[1] * h, toy_box[2] * w, toy_box[3] * h
    centre = np.array([bx + bw / 2, by + bh / 2])
    grown = centre + (hull - hull.mean(axis=0)) * 1.15 + (hull.mean(axis=0) - centre)
    outline = tuple((round(float((x - centre[0]) / bw), 4), round(float((y - centre[1]) / bh), 4))
                    for x, y in grown) if len(hull) >= 3 else ()
    # the reference: a crowded spot of calmer points close to the toy
    size = max(bw, bh)
    near = np.linalg.norm(tr[:, 0] - centre, axis=1) <= REF_REACH * size
    cand = (~toy) & near & (amps < REF_MOTION * amps[best])
    body_box = None
    body_centre = None
    if cand.sum() >= 5:
        cp = tr[cand, 0]
        r = max(12.0, 0.5 * min(bw, bh))     # a spot about as big as the toy is wide, not the whole body
        counts = np.array([(np.linalg.norm(cp - q, axis=1) <= r).sum() for q in cp])
        dist = np.linalg.norm(cp - centre, axis=1)
        # enough pattern (neighbours) and close to the toy: the body right where it is touched
        k = int(np.argmax(np.minimum(counts, 12) * 1000 - dist))
        group = cp[np.linalg.norm(cp - cp[k], axis=1) <= r]
        if len(group) >= 5:
            body_box = _box(group, 0.1, w, h)
            body_centre = group.mean(axis=0)
    # stroke direction: main direction of the toy points, head away from the reference
    a = axes[toy].copy()
    a[a @ a[0] < 0] *= -1
    u = a.mean(axis=0)
    u = u / max(np.linalg.norm(u), 1e-9)
    if body_centre is not None:
        if np.dot(u, centre - body_centre) < 0:
            u = -u
    elif u[1] > 0:                                          # no reference: out = up
        u = -u
    half = 0.5 * size
    p0, p1 = centre - u * half, centre + u * half
    stroke = (round(float(p0[0] / w), 4), round(float(p0[1] / h), 4),
              round(float(p1[0] / w), 4), round(float(p1[1] / h), 4))
    sure = float(min(1.0, shares[best] * 1.5) * min(1.0, toy.sum() / 8))
    return Found(tuple(round(float(v), 4) for v in toy_box), outline,
                 None if body_box is None else tuple(round(float(v), 4) for v in body_box),
                 stroke, float(np.median(rates[toy])), sure)


def find_in_video(path: str, start_ms: float) -> Found | None:
    frames, rate = read_frames(path, start_ms)
    return find(frames, rate) if frames else None
