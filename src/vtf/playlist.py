"""Playlist for the player mode: order, repeat, shuffle, skipping videos
without a script, and M3U files. No Qt, so it can be tested on its own."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from .funscript import funscript_path_for


#: how long before the script starts the player jumps in / counts down
LEAD_MS = 10_000


def script_start_ms(actions, min_change: int = 10) -> int | None:
    """When the script really starts moving: the action a first stroke of at least
    `min_change` positions starts from. None if it never moves."""
    for a, b in zip(actions, actions[1:]):
        if abs(b.pos - a.pos) >= min_change:
            return int(a.at)
    return None


def intro_skip_ms(actions, lead_ms: int = LEAD_MS) -> int:
    """Where to start playing to skip a quiet intro: `lead_ms` before the script
    starts. 0 when the script starts early anyway (less than lead + 5 s in)."""
    start = script_start_ms(actions)
    if start is None or start < lead_ms + 5000:
        return 0
    return start - lead_ms


#: the player skips stretches without strokes at least this long ("Pausen überspringen")
QUIET_MIN_MS = 15_000
#: ... and comes back in this long before the next stroke
SKIP_LEAD_MS = 2_000


def quiet_stretches(actions, duration_ms: float = 0, min_ms: int = QUIET_MIN_MS,
                    min_change: int = 10) -> list[tuple[int, int, int]]:
    """Stretches after the script started where it does not move: (from, to, jump to).
    Between two strokes the player jumps to SKIP_LEAD_MS before the next one; after the
    last stroke (with `duration_ms`) to the end of the video. The quiet intro is
    intro_skip_ms()'s business."""
    moves = [(int(a.at), int(b.at)) for a, b in zip(actions, actions[1:]) if abs(b.pos - a.pos) >= min_change]
    out = []
    for (_s0, e0), (s1, _e1) in zip(moves, moves[1:]):
        if s1 - e0 >= min_ms:
            out.append((e0, s1, s1 - SKIP_LEAD_MS))
    if moves and duration_ms and duration_ms - moves[-1][1] >= min_ms:
        out.append((moves[-1][1], int(duration_ms), int(duration_ms)))
    return out


def skip_target(ms: float, cuts=(), quiet=()) -> int | None:
    """Where the player jumps to from ms: the end of a cut it is in, or the jump target
    of a quiet stretch (a moment after it began, so the last stroke ends). None: play on."""
    for a, b in cuts:
        if a <= ms < b:
            return int(b)
    for a, b, to in quiet:
        if a + SKIP_LEAD_MS <= ms < to:
            return int(to)
    return None


def has_script(path: str | Path) -> bool:
    return funscript_path_for(path).is_file()


@dataclass
class Playlist:
    items: list[str] = field(default_factory=list)
    current: int = -1
    repeat: bool = False
    shuffle: bool = False
    skip_without_script: bool = True
    _order: list[int] = field(default_factory=list, repr=False)
    _rng: random.Random = field(default_factory=random.Random, repr=False)

    # ------------------------------------------------------------ editing
    def add(self, paths) -> int:
        """Append videos (duplicates are ignored); returns how many were added."""
        n = 0
        for p in paths:
            p = str(Path(p).resolve())
            if p not in self.items:
                self.items.append(p)
                n += 1
        if n:
            self._order = []
        return n

    def remove(self, index: int) -> None:
        if not 0 <= index < len(self.items):
            return
        del self.items[index]
        if index < self.current:
            self.current -= 1
        elif index == self.current:
            self.current = min(self.current, len(self.items) - 1) if self.items else -1
        self._order = []

    def move(self, src: int, dst: int) -> None:
        if not (0 <= src < len(self.items) and 0 <= dst < len(self.items)) or src == dst:
            return
        cur = self.items[self.current] if 0 <= self.current < len(self.items) else None
        self.items.insert(dst, self.items.pop(src))
        if cur is not None:
            self.current = self.items.index(cur)
        self._order = []

    def set_items(self, items: list[str]) -> None:
        """Replace the order (e.g. after drag & drop), keeping the current video."""
        cur = self.items[self.current] if 0 <= self.current < len(self.items) else None
        self.items = list(items)
        self.current = self.items.index(cur) if cur in self.items else -1
        self._order = []

    def clear(self) -> None:
        self.items.clear()
        self.current = -1
        self._order = []

    # ------------------------------------------------------------ navigation
    def playable(self, index: int) -> bool:
        if not 0 <= index < len(self.items):
            return False
        return not self.skip_without_script or has_script(self.items[index])

    def _sequence(self) -> list[int]:
        """Play order: list order, or a shuffled order that stays fixed until the list changes."""
        if not self.shuffle:
            return list(range(len(self.items)))
        if sorted(self._order) != list(range(len(self.items))):
            self._order = list(range(len(self.items)))
            self._rng.shuffle(self._order)
        return self._order

    def next_index(self) -> int | None:
        return self._step(+1)

    def prev_index(self) -> int | None:
        return self._step(-1)

    def _step(self, direction: int) -> int | None:
        seq = self._sequence()
        if not seq:
            return None
        pos = seq.index(self.current) if self.current in seq else (-1 if direction > 0 else len(seq))
        for k in range(1, len(seq) + 1):
            p = pos + direction * k
            if not 0 <= p < len(seq):
                if not self.repeat:
                    return None
                if self.shuffle and direction > 0 and p >= len(seq):
                    self._reshuffle(avoid=self.current)
                    seq = self._order
                p %= len(seq)
            if self.playable(seq[p]):
                return seq[p]
        return None

    def _reshuffle(self, avoid: int) -> None:
        self._rng.shuffle(self._order)
        if len(self._order) > 1 and self._order[0] == avoid:     # no repeat across rounds
            self._order[0], self._order[-1] = self._order[-1], self._order[0]

    def first_playable(self) -> int | None:
        seq = self._sequence()
        return next((i for i in seq if self.playable(i)), None)

    # ------------------------------------------------------------ files
    def save_m3u(self, path: str | Path) -> None:
        lines = ["#EXTM3U"] + [str(p) for p in self.items]
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def load_m3u(self, path: str | Path) -> int:
        base = Path(path).resolve().parent
        paths = []
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("file://"):
                from urllib.parse import unquote, urlparse
                line = unquote(urlparse(line).path)
            p = Path(line)
            paths.append(p if p.is_absolute() else base / p)
        return self.add(paths)
