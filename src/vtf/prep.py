"""Tracking preparation per video: <video>.vtf.json next to the video.

Holds the tracking sections (areas, start/end, calibration, corrections) so a
video can be prepared once in the editor and later processed in a batch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .analysis import TrackSection

PREP_VERSION = 1


def prep_path(video: str | Path) -> Path:
    return Path(video).with_suffix(".vtf.json")


def _box(b) -> tuple:
    return tuple(round(float(v), 4) for v in b)


def _opt_int(d: dict, key: str) -> int | None:
    return None if d.get(key) is None else int(d[key])


def save_prep(video: str | Path, sections) -> Path | None:
    """Write the sections; with no sections the file is removed. Returns the path written."""
    path = prep_path(video)
    secs = [s for s in sections if s.boxes]
    if not secs:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return None
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
        } for s in secs],
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


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
            ))
        return [s for s in out if s.boxes]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return []


def is_prepared(video: str | Path) -> bool:
    return bool(load_prep(video))
