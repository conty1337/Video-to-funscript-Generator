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

    TEMPLATE_EVERY = 3            # frames between drift corrections
    TEMPLATE_MIN_SCORE = 0.65     # this sure: pull the area back to where it looks right
    GOOD_SCORE = 0.6              # still looks like itself
    COVERED_SCORE = 0.35          # looks like something else: covered (twice in a row)
    MAX_TEMPLATES = 6

    def __init__(self, box, shape: tuple[int, int], first: np.ndarray | None = None,
                 turn: bool = True):
        self.h, self.w = shape
        # turn = follow turning and size too; off: the area only moves (machines, rigid things)
        self.turn = turn
        x, y, bw, bh = box[:4]
        self.size = np.array([max(8.0, bw), max(8.0, bh)], np.float64)
        self.center = np.array([x + bw / 2, y + bh / 2], np.float64)
        self.angle = float(box[4]) if len(box) > 4 else 0.0
        self.scale = 1.0
        self.pts: np.ndarray | None = None
        self.lost = False
        self.frames = 0
        self.lost_frames = 0
        # appearances to compare with: (upright patch, scale then, angle offset). The patch
        # is cut out along the area (turned with it), 1 patch px = 1 image px at `scale then`.
        self.templates: list[tuple[np.ndarray, float, float]] = []
        self.recent = None              # same kind, seen lately while tracking was sure
        # covering: when the area stops looking like itself (something moved over it),
        # it goes back to where it last looked right and waits there until it shows again
        self.covered = False
        self.bad_checks = 0
        self.unsure_checks = 0          # comparisons in a row that did not look quite right
        self.last_score = 1.0
        self.good = (self.center.copy(), self.scale, self.angle)
        if first is not None:
            t = self._capture(first)
            if t is not None:
                self.templates.append(t)
                self.recent = t

    @property
    def template(self):
        """The appearance when the area was marked (None if too small)."""
        return self.templates[0][0] if self.templates else None

    def inherit(self, old: AreaTracker) -> None:
        """Keep the appearances `old` knew (after a correction): the area then has
        several views to compare with, e.g. from the front and from the side."""
        s_old = max(old.scale, 1e-3)
        for patch, ref, off in old.templates:
            if len(self.templates) >= self.MAX_TEMPLATES:
                break
            self.templates.append((patch, ref / s_old, old.angle + off - self.angle))

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
    def _sample(self, gray: np.ndarray, angle: float, per_px: float, wh) -> np.ndarray:
        """Image around the centre, turned by `angle`, `per_px` image px per patch px."""
        pw, ph = int(round(wh[0])), int(round(wh[1]))
        a = np.radians(angle)
        c, s = np.cos(a), np.sin(a)
        mat = per_px * np.array([[c, -s], [s, c]])
        b = self.center - mat @ np.array([pw / 2, ph / 2])
        m = np.hstack([mat, b[:, None]])
        return cv2.warpAffine(gray, m, (pw, ph), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)

    def _capture(self, gray: np.ndarray):
        wh = self.size * self.scale
        if wh.min() < 12:
            return None
        return (self._sample(gray, self.angle, 1.0, wh), self.scale, 0.0)

    def _match(self, gray: np.ndarray, tpl, reach: float) -> tuple[float, np.ndarray]:
        """Best place of a template within `reach` box sizes around the centre:
        (score, offset of the centre in image px)."""
        patch, ref, off = tpl
        per_px = self.scale / max(ref, 1e-3)
        th, tw = patch.shape
        if not 0.4 < per_px < 2.5:
            return -1.0, np.zeros(2)
        area = self._sample(gray, self.angle + off, per_px, (tw * (1 + 2 * reach), th * (1 + 2 * reach)))
        if area.shape[0] <= th or area.shape[1] <= tw:
            return -1.0, np.zeros(2)
        res = cv2.matchTemplate(area, patch, cv2.TM_CCOEFF_NORMED)
        _mn, score, _ml, (bx, by) = cv2.minMaxLoc(res)
        local = np.array([bx + tw / 2 - area.shape[1] / 2, by + th / 2 - area.shape[0] / 2])
        a = np.radians(self.angle + off)
        c, s = np.cos(a), np.sin(a)
        return float(score), per_px * (np.array([[c, -s], [s, c]]) @ local)

    def _best(self, gray: np.ndarray, tpls, reach: float) -> tuple[float, np.ndarray]:
        best = (-1.0, np.zeros(2))
        for t in tpls:
            r = self._match(gray, t, reach)
            if r[0] > best[0]:
                best = r
        return best

    def _reacquire(self, gray: np.ndarray, reach: float = 1.5) -> bool:
        """After a loss: look for a known appearance around the last position."""
        tpls = ([self.recent] if self.recent is not None else []) + self.templates
        if not tpls:
            return False
        # it may have turned or changed size meanwhile: try a few turns and sizes
        base = (self.angle, self.scale)
        best = (-1.0, np.zeros(2), base)
        turns = (0.0, -8.0, 8.0, -16.0, 16.0) if self.turn else (0.0,)
        sizes = (1.0, 1.1, 1 / 1.1) if self.turn else (1.0,)
        for d_ang in turns:
            for d_sc in sizes:
                self.angle, self.scale = base[0] + d_ang, base[1] * d_sc
                score, off = self._best(gray, tpls, reach)
                if score > best[0]:
                    best = (score, off, (self.angle, self.scale))
            if best[0] > 0.8:
                break
        self.angle, self.scale = base
        score, off, (ang, sc) = best
        if score < self.GOOD_SCORE:
            return False
        self.angle, self.scale = ang, sc
        self.center = np.clip(self.center + off, [0, 0], [self.w - 1, self.h - 1])
        self.pts = None
        return True

    def _drift_correction(self, gray: np.ndarray) -> float | None:
        """Pull the area back to where it looks like it did when it was marked (or
        corrected). The comparison turns and scales with the area. Returns how well
        it matched (None without a template)."""
        if not self.templates:
            return None
        score, off = self._best(gray, self.templates, 0.5)
        if score < self.TEMPLATE_MIN_SCORE:
            return score
        if 1.0 < np.hypot(*off) < 0.5 * float(np.max(self.size * self.scale)):
            pull = 1.0 if score > 0.8 else 0.5                # very sure: all the way
            self.center = np.clip(self.center + pull * off, [0, 0], [self.w - 1, self.h - 1])
        return score

    def _check_covered(self, score: float | None) -> bool:
        """After a comparison: True when the area has just been found covered."""
        if score is None:
            return False
        self.last_score = score
        if score >= self.GOOD_SCORE:
            self.bad_checks = 0
            self.unsure_checks = 0
            self.good = (self.center.copy(), self.scale, self.angle)
            return False
        self.unsure_checks += 1
        # something partly in front (a hand) can drag the points along slowly: not looking
        # right for a while AND having moved well away from the last good place = covered too
        away = float(np.hypot(*(self.center - self.good[0])))
        dragged = self.unsure_checks >= 4 and away > 0.35 * float(np.max(self.size * self.scale))
        if score < self.COVERED_SCORE or dragged:
            self.bad_checks += 2 if dragged else 1
            if self.bad_checks >= 2:
                # the points followed whatever moved over it: back to the last good place
                self.center, self.scale, self.angle = self.good[0].copy(), self.good[1], self.good[2]
                self.covered = True
                self.pts = None
                return True
        return False

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
            if (self.frames % 15 == 0 and self.pts is not None and len(self.pts) >= MIN_POINTS * 1.5
                    and self.last_score >= self.GOOD_SCORE):
                t = self._capture(cur)
                if t is not None:
                    self.recent = t
        return d

    def _step(self, prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
        self.frames += 1
        if self.covered:
            # wait at the last good place and look for it a bit around, every other frame
            if self.frames % 2 == 0 and self._reacquire(cur, 2.0):
                self.covered = False
                self.bad_checks = 0
                self.unsure_checks = 0
                self.lost = False
                self.lost_frames = 0
                self.good = (self.center.copy(), self.scale, self.angle)
                self._refill(cur)
            else:
                self.lost = True
            return np.zeros(2)
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
        if len(p0) >= 6 and not self.turn:
            # only moving: the shift on which most points agree (RANSAC on a pure shift)
            m, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=2.0,
                                                 maxIters=200, confidence=0.98)
            keep = inl.ravel().astype(bool) if inl is not None and int(inl.sum()) >= 5 else None
            shift = np.median((p1 - p0)[keep] if keep is not None else (p1 - p0), axis=0)
            self.center = self.center + shift
            if keep is not None:
                p1 = p1[keep]
            moved = True
        elif len(p0) >= 6:
            # shift + rotation + scale from the points; RANSAC ignores points that slipped
            m, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=2.0,
                                                 maxIters=200, confidence=0.98)
            if m is not None and inl is not None and int(inl.sum()) >= 5:
                keep = inl.ravel().astype(bool)
                sc = float(np.hypot(m[0, 0], m[1, 0]))
                rot = float(np.degrees(np.arctan2(m[1, 0], m[0, 0])))
                # turning and size can only be read from points that spread over the area;
                # a small cluster of points makes them wobble and the box grows or tilts
                spread = np.ptp(p0[keep], axis=0) if keep.sum() >= 2 else np.zeros(2)
                wide = bool(np.all(spread >= 0.4 * self.size * self.scale))
                if wide and 0.8 < sc < 1.25 and abs(rot) < 20:   # believable change in one frame
                    self.center = m[:, :2] @ self.center + m[:, 2]
                    # size changes slowly and stays within a sensible range
                    self.scale = float(np.clip(self.scale * sc ** 0.5, 0.6, 1.6))
                    self.angle += rot
                else:
                    self.center = self.center + np.median((p1 - p0)[keep], axis=0)
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
            score = self._drift_correction(cur)
            if score is not None and score < self.GOOD_SCORE and self.recent is not None:
                # the look may have changed slowly (light, pose): the recent look counts too
                score = max(score, self._match(cur, self.recent, 0.5)[0])
            if self._check_covered(score):
                self.lost = True
                return np.zeros(2)
            d = d + (self.center - before)
        # keep only points still inside the (slightly enlarged) area
        self.pts = p1[self.inside(p1)].reshape(-1, 1, 2).astype(np.float32)
        return d


