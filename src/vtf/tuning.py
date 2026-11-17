"""Suggest script settings: the script is made from the finished analysis again with other
sensitivities, smoothings and minimum gaps (that takes seconds, no new analysis) and each
result is rated:

* how well its strokes follow the movement (the curve barely smoothed is the measure), and
* how few spots the script check finds per minute (trembling, too fast, gaps …).

There is no "right" curve to compare with: a rating that only counted findings would smooth
real small strokes away, the first part keeps that in check. A suggestion, not a verdict."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .analysis import MotionData
from .generator import GeneratorSettings, generate
from .quality import check

SENSITIVITIES = tuple(range(30, 95, 5))      # 13
SMOOTHINGS = tuple(range(50, 250, 25))       # 8
MIN_GAPS = (60, 90, 120, 150, 180)           # 5: 520 tries, about a minute for 45 min of video
ISSUE_WEIGHT = 0.02          # rating lost per finding per minute
REF_SMOOTHING_MS = 40


def _fit(t_ms: np.ndarray, ref: np.ndarray, actions) -> float:
    """How well the point curve follows the movement (correlation, 0 if it cannot be told)."""
    if len(actions) < 4:
        return 0.0
    curve = np.interp(t_ms, [a.at for a in actions], [a.pos for a in actions])
    lo, hi = actions[0].at, actions[-1].at
    keep = (t_ms >= lo) & (t_ms <= hi)
    if keep.sum() < 10 or np.std(curve[keep]) < 1e-6 or np.std(ref[keep]) < 1e-6:
        return 0.0
    return float(np.corrcoef(curve[keep], ref[keep])[0, 1])


Rated = tuple[float, GeneratorSettings, int, float]      # rating, settings, findings, strokes per minute


def suggest(motion: MotionData, base: GeneratorSettings, sections=(), cuts=(), top: int = 3,
            stop=lambda: False, progress=None) -> tuple[list[Rated], Rated]:
    """The best few, best first, and how the current settings do. Only sensitivity,
    smoothing and minimum gap change; direction, range etc. stay as they are."""
    ref = generate(motion, replace(base, smoothing_ms=REF_SMOOTHING_MS), sections)
    t_ms, sig = ref.t_ms, ref.signal        # already turned round with "invert", like the points
    minutes = max(1e-6, motion.duration_ms / 60000)
    grid = [(s, g, m) for s in SENSITIVITIES for g in SMOOTHINGS for m in MIN_GAPS]

    def rate(cand: GeneratorSettings, acts) -> Rated:
        issues = check(acts, base.max_speed, cuts)
        return (_fit(t_ms, sig, acts) - ISSUE_WEIGHT * len(issues) / minutes, cand, len(issues),
                len(acts) / 2 / minutes)

    current = rate(base, generate(motion, base, sections).actions)
    out, seen = [], set()
    for k, (sens, smooth, gap) in enumerate(grid):
        if stop():
            break
        if progress:
            progress(k / len(grid))
        cand = replace(base, sensitivity=sens, smoothing_ms=smooth, min_interval_ms=gap)
        acts = generate(motion, cand, sections).actions
        key = hash(tuple((a.at, a.pos) for a in acts))
        if key in seen:                     # the same script with other numbers: once is enough
            continue
        seen.add(key)
        out.append(rate(cand, acts))
    def change(c):                          # equal ratings: the one closest to the current settings
        return (abs(c.sensitivity - base.sensitivity) / 10 + abs(c.smoothing_ms - base.smoothing_ms) / 50
                + abs(c.min_interval_ms - base.min_interval_ms) / 40)
    out.sort(key=lambda x: (-round(x[0], 4), change(x[1])))
    return out[:top], current
