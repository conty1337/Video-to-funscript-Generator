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
