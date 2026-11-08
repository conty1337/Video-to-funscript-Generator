"""Funscript file format (JSON)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Action:
    at: int   # milliseconds
    pos: int  # 0..100


@dataclass
class Funscript:
    actions: list[Action] = field(default_factory=list)
    inverted: bool = False
    range: int = 100
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        acts = sorted(self.actions, key=lambda a: a.at)
        return {
            "version": "1.0",
            "inverted": self.inverted,
            "range": self.range,
            "actions": [{"at": int(a.at), "pos": int(max(0, min(100, a.pos)))} for a in acts],
            "metadata": self.metadata,
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, separators=(",", ":"))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str | Path) -> "Funscript":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        acts = [Action(int(a["at"]), int(a["pos"])) for a in d.get("actions", [])]
        acts.sort(key=lambda a: a.at)
        return cls(acts, bool(d.get("inverted", False)), int(d.get("range", 100)),
                   d.get("metadata", {}) or {})


def position_at(actions: list[Action], ms: float) -> float | None:
    """Linearly interpolated position at ``ms`` (actions sorted by time)."""
    if not actions:
        return None
    if ms <= actions[0].at:
        return float(actions[0].pos)
    if ms >= actions[-1].at:
        return float(actions[-1].pos)
    lo, hi = 0, len(actions) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if actions[mid].at <= ms:
            lo = mid
        else:
            hi = mid
    a, b = actions[lo], actions[hi]
    return a.pos + (b.pos - a.pos) * (ms - a.at) / max(1, b.at - a.at)


#: Script axes (TCode names) -> (label, file suffix). The multi-axis convention
#: used by MultiFunPlayer, OFS & co.: <video>.funscript is the stroke (L0),
#: the other axes are <video>.<name>.funscript next to it.
AXES: dict[str, tuple[str, str]] = {
    "L0": ("Hub", ""),
    "L1": ("Surge", "surge"),
    "L2": ("Sway", "sway"),
    "R0": ("Twist", "twist"),
    "R1": ("Roll", "roll"),
    "R2": ("Pitch", "pitch"),
}


def funscript_path_for(video: str | Path, axis: str = "L0") -> Path:
    """Players expect ``<video name>.funscript`` (and ``<video name>.roll.funscript`` etc.)."""
    suffix = AXES[axis][1]
    p = Path(video)
    return p.with_suffix(f".{suffix}.funscript" if suffix else ".funscript")


def load_axes(video: str | Path) -> dict[str, list[Action]]:
    """All axis scripts lying next to the video (missing or broken files are left out)."""
    out = {}
    for axis in AXES:
        f = funscript_path_for(video, axis)
        if f.is_file():
            try:
                out[axis] = Funscript.load(f).actions
            except (OSError, ValueError, KeyError, TypeError):
                pass
    return out
