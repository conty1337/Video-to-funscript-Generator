"""Where scripts are kept: next to the video (what players expect) and/or in one script folder;
and where the video data (<video>.vtf.json: boxes, sections, cuts, bookmarks) goes.

Saved in ~/.config/video-to-funscript/scripts.json:
{"folder": "...", "next_to_video": true, "data_folder": "...", "thumbnails": true}.
No folder set = next to the video only. Finding a script looks next to the video first, then
in the script folder: directly in it, then in its subfolders (by file name). No Qt.
"""

from __future__ import annotations

import json
import os
import shutil
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
    data_folder: Path | None = None          # video data (.vtf.json); None = next to the video
    thumbnails: bool = True                  # keep small preview pictures of the videos (thumbs.py)

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
    if not isinstance(d, dict):
        return ScriptDir()

    def path(key: str) -> Path | None:
        v = str(d.get(key) or "").strip()
        return Path(v).expanduser() if v else None
    return ScriptDir(path("folder"), bool(d.get("next_to_video", True)), path("data_folder"),
                     bool(d.get("thumbnails", True)))


def save_settings(folder: str | Path | None, next_to_video: bool = True,
                  data_folder: str | Path | None = None, thumbnails: bool = True) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"folder": str(folder) if folder else "", "next_to_video": bool(next_to_video),
                               "data_folder": str(data_folder) if data_folder else "",
                               "thumbnails": bool(thumbnails)},
                              indent=1), encoding="utf-8")
    os.replace(tmp, f)
    _index_cache.clear()


def script_name(video: str | Path, axis: str = "L0") -> str:
    return funscript_path_for(Path(video).name, axis).name


def save_paths(video: str | Path, axis: str = "L0", sd: ScriptDir | None = None) -> list[Path]:
    """Where saving writes the script: next to the video and/or into the script folder."""
    sd = sd or settings()
    to_folder = sd.active and foreign(video) is None
    out = []
    if not to_folder or sd.next_to_video:
        out.append(funscript_path_for(video, axis))
    if to_folder:
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
    if not sd.active or not sd.folder.is_dir() or foreign(video) is not None:
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


# ------------------------------------------------------------ script -> video
# Which video a script belongs to, remembered whenever a video is opened or saved, so that
# opening a script (or moving files between folders) finds the video again.
SEARCH_MAX_FILES = 50_000


def _links_file() -> Path:
    return _file().with_name("videos.json")


def _load_links() -> dict[str, str]:
    try:
        d = json.loads(_links_file().read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_links(links: dict[str, str]) -> None:
    f = _links_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(links, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


def video_stem(path: str | Path) -> str:
    """The video name a script or data file belongs to: "Clip" for Clip.funscript,
    Clip.roll.funscript and Clip.vtf.json."""
    name = Path(path).name
    for end in (".vtf.json", ".funscript"):
        if name.lower().endswith(end):
            name = name[: -len(end)]
            break
    for _label, suffix in AXES.values():
        if suffix and name.lower().endswith("." + suffix):
            return name[: -len(suffix) - 1]
    return name


def remember_video(video: str | Path, stem: str | None = None) -> None:
    """Note where this video lies, under its own name (or the script name `stem`, when the
    user picked a video with another name for a script)."""
    p = Path(video).resolve()
    if stem is None and foreign(p) is not None:
        return                                   # the name stays with the first video
    key = (stem or p.stem).casefold()
    links = _load_links()
    if links.get(key) != str(p):
        links[key] = str(p)
        _save_links(links)


def remembered_video(stem: str) -> Path | None:
    v = _load_links().get(stem.casefold())
    return Path(v) if v else None


def foreign(video: str | Path) -> Path | None:
    """Another existing video with the same name that owns this name in the script and data
    folders (files there are named after the video only). Then this video keeps its script
    and data next to itself, so neither overwrites the other's."""
    p = Path(video).resolve()
    last = remembered_video(p.stem)
    return last if last is not None and last != p and last.is_file() else None


def _video_exts() -> set[str]:
    # imported here: batch builds on this module
    from .batch import VIDEO_EXTS
    return set(VIDEO_EXTS)


def _search(stem: str, folders, exts) -> Path | None:
    want = stem.casefold()
    seen = 0
    for folder in folders:
        if folder is None or not Path(folder).is_dir():
            continue
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            for f in files:
                p = Path(f)
                if p.suffix.lower() in exts and p.stem.casefold() == want:
                    return Path(root) / f
            seen += len(files)
            if seen > SEARCH_MAX_FILES:
                break
    return None


def find_video(script: str | Path, search: tuple = ()) -> Path | None:
    """The video of a script or data file: next to it, where it was last seen, or by name
    in the folder it was last seen in and in `search` (e.g. the library folder)."""
    script = Path(script)
    stem, exts = video_stem(script), _video_exts()
    for ext in sorted(exts):
        for cand in (script.with_name(stem + ext), script.with_name(stem + ext.upper())):
            if cand.is_file():
                return cand
    last = remembered_video(stem)
    if last is not None and last.is_file():
        return last
    places = [last.parent if last is not None else None, *search]
    found = _search(stem, places, exts)
    if found is not None:
        remember_video(found)
    return found


# ------------------------------------------------------------ moving files on a folder change
def files_in(folder: Path | None, kind: str) -> list[Path]:
    """Scripts ("scripts") or data files ("data") below a folder."""
    if folder is None or not folder.is_dir():
        return []
    end = ".funscript" if kind == "scripts" else ".vtf.json"
    out = []
    for root, _dirs, files in os.walk(folder):
        out += [Path(root) / f for f in files if f.lower().endswith(end)]
    return sorted(out)


def move_files(old: Path, new: Path | None, kind: str) -> tuple[int, int]:
    """Move the scripts or data files from `old` into `new` (subfolders kept), or with
    new=None next to their videos (where known). Files are never overwritten: those that
    cannot go anywhere stay. Returns (moved, left)."""
    moved = left = 0
    for f in files_in(old, kind):
        if new is not None:
            target = new / f.relative_to(old)
        else:
            video = remembered_video(video_stem(f))
            target = video.parent / f.name if video is not None and video.is_file() else None
        if target is None or target.exists() or target == f:
            left += 1
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(f, target)
        except OSError:
            try:
                shutil.move(str(f), str(target))        # another drive
            except OSError:
                left += 1
                continue
        moved += 1
    _index_cache.clear()
    return moved, left
