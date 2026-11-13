"""Batch processing of whole video folders."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from . import __version__
from .analysis import AnalysisCancelled, AnalysisSettings, analyze_motion_cached
from .funscript import Funscript
from .generator import GeneratorSettings, generate
from .i18n import tr
from .prep import is_prepared, load_prep
from .scriptdir import has_script, save_paths

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".mpg", ".mpeg", ".flv", ".ts"}


@dataclass
class BatchResult:
    done: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False


def find_videos(folder: str | Path, recursive: bool = True) -> list[Path]:
    folder = Path(folder)
    it = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


def is_analyzed(video: str | Path) -> bool:
    return has_script(video)


def process_video(video: str | Path, an: AnalysisSettings, gen: GeneratorSettings,
                  progress: Callable[[float], None] | None = None,
                  cancel: Callable[[], bool] | None = None,
                  output: str | Path | None = None) -> Path:
    sections = load_prep(video)
    if sections:            # prepared in the editor: track the marked areas
        an = replace(an, mode="track", track_sections=tuple(sections), track_boxes=())
    else:
        an = replace(an, mode="flow", track_sections=(), track_boxes=())
    motion = analyze_motion_cached(video, an, progress, cancel)
    script = generate(motion, gen, sections)
    targets = [Path(output)] if output else save_paths(video)
    fs = Funscript(script.actions, metadata=make_metadata(video))
    for t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)
        fs.save(t)
    return targets[0]


def make_metadata(video: str | Path) -> dict:
    return {
        "title": Path(video).stem,
        "creator": "Video-to-Funscript Generator",
        "description": tr("Automatisch erzeugt"),
        "notes": f"vtf {__version__}",
    }


def run_batch(folder: str | Path, an: AnalysisSettings, gen: GeneratorSettings,
              recursive: bool = True, overwrite: bool = False,
              on_file: Callable[[int, int, Path], None] | None = None,
              on_progress: Callable[[float], None] | None = None,
              log: Callable[[str], None] | None = None,
              cancel: Callable[[], bool] | None = None) -> BatchResult:
    log = log or (lambda _m: None)
    videos = find_videos(folder, recursive)
    res = BatchResult()
    log(tr("{0} Videos gefunden.", len(videos)))
    for i, v in enumerate(videos):
        if cancel and cancel():
            res.cancelled = True
            break
        if on_file:
            on_file(i, len(videos), v)
        if not overwrite and is_analyzed(v):
            res.skipped.append(v)
            log(tr("Übersprungen (bereits analysiert): {0}", v.name))
            continue
        try:
            prepared = is_prepared(v)
            out = process_video(v, an, gen, on_progress, cancel)
            res.done.append(v)
            log(tr("Fertig: {0} -> {1}", v.name, out.name) + (tr("  (Tracking, vorbereitet)") if prepared else ""))
        except AnalysisCancelled:
            res.cancelled = True
            log("Abgebrochen.")
            break
        except Exception as e:  # keep going with the next file
            res.failed.append((v, str(e)))
            log(tr("Fehler bei {0}: {1}", v.name, e))
    return res
