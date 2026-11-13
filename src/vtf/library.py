"""Funscript library: every video with a script in a folder, with its numbers for
sorting and searching, a heatmap preview and a rating (kept locally). No Qt.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .batch import VIDEO_EXTS
from .funscript import Funscript
from .i18n import tr
from .scriptdir import files_in, find_script, find_video, video_stem
from .scriptdir import settings as script_settings

STROKE_MIN = 10          # a step of at least this many positions is a stroke
HEAT_BUCKETS = 120       # slices of the heatmap preview


@dataclass
class Entry:
    video: Path
    script: Path
    duration_ms: int = 0
    strokes: int = 0
    per_min: float = 0.0          # strokes per minute while the script moves
    avg_speed: float = 0.0        # positions per second, average over the strokes
    max_speed: float = 0.0
    mtime: float = 0.0
    heat: list = field(default_factory=list)   # fastest speed per slice, HEAT_BUCKETS of them
    rating: int = 0               # 0..5
    missing: bool = False         # a script in the script folder whose video was not found

    @property
    def name(self) -> str:
        return self.video.stem


def _numbers(e: Entry) -> None:
    try:
        acts = Funscript.load(e.script).actions
    except (OSError, ValueError, KeyError, TypeError):
        return
    acts = sorted(acts, key=lambda a: a.at)
    if len(acts) < 2:
        return
    e.duration_ms = int(acts[-1].at)
    steps = [(a, b) for a, b in zip(acts, acts[1:]) if abs(b.pos - a.pos) >= STROKE_MIN]
    e.strokes = len(steps) // 2
    if steps:
        moving = sum(b.at - a.at for a, b in steps) / 1000.0
        speeds = [abs(b.pos - a.pos) * 1000.0 / max(1, b.at - a.at) for a, b in steps]
        e.per_min = (len(steps) / 2) / max(moving, 1e-6) * 60
        e.avg_speed = sum(speeds) / len(speeds)
        e.max_speed = max(speeds)
    n = HEAT_BUCKETS
    heat = [0.0] * n
    dur = max(1, e.duration_ms)
    for a, b in zip(acts, acts[1:]):
        v = abs(b.pos - a.pos) * 1000.0 / max(1, b.at - a.at)
        for i in range(min(n - 1, int(a.at / dur * n)), min(n - 1, int(b.at / dur * n)) + 1):
            heat[i] = max(heat[i], v)
    e.heat = heat


def scan(folder: str | Path, recursive: bool = True) -> list[Entry]:
    """Every video in the folder (and below) that has a script (next to it or in the script
    folder), plus the scripts in the script folder whose video is gone (moved or renamed)."""
    root = Path(folder)
    if not root.is_dir():
        return []
    files = root.rglob("*") if recursive else root.iterdir()
    ratings = load_ratings()
    sd = script_settings()
    out = []
    for v in files:
        if not v.is_file() or v.suffix.lower() not in VIDEO_EXTS:
            continue
        s = find_script(v, "L0", sd)
        if s is None:
            continue
        e = Entry(v, s, mtime=s.stat().st_mtime, rating=int(ratings.get(_key(v), 0)))
        _numbers(e)
        out.append(e)
    out += _orphans(sd, {e.script for e in out})
    return sorted(out, key=lambda e: e.name.lower())


def _orphans(sd, listed: set) -> list[Entry]:
    """Scripts in the script folder whose video cannot be found anywhere."""
    out = []
    for s in files_in(sd.folder, "scripts") if sd.active else []:
        stem = video_stem(s)
        if s in listed or s.name.lower() != (stem + ".funscript").lower() or find_video(s) is not None:
            continue                      # listed already, an extra axis, or its video is somewhere else
        e = Entry(s.with_name(stem), s, mtime=s.stat().st_mtime, missing=True)
        _numbers(e)
        out.append(e)
    return out


SORTS = {
    "name": (tr("Name"), lambda e: e.name.lower(), False),
    "rating": (tr("Bewertung"), lambda e: (e.rating, e.name.lower()), True),
    "tempo": (tr("Tempo"), lambda e: e.avg_speed, True),
    "new": (tr("Neueste"), lambda e: e.mtime, True),
    "length": (tr("Länge"), lambda e: e.duration_ms, True),
}


def filtered(entries: list[Entry], text: str = "", sort: str = "name", min_rating: int = 0) -> list[Entry]:
    """Entries whose name contains every word of `text`, rated at least `min_rating`, sorted."""
    words = text.lower().split()
    out = [e for e in entries if all(w in e.name.lower() for w in words) and e.rating >= min_rating]
    _label, key, rev = SORTS.get(sort, SORTS["name"])
    return sorted(out, key=key, reverse=rev)


# ------------------------------------------------------------ ratings (local only)

def _ratings_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "video-to-funscript" / "library.json"


def _key(video: str | Path) -> str:
    return str(Path(video).resolve())


def load_ratings() -> dict[str, int]:
    try:
        d = json.loads(_ratings_file().read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in d.get("ratings", {}).items()}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def save_rating(video: str | Path, stars: int) -> None:
    stars = max(0, min(5, int(stars)))
    r = load_ratings()
    if stars:
        r[_key(video)] = stars
    else:
        r.pop(_key(video), None)
    f = _ratings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"ratings": r}, indent=1), encoding="utf-8")
    os.replace(tmp, f)
