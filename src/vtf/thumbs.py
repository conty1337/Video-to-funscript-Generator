"""Small preview pictures of the videos, for the library, the playlist and the phone.

One JPEG per video name (<name>.jpg), kept in the folder for video data (thumbs/ inside it)
or else in ~/.local/share/video-to-funscript/thumbs, never next to the video. Taken from a
frame with something on it, not the black start. Can be switched off (Speicherorte), then
the pictures are deleted. No Qt.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import cv2
import numpy as np

from .scriptdir import foreign, settings

WIDTH = 320
QUALITY = 82
SPOTS = (0.12, 0.3, 0.5, 0.7)          # where in the video to look for a good frame


def thumb_dir() -> Path:
    data = settings().data_folder
    if data is not None:
        return data / "thumbs"
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "video-to-funscript" / "thumbs"


def thumb_path(video_or_stem: str | Path) -> Path:
    """Where the picture of a video (or a video name) lies or will lie."""
    p = Path(video_or_stem)
    if not p.suffix:
        return thumb_dir() / f"{p.name}.jpg"
    if foreign(p) is not None:                  # same name as another video: its own picture
        tag = hashlib.sha1(str(p.resolve().parent).encode()).hexdigest()[:8]
        return thumb_dir() / f"{p.stem}.{tag}.jpg"
    return thumb_dir() / f"{p.stem}.jpg"


def enabled() -> bool:
    return settings().thumbnails


def get_thumb(video_or_stem: str | Path) -> Path | None:
    """The picture if there is one (and pictures are on)."""
    if not enabled():
        return None
    p = thumb_path(video_or_stem)
    return p if p.is_file() else None


def _pick_frame(cap: cv2.VideoCapture) -> np.ndarray | None:
    """The frame with the most going on (contrast) among a few spots, skipping black starts."""
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    best, best_score = None, -1.0
    for spot in SPOTS if n > 1 else (0.0,):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * spot))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        score = float(cv2.cvtColor(cv2.resize(frame, (64, 36)), cv2.COLOR_BGR2GRAY).std())
        if score > best_score:
            best, best_score = frame, score
        if score > 40:                  # plenty to see already
            break
    return best


def make_thumb(video: str | Path, force: bool = False) -> Path | None:
    """Create the picture of a video (kept while it is newer than the video). None when
    pictures are off or the video cannot be read."""
    if not enabled():
        return None
    video = Path(video)
    out = thumb_path(video)
    try:
        if not force and out.is_file() and out.stat().st_mtime >= video.stat().st_mtime:
            return out
    except OSError:
        return None
    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            return None
        frame = _pick_frame(cap)
    finally:
        cap.release()
    if frame is None:
        return None
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (WIDTH, max(1, round(h * WIDTH / max(1, w)))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
    if not ok:
        return None
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_bytes(buf.tobytes())
        os.replace(tmp, out)
    except OSError:
        return None
    return out


def thumb_bytes(video_or_stem: str | Path) -> bytes | None:
    p = get_thumb(video_or_stem)
    try:
        return p.read_bytes() if p is not None else None
    except OSError:
        return None


def delete_all(folder: Path | None = None) -> int:
    """Remove every picture (when they are switched off). Returns how many."""
    folder = folder or thumb_dir()
    n = 0
    for f in folder.glob("*.jpg") if folder.is_dir() else []:
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass
    return n
