"""Editing operations on a list of actions for a set of selected indices.

Pure functions (no Qt) so they can be tested on their own. Every function
returns a new, time-sorted action list; the input is never modified.
"""

from __future__ import annotations

import math

from .funscript import Action

Clip = list[tuple[int, int]]          # (ms relative to the first point, position)


def _clamp_pos(p: float) -> int:
    return round(min(100.0, max(0.0, p)))


def move(actions: list[Action], sel: set[int], dt: float, dpos: float,
         duration_ms: float) -> list[Action]:
    """Shift the selected points together by dt ms and dpos positions.

    The shift is limited so the selection stays inside 0..duration and 0..100
    and does not pass a point that is not selected (the order is kept)."""
    if not sel:
        return list(actions)
    idx = sorted(i for i in sel if 0 <= i < len(actions))
    first, last = idx[0], idx[-1]
    lo = (actions[first - 1].at + 1 if first > 0 else 0) - actions[first].at
    hi = (actions[last + 1].at - 1 if last + 1 < len(actions) else duration_ms) - actions[last].at
    # unselected points inside the selected range also bound the shift
    for k in range(first, last + 1):
        if k in sel:
            continue
        for i in idx:
            gap = actions[k].at - actions[i].at
            if gap > 0:
                hi = min(hi, gap - 1)
            elif gap < 0:
                lo = max(lo, gap + 1)
    dt = round(min(max(dt, lo), hi))
    pos = [actions[i].pos for i in idx]
    dpos = min(max(dpos, -min(pos)), 100 - max(pos))
    out = list(actions)
    for i in idx:
        out[i] = Action(actions[i].at + dt, _clamp_pos(actions[i].pos + dpos))
    return sorted(out, key=lambda a: a.at)


def delete(actions: list[Action], sel: set[int]) -> list[Action]:
    return [a for i, a in enumerate(actions) if i not in sel]


def copy(actions: list[Action], sel: set[int]) -> Clip:
    idx = sorted(i for i in sel if 0 <= i < len(actions))
    if not idx:
        return []
    t0 = actions[idx[0]].at
    return [(actions[i].at - t0, actions[i].pos) for i in idx]


def paste(actions: list[Action], clip: Clip, at_ms: int, duration_ms: float) -> tuple[list[Action], set[int]]:
    """Insert the clip starting at `at_ms`; points already in that time span are
    replaced. Returns the new list and the indices of the pasted points."""
    if not clip:
        return list(actions), set()
    at_ms = int(max(0, at_ms))
    new = [Action(at_ms + dt, p) for dt, p in clip if at_ms + dt <= duration_ms]
    if not new:
        return list(actions), set()
    start, end = new[0].at, new[-1].at
    kept = [a for a in actions if not start <= a.at <= end]
    out = sorted(kept + new, key=lambda a: a.at)
    ids = {id(a) for a in new}
    return out, {i for i, a in enumerate(out) if id(a) in ids}


def smooth(actions: list[Action], sel: set[int], strength: float = 0.5) -> list[Action]:
    """Pull the selected points towards the average of their neighbours
    (removes jitter and small spikes). strength 0..1."""
    out = list(actions)
    for i in sorted(sel):
        if not 0 <= i < len(actions):
            continue
        prev = actions[i - 1].pos if i > 0 else actions[i].pos
        nxt = actions[i + 1].pos if i + 1 < len(actions) else actions[i].pos
        avg = (prev + nxt) / 2
        out[i] = Action(actions[i].at, _clamp_pos(actions[i].pos + (avg - actions[i].pos) * strength))
    return out


def scale(actions: list[Action], sel: set[int], factor: float) -> list[Action]:
    """Make the strokes of the selection bigger (factor > 1) or smaller (< 1),
    around the middle of the selection's range."""
    idx = [i for i in sel if 0 <= i < len(actions)]
    if not idx:
        return list(actions)
    pos = [actions[i].pos for i in idx]
    mid = (min(pos) + max(pos)) / 2
    out = list(actions)
    for i in idx:
        out[i] = Action(actions[i].at, _clamp_pos(mid + (actions[i].pos - mid) * factor))
    return out


def in_rect(actions: list[Action], t0: float, t1: float, p0: float, p1: float) -> set[int]:
    """Indices of the points inside the time range t0..t1 and position range p0..p1."""
    t0, t1 = sorted((t0, t1))
    p0, p1 = sorted((p0, p1))
    return {i for i, a in enumerate(actions) if t0 <= a.at <= t1 and p0 <= a.pos <= p1}


#: rhythm patterns: (code, label)
PATTERNS = (("even", "Gleichmäßig"), ("faster", "Schneller werdend"), ("slower", "Langsamer werdend"),
            ("double", "Doppelschlag"), ("wave", "Wellen (Tiefe schwankt)"))


def _rhythm_points(start: int, end: int, bpm: float, lo: int, hi: int, pattern: str) -> list[Action]:
    span = end - start
    half = 30_000.0 / max(1.0, bpm)               # ms from top to bottom (one stroke = down + up)
    pts: list[Action] = []
    t, k = float(start), 0
    while t <= end + 0.5:
        frac = (t - start) / span if span > 0 else 0.0
        top = k % 2 == 0
        a, b = lo, hi
        if pattern == "wave":                     # depth swells over ~8 strokes
            amp = 0.55 + 0.45 * math.sin(2 * math.pi * (t - start) / (16 * half))
            mid = (lo + hi) / 2
            a, b = mid - (mid - lo) * amp, mid + (hi - mid) * amp
        pts.append(Action(int(round(t)), _clamp_pos(b if top else a)))
        if pattern == "faster":
            step = half / (1.0 + frac)            # up to twice the tempo at the end
        elif pattern == "slower":
            step = half / (2.0 - frac)            # from twice the tempo down to the tempo
        elif pattern == "double":                 # two quick strokes, then a pause
            step = half * 0.5
            if k % 4 == 3:                        # hold at the bottom, then go on
                if t + half <= end:
                    pts.append(Action(int(round(t + half)), pts[-1].pos))
                step = half * 1.5
        else:
            step = half
        t += step
        k += 1
    return pts


def rhythm(actions: list[Action], start_ms: float, end_ms: float, bpm: float, lo: int = 0,
           hi: int = 100, pattern="even") -> tuple[list[Action], set[int]]:
    """Replace the points between start and end with a regular rhythm.
    pattern: a code from PATTERNS or a patterns.Pattern (Muster-Steuerung).
    bpm = strokes per minute (one stroke = down and up). Returns the new list and
    the indices of the inserted points."""
    start, end = int(round(min(start_ms, end_ms))), int(round(max(start_ms, end_ms)))
    lo, hi = sorted((int(lo), int(hi)))
    if end - start < 50:
        return list(actions), set()
    if isinstance(pattern, str):
        new = _rhythm_points(start, end, bpm, lo, hi, pattern)
    else:                                         # a stroke pattern from the library (patterns.Pattern)
        from .patterns import actions as pattern_actions
        new = pattern_actions(pattern, bpm, lo, hi, start, end, start_pos=hi)
    keep = [a for a in actions if not start <= a.at <= end]
    out = sorted(keep + new, key=lambda a: a.at)
    ids = {id(a) for a in new}
    return out, {i for i, a in enumerate(out) if id(a) in ids}
