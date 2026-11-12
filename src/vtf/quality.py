"""Script check: finds the places in a script worth a second look, so not the whole
script has to be watched. No Qt, so it can be tested on its own.

Each finding is an Issue(kind, start ms, end ms, text). Kinds:

* "fast":   steps faster than the device can go
* "gap":    no strokes for a while in the middle of the script (outside cuts)
* "lost":   tracking lost an area there (from the analysis)
* "jitter": many tiny strokes in a row (trembling instead of strokes)
* "small":  strokes suddenly much smaller than around them (the box may have slipped)
* "beat":   one stroke much slower than the ones around it (the rhythm stops)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .i18n import plural, tr

DEFAULT_MAX_SPEED = 500      # positions per second: a full stroke in 200 ms (Fredorch F21S)
GAP_MS = 4000                # no stroke for this long inside the script
STROKE_MIN = 10              # a step of at least this many positions is a stroke
JITTER_POS = 12              # steps smaller than this ...
JITTER_COUNT = 5             # ... this many in a row
SMALL_FRAC = 0.35            # strokes below this part of the strokes around them
BEAT_FACTOR = 2.5            # a stroke this many times longer than the ones around it
AROUND = 8                   # strokes on each side that count as "around"
LOST_MIN_MS = 300            # shorter lost stretches are bridged anyway

KIND_LABELS = {
    "fast": tr("zu schnell"),
    "gap": tr("Lücke"),
    "lost": tr("Bereich verloren"),
    "jitter": tr("Zittern"),
    "small": tr("Hübe plötzlich klein"),
    "beat": tr("Takt setzt aus"),
}


@dataclass(frozen=True)
class Issue:
    kind: str
    start_ms: int
    end_ms: int
    text: str

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


def _fmt(ms: float) -> str:
    s = max(0.0, ms) / 1000
    return f"{int(s // 60)}:{s % 60:04.1f}"


def _in_cut(a: float, b: float, cuts) -> bool:
    return any(c0 <= a and b <= c1 for c0, c1 in cuts)


def _merge(issues: list[Issue], join_ms: int = 1500) -> list[Issue]:
    """Findings of one kind close to each other become one."""
    out: list[Issue] = []
    for it in sorted(issues, key=lambda x: (x.kind, x.start_ms)):
        if out and out[-1].kind == it.kind and it.start_ms - out[-1].end_ms <= join_ms:
            last = out[-1]
            out[-1] = Issue(last.kind, last.start_ms, max(last.end_ms, it.end_ms), last.text)
        else:
            out.append(it)
    return sorted(out, key=lambda x: x.start_ms)


def check(actions, max_speed: float = 0, cuts=(), lost=()) -> list[Issue]:
    """Places worth a look, sorted by time. `max_speed`: positions per second (0 = the
    default), `cuts`: (from, to) the player skips, `lost`: (from, to) where tracking lost
    an area."""
    acts = sorted(actions, key=lambda a: a.at)
    issues: list[Issue] = []
    limit = max_speed or DEFAULT_MAX_SPEED
    for a, b in zip(acts, acts[1:]):
        v = abs(b.pos - a.pos) * 1000.0 / max(1, b.at - a.at)
        if v > limit * 1.05 and not _in_cut(a.at, b.at, cuts):
            issues.append(Issue("fast", a.at, b.at, tr("{0:.0f} Pos./s, das Gerät schafft {1:.0f}", v, limit)))

    # strokes: steps of at least STROKE_MIN
    strokes = [(a, b) for a, b in zip(acts, acts[1:]) if abs(b.pos - a.pos) >= STROKE_MIN]
    for a, b in strokes:            # a crawl over several seconds is no stroke either
        if b.at - a.at >= GAP_MS and not _in_cut(a.at, b.at, cuts):
            issues.append(Issue("gap", a.at, b.at, tr("{0:.0f} s nur langsames Gleiten", (b.at - a.at) / 1000)))
    for (a0, b0), (a1, b1) in zip(strokes, strokes[1:]):
        gap = a1.at - b0.at
        if gap >= GAP_MS and not _in_cut(b0.at, a1.at, cuts):
            issues.append(Issue("gap", b0.at, a1.at, tr("{0:.0f} s ohne Hub", gap / 1000)))

    # trembling: many tiny steps in a row
    run: list = []
    for a, b in zip(acts, acts[1:]):
        if abs(b.pos - a.pos) < JITTER_POS and b.at - a.at < 600:
            run.append((a, b))
            continue
        if len(run) >= JITTER_COUNT and not _in_cut(run[0][0].at, run[-1][1].at, cuts):
            issues.append(Issue("jitter", run[0][0].at, run[-1][1].at, tr("{0} winzige Bewegungen", len(run))))
        run = []
    if len(run) >= JITTER_COUNT and not _in_cut(run[0][0].at, run[-1][1].at, cuts):
        issues.append(Issue("jitter", run[0][0].at, run[-1][1].at, tr("{0} winzige Bewegungen", len(run))))

    # compared with the strokes around: suddenly small, or one much slower
    if len(strokes) >= 2 * AROUND + 1:
        heights = np.array([abs(b.pos - a.pos) for a, b in strokes], np.float64)
        lengths = np.array([b.at - a.at for a, b in strokes], np.float64)
        for k, (a, b) in enumerate(strokes):
            if k == 0 or k == len(strokes) - 1:          # the first and last move are often half strokes
                continue
            lo, hi = max(0, k - AROUND), min(len(strokes), k + AROUND + 1)
            others = np.r_[np.arange(lo, k), np.arange(k + 1, hi)]
            if _in_cut(a.at, b.at, cuts):
                continue
            h = float(np.median(heights[others]))
            if heights[k] < SMALL_FRAC * h:
                issues.append(Issue("small", a.at, b.at,
                                    tr("Hub {0:.0f} statt etwa {1:.0f} – Kästchen verrutscht?", heights[k], h)))
            L = float(np.median(lengths[others]))
            if lengths[k] > BEAT_FACTOR * L and lengths[k] < GAP_MS:
                issues.append(Issue("beat", a.at, b.at,
                                    tr("ein Hub {0:.1f} s statt etwa {1:.1f} s", lengths[k] / 1000, L / 1000)))

    for a, b in lost:
        if b - a >= LOST_MIN_MS and not _in_cut(a, b, cuts):
            issues.append(Issue("lost", int(a), int(b), tr("Tracking hat hier einen Bereich verloren")))
    return _merge(issues)


def summary(issues: list[Issue]) -> str:
    """'3 Stellen: 2× zu schnell, 1× Lücke' (or that nothing was found)."""
    if not issues:
        return tr("Prüfung: nichts aufgefallen")
    counts: dict[str, int] = {}
    for it in issues:
        counts[it.label] = counts.get(it.label, 0) + 1
    parts = ", ".join(f"{n}× {k}" for k, n in counts.items())
    return plural(len(issues), "{0} Stelle prüfen: {1}", "{0} Stellen prüfen: {1}", parts)


def describe(it: Issue) -> str:
    return f"{_fmt(it.start_ms)}  {it.label}: {it.text}"
