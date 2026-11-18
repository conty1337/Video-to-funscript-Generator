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
from .prep import load_cuts
from .quality import check, summary
from .scriptdir import RATING_KEY, files_in, find_script, find_video, video_stem
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
    issues: int = 0               # places worth a look (the editor's check)
    score: int = -1               # the script's rating 0..100 (saved with it), -1 = none
    issue_text: str = ""
    played_s: float = 0.0         # played on this computer, all days (statistics)
    last_played: str = ""         # ISO day it was last played

    @property
    def name(self) -> str:
        return self.video.stem


def _numbers(e: Entry) -> None:
    try:
        fs = Funscript.load(e.script)
    except (OSError, ValueError, KeyError, TypeError):
        return
    acts = fs.actions
    try:
        e.score = int((fs.metadata or {}).get(RATING_KEY, -1))
    except (TypeError, ValueError):
        e.score = -1
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
    found = check(acts, 0, load_cuts(e.video))
    e.issues, e.issue_text = len(found), summary(found)


def _add_played(entries: list[Entry]) -> None:
    """Fill in when and how long each video was played (from the statistics, by name)."""
    from .stats import load
    total: dict[str, float] = {}
    last: dict[str, str] = {}
    for day, d in load().get("days", {}).items():
        for name, sec in (d.get("videos") or {}).items():
            total[name] = total.get(name, 0.0) + float(sec or 0)
            last[name] = max(last.get(name, ""), day)
    for e in entries:
        e.played_s, e.last_played = total.get(e.name, 0.0), last.get(e.name, "")


def scan(folder: str | Path, recursive: bool = True) -> list[Entry]:
    """Every video in the folder (and below) that has a script (next to it or in the script
    folder), plus the scripts in the script folder or this folder that no video here brought in."""
    root = Path(folder)
    if not root.is_dir():
        return []
    files = root.rglob("*") if recursive else root.iterdir()
    ratings = load_ratings()
    sd = script_settings()
    out, loose = [], []
    for v in files:
        if v.name.lower().endswith(".funscript") and v.is_file():
            loose.append(v)
        if not v.is_file() or v.suffix.lower() not in VIDEO_EXTS:
            continue
        s = find_script(v, "L0", sd)
        if s is None:
            continue
        e = Entry(v, s, mtime=s.stat().st_mtime, rating=int(ratings.get(_key(v), 0)))
        _numbers(e)
        out.append(e)
    out += _orphans(sd, out, loose, ratings)
    _add_played(out)
    return sorted(out, key=lambda e: e.name.lower())


def _orphans(sd, listed: list[Entry], loose: list[Path], ratings: dict) -> list[Entry]:
    """Scripts in the script folder or the library folder that no video above brought in:
    with their video when it lies elsewhere (e.g. scripts on a network folder, videos local),
    else marked as missing."""
    scripts = {e.script for e in listed}
    videos = {_key(e.video) for e in listed}
    out = []
    for s in dict.fromkeys([*(files_in(sd.folder, "scripts") if sd.active else []), *loose]):
        stem = video_stem(s)
        if s in scripts or s.name.lower() != (stem + ".funscript").lower():
            continue                      # listed already, or an extra axis
        video = find_video(s)
        if video is not None and _key(video) in videos:
            continue                      # a copy of a script already listed with its video
        e = Entry(video or s.with_name(stem), s, mtime=s.stat().st_mtime, missing=video is None)
        if video is not None:
            e.rating = int(ratings.get(_key(video), 0))
            videos.add(_key(video))
        _numbers(e)
        out.append(e)
    return out


SORTS = {
    "name": (tr("Name"), lambda e: e.name.lower(), False),
    "rating": (tr("Bewertung"), lambda e: (e.rating, e.name.lower()), True),
    "tempo": (tr("Tempo"), lambda e: e.avg_speed, True),
    "new": (tr("Neueste"), lambda e: e.mtime, True),
    "length": (tr("Länge"), lambda e: e.duration_ms, True),
    "recent": (tr("Zuletzt gespielt"), lambda e: (e.last_played, e.played_s), True),
    "played": (tr("Oft gespielt"), lambda e: e.played_s, True),
    "check": (tr("Zu prüfen"), lambda e: e.issues, True),
    "score": (tr("Wertung"), lambda e: e.score, True),
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
