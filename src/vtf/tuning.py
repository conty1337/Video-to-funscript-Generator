"""Suggest script settings: the script is made from the finished analysis again with other
sensitivities, smoothings and minimum gaps (no new analysis) and each result is rated:

* how well its strokes follow the movement (the curve barely smoothed is the measure), and
* how few spots the script check finds per minute (trembling, too fast, gaps …).

There is no "right" curve to compare with: a rating that only counted findings would smooth
real small strokes away, the first part keeps that in check. A suggestion, not a verdict.

The search narrows in by itself: the current settings and a coarse grid over the whole range,
then around the best three with half the steps (down to 1 %, 5 ms, 5 ms), again and again,
until two rounds in a row bring nothing noticeably better."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .analysis import MotionData
from .generator import GeneratorSettings, generate
from .quality import check

SENSITIVITIES = (30, 45, 60, 75, 90)         # coarse grid: 5 x 4 x 3 = 60 tries
SMOOTHINGS = (50, 110, 170, 230)
MIN_GAPS = (60, 120, 180)
STEPS = (15, 60, 60)         # their spacing; each round halves it ...
FINEST = (1, 5, 5)           # ... down to this
LIMITS = ((0, 100), (0, 500), (0, 1000))
MAX_TRIES = 400              # safety: about a minute for 45 min of video
MIN_GAIN = 0.001             # less rating gain than this per round is no improvement
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

    def rate(cand: GeneratorSettings, acts) -> Rated:
        issues = check(acts, base.max_speed, cuts)
        return (_fit(t_ms, sig, acts) - ISSUE_WEIGHT * len(issues) / minutes, cand, len(issues),
                len(acts) / 2 / minutes)

    tried: dict[tuple, tuple[Rated, int]] = {}      # (sens, smooth, gap) -> (rating, script hash)

    def tryit(k: tuple) -> None:
        k = tuple(int(min(hi, max(lo, v))) for v, (lo, hi) in zip(k, LIMITS))
        if k in tried or len(tried) >= MAX_TRIES or stop():
            return
        cand = replace(base, sensitivity=k[0], smoothing_ms=k[1], min_interval_ms=k[2])
        acts = generate(motion, cand, sections).actions
        tried[k] = (rate(cand, acts), hash(tuple((a.at, a.pos) for a in acts)))
        if progress:
            progress(min(0.99, len(tried) / 250))

    def leaders(n: int) -> list[tuple]:
        return sorted(tried, key=lambda k: -tried[k][0][0])[:n]

    now = (base.sensitivity, base.smoothing_ms, base.min_interval_ms)
    tryit(now)                                  # the current settings take part too
    current = tried[tuple(int(min(hi, max(lo, v))) for v, (lo, hi) in zip(now, LIMITS))][0]
    for k in ((a, b, c) for a in SENSITIVITIES for b in SMOOTHINGS for c in MIN_GAPS):
        tryit(k)
    steps, idle = STEPS, 0
    while tried and not stop() and len(tried) < MAX_TRIES:
        best = tried[leaders(1)[0]][0][0]
        steps = tuple(max(f, s // 2) for s, f in zip(steps, FINEST))
        for c in leaders(3):
            for d in ((i, j, k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)):
                tryit(tuple(v + dv * st for v, dv, st in zip(c, d, steps)))
        idle = idle + 1 if tried[leaders(1)[0]][0][0] < best + MIN_GAIN else 0
        if idle >= 2 or (idle and steps == FINEST):
            break                               # nothing noticeably better any more: done
    out, seen = [], set()
    for k in leaders(len(tried)):
        r, h = tried[k]
        if h not in seen:                       # the same script with other numbers: once is enough
            seen.add(h)
            out.append(r)
    def change(c):                          # equal ratings: the one closest to the current settings
        return (abs(c.sensitivity - base.sensitivity) / 10 + abs(c.smoothing_ms - base.smoothing_ms) / 50
                + abs(c.min_interval_ms - base.min_interval_ms) / 40)
    out.sort(key=lambda x: (-round(x[0], 4), change(x[1])))
    return out[:top], current
