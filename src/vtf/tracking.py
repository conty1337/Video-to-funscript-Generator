"""Tracking of marked areas (body parts, objects) with many points each.

Each area is followed with sparse Lucas-Kanade optical flow on up to 60
feature points inside it. A forward-backward check drops points that slipped
off; the area moves by the median of the remaining points, and lost points are
searched again inside the area. Points outside all areas measure the camera
motion.

An area can also be an outline (a polygon drawn around the thing, e.g. a toy):
then only points inside it, a few pixels away from its edge, are followed.
Comparing its look still uses the whole rectangle around it: measured on real
videos, a plain or shiny toy alone looks too much like a shadow, the things
next to it make the look unique.

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
LOOK_CHECK_POINTS = 20   # fewer points than this: every frame also check by its look
LOOK_MARGIN = 0.1        # ... and go there when the look is this much more sure
LOOK_CHECK_OFF = 0.1     # ... but only when the points moved this far (box sizes) from the expected move
LOST_REACH_FRAC = 0.1    # after a loss look at least this part of the picture width around
FEW_POINTS = 6           # fewer than this: follow the area by its look (template), not by points
FB_MAX_ERR = 1.0         # px, forward-backward consistency
MAX_AREAS = 3
OUTLINE_INSET = 0.06     # outline: points stay this part of the area's smaller side inside
OUTLINE_INSET_PX = (2, 6)  # ... but at least / at most this many px of the analysis picture


def outline_inset(w: float, h: float) -> float:
    """How far (px) inside an outline of an area w x h px its points are taken."""
    lo, hi = OUTLINE_INSET_PX
    return float(np.clip(OUTLINE_INSET * min(w, h), lo, hi))


def _fill_inside(shape: tuple[int, int], poly: np.ndarray, inset: float) -> np.ndarray:
    """Mask of the polygon, shrunk by `inset` px; if that leaves too little, less shrunk."""
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [np.round(poly).astype(np.int32)], 255)
    full = int(np.count_nonzero(m))
    k = int(round(inset))
    while k > 0:
        e = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
        if np.count_nonzero(e) >= max(20, 0.25 * full):
            return e
        k -= 1
    return m


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


def _shifted(img: np.ndarray, gx: int, gy: int) -> np.ndarray:
    """The image moved by (-gx, -gy): what is at (x+gx, y+gy) comes to (x, y); edges repeat."""
    h, w = img.shape[:2]
    pad = max(abs(gx), abs(gy))
    big = np.pad(img, pad, mode="edge")
    return np.ascontiguousarray(big[pad + gy:pad + gy + h, pad + gx:pad + gx + w])


def _lk(prev: np.ndarray, cur: np.ndarray, pts: np.ndarray,
        guess: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Track points prev -> cur; returns new points and a mask of the reliable ones.
    ``guess``: where the points probably moved (px). Fast motion goes beyond the search
    window; the current frame is moved back by the guess first, so the plain search only
    has to find the rest (the guess is added again afterwards)."""
    g = np.zeros(2, np.int64)
    if guess is not None:
        g = np.round(np.asarray(guess, np.float64)).astype(np.int64)
    if g.any():
        cur = _shifted(cur, int(g[0]), int(g[1]))
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, **LK_PARAMS)
    back, st2, _ = cv2.calcOpticalFlowPyrLK(cur, prev, nxt, None, **LK_PARAMS)
    err = np.linalg.norm((pts - back).reshape(-1, 2), axis=1)
    good = (st.ravel() == 1) & (st2.ravel() == 1) & (err < FB_MAX_ERR)
    if g.any():
        nxt = (nxt + g.astype(np.float32).reshape(1, 1, 2)).astype(np.float32)
    return nxt, good


