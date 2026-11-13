"""Where scripts are kept: next to the video (what players expect) and/or in one script folder.

Saved in ~/.config/video-to-funscript/scripts.json: {"folder": "...", "next_to_video": true}.
No folder set = next to the video only. Finding a script looks next to the video first, then
in the script folder: directly in it, then in its subfolders (by file name). No Qt.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .funscript import AXES, funscript_path_for

INDEX_TTL_S = 5.0            # subfolder index reused this long (library scans ask once per video)
MAX_FILES = 50_000           # stop indexing huge trees


@dataclass(frozen=True)
class ScriptDir:
    folder: Path | None = None
    next_to_video: bool = True

    @property
    def active(self) -> bool:
        return self.folder is not None


def _file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "video-to-funscript" / "scripts.json"


def settings() -> ScriptDir:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ScriptDir()
    folder = str(d.get("folder") or "").strip() if isinstance(d, dict) else ""
    nxt = bool(d.get("next_to_video", True)) if isinstance(d, dict) else True
    return ScriptDir(Path(folder).expanduser() if folder else None, nxt)


def save_settings(folder: str | Path | None, next_to_video: bool = True) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"folder": str(folder) if folder else "", "next_to_video": bool(next_to_video)},
                              indent=1), encoding="utf-8")
    os.replace(tmp, f)
    _index_cache.clear()


def script_name(video: str | Path, axis: str = "L0") -> str:
    return funscript_path_for(Path(video).name, axis).name


def save_paths(video: str | Path, axis: str = "L0", sd: ScriptDir | None = None) -> list[Path]:
    """Where saving writes the script: next to the video and/or into the script folder."""
    sd = sd or settings()
    out = []
    if not sd.active or sd.next_to_video:
        out.append(funscript_path_for(video, axis))
    if sd.active:
        out.append(sd.folder / script_name(video, axis))
    return out


_index_cache: dict[str, tuple[float, dict[str, Path]]] = {}


def _index(folder: Path) -> dict[str, Path]:
    """Script files below `folder` by lower-case name (the shallowest one wins)."""
    key = str(folder)
    hit = _index_cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < INDEX_TTL_S:
        return hit[1]
    found: dict[str, Path] = {}
    seen = 0
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for f in files:
            if f.lower().endswith(".funscript"):
                found.setdefault(f.casefold(), Path(root) / f)
        seen += len(files)
        if seen > MAX_FILES:
            break
    _index_cache[key] = (now, found)
    return found


def find_script(video: str | Path, axis: str = "L0", sd: ScriptDir | None = None) -> Path | None:
    """The existing script for a video: next to it, else in the script folder (or below)."""
    near = funscript_path_for(video, axis)
    if near.is_file():
        return near
    sd = sd or settings()
    if not sd.active or not sd.folder.is_dir():
        return None
    name = script_name(video, axis)
    direct = sd.folder / name
    if direct.is_file():
        return direct
    hit = _index(sd.folder).get(name.casefold())
    return hit if hit is not None and hit.is_file() else None


def has_script(video: str | Path, sd: ScriptDir | None = None) -> bool:
    return find_script(video, "L0", sd) is not None


def find_axes(video: str | Path, sd: ScriptDir | None = None) -> dict[str, Path]:
    """Every axis script of a video that exists (next to it or in the script folder)."""
    sd = sd or settings()
    return {axis: p for axis in AXES if (p := find_script(video, axis, sd)) is not None}
