"""Scene changes: hard cuts in the video, where tracking boxes no longer fit and have to be
marked anew. Found from how much the brightness histogram of small grayscale frames jumps
from one look to the next (about ten looks per second). No Qt, testable on its own."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from .video import iter_gray_frames, probe

WIDTH = 160          # frames are looked at this small
LOOKS_PER_S = 10     # about this many frames per second
BINS = 32
CUT_DIFF = 0.4       # histogram change (0..1) from one look to the next that counts as a cut
MIN_GAP_MS = 1000    # cuts closer than this: one


def _hist(frame: np.ndarray) -> np.ndarray:
    h = np.bincount((frame.ravel().astype(np.uint16) * BINS) >> 8, minlength=BINS).astype(np.float64)
    return h / max(1.0, h.sum())


def cuts_in(frames: Iterable[np.ndarray], ms_per_frame: float,
            cancel: Callable[[], bool] = lambda: False) -> list[int]:
    """The times (ms) where the picture changes at once."""
    out: list[int] = []
    prev = None
    for k, f in enumerate(frames):
        if cancel():
            break
        h = _hist(f)
        if prev is not None and 0.5 * float(np.abs(h - prev).sum()) > CUT_DIFF:
            ms = int(round(k * ms_per_frame))
            if not out or ms - out[-1] >= MIN_GAP_MS:
                out.append(ms)
        prev = h
    return out


def find_cuts(video: str | Path, hwaccel: str = "auto", progress: Callable[[float], None] | None = None,
              cancel: Callable[[], bool] = lambda: False) -> list[int]:
    info = probe(video)
    step = max(1, int(round(info.fps / LOOKS_PER_S)))
    total = max(1, int(info.frame_count / step)) if info.frame_count else 0

    def frames():
        for k, f in enumerate(iter_gray_frames(info, WIDTH, hwaccel, step)):
            if progress and total and k % 50 == 0:
                progress(min(1.0, k / total))
            yield f
    return cuts_in(frames(), 1000.0 * step / info.fps, cancel)


def needs_new_boxes(cuts, ranges, slack_ms: int = 1500) -> list[int]:
    """Cuts inside a tracking section (`ranges`: (index, start, end)) that no section starts
    at (1 s before to `slack_ms` after the cut: hardly anyone hits the frame): there the
    boxes follow something else."""
    starts = [a for _i, a, _b in ranges]
    return [c for c in cuts
            if any(a < c < b for _i, a, b in ranges) and not any(c - 1000 <= s <= c + slack_ms for s in starts)]
