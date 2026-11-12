"""Turn raw motion data into a position curve and funscript actions."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .analysis import MotionData, section_ranges
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
    max_speed: int = 0              # positions per second the device can do; 0 = no limit


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


def stroke_dir(motion: MotionData, stroke) -> np.ndarray | None:
    """Unit vector (analysis px) of a stroke arrow (x0, y0, x1, y1 normalized), pointing
    to its head ("out"); None without a usable arrow."""
    if not stroke or len(stroke) < 4:
        return None
    w = float(motion.meta.get("width", 1) or 1)
    h = float(motion.meta.get("height", 1) or 1)
    v = np.array([(stroke[2] - stroke[0]) * w, (stroke[3] - stroke[1]) * h], np.float64)
    n = float(np.hypot(*v))
    return v / n if n > 1e-6 else None


def project_sections(motion: MotionData, axis: str, sections=()) -> np.ndarray:
    """Like project(), but tracking sections with a stroke arrow measure only along it:
    sideways shaking and wobbling do not count."""
    vel = project(motion, axis)
    if motion.track_section is None:
        return vel
    for i, sec in enumerate(sections):
        u = stroke_dir(motion, getattr(sec, "stroke", None))
        if u is None:
            continue
        mask = motion.track_section == i
        vel[mask] = motion.dx[mask] * u[0] + motion.dy[mask] * u[1]
    return vel


# ------------------------------------------------------------- pipeline

def build_signal(motion: MotionData, s: GeneratorSettings, sections=()) -> tuple[np.ndarray, np.ndarray]:
    fps = motion.fps
    n = len(motion)
    t_ms = np.arange(n) * (1000.0 / fps)
    if n < 3:
        return t_ms, np.full(n, 0.5)

    vel = project_sections(motion, s.axis, sections)
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


def find_extrema(signal: np.ndarray, delta) -> list[int]:
    """Alternating peak/valley indices with hysteresis ``delta`` (a number, or one per sample)."""
    n = len(signal)
    if n == 0:
        return []
    dl = np.broadcast_to(np.asarray(delta, np.float64), (n,))
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
        if looking_for_max is not False and v < mx - dl[mx_i]:
            if looking_for_max is None and mn_i < mx_i:
                out.append(mn_i)
            out.append(mx_i)
            looking_for_max = False
            mn, mn_i = v, i
        elif looking_for_max is not True and v > mn + dl[mn_i]:
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


LOCAL_MS = 1000          # how far back and ahead "recent strokes" reach for the threshold
LOCAL_FLOOR = 0.35       # the threshold never drops below this part (quiet passages stay quiet)


def local_delta(t_ms: np.ndarray, signal: np.ndarray, delta: float) -> np.ndarray:
    """Threshold per sample that follows the size of the strokes around it: right after big
    strokes the curve is scaled to them, so small strokes look tiny; measured against what
    happens within about a second they still count."""
    n = len(signal)
    if n < 3:
        return np.full(n, delta)
    dt = float(t_ms[1] - t_ms[0]) or 1.0
    k = max(3, int(round(LOCAL_MS / dt)))
    span = _rolling(signal, k, np.max) - _rolling(signal, k, np.min)
    return delta * np.clip(span, LOCAL_FLOOR, 1.0)


def turn_time(t_ms: np.ndarray, signal: np.ndarray, i: int) -> float:
    """Time of the turning point at sample i, between frames: the top of a parabola through
    the sample and its neighbours (a turn rarely falls exactly on a frame)."""
    if not 0 < i < len(signal) - 1:
        return float(t_ms[i])
    a, b, c = float(signal[i - 1]), float(signal[i]), float(signal[i + 1])
    den = a - 2 * b + c
    if den == 0:
        return float(t_ms[i])
    f = max(-0.5, min(0.5, 0.5 * (a - c) / den))
    return float(t_ms[i]) + f * float(t_ms[i + 1] - t_ms[i])


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


def extract_actions(t_ms: np.ndarray, signal: np.ndarray, s: GeneratorSettings,
                    delta_scale: float = 1.0) -> list[Action]:
    sens = float(np.clip(s.sensitivity, 0, 100)) / 100.0
    delta = (0.02 + (1.0 - sens) * 0.5) * delta_scale
    idx = find_extrema(signal, local_delta(t_ms, signal, delta))
    idx = enforce_min_interval(idx, t_ms, signal, s.min_interval_ms)
    lo, hi = sorted((int(s.pos_min), int(s.pos_max)))
    actions: list[Action] = []
    for k, i in enumerate(idx):
        v = float(signal[i])
        if s.full_stroke and len(idx) > 1:
            nb = signal[idx[k + 1]] if k + 1 < len(idx) else signal[idx[k - 1]]
            v = 1.0 if v >= nb else 0.0
        pos = int(round(lo + v * (hi - lo)))
        at = int(round(turn_time(t_ms, signal, i)))
        if actions and at <= actions[-1].at:
            continue
        actions.append(Action(at, max(0, min(100, pos))))
    return actions


def limit_speed(actions: list[Action], max_speed: float) -> list[Action]:
    """Shorten strokes the device cannot do: no step faster than max_speed
    positions per second. Each point moves towards the previous one, so the
    timing stays and only the amplitude shrinks."""
    if max_speed <= 0 or len(actions) < 2:
        return list(actions)
    out = [actions[0]]
    for a in actions[1:]:
        prev = out[-1]
        dt = max(1, a.at - prev.at)
        reach = max_speed * dt / 1000.0
        delta = a.pos - prev.pos
        if abs(delta) > reach:
            pos = int(round(prev.pos + (reach if delta > 0 else -reach)))
            out.append(Action(a.at, max(0, min(100, pos))))
        else:
            out.append(a)
    return out


def speeds(actions: list[Action]) -> list[float]:
    """Speed of each step in positions per second (len = len(actions) - 1)."""
    return [abs(b.pos - a.pos) * 1000.0 / max(1, b.at - a.at) for a, b in zip(actions, actions[1:])]


def generate(motion: MotionData, s: GeneratorSettings | None = None, sections=()) -> GeneratedScript:
    """Script from the analysis. With tracking sections, points are only made
    inside them, and calibrated sections use the real depth."""
    s = s or GeneratorSettings()
    t_ms, sig = build_signal(motion, s, sections)
    if motion.track_section is None or not sections:
        return GeneratedScript(t_ms, sig, limit_speed(extract_actions(t_ms, sig, s), s.max_speed))
    sig = sig.copy()
    ranges = section_ranges(sections, motion.duration_ms + 1000)
    calibrated: set[int] = set()
    for sec, _a, _b in ranges:
        depth = depth_signal(motion, s, sections[sec], sec)
        if depth is not None:
            mask = motion.track_section == sec
            sig[mask] = depth[mask]
            calibrated.add(sec)
    actions: list[Action] = []
    for sec, a, b in ranges:
        inside = (t_ms >= a) & (t_ms < b)
        if inside.any():
            # the real depth is not stretched to the full range: smaller strokes must count too
            scale = 0.4 if sec in calibrated else 1.0
            actions += extract_actions(t_ms[inside], sig[inside], s, delta_scale=scale)
    actions.sort(key=lambda x: x.at)
    actions = limit_speed(actions, s.max_speed)
    return GeneratedScript(t_ms, sig, actions)


def _measure(motion: MotionData, n_areas: int, axis: str, u: np.ndarray | None = None) -> np.ndarray:
    """What the depth is read from, per sample (px of the analysis frame):
    the distance between areas 1 and 2, or with one area its position. With a stroke
    direction `u`: only the part along it."""
    w = float(motion.meta.get("width", 1) or 1)
    h = float(motion.meta.get("height", 1) or 1)
    tr = motion.tracks.astype(np.float64) * np.array([w, h])
    if u is not None:
        rel = tr[:, 0] - tr[:, 1] if n_areas >= 2 else tr[:, 0]
        return rel @ u
    if n_areas >= 2:
        return np.hypot(*(tr[:, 1] - tr[:, 0]).T)
    if axis == "horizontal":
        return tr[:, 0, 0]
    return -tr[:, 0, 1]                      # up = out


def depth_signal(motion: MotionData, s: GeneratorSettings, section, index: int) -> np.ndarray | None:
    """0..1 per sample for a calibrated section: 0 = like the "fully in" moment,
    1 = like the "fully out" moment. None if the section is not calibrated or the
    moments are not inside it."""
    if not getattr(section, "calibrated", False) or motion.tracks is None:
        return None
    mask = motion.track_section == index
    if not mask.any():
        return None
    n_areas = int(np.sum(~np.isnan(motion.track_sizes[index, :, 0]))) if motion.track_sizes is not None else 1
    m = _measure(motion, n_areas, s.axis, stroke_dir(motion, getattr(section, "stroke", None)))
    fps = motion.fps

    def at(ms):
        i = round(ms / 1000 * fps)
        if not 0 <= i < len(m) or not mask[i] or np.isnan(m[i]):
            return None
        lo, hi = max(0, i - 1), min(len(m), i + 2)          # a little averaging
        return float(np.nanmean(m[lo:hi]))

    m_in, m_out = at(section.calib_in_ms), at(section.calib_out_ms)
    if m_in is None or m_out is None or abs(m_out - m_in) < 1.0:
        return None
    vals = np.where(mask, m, m_in)
    vals = gaussian_smooth(np.nan_to_num(vals, nan=m_in), s.smoothing_ms / 1000.0 * fps / 2.0)
    sig = (vals - m_in) / (m_out - m_in)
    if s.invert:
        sig = 1.0 - sig
    return np.clip(sig, 0.0, 1.0)


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
    valid = ~np.isnan(vec).any(axis=1)              # both areas tracked
    if valid.sum() < 3:
        return None
    idx = np.arange(len(vec))
    raw = np.arctan2(vec[valid, 1], vec[valid, 0])
    ang = np.interp(idx, idx[valid], np.unwrap(raw))   # gaps between sections: bridged
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
    sig[~valid] = 0.5                               # neutral where nothing is tracked
    return np.arange(len(ang)) * (1000.0 / fps), sig


def generate_axes(motion: MotionData, s: GeneratorSettings | None = None,
                  sections=()) -> dict[str, list[Action]]:
    """Further axes that can be derived from the analysis:

    * L2 sway: the horizontal motion
    * R1 roll: the angle between tracked areas 1 and 2 (tracking with >= 2 areas)
    """
    from dataclasses import replace

    s = s or GeneratorSettings()
    out: dict[str, list[Action]] = {}
    sway = replace(s, axis="horizontal", invert=False, full_stroke=False, pos_min=0, pos_max=100)
    out["L2"] = generate(motion, sway).actions
    ranges = section_ranges(sections, motion.duration_ms + 1000) if motion.track_section is not None else []
    if ranges:                                      # tracking: only inside the sections
        out["L2"] = [a for a in out["L2"] if any(lo <= a.at < hi for _i, lo, hi in ranges)]
    roll = roll_signal(motion, replace(s, invert=False))
    if roll is not None:
        t_ms, sig = roll
        acts = extract_actions(t_ms, sig, replace(s, full_stroke=False, pos_min=0, pos_max=100))
        if ranges:
            acts = [a for a in acts if any(lo <= a.at < hi for _i, lo, hi in ranges)]
        out["R1"] = acts
    return out
