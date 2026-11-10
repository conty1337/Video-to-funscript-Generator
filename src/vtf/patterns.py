"""Stroke patterns built from blocks (Baukasten): library, rendering, playback.

A pattern is a list of blocks. Each block is one of:

- ``stroke`` (Hub): ``repeat`` times down to ``lo`` and up to ``hi``. Tempo and depth
  can change over the repeats (``speed_to``, ``lo_to``, ``hi_to``).
- ``move`` (Fahren): one movement to ``hi``.
- ``hold`` (Halten): go to ``hi`` if not there yet, then stay for ``length`` strokes.

Depths are percent of the stroke range the user sets (0 = bottom, 100 = top), tempo is
relative to the strokes per minute the user sets. One movement (down or up) at tempo
1 takes half a stroke. The pattern repeats endlessly.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .funscript import Action, position_at

KINDS = (("stroke", "Hub"), ("move", "Fahren"), ("hold", "Halten"))
MIN_SPEED, MAX_SPEED = 0.1, 6.0


@dataclass
class Block:
    kind: str = "stroke"
    repeat: int = 1
    speed: float = 1.0
    lo: int = 0
    hi: int = 100
    speed_to: float | None = None      # ramp over the repeats (stroke only)
    lo_to: int | None = None
    hi_to: int | None = None
    length: float = 1.0                # hold: in strokes

    @property
    def ramp(self) -> bool:
        return self.kind == "stroke" and any(v is not None for v in (self.speed_to, self.lo_to, self.hi_to))

    def describe(self) -> str:
        if self.kind == "move":
            return f"Fahren auf {self.hi} %  ·  Tempo {_num(self.speed)}×"
        if self.kind == "hold":
            return f"Halten bei {self.hi} %  ·  {_num(self.length)} Hub lang"
        depth = f"{self.lo}–{self.hi} %"
        if self.lo_to is not None or self.hi_to is not None:
            depth += f" → {_v(self.lo_to, self.lo)}–{_v(self.hi_to, self.hi)} %"
        speed = f"{_num(self.speed)}×"
        if self.speed_to is not None:
            speed += f" → {_num(self.speed_to)}×"
        return f"{self.repeat}× Hub {depth}  ·  Tempo {speed}"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> Block:
        b = cls()
        for k in ("kind", "repeat", "speed", "lo", "hi", "speed_to", "lo_to", "hi_to", "length"):
            if k in d and d[k] is not None:
                setattr(b, k, d[k])
        b.kind = b.kind if b.kind in dict(KINDS) else "stroke"
        b.repeat = max(1, min(64, int(b.repeat)))
        b.speed = _speed(b.speed)
        b.speed_to = None if b.speed_to is None else _speed(b.speed_to)
        b.lo, b.hi = _pct(b.lo), _pct(b.hi)
        b.lo_to = None if b.lo_to is None else _pct(b.lo_to)
        b.hi_to = None if b.hi_to is None else _pct(b.hi_to)
        b.length = max(0.25, min(32.0, float(b.length)))
        return b


@dataclass
class Pattern:
    name: str
    blocks: list[Block] = field(default_factory=list)
    category: str = "Eigene"
    builtin: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "blocks": [b.to_dict() for b in self.blocks]}

    @classmethod
    def from_dict(cls, d: dict) -> Pattern:
        return cls(str(d.get("name") or "Muster"), [Block.from_dict(b) for b in d.get("blocks", [])
                                                     if isinstance(b, dict)])

    def copy(self, name: str | None = None) -> Pattern:
        return Pattern(name or self.name, [Block.from_dict(b.to_dict()) for b in self.blocks])


def _num(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def _v(v, default):
    return default if v is None else v


def _speed(v) -> float:
    return round(max(MIN_SPEED, min(MAX_SPEED, float(v))), 2)


def _pct(v) -> int:
    return int(max(0, min(100, round(float(v)))))


# ------------------------------------------------------------ rendering
def _segments(p: Pattern, bpm: float, start: float) -> list[tuple[float, float]]:
    half = 30_000.0 / max(1.0, bpm)
    out: list[tuple[float, float]] = []
    pos = start
    for b in p.blocks:
        if b.kind == "stroke":
            n = max(1, b.repeat)
            for i in range(n):
                f = i / (n - 1) if n > 1 else 0.0
                sp = b.speed + (_v(b.speed_to, b.speed) - b.speed) * f
                lo = b.lo + (_v(b.lo_to, b.lo) - b.lo) * f
                hi = b.hi + (_v(b.hi_to, b.hi) - b.hi) * f
                d = half / max(MIN_SPEED, sp)
                out += [(d, lo / 100), (d, hi / 100)]
        elif b.kind == "move":
            out.append((half / max(MIN_SPEED, b.speed), b.hi / 100))
        else:
            target = b.hi / 100
            if abs(pos - target) > 0.005:
                out.append((half / max(MIN_SPEED, b.speed), target))
            out.append((2 * half * max(0.25, b.length), target))
        if out:
            pos = out[-1][1]
    return out


def segments(p: Pattern, bpm: float) -> list[tuple[float, float]]:
    """One run of the pattern: (duration ms, position 0..1) per movement, starting
    where the previous run ended (the pattern loops)."""
    first = _segments(p, bpm, 1.0)
    return _segments(p, bpm, first[-1][1]) if first else []


def cycle_ms(p: Pattern, bpm: float) -> float:
    return sum(d for d, _ in segments(p, bpm))


def preview(p: Pattern, bpm: float = 60, cycles: int = 1) -> list[tuple[float, float]]:
    """Points (ms, position 0..1) of `cycles` runs, for pictures."""
    segs = segments(p, bpm)
    if not segs:
        return []
    t, pts = 0.0, [(0.0, segs[-1][1])]
    for _ in range(max(1, cycles)):
        for d, f in segs:
            t += d
            pts.append((t, f))
    return pts


def block_ranges(p: Pattern, bpm: float = 60) -> list[tuple[float, float]]:
    """Time span (ms) of each block within one run."""
    end_pos = (segments(p, bpm) or [(0, 1.0)])[-1][1]
    out, t = [], 0.0
    pos = end_pos
    for b in p.blocks:
        segs = _segments(Pattern("", [b]), bpm, pos)
        d = sum(s[0] for s in segs)
        out.append((t, t + d))
        t += d
        if segs:
            pos = segs[-1][1]
    return out


def actions(p: Pattern, bpm: float, lo: float, hi: float, start_ms: float, end_ms: float,
            start_pos: float | None = None) -> list[Action]:
    """The pattern repeated from start_ms to end_ms, positions mapped to lo..hi.
    The first point is at start_ms (at start_pos if given)."""
    segs = segments(p, bpm)
    if not segs or sum(d for d, _ in segs) < 1:
        return []
    lo, hi = sorted((float(lo), float(hi)))

    def to_pos(f: float) -> int:
        return int(round(max(0.0, min(100.0, lo + f * (hi - lo)))))

    t = float(start_ms)
    first = to_pos(segs[-1][1]) if start_pos is None else int(round(max(0, min(100, start_pos))))
    out = [Action(int(round(t)), first)]
    while True:
        for d, f in segs:
            t += d
            if t > end_ms + 0.5:
                return out
            at = int(round(t))
            if at > out[-1].at:
                out.append(Action(at, to_pos(f)))


# ------------------------------------------------------------ playback
class PatternRunner:
    """Plays a pattern on a device through a ScriptSync with a free-running clock.
    Tempo, stroke range and pattern can change while it runs, without a jump."""

    AHEAD_MS = 30_000

    def __init__(self, sync):
        self.sync = sync
        self.pattern: Pattern | None = None
        self.bpm, self.lo, self.hi = 60.0, 0, 100
        self.running = False
        self.acts: list[Action] = []
        self.anchor = 0.0                  # clock time a run started

    def configure(self, now: float, pattern: Pattern | None = None, bpm: float | None = None,
                  lo: float | None = None, hi: float | None = None) -> None:
        if pattern is not None:
            self.pattern = pattern
        if bpm is not None:
            self.bpm = float(bpm)
        if lo is not None:
            self.lo = lo
        if hi is not None:
            self.hi = hi
        if self.running:
            self._build(now, self.position(now))

    def start(self, now: float) -> None:
        if self.pattern is None:
            return
        self.running = True
        self.sync.stop()
        self._build(now, None)

    def stop(self) -> None:
        if self.running:
            self.running = False
            self.sync.stop()
        self.acts = []

    def _build(self, now: float, start_pos: float | None) -> None:
        self.anchor = now
        self.acts = actions(self.pattern, self.bpm, self.lo, self.hi, now, now + self.AHEAD_MS, start_pos)
        self.sync.set_actions(self.acts)

    def position(self, now: float) -> float | None:
        return position_at(self.acts, now) if self.acts else None

    def phase(self, now: float) -> float | None:
        """Where in the run (0..1) the pattern is."""
        if not self.running or self.pattern is None:
            return None
        c = cycle_ms(self.pattern, self.bpm)
        return ((now - self.anchor) % c) / c if c > 0 else None

    def tick(self, now: float) -> None:
        if not self.running or not self.acts:
            return
        last = self.acts[-1]
        if now > last.at - self.AHEAD_MS / 3:
            more = actions(self.pattern, self.bpm, self.lo, self.hi, last.at, last.at + self.AHEAD_MS, last.pos)
            self.acts = [a for a in self.acts if a.at >= now - 2000] + more[1:]
            self.sync.set_actions(self.acts)
        self.sync.tick(now, True)


# ------------------------------------------------------------ library
def _s(repeat=1, speed=1.0, lo=0, hi=100, **kw) -> Block:
    return Block("stroke", repeat, speed, lo, hi, **kw)


def _m(pos, speed=1.0) -> Block:
    return Block("move", 1, speed, 0, pos)


def _h(pos, length=1.0, speed=1.0) -> Block:
    return Block("hold", 1, speed, 0, pos, length=length)


_LIB = {
    "Grundlagen": {
        "Gleichmäßig": [_s()],
        "Langsam und tief": [_s(speed=0.5)],
        "Schnell": [_s(speed=2)],
        "Kurz oben": [_s(4, 1.5, 60, 100)],
        "Kurz unten": [_s(4, 1.5, 0, 40)],
        "Mitte": [_s(4, 1.3, 30, 70)],
        "Nur die Spitze": [_s(6, 2.2, 80, 100)],
        "Lang und kurz": [_s(1, 0.8), _s(3, 1.8, 55, 100)],
    },
    "Rhythmus": {
        "Doppelschlag": [_s(2, 2), _h(100, 0.5)],
        "Dreifach": [_s(3, 2.2), _h(100, 0.75)],
        "Herzschlag": [_s(1, 2.5, 45, 100), _s(1, 2), _h(100, 1)],
        "Galopp": [_s(1, 1.2), _s(2, 2.6, 30, 100)],
        "Walzer": [_s(1, 1), _s(2, 1.8, 50, 100)],
        "Stakkato": [_s(1, 3), _h(100, 0.25), _s(1, 3), _h(100, 0.25), _s(1, 3), _h(100, 0.5)],
        "Klopfen": [_m(0, 1), _s(6, 2.5, 0, 25), _m(100, 1)],
        "Pulsieren": [_s(6, 3, 40, 60), _s(1, 1)],
    },
    "Steigerung": {
        "Schneller werdend": [_s(10, 0.6, speed_to=2.2)],
        "Langsamer werdend": [_s(10, 2.2, speed_to=0.6)],
        "Tiefer werdend": [_s(8, 1.2, 75, 100, lo_to=0)],
        "Flacher werdend": [_s(8, 1.2, 0, 100, lo_to=75)],
        "Treppe": [_s(2, 1.4, 80, 100), _s(2, 1.3, 60, 100), _s(2, 1.2, 40, 100), _s(2, 1.1, 20, 100),
                   _s(2, 1, 0, 100)],
        "Welle": [_s(6, 1.3, 65, 100, lo_to=0), _s(6, 1.3, 0, 100, lo_to=65)],
        "Anschwellen": [_s(12, 0.5, 60, 100, speed_to=2.5, lo_to=0), _h(100, 1.5)],
        "Achterbahn": [_s(5, 0.5, 50, 100, speed_to=2.2, lo_to=0), _s(5, 2.2, 0, 100, speed_to=0.5, lo_to=50)],
    },
    "Necken": {
        "Necken": [_s(6, 1.5, 70, 100), _m(0, 0.4), _m(100, 0.4)],
        "Langsam rein, schnell raus": [_m(0, 0.5), _m(100, 2)],
        "Schnell rein, langsam raus": [_m(0, 2), _m(100, 0.5)],
        "Kurz vor Schluss": [_s(8, 2), _h(100, 2), _s(4, 0.7, 80, 100)],
        "Melken": [_s(1, 0.7), _s(3, 2, 0, 30), _m(100, 1)],
        "Zittern unten": [_m(0, 1), _s(8, 4, 0, 12), _m(100, 1)],
        "Zittern oben": [_s(8, 4, 88, 100), _s(1, 1)],
        "Tief halten": [_m(0, 1), _h(0, 1), _m(100, 2)],
        "Ruhig": [_s(1, 0.35), _h(100, 1)],
        "Abwechslung": [_s(3, 1), _s(4, 2, 50, 100), _m(0, 0.6), _h(0, 0.5), _m(100, 1.5), _s(4, 3, 85, 100)],
        "Fast drin": [_s(6, 1.5, 10, 40), _m(100, 0.6)],
        "Rein und halten": [_m(0, 0.8), _h(0, 2), _m(100, 0.8), _h(100, 1)],
        "Nur einmal tief": [_s(5, 1.6, 55, 100), _s(1, 0.5)],
        "Ebbe und Flut": [_s(4, 0.7, 50, 100, lo_to=0), _h(100, 1), _s(4, 0.7, 0, 100, lo_to=50), _h(100, 1)],
        "Schmetterling": [_s(3, 2.5, 80, 100), _s(1, 0.8), _s(3, 2.5, 0, 20), _m(100, 0.8)],
    },
    "Mehr Rhythmus": {
        "Vierer": [_s(4, 2), _h(100, 1)],
        "Synkope": [_s(1, 1), _s(1, 2.5), _h(100, 0.5), _s(1, 2.5)],
        "Morse": [_s(3, 3, 60, 100), _h(100, 0.5), _s(3, 1.2), _h(100, 0.5)],
        "Echo": [_s(1, 1.2), _s(1, 2, 40, 100), _s(1, 3, 75, 100), _h(100, 0.75)],
        "Pendel": [_m(0, 1.5), _h(0, 0.25), _m(100, 1.5), _h(100, 0.25)],
        "Tango": [_s(1, 0.8), _s(1, 0.8), _s(3, 2.5, 30, 100)],
    },
    "Mehr Steigerung": {
        "Sprint": [_s(6, 1), _s(10, 2.5)],
        "Pyramide": [_s(2, 1, 75, 100), _s(2, 1.2, 50, 100), _s(2, 1.4, 25, 100), _s(2, 1.6, 0, 100),
                     _s(2, 1.4, 25, 100), _s(2, 1.2, 50, 100)],
        "Brandung": [_s(5, 0.8, 70, 100, speed_to=2, lo_to=0), _h(100, 0.5), _s(2, 0.6)],
        "Treppe hoch und runter": [_s(6, 0.8, speed_to=2.4), _s(6, 2.4, speed_to=0.8)],
        "Langer Anlauf": [_s(16, 0.4, 70, 100, speed_to=2.4, lo_to=0)],
    },
    "Intensiv": {
        "Dauerfeuer": [_s(1, 3)],
        "Vollgas tief": [_s(1, 2.5, 0, 100)],
        "Hammer": [_m(0, 3), _h(0, 0.25), _m(100, 1)],
        "Turbo-Zittern": [_s(12, 5, 0, 20), _s(12, 5, 80, 100)],
        "Wechselbad": [_s(4, 3), _s(2, 0.5)],
        "Gewitter": [_s(6, 3.5, 40, 100), _h(100, 0.5), _s(2, 3), _s(4, 4, 0, 30), _m(100, 2)],
    },
}

BUILTIN: list[Pattern] = [Pattern(name, blocks, cat, True)
                          for cat, items in _LIB.items() for name, blocks in items.items()]

#: building blocks offered in the editor: (label, block)
PALETTE = (
    ("Voller Hub", _s()),
    ("Kurz oben", _s(4, 1.5, 60, 100)),
    ("Kurz unten", _s(4, 1.5, 0, 40)),
    ("Mitte", _s(4, 1.3, 30, 70)),
    ("Zittern", _s(8, 4, 0, 12)),
    ("Langsam", _s(1, 0.5)),
    ("Schnell", _s(4, 2)),
    ("Schneller werdend", _s(8, 0.6, speed_to=2.2)),
    ("Tiefer werdend", _s(8, 1.2, 75, 100, lo_to=0)),
    ("Nach unten", _m(0)),
    ("Nach oben", _m(100)),
    ("Pause oben", _h(100)),
    ("Pause unten", _h(0)),
)


# ------------------------------------------------------------ programs (Abläufe)
@dataclass
class Step:
    """One part of a program: a pattern (or a part of it) for some minutes with its
    own tempo and depth."""

    pattern: str = "Gleichmäßig"
    minutes: float = 5.0
    bpm: int = 60
    depth: int = 100                  # 10..100 % from the top (out)
    first: int = 1                    # blocks used: first..last (1-based), last 0 = to the end
    last: int = 0

    def part(self, pattern: Pattern) -> Pattern:
        n = len(pattern.blocks)
        a = max(1, min(n, self.first))
        b = n if self.last <= 0 else max(a, min(n, self.last))
        if a == 1 and b == n:
            return pattern
        return Pattern(f"{pattern.name} ({a}–{b})", pattern.blocks[a - 1:b], pattern.category)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Step:
        st = cls()
        for k in ("pattern", "minutes", "bpm", "depth", "first", "last"):
            if k in d and d[k] is not None:
                setattr(st, k, d[k])
        st.pattern = str(st.pattern)
        st.minutes = max(0.25, min(600.0, float(st.minutes)))
        st.bpm = int(max(10, min(240, round(float(st.bpm)))))
        st.depth = int(max(10, min(100, round(float(st.depth)))))
        st.first = max(1, int(st.first))
        st.last = max(0, int(st.last))
        return st


@dataclass
class Program:
    name: str
    steps: list[Step] = field(default_factory=list)
    loop: bool = False                # at the end: start again (else stop)
    smooth: bool = True               # glide tempo and depth into the next step
    builtin: bool = False

    @property
    def total_ms(self) -> float:
        return sum(st.minutes for st in self.steps) * 60_000

    def starts(self) -> list[float]:
        out, t = [], 0.0
        for st in self.steps:
            out.append(t)
            t += st.minutes * 60_000
        return out

    def to_dict(self) -> dict:
        return {"name": self.name, "loop": self.loop, "smooth": self.smooth,
                "steps": [st.to_dict() for st in self.steps]}

    @classmethod
    def from_dict(cls, d: dict) -> Program:
        return cls(str(d.get("name") or "Ablauf"),
                   [Step.from_dict(x) for x in d.get("steps", []) if isinstance(x, dict)],
                   bool(d.get("loop", False)), bool(d.get("smooth", True)))

    def copy(self, name: str | None = None) -> Program:
        p = Program.from_dict(self.to_dict())
        p.name = name or self.name
        return p


def _st(pattern, minutes, bpm, depth, first=1, last=0) -> Step:
    return Step(pattern, minutes, bpm, depth, first, last)


BUILTIN_PROGRAMS: list[Program] = [
    Program("Eine Stunde", [_st("Ruhig", 8, 40, 50), _st("Gleichmäßig", 7, 50, 70), _st("Welle", 10, 60, 85),
                            _st("Necken", 8, 70, 100), _st("Treppe", 7, 70, 100), _st("Anschwellen", 12, 85, 100),
                            _st("Ruhig", 8, 40, 60)], builtin=True),
    Program("Kurz und knackig (15 min)", [_st("Gleichmäßig", 3, 50, 70), _st("Galopp", 4, 70, 90),
                                          _st("Sprint", 5, 90, 100), _st("Ruhig", 3, 40, 60)], builtin=True),
    Program("Langsam steigern (30 min)", [_st("Langsam und tief", 6, 30, 100), _st("Gleichmäßig", 6, 45, 90),
                                          _st("Gleichmäßig", 6, 60, 90), _st("Schnell", 6, 50, 100),
                                          _st("Dauerfeuer", 6, 40, 100)], builtin=True),
    Program("Necken ohne Ende", [_st("Necken", 5, 60, 100), _st("Kurz oben", 5, 70, 60),
                                 _st("Kurz vor Schluss", 5, 70, 100), _st("Fast drin", 5, 60, 100)],
            loop=True, builtin=True),
]


#: moods for the random generator: label, pattern groups (None = all but "Intensiv"),
#: tempo range, depth range
MOODS = {
    "ruhig": ("Ruhig", ("Grundlagen", "Necken", "Steigerung"), (30, 60), (40, 80)),
    "gemischt": ("Gemischt", None, (40, 100), (50, 100)),
    "intensiv": ("Intensiv", ("Intensiv", "Rhythmus", "Mehr Rhythmus", "Steigerung", "Mehr Steigerung"),
                 (70, 150), (80, 100)),
}


def random_program(mood: str = "gemischt", bpm: tuple[int, int] | None = None,
                   depth: tuple[int, int] | None = None, minutes: tuple[float, float] = (1.0, 3.0),
                   total_minutes: float = 240, seed: int | None = None,
                   extra: list[Pattern] | None = None) -> Program:
    """An endless-feeling program: random patterns (sometimes only a part of one),
    tempo and depth within the ranges, each for a random time. Loops at the end."""
    import random

    rng = random.Random(seed)
    _label, cats, mood_bpm, mood_depth = MOODS.get(mood, MOODS["gemischt"])
    b0, b1 = sorted(bpm or mood_bpm)
    d0, d1 = sorted(depth or mood_depth)
    m0, m1 = sorted(minutes)
    pool = [p for p in BUILTIN if (p.category in cats if cats else p.category != "Intensiv")]
    pool += list(extra or [])
    steps: list[Step] = []
    t, last = 0.0, None
    while t < total_minutes and pool:
        p = rng.choice([x for x in pool if x.name != last] or pool)
        last = p.name
        mins = round(rng.uniform(max(0.25, m0), max(0.25, m1)) * 4) / 4
        first, end = 1, 0
        n = len(p.blocks)
        if n > 2 and rng.random() < 0.3:          # now and then only a part of the pattern
            first = rng.randint(1, n - 1)
            end = rng.randint(first, n)
        steps.append(Step(p.name, mins, rng.randint(b0, b1), rng.randint(d0, max(d0, d1)), first, end))
        t += mins
    return Program("Zufall", steps, loop=True, smooth=True)


class ProgramRunner:
    """Plays a program: switches pattern, tempo and depth by the clock, optionally
    gliding into each step. Offsets from keys or controller apply on top."""

    SMOOTH_MS = 8000
    APPLY_EVERY_MS = 250

    def __init__(self, sync, lookup):
        self.runner = PatternRunner(sync)
        self.lookup = lookup              # name -> Pattern | None
        self.program: Program | None = None
        self.start_ms = 0.0
        self.index = -1
        self.offset_bpm = 0.0
        self.offset_depth = 0.0
        self.finished = False
        self.loops = 0                    # how often the program started again
        self._applied: tuple | None = None
        self._applied_at = -1e9

    @property
    def running(self) -> bool:
        return self.runner.running

    def _pattern(self, st: Step) -> Pattern:
        p = self.lookup(st.pattern) or BUILTIN[0]
        return st.part(p) if p.blocks else BUILTIN[0]

    def elapsed(self, now: float) -> float:
        return max(0.0, now - self.start_ms) if self.running else 0.0

    def step_at(self, elapsed: float) -> int:
        starts = self.program.starts()
        i = 0
        for k, t in enumerate(starts):
            if elapsed >= t:
                i = k
        return i

    def targets(self, elapsed: float) -> tuple[int, float, float]:
        """(step, tempo, depth) at a time in the program, before the offsets."""
        steps = self.program.steps
        i = self.step_at(elapsed)
        st = steps[i]
        bpm, depth = float(st.bpm), float(st.depth)
        into = elapsed - self.program.starts()[i]
        prev = steps[i - 1] if i > 0 else (steps[-1] if self.loops > 0 else None)
        if self.program.smooth and prev is not None and into < self.SMOOTH_MS:
            f = into / self.SMOOTH_MS
            bpm = prev.bpm + (bpm - prev.bpm) * f
            depth = prev.depth + (depth - prev.depth) * f
        return i, bpm, depth

    def effective(self, bpm: float, depth: float) -> tuple[float, float]:
        return (max(10.0, min(240.0, bpm + self.offset_bpm)), max(10.0, min(100.0, depth + self.offset_depth)))

    def start(self, now: float, program: Program, index: int = 0) -> None:
        if not program.steps:
            return
        self.program = program
        self.finished = False
        self.loops = 0
        self.start_ms = now - program.starts()[max(0, min(len(program.steps) - 1, index))]
        self.index = -1
        self._apply(now, force=True)
        self.runner.start(now)

    def stop(self) -> None:
        self.runner.stop()
        self.index = -1

    def skip(self, now: float, d: int) -> None:
        if not self.running or not self.program:
            return
        n = len(self.program.steps)
        i = (self.index + d) % n
        self.start_ms = now - self.program.starts()[i]
        self._apply(now, force=True)

    def _apply(self, now: float, force: bool = False) -> None:
        i, bpm, depth = self.targets(self.elapsed(now) if self.running else now - self.start_ms)
        bpm, depth = self.effective(bpm, depth)
        key = (i, round(bpm), round(depth))
        if not force and key == self._applied:
            return
        if not force and i == self.index and now - self._applied_at < self.APPLY_EVERY_MS:
            return
        pattern = self._pattern(self.program.steps[i]) if (force or i != self.index) else None
        self.index = i
        self._applied, self._applied_at = key, now
        self.runner.configure(now, pattern=pattern, bpm=round(bpm), lo=100 - round(depth), hi=100)

    def tick(self, now: float) -> None:
        if not self.running or not self.program:
            return
        total = self.program.total_ms
        if now - self.start_ms >= total:
            if not self.program.loop:
                self.stop()
                self.finished = True
                return
            self.loops += int((now - self.start_ms) // total)
            self.start_ms += total * ((now - self.start_ms) // total)
        self._apply(now)
        self.runner.tick(now)

    def current(self, now: float) -> tuple[float, float] | None:
        """Tempo and depth playing now (with offsets)."""
        if not self.running:
            return None
        _i, bpm, depth = self.targets(self.elapsed(now))
        return self.effective(bpm, depth)


def programs_path() -> Path:
    return user_path().with_name("programs.json")


def load_programs(path: Path | None = None) -> list[Program]:
    path = path or programs_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("programs", []) if isinstance(data, dict) else []
    return [p for p in (Program.from_dict(d) for d in items if isinstance(d, dict)) if p.steps]


def save_programs(programs: list[Program], path: Path | None = None) -> None:
    path = path or programs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "programs": [p.to_dict() for p in programs]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def user_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "video-to-funscript" / "patterns.json"


def load_user(path: Path | None = None) -> list[Pattern]:
    path = path or user_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("patterns", []) if isinstance(data, dict) else []
    out = [Pattern.from_dict(d) for d in items if isinstance(d, dict)]
    return [p for p in out if p.blocks]


def save_user(patterns: list[Pattern], path: Path | None = None) -> None:
    path = path or user_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "patterns": [p.to_dict() for p in patterns]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def unique_name(name: str, taken) -> str:
    taken = {n.casefold() for n in taken}
    name = name.strip() or "Mein Muster"
    if name.casefold() not in taken:
        return name
    i = 2
    while f"{name} {i}".casefold() in taken:
        i += 1
    return f"{name} {i}"