class MultiTracker:
    """Tracks up to MAX_AREAS areas plus the background (camera)."""

    def __init__(self, boxes_px: list, shape: tuple[int, int], compensate_camera: bool = True,
                 first: np.ndarray | None = None, turn: bool = True, anchor: bool = False):
        self.turn = turn
        # anchor: areas 2 and 3 are not followed, they stay where they were marked (moving
        # only with the camera, if that is compensated) - nothing can drag them away
        self.anchor = anchor
        if not boxes_px:
            raise ValueError("mindestens ein Tracking-Bereich nötig")
        self.shape = shape
        self.areas = [AreaTracker(b, shape, first, turn) for b in boxes_px[:MAX_AREAS]]
        self.compensate = compensate_camera
        self._bg: np.ndarray | None = None     # background points, kept from frame to frame

    def centers(self) -> np.ndarray:
        return np.array([a.center for a in self.areas])

    def reset_area(self, index: int, box_px, gray: np.ndarray) -> None:
        """Put area `index` where the user says it is (a correction) and start over there.
        The appearances known so far are kept for comparing."""
        new = AreaTracker(box_px, self.shape, gray, self.turn)
        new.inherit(self.areas[index])
        self.areas[index] = new

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
        if self.anchor and len(self.areas) > 1:
            cam = self._camera(prev, cur) if self.compensate else np.zeros(2)
            moves = [self.areas[0].step(prev, cur)]
            for a in self.areas[1:]:
                a.center = np.clip(a.center + cam, [0, 0], [a.w - 1, a.h - 1])
                a.lost = False
                moves.append(cam)
        else:
            moves = [a.step(prev, cur) for a in self.areas]
        if any(a.lost for a in self.areas):
            return np.zeros(2)          # one area not followed: no reliable motion in this frame
        if len(moves) == 1:
            d = moves[0]
            if self.compensate:
                d = d - self._camera(prev, cur)
            return d
        return moves[0] - np.mean(moves[1:], axis=0)
