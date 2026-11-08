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


def funscript_path_for(video: str | Path) -> Path:
    """Players expect ``<video name>.funscript`` next to the video."""
    return Path(video).with_suffix(".funscript")
