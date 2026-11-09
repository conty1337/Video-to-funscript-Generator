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


def box_corners(cx: float, cy: float, w: float, h: float, angle_deg: float) -> np.ndarray:
    """The 4 corners of a rotated rectangle (px)."""
    a = np.radians(angle_deg)
    c, s = np.cos(a), np.sin(a)
    half = np.array([[-w, -h], [w, -h], [w, h], [-w, h]]) / 2
    rot = np.array([[c, -s], [s, c]])
    return half @ rot.T + np.array([cx, cy])


class AreaTracker:
    """One marked area. Box (px): (x, y, w, h) or (x, y, w, h, angle_deg), where
    x/y/w/h describe the unrotated rectangle and the angle turns it around its centre."""

    TEMPLATE_EVERY = 5            # frames between drift corrections
    TEMPLATE_MIN_SCORE = 0.7

    def __init__(self, box, shape: tuple[int, int], first: np.ndarray | None = None):
        self.h, self.w = shape
        x, y, bw, bh = box[:4]
        self.size = np.array([max(8.0, bw), max(8.0, bh)], np.float64)
        self.center = np.array([x + bw / 2, y + bh / 2], np.float64)
        self.angle = float(box[4]) if len(box) > 4 else 0.0
        self.angle0 = self.angle
        self.scale = 1.0
        self.pts: np.ndarray | None = None
        self.lost = False
        self.frames = 0
        self.lost_frames = 0
        self.template = None
        self.recent = None              # (patch, angle) seen lately while tracking was sure
        if first is not None:
            self._take_template(first)
            self.recent = (self.template, self.angle) if self.template is not None else None

    # ------------------------------------------------------------ geometry
    def corners(self) -> np.ndarray:
        w, h = self.size * self.scale
        return box_corners(self.center[0], self.center[1], w, h, self.angle)

    def rect(self) -> tuple[int, int, int, int]:
        """Axis-aligned bounding box of the (rotated) area, clipped to the frame."""
        c = self.corners()
        x0, y0 = np.floor(c.min(axis=0)).astype(int)
        x1, y1 = np.ceil(c.max(axis=0)).astype(int)
        return max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)

    def _local_mask(self, rect) -> np.ndarray:
        x0, y0, x1, y1 = rect
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillConvexPoly(m, np.round(self.corners() - [x0, y0]).astype(np.int32), 255)
        return m

    def mask(self) -> np.ndarray:
        m = np.zeros((self.h, self.w), np.uint8)
        cv2.fillConvexPoly(m, np.round(self.corners()).astype(np.int32), 255)
        return m

    def inside(self, pts: np.ndarray, margin: float = 1.25) -> np.ndarray:
        """Which points lie in the (slightly enlarged) rotated area."""
        a = np.radians(self.angle)
        c, s = np.cos(a), np.sin(a)
        rel = pts - self.center
        local = np.stack([rel[:, 0] * c + rel[:, 1] * s, -rel[:, 0] * s + rel[:, 1] * c], axis=1)
        half = self.size * self.scale / 2 * margin
        return np.all(np.abs(local) <= half, axis=1)

    # ------------------------------------------------------------ appearance
    def _take_template(self, gray: np.ndarray) -> None:
        x0, y0, x1, y1 = self.rect()
        if x1 - x0 >= 12 and y1 - y0 >= 12:
            self.template = gray[y0:y1, x0:x1].copy()

    def _patch(self, gray: np.ndarray):
        x0, y0, x1, y1 = self.rect()
        return gray[y0:y1, x0:x1].copy() if x1 - x0 >= 12 and y1 - y0 >= 12 else None

    def _reacquire(self, gray: np.ndarray) -> bool:
        """After a loss: look for the recent appearance around the last position."""
        if self.recent is None:
            return False
        tpl = self.recent[0]
        th, tw = tpl.shape
        cx, cy = self.center
        rx, ry = tw * 1.0, th * 1.0
        x0, y0 = int(max(0, cx - tw / 2 - rx)), int(max(0, cy - th / 2 - ry))
        x1, y1 = int(min(self.w, cx + tw / 2 + rx)), int(min(self.h, cy + th / 2 + ry))
        if x1 - x0 <= tw or y1 - y0 <= th:
            return False
        res = cv2.matchTemplate(gray[y0:y1, x0:x1], tpl, cv2.TM_CCOEFF_NORMED)
        _mn, score, _ml, (bx, by) = cv2.minMaxLoc(res)
        if score < 0.6:
            return False
        self.center = np.array([x0 + bx + tw / 2, y0 + by + th / 2], np.float64)
        self.angle = self.recent[1]
        self.pts = None
        return True

    def _drift_correction(self, gray: np.ndarray) -> None:
        """Pull the area back to where it looks like it did when it was marked.
        Only while it is about as big and as turned as then (the template is upright)."""
        if self.template is None or abs(self.angle - self.angle0) > 15 or not 0.85 < self.scale < 1.2:
            return
        th, tw = self.template.shape
        cx, cy = self.center
        rx, ry = tw * 0.5, th * 0.5                         # search +-half a box around
        x0, y0 = int(max(0, cx - tw / 2 - rx)), int(max(0, cy - th / 2 - ry))
        x1, y1 = int(min(self.w, cx + tw / 2 + rx)), int(min(self.h, cy + th / 2 + ry))
        if x1 - x0 <= tw or y1 - y0 <= th:
            return
        res = cv2.matchTemplate(gray[y0:y1, x0:x1], self.template, cv2.TM_CCOEFF_NORMED)
        _mn, score, _ml, (bx, by) = cv2.minMaxLoc(res)
        if score < self.TEMPLATE_MIN_SCORE:
            return
        found = np.array([x0 + bx + tw / 2, y0 + by + th / 2])
        off = found - self.center
        if 1.0 < np.hypot(*off) < 0.5 * max(tw, th):
            self.center = self.center + 0.5 * off            # gently, half the way

    # ------------------------------------------------------------ tracking
    def _refill(self, gray: np.ndarray) -> None:
        rect = self.rect()
        x0, y0, x1, y1 = rect
        if x1 - x0 < 8 or y1 - y0 < 8:
            return
        new = _features(gray, self._local_mask(rect), MAX_POINTS, rect=rect)
        if new is None:
            return
        self.pts = new if self.pts is None or not len(self.pts) else np.vstack([self.pts, new])[:MAX_POINTS * 2]

    def max_jump(self) -> float:
        """Largest believable move between two frames (px)."""
        return max(40.0, float(np.hypot(*(self.size * self.scale))))

    def step(self, prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
        """Move the area from prev to cur; returns its displacement (px).
        Sets ``lost`` when the area could not be followed in this frame."""
        d = self._step(prev, cur)
        if self.lost:
            self.lost_frames += 1
        else:
            self.lost_frames = 0
            if self.frames % 15 == 0 and self.pts is not None and len(self.pts) >= MIN_POINTS * 1.5:
                patch = self._patch(cur)
                if patch is not None:
                    self.recent = (patch, self.angle)
        return d

    def _step(self, prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
        self.frames += 1
        if self.lost_frames >= 2 and self._reacquire(cur):
            # found again: position fixed, but no motion from this frame (unknown path)
            self.lost_frames = 0
            self.lost = False
            self._refill(cur)
            return np.zeros(2)
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
        p0 = self.pts[good].reshape(-1, 2)
        p1 = nxt[good].reshape(-1, 2)
        old = self.center.copy()
        moved = False
        if len(p0) >= 6:
            # shift + rotation + scale from the points; RANSAC ignores points that slipped
            m, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=2.0,
                                                 maxIters=200, confidence=0.98)
            if m is not None and inl is not None and int(inl.sum()) >= 5:
                sc = float(np.hypot(m[0, 0], m[1, 0]))
                rot = float(np.degrees(np.arctan2(m[1, 0], m[0, 0])))
                if 0.8 < sc < 1.25 and abs(rot) < 20:        # believable change in one frame
                    new_c = m[:, :2] @ self.center + m[:, 2]
                    self.center = new_c
                    self.scale = float(np.clip(self.scale * sc, 0.5, 2.0))
                    self.angle += rot
                    keep = inl.ravel().astype(bool)
                    p1 = p1[keep]
                    moved = True
        if not moved:
            self.center = self.center + np.median(p1 - p0, axis=0)
        d = self.center - old
        if np.hypot(*d) > self.max_jump():                   # impossible jump: do not believe it
            self.center = old
            self.pts = None
            self.lost = True
            return np.zeros(2)
        self.center = np.clip(self.center, [0, 0], [self.w - 1, self.h - 1])
        d = self.center - old
        self.lost = False
        if self.frames % self.TEMPLATE_EVERY == 0:
            before = self.center.copy()
            self._drift_correction(cur)
            d = d + (self.center - before)
        # keep only points still inside the (slightly enlarged) area
        self.pts = p1[self.inside(p1)].reshape(-1, 1, 2).astype(np.float32)
        return d


class MultiTracker:
    """Tracks up to MAX_AREAS areas plus the background (camera)."""

    def __init__(self, boxes_px: list, shape: tuple[int, int], compensate_camera: bool = True,
                 first: np.ndarray | None = None):
        if not boxes_px:
            raise ValueError("mindestens ein Tracking-Bereich nötig")
        self.shape = shape
        self.areas = [AreaTracker(b, shape, first) for b in boxes_px[:MAX_AREAS]]
        self.compensate = compensate_camera
        self._bg: np.ndarray | None = None     # background points, kept from frame to frame

    def centers(self) -> np.ndarray:
        return np.array([a.center for a in self.areas])

    def reset_area(self, index: int, box_px, gray: np.ndarray) -> None:
        """Put area `index` where the user says it is (a correction) and start over there."""
        self.areas[index] = AreaTracker(box_px, self.shape, gray)

    def geometry(self) -> np.ndarray:
        """(scale, angle_deg) per area."""
        return np.array([[a.scale, a.angle] for a in self.areas])

    def lost(self) -> np.ndarray:
        return np.array([a.lost for a in self.areas])

    def _camera(self, prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
        """Camera motion = median motion of points outside the areas. The points
        are reused from frame to frame and only searched again when too few are left."""
        if self._bg is None or len(self._bg) < 40:
            mask = np.full(self.shape, 255, np.uint8)
            for a in self.areas:
                mask[a.mask() > 0] = 0
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
        if any(a.lost for a in self.areas):
            return np.zeros(2)          # one area not followed: no reliable motion in this frame
        if len(moves) == 1:
            d = moves[0]
            if self.compensate:
                d = d - self._camera(prev, cur)
            return d
        return moves[0] - np.mean(moves[1:], axis=0)