def _subpixel(res: np.ndarray, x: int, y: int) -> tuple[float, float]:
    """Where between the pixels the best match really is: the top of a parabola through the
    best score and its neighbours, per direction (0 at the border)."""
    def peak(a: float, b: float, c: float) -> float:
        den = a - 2 * b + c
        return max(-0.5, min(0.5, 0.5 * (a - c) / den)) if den < 0 else 0.0

    h, w = res.shape
    fx = peak(res[y, x - 1], res[y, x], res[y, x + 1]) if 0 < x < w - 1 else 0.0
    fy = peak(res[y - 1, x], res[y, x], res[y + 1, x]) if 0 < y < h - 1 else 0.0
    return float(fx), float(fy)


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
    PREDICT = 1.0                 # part of the last move where the search in the next frame starts
    OUT_VISIBLE = 0.4             # less of the area than this in the frame: it has left the frame
    BLUR_SPEED = 0.12             # faster (part of the area per frame): its look is blurred
    EDGE_VISIBLE = 0.85           # less than this: partly out, its look cannot be compared

    def __init__(self, box, shape: tuple[int, int], first: np.ndarray | None = None,
                 turn: bool = True, outline=None):
        self.h, self.w = shape
        # outline: polygon in the area's own coordinates (-0.5..0.5 of its width/height,
        # turned with it); None = the whole rectangle
        o = None if outline is None or len(outline) < 3 else np.asarray(outline, np.float64).reshape(-1, 2)
        self.outline = o
        # turn = follow turning and size too; off: the area only moves (machines, rigid things)
        self.turn = turn
        x, y, bw, bh = box[:4]
        self.size = np.array([max(8.0, bw), max(8.0, bh)], np.float64)
        self.center = np.array([x + bw / 2, y + bh / 2], np.float64)
        self.angle = float(box[4]) if len(box) > 4 else 0.0
        self.scale = 1.0
        self.vel = np.zeros(2)          # last move per frame: where to look first in the next one
        self.pts: np.ndarray | None = None
        self.lost = False
        self.outside = False            # mostly out of the frame: not followed, waiting at the edge
        self.frames = 0
        self.good_frame = 0
        self.slipped = 0                # set when found covered: frames before that were wrong too
        self.search_reach = 2.0         # while covered: look this far around (box sizes)
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
        self._set_good()
        if first is not None:
            t = self._capture(first)
            if t is not None:
                self.templates.append(t)
                self.recent = t

    def _set_good(self) -> None:
        """Remember this place as the last one where the area surely looked right."""
        self.good = (self.center.copy(), self.scale, self.angle)
        self.good_frame = self.frames

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

    def _keep_near(self, c: np.ndarray) -> np.ndarray:
        """The centre may leave the frame by up to half the area (the visible rest is
        still followed), not further."""
        half = self.size * self.scale / 2
        return np.clip(c, -half, [self.w - 1 + half[0], self.h - 1 + half[1]])

    def visible(self) -> float:
        """Part of the area that lies inside the frame (0..1)."""
        c = self.corners().astype(np.float32)
        full = float(np.prod(self.size * self.scale))
        if full <= 0:
            return 0.0
        frame = np.float32([[0, 0], [self.w, 0], [self.w, self.h], [0, self.h]])
        inter, _poly = cv2.intersectConvexConvex(c, frame)
        return float(np.clip(inter / full, 0.0, 1.0))

    def outline_px(self) -> np.ndarray | None:
        """The outline in image px (None without one)."""
        if self.outline is None:
            return None
        a = np.radians(self.angle)
        c, s = np.cos(a), np.sin(a)
        loc = self.outline * (self.size * self.scale)
        return loc @ np.array([[c, s], [-s, c]]) + self.center

    def inset(self) -> float:
        w, h = self.size * self.scale
        return outline_inset(w, h)

    def _local_mask(self, rect) -> np.ndarray:
        x0, y0, x1, y1 = rect
        if self.outline is not None:
            return _fill_inside((y1 - y0, x1 - x0), self.outline_px() - [x0, y0], self.inset())
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillConvexPoly(m, np.round(self.corners() - [x0, y0]).astype(np.int32), 255)
        return m

    def mask(self) -> np.ndarray:
        m = np.zeros((self.h, self.w), np.uint8)
        cv2.fillConvexPoly(m, np.round(self.corners()).astype(np.int32), 255)
        return m

    def inside(self, pts: np.ndarray, margin: float = 1.25) -> np.ndarray:
        """Which points lie in the (slightly enlarged) rotated area. With an outline:
        inside it (1 px slack), points that slid onto what lies around it are dropped."""
        if self.outline is not None:
            poly = self.outline_px().astype(np.float32).reshape(-1, 1, 2)
            p = np.asarray(pts, np.float32).reshape(-1, 2)
            return np.array([cv2.pointPolygonTest(poly, (float(x), float(y)), True) >= -1.0
                             for x, y in p], bool)
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
        fx, fy = _subpixel(res, bx, by)
        local = np.array([bx + fx + tw / 2 - area.shape[1] / 2, by + fy + th / 2 - area.shape[0] / 2])
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

    def _lost_reach(self) -> float:
        """How far (box sizes) to look after losing it: 1.5 boxes, but a small box on
        something fast (a machine) can be several of its sizes away by then, so at least
        a tenth of the picture."""
        box = float(np.max(self.size * self.scale))
        return min(4.0, max(1.5, LOST_REACH_FRAC * self.w / max(box, 1.0)))

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
        self.center = self._keep_near(self.center + off)
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
        dist = float(np.hypot(*off))
        if 1.0 < dist < 0.5 * float(np.max(self.size * self.scale)):
            pull = 1.0 if score > 0.8 else 0.5                # very sure: all the way
            self.center = self._keep_near(self.center + pull * off)
        elif dist >= 0.5 * float(np.max(self.size * self.scale)):
            if score > 0.8:
                # clearly there, a good bit away (the points slid off it): go there
                self.center = self._keep_near(self.center + off)
            else:
                # it looks right only somewhere else: that says nothing good about here
                return min(score, self.GOOD_SCORE - 0.05)
        return score

    def cover(self) -> None:
        """The points followed whatever moved over the area: back to the last good place,
        wait there until it shows again."""
        self.center, self.scale, self.angle = self.good[0].copy(), self.good[1], self.good[2]
        self.covered = True
        self.lost = True
        # the frames since then followed the wrong thing too: tell the analysis
        self.slipped = max(0, self.frames - self.good_frame - 1)
        self.pts = None

    def _check_covered(self, score: float | None) -> bool:
        """After a comparison: True when the area has just been found covered."""
        if score is None:
            return False
        self.last_score = score
        if score >= self.GOOD_SCORE:
            self.bad_checks = 0
            self.unsure_checks = 0
            self._set_good()
            return False
        if float(np.hypot(*self.vel)) > self.BLUR_SPEED * float(np.max(self.size * self.scale)):
            # moving fast: motion blur makes it look different, that says nothing about covering
            return False
        self.unsure_checks += 1
        # something partly in front (a hand) can drag the points along slowly: not looking
        # right for a while AND having moved well away from the last good place = covered too
        away = float(np.hypot(*(self.center - self.good[0])))
        dragged = self.unsure_checks >= 4 and away > 0.35 * float(np.max(self.size * self.scale))
        if score < self.COVERED_SCORE or dragged:
            self.bad_checks += 2 if dragged else 1
            if self.bad_checks >= 2:
                self.cover()
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
        # predict the next move from this one (strokes change speed smoothly); unknown when lost
        self.vel = np.zeros(2) if self.lost else self.PREDICT * np.asarray(d, np.float64)
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

    def _look_tpls(self) -> list:
        return ([self.recent] if self.recent is not None else []) + self.templates

    def _find_by_look(self, cur: np.ndarray, start: np.ndarray) -> tuple[float, np.ndarray, float]:
        """Best place by its look around `start` (and the size it has there):
        (score, centre, scale). Leaves the area as it was."""
        old_c, base = self.center.copy(), self.scale
        box = float(np.max(self.size * self.scale))
        reach = min(2.0, 0.6 + float(np.hypot(*self.vel)) / max(box, 1.0))
        self.center = np.asarray(start, np.float64)
        best = (-1.0, self.center.copy(), base)
        # it may grow or shrink from frame to frame (coming closer to the camera)
        for f in ((1.0, 1.08, 1 / 1.08) if self.turn else (1.0,)):
            self.scale = float(np.clip(base * f, 0.4, 2.5))
            score, off = self._best(cur, self._look_tpls(), reach)
            if score > best[0]:
                best = (score, self.center + off, self.scale)
        self.center, self.scale = old_c, base
        return best

    def _step_by_look(self, cur: np.ndarray) -> np.ndarray:
        """Too few corner points to follow (a small or plain area, e.g. a dark ring on a
        toy): find it by its look instead, around where the last move points."""
        old = self.center.copy()
        if not self._look_tpls():
            self.lost = True
            return np.zeros(2)
        score, c, sc = self._find_by_look(cur, old + self.vel)
        if score < self.GOOD_SCORE or np.hypot(*(c - old - self.vel)) > self.max_jump():
            self.lost = True
            return np.zeros(2)
        self.scale = sc
        self.center = self._keep_near(c)
        self.lost = False
        self.last_score = score
        return self.center - old

    def _leave(self) -> None:
        """The area has left the frame. It waits at the edge, moved inside so that a
        comparison sees all of it, until it comes back."""
        self.outside = True
        self.lost = True
        self.covered = False
        self.pts = None
        half = self.size * self.scale / 2
        lo = half + 1
        hi = np.array([self.w - 1, self.h - 1]) - half - 1
        self.center = np.where(lo <= hi, np.clip(self.center, lo, np.maximum(lo, hi)), self.center)

    def _wait_outside(self, cur: np.ndarray) -> np.ndarray:
        """Out of the frame: look for it at the edge, a bit around where it left."""
        if self._reacquire(cur, 1.0):
            self.outside = False
            self.lost = False
            self.lost_frames = 0
            self.bad_checks = 0
            self.unsure_checks = 0
            self._set_good()
            self._refill(cur)
        else:
            self.lost = True
        return np.zeros(2)

    def _step(self, prev: np.ndarray, cur: np.ndarray) -> np.ndarray:
        self.frames += 1
        if self.covered:
            # wait at the last good place and look for it a bit around, every other frame
            if self.frames % 2 == 0 and self._reacquire(cur, self.search_reach):
                self.covered = False
                self.search_reach = 2.0
                self.bad_checks = 0
                self.unsure_checks = 0
                self.lost = False
                self.lost_frames = 0
                self._set_good()
                self._refill(cur)
            else:
                self.lost = True
            return np.zeros(2)
        if self.outside:
            return self._wait_outside(cur)
        if self.lost_frames >= 2 and self._reacquire(cur, self._lost_reach()):
            # found again: position fixed, but no motion from this frame (unknown path)
            self.lost_frames = 0
            self.lost = False
            self._refill(cur)
            return np.zeros(2)
        if self.pts is None or len(self.pts) < MIN_POINTS:
            self._refill(prev)
        if self.pts is None or len(self.pts) < FEW_POINTS:
            return self._step_by_look(cur)
        nxt, good = _lk(prev, cur, self.pts, self.vel)
        if good.sum() < 3 and len(self.pts) < MIN_POINTS:
            # the few points did not hold (fast, blurred): try by its look before giving up
            self.pts = None
            d = self._step_by_look(cur)
            if not self.lost or self.visible() >= self.EDGE_VISIBLE:
                return d
        if good.sum() < 3:
            self.pts = None
            self.lost = True
            if self.visible() < self.EDGE_VISIBLE:
                self._leave()
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
        if (len(p0) < LOOK_CHECK_POINTS and self._look_tpls() and self.frames > 1
                and np.hypot(*(self.center - old - self.vel)) > LOOK_CHECK_OFF * float(np.max(self.size * self.scale))):
            # few points (often on its edge) and a move other than expected: they may have
            # stuck to the background next to it. Its look decides when it is clearly
            # better somewhere else.
            here = self._best(cur, self._look_tpls(), 0.15)[0]
            score, c, sc = self._find_by_look(cur, old + self.vel)
            if score >= self.GOOD_SCORE and score > here + LOOK_MARGIN:
                self.center, self.scale = c, sc
                p1 = p1[:0]                     # points start anew around the found place
        d = self.center - old
        # impossible jump: do not believe it. Measured against the expected move, so a
        # fast stroke that keeps its speed is no jump
        if np.hypot(*(d - self.vel)) > self.max_jump():
            self.center = old
            self.pts = None
            self.lost = True
            return np.zeros(2)
        self.center = self._keep_near(self.center)
        d = self.center - old
        self.lost = False
        vis = self.visible()
        if vis < self.OUT_VISIBLE:
            # gone over the edge: what is left is too little to follow
            self._leave()
            return np.zeros(2)
        # partly out of the frame: its look cannot be compared (half of it is missing)
        if self.frames % self.TEMPLATE_EVERY == 0 and vis >= self.EDGE_VISIBLE:
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
                 first: np.ndarray | None = None, turn: bool = True, anchor: bool = False,
                 outlines=()):
        self.turn = turn
        # anchor: areas 2 and 3 are not followed, they stay where they were marked (moving
        # only with the camera, if that is compensated) - nothing can drag them away
        self.anchor = anchor
        if not boxes_px:
            raise ValueError("mindestens ein Tracking-Bereich nötig")
        self.shape = shape
        self.outlines = [outlines[i] if i < len(outlines) and outlines[i] else None
                         for i in range(min(len(boxes_px), MAX_AREAS))]
        self.areas = [AreaTracker(b, shape, first, turn, o) for b, o in zip(boxes_px[:MAX_AREAS], self.outlines)]
        self.compensate = compensate_camera
        self._bg: np.ndarray | None = None     # background points, kept from frame to frame

    def centers(self) -> np.ndarray:
        return np.array([a.center for a in self.areas])

    def reset_area(self, index: int, box_px, gray: np.ndarray) -> None:
        """Put area `index` where the user says it is (a correction) and start over there.
        The appearances known so far are kept for comparing."""
        new = AreaTracker(box_px, self.shape, gray, self.turn, self.outlines[index])
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
