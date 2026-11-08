"""Tracking of marked areas (body parts, objects) with many points each.

Each area is followed with sparse Lucas-Kanade optical flow on up to 60
feature points inside it. A forward-backward check drops points that slipped
off; the area moves by the median of the remaining points, and lost points are
searched again inside the area. Points outside all areas measure the camera
motion.

Result per frame: the centre of every area (for the overlay and for angles,
e.g. roll) and one motion vector for the script:

* one area:   its motion (minus the camera motion, if wanted)
* two or more: motion of area 1 relative to the others - independent of the
  camera, e.g. hips relative to thighs
"""

from __future__ import annotations

import cv2
import numpy as np

LK_PARAMS = {"winSize": (21, 21), "maxLevel": 3,
             "criteria": (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03)}
MAX_POINTS = 60
MIN_POINTS = 12          # fewer left -> search new points in the area
FB_MAX_ERR = 1.0         # px, forward-backward consistency
MAX_AREAS = 3


def _features(gray: np.ndarray, mask: np.ndarray | None, n: int,
              rect: tuple[int, int, int, int] | None = None) -> np.ndarray | None:
    """Corner points; with `rect` only that part of the image is searched (much
    cheaper than masking the whole frame)."""
    off = (0, 0)
    if rect is not None:
        x0, y0, x1, y1 = rect
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        gray = gray[y0:y1, x0:x1]
        off = (x0, y0)
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=n, qualityLevel=0.01, minDistance=3, mask=mask,
                                  blockSize=5)
    if pts is None:
        return None
    pts = pts.astype(np.float32)
    pts[..., 0] += off[0]
    pts[..., 1] += off[1]
    return pts


def _lk(prev: np.ndarray, cur: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Track points prev -> cur; returns new points and a mask of the reliable ones."""
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, **LK_PARAMS)
    back, st2, _ = cv2.calcOpticalFlowPyrLK(cur, prev, nxt, None, **LK_PARAMS)
    err = np.linalg.norm((pts - back).reshape(-1, 2), axis=1)
    good = (st.ravel() == 1) & (st2.ravel() == 1) & (err < FB_MAX_ERR)
    return nxt, good


class AreaTracker:
    def __init__(self, box: tuple[float, float, float, float], shape: tuple[int, int]):
        self.h, self.w = shape
        x, y, bw, bh = box
        self.size = np.array([max(8.0, bw), max(8.0, bh)], np.float64)
        self.center = np.array([x + bw / 2, y + bh / 2], np.float64)
        self.pts: np.ndarray | None = None
        self.lost = False

    def rect(self) -> tuple[int, int, int, int]:
        x0, y0 = (self.center - self.size / 2).astype(int)
        x1, y1 = (self.center + self.size / 2).astype(int)
        return max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)

    def mask(self) -> np.ndarray:
        m = np.zeros((self.h, self.w), np.uint8)
        x0, y0, x1, y1 = self.rect()
        m[y0:y1, x0:x1] = 255
        return m

    def _refill(self, gray: np.ndarray) -> None:
        new = _features(gray, None, MAX_POINTS, rect=self.rect())
        if new is None:
            return
        self.pts = new if self.pts is None or not len(self.pts) else np.vstack([self.pts, new])[:MAX_POINTS * 2]

    def step(self, prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
        """Move the area from prev to cur; returns its displacement (px)."""
        if self.pts is None or len(self.pts) < MIN_POINTS:
            self._refill(prev)
        if self.pts is None or len(self.pts) < 3:
            self.lost = True
            return np.zeros(2)
        nxt, good = _lk(prev, cur, self.pts)
        if good.sum() < 3:
            self.pts = None
            self.lost = True
            return np.zeros(2)
        self.lost = False
        d = np.median((nxt[good] - self.pts[good]).reshape(-1, 2), axis=0).astype(np.float64)
        self.center = np.clip(self.center + d, [0, 0], [self.w - 1, self.h - 1])
        kept = nxt[good].reshape(-1, 2)
        # drop points that drifted out of the (slightly enlarged) area
        half = self.size / 2 * 1.25
        inside = np.all(np.abs(kept - self.center) <= half, axis=1)
        self.pts = kept[inside].reshape(-1, 1, 2).astype(np.float32)
        return d


class MultiTracker:
    """Tracks up to MAX_AREAS areas plus the background (camera)."""

    def __init__(self, boxes_px: list[tuple[float, float, float, float]], shape: tuple[int, int],
                 compensate_camera: bool = True):
        if not boxes_px:
            raise ValueError("mindestens ein Tracking-Bereich nötig")
        self.shape = shape
        self.areas = [AreaTracker(b, shape) for b in boxes_px[:MAX_AREAS]]
        self.compensate = compensate_camera
        self._bg: np.ndarray | None = None     # background points, kept from frame to frame

    def centers(self) -> np.ndarray:
        return np.array([a.center for a in self.areas])

    def _camera(self, prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
        """Camera motion = median motion of points outside the areas. The points
        are reused from frame to frame and only searched again when too few are left."""
        if self._bg is None or len(self._bg) < 40:
            mask = np.full(self.shape, 255, np.uint8)
            for a in self.areas:
                x0, y0, x1, y1 = a.rect()
                mask[y0:y1, x0:x1] = 0
            self._bg = _features(prev, mask, 120)
        if self._bg is None or len(self._bg) < 8:
            self._bg = None
            return np.zeros(2)
        pts = self._bg
        nxt, good = _lk(prev, cur, pts)
        if good.sum() < 8:
            self._bg = None
            return np.zeros(2)
        d = np.median((nxt[good] - pts[good]).reshape(-1, 2), axis=0).astype(np.float64)
        kept = nxt[good].reshape(-1, 2)
        h, w = self.shape
        inside = (kept[:, 0] >= 0) & (kept[:, 0] < w) & (kept[:, 1] >= 0) & (kept[:, 1] < h)
        for a in self.areas:                     # points that walked into an area are dropped
            x0, y0, x1, y1 = a.rect()
            inside &= ~((kept[:, 0] >= x0) & (kept[:, 0] < x1) & (kept[:, 1] >= y0) & (kept[:, 1] < y1))
        self._bg = kept[inside].reshape(-1, 1, 2).astype(np.float32)
        return d

    def step(self, prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
        """Returns the motion vector (dx, dy) for the script for this frame pair."""
        moves = [a.step(prev, cur) for a in self.areas]
        if len(moves) == 1:
            d = moves[0]
            if self.compensate:
                d = d - self._camera(prev, cur)
            return d
        return moves[0] - np.mean(moves[1:], axis=0)
