"""Tracking preparation per video: <video>.vtf.json, next to the video or in the folder for
video data (Datei → Speicherorte). A file found next to the video moves into that folder the
next time it is saved.

Holds the tracking sections (areas, start/end, calibration, corrections) so a
video can be prepared once in the editor and later processed in a batch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .analysis import TrackSection
from .scriptdir import settings

PREP_VERSION = 1


def _near(video: str | Path) -> Path:
    return Path(video).with_suffix(".vtf.json")


def _target(video: str | Path) -> Path:
    """Where saving writes the file."""
    folder = settings().data_folder
    return folder / _near(Path(video).name).name if folder is not None else _near(video)


def prep_path(video: str | Path) -> Path:
    """The file of a video: where it is now (folder for video data first, then next to the
    video), or where it will be written."""
    target = _target(video)
    if target.is_file():
        return target
    near = _near(video)
    return near if near.is_file() else target


def _box(b) -> tuple:
    return tuple(round(float(v), 4) for v in b)


def _outlines(lines) -> tuple:
    return tuple(tuple((round(float(u), 4), round(float(v), 4)) for u, v in o) for o in lines)


def _opt_int(d: dict, key: str) -> int | None:
    return None if d.get(key) is None else int(d[key])


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(video: str | Path, data: dict) -> Path | None:
    """Write the file to its target; a copy left next to the video is removed (moved). With
    no sections, bookmarks or cuts there is no file."""
    target, near = _target(video), _near(video)
    keep = data_has(data)
    if keep:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, target)
    stale = ([near] if near != target else []) if keep else list({target, near})
    for p in stale:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    return target if keep else None


def data_has(data: dict) -> bool:
    return bool(data.get("sections") or data.get("bookmarks") or data.get("cuts"))


def save_bookmarks(video: str | Path, bookmarks) -> Path | None:
    """Bookmarks/chapters [(ms, name), ...] in the same file (sections are kept)."""
    path = prep_path(video)
    data = _read(path)
    data.setdefault("version", PREP_VERSION)
    data["bookmarks"] = [[int(ms), str(name)] for ms, name in sorted(bookmarks)]
    return _write(video, data)


def load_bookmarks(video: str | Path) -> list[tuple[int, str]]:
    try:
        return sorted((int(b[0]), str(b[1])) for b in _read(prep_path(video)).get("bookmarks", []))
    except (ValueError, TypeError, IndexError):
        return []


def save_cuts(video: str | Path, cuts) -> Path | None:
    """Cuts [(from ms, to ms), ...]: stretches the player skips (the rest is kept)."""
    path = prep_path(video)
    data = _read(path)
    data.setdefault("version", PREP_VERSION)
    data["cuts"] = [[int(a), int(b)] for a, b in sorted(cuts)]
    return _write(video, data)


def load_cuts(video: str | Path) -> list[tuple[int, int]]:
    try:
        return sorted((int(c[0]), int(c[1])) for c in _read(prep_path(video)).get("cuts", []))
    except (ValueError, TypeError, IndexError):
        return []


def save_prep(video: str | Path, sections) -> Path | None:
    """Write the sections (bookmarks are kept); with neither the file is removed.
    Returns the path written."""
    path = prep_path(video)
    secs = [s for s in sections if s.boxes]
    old = _read(path)
    data = {
        "version": PREP_VERSION,
        "mode": "track",
        "sections": [{
            "start_ms": int(s.start_ms),
            "end_ms": None if s.end_ms is None else int(s.end_ms),
            "boxes": [list(_box(b)) for b in s.boxes],
            "calib_in_ms": s.calib_in_ms,
            "calib_out_ms": s.calib_out_ms,
            "keys": [[int(ms), int(a), list(_box(b))] for ms, a, b in s.keys],
            **({"outlines": [[list(pt) for pt in o] for o in _outlines(s.outlines)]}
               if any(s.outlines) else {}),
            **({"stroke": list(_box(s.stroke))} if s.stroke else {}),
        } for s in secs],
    }
    for key in ("bookmarks", "cuts"):
        if old.get(key):
            data[key] = old[key]
    return _write(video, data)


def load_prep(video: str | Path) -> list[TrackSection]:
    """The prepared sections, or [] if there is no (readable) preparation."""
    path = prep_path(video)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        out = []
        for d in data.get("sections", []):
            out.append(TrackSection(
                start_ms=int(d["start_ms"]),
                boxes=tuple(_box(b) for b in d.get("boxes", [])),
                end_ms=_opt_int(d, "end_ms"),
                calib_in_ms=_opt_int(d, "calib_in_ms"),
                calib_out_ms=_opt_int(d, "calib_out_ms"),
                keys=tuple((int(k[0]), int(k[1]), _box(k[2])) for k in d.get("keys", [])),
                outlines=_outlines(d.get("outlines", [])),
                stroke=_box(d["stroke"]) if d.get("stroke") else None,
            ))
        return [s for s in out if s.boxes]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return []


def is_prepared(video: str | Path) -> bool:
    return bool(load_prep(video))
