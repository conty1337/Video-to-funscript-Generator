"""Turn raw motion data into a position curve and funscript actions."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .analysis import MotionData
from .funscript import Action


@dataclass
class GeneratorSettings:
    axis: str = "vertical"          # "vertical", "horizontal", "auto"
    invert: bool = False
    sensitivity: int = 60           # 0..100, higher = more (smaller) strokes detected
    smoothing_ms: int = 100         # gaussian smoothing of the position curve
    drift_window_ms: int = 2500     # removes slow drift (high-pass)
    normalize_window_ms: int = 5000  # local amplitude normalization; 0 = global
    min_interval_ms: int = 120      # minimum time between two actions
    pos_min: int = 0
    pos_max: int = 100
    full_stroke: bool = False       # peaks -> pos_max, valleys -> pos_min


@dataclass
class GeneratedScript:
    t_ms: np.ndarray                # time of each curve sample
    signal: np.ndarray              # normalized curve 0..1
    actions: list[Action] = field(default_factory=list)


# ------------------------------------------------------------- helpers

def _conv_reflect(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    k = len(kernel)
    if k <= 1 or len(x) == 0:
        return x.astype(np.float64)
    pad = k // 2
    mode = "reflect" if len(x) > pad else "edge"
    xp = np.pad(x.astype(np.float64), (pad, k - 1 - pad), mode=mode)
    return np.convolve(xp, kernel, mode="valid")


def gaussian_smooth(x: np.ndarray, sigma: float) -> np.ndarray:
    if sigma < 0.3:
        return x.astype(np.float64)
    r = int(np.ceil(3 * sigma))
    t = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (t / sigma) ** 2)
    return _conv_reflect(x, k / k.sum())


def moving_average(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return x.astype(np.float64)
    return _conv_reflect(x, np.full(n, 1.0 / n))


def _rolling(x: np.ndarray, n: int, fn) -> np.ndarray:
    n = max(1, min(n, len(x)))
    if n == 1:
        return x.copy()
    pad = n // 2
    xp = np.pad(x, (pad, n - 1 - pad), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(xp, n)
    return fn(view, axis=1)


def project(motion: MotionData, axis: str) -> np.ndarray:
    """Velocity along the chosen axis; positive = 'up' (towards pos 100)."""
    dx = motion.dx.astype(np.float64)
    dy = motion.dy.astype(np.float64)
    if axis == "horizontal":
        return dx
    if axis == "auto" and len(dx) > 2:
        m = np.vstack([dx, dy])
        cov = m @ m.T
        vals, vecs = np.linalg.eigh(cov)
        ux, uy = vecs[:, int(np.argmax(vals))]
        if uy > 0:      # orient so that the vertical component points up
            ux, uy = -ux, -uy
        return dx * ux + dy * uy
    return -dy          # image y grows downwards


# ------------------------------------------------------------- pipeline

def build_signal(motion: MotionData, s: GeneratorSettings) -> tuple[np.ndarray, np.ndarray]:
    fps = motion.fps
    n = len(motion)
    t_ms = np.arange(n) * (1000.0 / fps)
    if n < 3:
        return t_ms, np.full(n, 0.5)

    vel = project(motion, s.axis)
    pos = np.cumsum(vel)
    drift_n = int(round(s.drift_window_ms / 1000.0 * fps))
    if drift_n > 2:
        pos = pos - moving_average(pos, drift_n)
    pos = gaussian_smooth(pos, s.smoothing_ms / 1000.0 * fps / 2.0)
    if s.invert:
        pos = -pos

    lo_g, hi_g = np.percentile(pos, [2, 98])
    span_g = max(hi_g - lo_g, 1e-6)
    norm_n = int(round(s.normalize_window_ms / 1000.0 * fps))
    if norm_n > 2:
        lo = gaussian_smooth(_rolling(pos, norm_n, np.min), norm_n / 6)
        hi = gaussian_smooth(_rolling(pos, norm_n, np.max), norm_n / 6)
        # quiet passages must not be blown up to full range
        span = np.maximum(hi - lo, 0.15 * span_g)
        mid = (hi + lo) / 2
        sig = (pos - mid) / span + 0.5
    else:
        sig = (pos - lo_g) / span_g
    return t_ms, np.clip(sig, 0.0, 1.0)


def find_extrema(signal: np.ndarray, delta: float) -> list[int]:
    """Alternating peak/valley indices with hysteresis ``delta``."""
    n = len(signal)
    if n == 0:
        return []
    out: list[int] = []
    mn_i = mx_i = 0
    mn = mx = signal[0]
    looking_for_max = None  # unknown at start
    for i in range(1, n):
        v = signal[i]
        if v > mx:
            mx, mx_i = v, i
        if v < mn:
            mn, mn_i = v, i
        if looking_for_max is not False and v < mx - delta:
            if looking_for_max is None and mn_i < mx_i:
                out.append(mn_i)
            out.append(mx_i)
            looking_for_max = False
            mn, mn_i = v, i
        elif looking_for_max is not True and v > mn + delta:
            if looking_for_max is None and mx_i < mn_i:
                out.append(mx_i)
            out.append(mn_i)
            looking_for_max = True
            mx, mx_i = v, i
    # close with the last pending extremum
    if looking_for_max is True and (not out or mx_i > out[-1]):
        out.append(mx_i)
    elif looking_for_max is False and (not out or mn_i > out[-1]):
        out.append(mn_i)
    return out


def enforce_min_interval(idx: list[int], t_ms: np.ndarray, signal: np.ndarray,
                         min_ms: float) -> list[int]:
    if min_ms <= 0 or len(idx) < 2:
        return list(idx)
    out: list[int] = []
    for i in idx:
        if out and t_ms[i] - t_ms[out[-1]] < min_ms:
            if len(out) == 1:
                out[0] = i
                continue
            # out[-1] is the opposite type of i, out[-2] the same type:
            # drop the short wiggle and keep the more extreme of out[-2] / i
            is_peak = signal[i] > signal[out[-1]]
            out.pop()
            prev = out[-1]
            if (is_peak and signal[i] > signal[prev]) or (not is_peak and signal[i] < signal[prev]):
                out[-1] = i
            continue
        out.append(i)
    return out


def extract_actions(t_ms: np.ndarray, signal: np.ndarray, s: GeneratorSettings) -> list[Action]:
    sens = float(np.clip(s.sensitivity, 0, 100)) / 100.0
    delta = 0.02 + (1.0 - sens) * 0.5
    idx = find_extrema(signal, delta)
    idx = enforce_min_interval(idx, t_ms, signal, s.min_interval_ms)
    lo, hi = sorted((int(s.pos_min), int(s.pos_max)))
    actions: list[Action] = []
    for k, i in enumerate(idx):
        v = float(signal[i])
        if s.full_stroke and len(idx) > 1:
            nb = signal[idx[k + 1]] if k + 1 < len(idx) else signal[idx[k - 1]]
            v = 1.0 if v >= nb else 0.0
        pos = int(round(lo + v * (hi - lo)))
        at = int(round(t_ms[i]))
        if actions and at <= actions[-1].at:
            continue
        actions.append(Action(at, max(0, min(100, pos))))
    return actions


def generate(motion: MotionData, s: GeneratorSettings | None = None) -> GeneratedScript:
    s = s or GeneratorSettings()
    t_ms, sig = build_signal(motion, s)
    return GeneratedScript(t_ms, sig, extract_actions(t_ms, sig, s))


# ------------------------------------------------------------- further axes

def roll_signal(motion: MotionData, s: GeneratorSettings) -> tuple[np.ndarray, np.ndarray] | None:
    """Roll from tracking: the angle of the line from area 1 to area 2.
    None without at least two tracked areas."""
    tr = motion.tracks
    if tr is None or tr.ndim != 3 or tr.shape[1] < 2 or len(tr) < 3:
        return None
    w = float(motion.meta.get("width", 1) or 1)
    h = float(motion.meta.get("height", 1) or 1)
    vec = (tr[:, 1] - tr[:, 0]) * np.array([w, h])
    ang = np.unwrap(np.arctan2(vec[:, 1], vec[:, 0]))
    fps = motion.fps
    drift_n = int(round(s.drift_window_ms / 1000.0 * fps))
    if drift_n > 2:
        ang = ang - moving_average(ang, drift_n)
    ang = gaussian_smooth(ang, s.smoothing_ms / 1000.0 * fps / 2.0)
    if s.invert:
        ang = -ang
    # +-30 degrees (or the actual range, if smaller) -> 0..1
    span = max(float(np.percentile(np.abs(ang), 98)), np.radians(5))
    span = min(span, np.radians(30))
    sig = np.clip(ang / (2 * span) + 0.5, 0.0, 1.0)
    return np.arange(len(ang)) * (1000.0 / fps), sig


def generate_axes(motion: MotionData, s: GeneratorSettings | None = None) -> dict[str, list[Action]]:
    """Further axes that can be derived from the analysis:

    * L2 sway: the horizontal motion
    * R1 roll: the angle between tracked areas 1 and 2 (tracking with >= 2 areas)
    """
    from dataclasses import replace

    s = s or GeneratorSettings()
    out: dict[str, list[Action]] = {}
    sway = replace(s, axis="horizontal", invert=False, full_stroke=False, pos_min=0, pos_max=100)
    out["L2"] = generate(motion, sway).actions
    roll = roll_signal(motion, replace(s, invert=False))
    if roll is not None:
        t_ms, sig = roll
        out["R1"] = extract_actions(t_ms, sig, replace(s, full_stroke=False, pos_min=0, pos_max=100))
    return out
