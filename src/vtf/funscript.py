"""Funscript file format (JSON)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import tr


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
        """Raises OSError or ValueError (also for a file of the wrong shape). An inverted script
        is flipped here, so the actions are always meant as written."""
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        try:
            inverted = d.get("inverted") is True
            acts = [Action(int(a["at"]), int(a["pos"])) for a in d.get("actions", [])]
            meta = d.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
        except (AttributeError, KeyError, TypeError) as e:
            raise ValueError(tr("Kein gültiges Funscript: {0}", e)) from e
        if inverted:
            acts = [Action(a.at, 100 - a.pos) for a in acts]
        acts.sort(key=lambda a: a.at)
        return cls(acts, metadata=meta)


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
    "L0": (tr("Hub"), ""),
    "L1": (tr("Surge"), "surge"),
    "L2": (tr("Sway"), "sway"),
    "R0": (tr("Twist"), "twist"),
    "R1": (tr("Roll"), "roll"),
    "R2": (tr("Pitch"), "pitch"),
}


def funscript_path_for(video: str | Path, axis: str = "L0") -> Path:
    """Players expect ``<video name>.funscript`` (and ``<video name>.roll.funscript`` etc.)."""
    suffix = AXES[axis][1]
    p = Path(video)
    return p.with_suffix(f".{suffix}.funscript" if suffix else ".funscript")


def load_axes(video: str | Path) -> dict[str, list[Action]]:
    """All axis scripts of the video, next to it or in the script folder (missing or broken
    files are left out)."""
    # imported here: scriptdir builds on this module
    from .scriptdir import find_axes

    out = {}
    for axis, f in find_axes(video).items():
        try:
            out[axis] = Funscript.load(f).actions
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return out
