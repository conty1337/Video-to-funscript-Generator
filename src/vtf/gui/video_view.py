"""Video preview widget.

Mode "roi":   drag a rectangle = analysis area, right click removes it.
Mode "track": drag up to three rectangles = areas to track (body parts,
              objects), right click opens a menu (nothing is deleted at once). Marked areas can be
              moved (drag inside), resized (drag a corner) and turned (round
              handle or mouse wheel). After the analysis the tracked areas are
              drawn where they are in the current frame; grabbing one there
              puts it right (a correction). Draw mode "outline": a drag draws a
              polygon around the thing instead of a rectangle; "stroke": a drag
              draws the stroke arrow. Right click on an area opens its menu
              (role, outline, remove).
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..tracking import outline_inset
from . import theme

TRACK_COLORS = ("#e5a043", "#5fb3f0", "#9ad06b")   # area 1, 2, 3
MAX_TRACKS = 3
MIN_SIZE = 0.012         # smallest area side, as a fraction of the frame
MAX_ZOOM = 8.0
SKEL_POINT = 10           # px around a body point that still picks it
SKEL_LINE = 7            # px around a body line that still picks it
GRAB = 8                 # px around a handle that still grabs it
MENU_SLACK = 12          # px beside an area where a right click still means that area
ROLE_NAMES = ("Toy", "Bezug", "Bezug")   # what area 1, 2, 3 are
STROKE_COLOR = "#e86fd0"


def lasso_to_box(points: list[tuple[float, float]], aspect: float):
    """A drawn outline (normalized frame points) -> (box (x, y, w, h), outline in the
    box's own coordinates), or None if it is too small. `aspect` = frame w / h."""
    pts = np.asarray(points, np.float64)
    if len(pts) < 3:
        return None
    # simplify in px-like coordinates (frame height = 1000)
    px = (pts * [1000 * aspect, 1000]).astype(np.float32).reshape(-1, 1, 2)
    simple = cv2.approxPolyDP(px, 2.0, True).reshape(-1, 2) / [1000 * aspect, 1000]
    if len(simple) < 3:
        return None
    x0, y0 = simple.min(axis=0)
    x1, y1 = simple.max(axis=0)
    w, h = x1 - x0, y1 - y0
    if w < MIN_SIZE or h < MIN_SIZE or cv2.contourArea(px) < (MIN_SIZE * 1000) ** 2 * aspect:
        return None
    uv = (simple - [x0 + w / 2, y0 + h / 2]) / [w, h]
    box = (round(float(x0), 4), round(float(y0), 4), round(float(w), 4), round(float(h), 4))
    return box, tuple((round(float(u), 4), round(float(v), 4)) for u, v in uv)


class VideoView(QWidget):
    roiChanged = Signal(object)  # normalized (x, y, w, h) or None
    boxDrawn = Signal(tuple)      # mode "track": a new area (x, y, w, h), normalized
    boxRotated = Signal(int, float)  # mode "track": marked area i turned to angle (degrees)
    boxEdited = Signal(int, tuple)   # mode "track": marked area i moved/resized to (x, y, w, h, angle)
    boxCorrected = Signal(int, tuple)  # mode "track": area i grabbed elsewhere and put right
    sectionMenuRequested = Signal(QPoint)  # mode "track": right click beside the areas (global pos)
    skeletonPicked = Signal(int, object)   # body recognition shown: (person, point | (a, b)) clicked
    outlineDrawn = Signal(tuple, tuple)    # mode "track", draw mode "outline": (box, outline)
    strokeDrawn = Signal(tuple)            # draw mode "stroke": arrow (x0, y0, x1, y1), head = out
    areaMenuRequested = Signal(int, QPoint)   # right click on area i (global position)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._img: QImage | None = None
        self._roi: tuple[float, float, float, float] | None = None
        self._drag_start: QPointF | None = None
        self._drag_rect: QRectF | None = None
        self.mode = "roi"
        self._tracks: list[tuple[float, float, float, float]] = []
        self._overlay: list[tuple] | None = None
        self._rotating: int | None = None      # index of the marked area being turned
        self._edit: tuple | None = None        # ("move"|"resize", area, start data) while dragging
        self.editable = True                   # marked areas may be moved/resized/turned here
        self._correcting: list | None = None   # [overlay] saved while an area is put right
        self._countdown: int | None = None     # seconds until the script starts (player)
        self._zoom = 1.0                       # Strg+wheel: zoom in to mark small things
        self._zc = (0.5, 0.5)                  # image point (normalized) in the middle when zoomed
        self._pan_from: tuple | None = None    # middle mouse drag: (start pos, centre then)
        self._skeleton: list | None = None     # body recognition: [(points (n, 2) normalized, seen)]
        self._skel_hover = None                # (person, point | (a, b)) under the mouse
        self.draw_mode = "box"                 # what a drag draws: "box", "outline", "stroke"
        self._outlines: list = []              # per shown area: outline in its own coordinates
        self._stroke = None                    # stroke arrow of the section shown
        self._lasso: list | None = None        # widget points while an outline is drawn
        self._arrow: tuple | None = None       # (start, end) widget points while the arrow is drawn
        self.analysis_width = 320              # px: how wide the analysis picture is (outline inset)
        self.setMouseTracking(True)
        self.set_mode("roi")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "play":
            self.setToolTip("")
            self.update()
            return
        self.setToolTip("Rechteck aufziehen = Analysebereich (ROI) festlegen, Rechtsklick = ROI entfernen"
                        if mode == "roi" else
                        "Bis zu 3 Bereiche aufziehen, die verfolgt werden (z.B. die beiden Körperteile). "
                        "Das startet einen Tracking-Abschnitt an dieser Stelle. Kästchen innen ziehen = "
                        "verschieben, an einer Ecke = Größe, runder Griff oder Mausrad = drehen. "
                        "Rechtsklick aufs Kästchen: Rolle, Umrandung, entfernen; daneben: Abschnitt-Menü. "
                        "Strg+Z macht es rückgängig")
        self.update()

    def set_draw_mode(self, mode: str) -> None:
        self.draw_mode = mode if mode in ("box", "outline", "stroke") else "box"
        self._lasso = self._arrow = None
        self.update()

    def set_area_info(self, outlines=(), stroke=None) -> None:
        """Outline per area (() = rectangle) and the stroke arrow, for drawing them."""
        self._outlines = [tuple(o) for o in outlines]
        self._stroke = tuple(stroke) if stroke else None
        self.update()

    def set_tracks(self, boxes) -> None:
        self._tracks = [tuple(b) for b in boxes][:MAX_TRACKS]
        self.update()

    def tracks(self) -> list:
        return list(self._tracks)

    def set_countdown(self, seconds: int | None) -> None:
        """Big countdown over the video: seconds until the script starts (None = off)."""
        if seconds != self._countdown:
            self._countdown = seconds
            self.update()

    def set_editable(self, on: bool) -> None:
        """Whether the marked areas shown can be changed (at a section start or a correction)."""
        self.editable = bool(on)
        self.update()

    def _can_edit(self) -> bool:
        return self.mode == "track" and self._overlay is None and self.editable

    def set_skeleton(self, people) -> None:
        """Show the recognised body points and lines to pick from: [(points normalized
        (n, 2), seen (n,) bool)], None hides them."""
        self._skeleton = [(np.asarray(pts, float), np.asarray(seen, bool)) for pts, seen in people] \
            if people else None
        self._skel_hover = None
        self.update()

    def skeleton_shown(self) -> bool:
        return self._skeleton is not None

    def _skel_px(self, r: QRectF, pts: np.ndarray) -> np.ndarray:
        return np.c_[r.x() + pts[:, 0] * r.width(), r.y() + pts[:, 1] * r.height()]

    def _skel_hit(self, pos: QPointF):
        """Point (preferred) or line of the shown skeleton under pos: (person, target)."""
        if not self._skeleton:
            return None
        from ..pose import LINKS
        r = self._image_rect()
        q = np.array([pos.x(), pos.y()])
        best = None
        for k, (pts, seen) in enumerate(self._skeleton):
            px = self._skel_px(r, pts)
            idx = np.nonzero(seen)[0]
            if len(idx):
                d = np.hypot(*(px[idx] - q).T)
                j = int(np.argmin(d))
                if d[j] <= SKEL_POINT and (best is None or best[0] > -1 or d[j] < best[1]):
                    best = (-1, float(d[j]), k, int(idx[j]))
        if best is not None:
            return best[2], best[3]
        for k, (pts, seen) in enumerate(self._skeleton):
            px = self._skel_px(r, pts)
            for a, b in LINKS:
                if not (seen[a] and seen[b]):
                    continue
                pa, pb = px[a], px[b]
                ab = pb - pa
                t = float(np.clip(np.dot(q - pa, ab) / max(np.dot(ab, ab), 1e-9), 0, 1))
                d = float(np.hypot(*(pa + t * ab - q)))
                if d <= SKEL_LINE and (best is None or d < best[1]):
                    best = (0, d, k, (a, b))
        return None if best is None else (best[2], best[3])

    def _paint_skeleton(self, p: QPainter, r: QRectF) -> None:
        from ..pose import LINKS, line_name, point_name
        hover = self._skel_hover
        accent = QColor("#38d6ff")
        for k, (pts, seen) in enumerate(self._skeleton):
            px = self._skel_px(r, pts)
            for a, b in LINKS:
                if not (seen[a] and seen[b]):
                    continue
                on = hover == (k, (a, b))
                hand = a >= 91 or b >= 91
                for col, width in ((QColor(0, 0, 0, 150), 5 if not hand else 3),
                                   (accent if on else QColor(255, 255, 255, 220), 3 if not hand else 1.5)):
                    p.setPen(QPen(col, width + (3 if on else 0), Qt.SolidLine, Qt.RoundCap))
                    p.drawLine(QPointF(*px[a]), QPointF(*px[b]))
            p.setPen(QPen(QColor(0, 0, 0, 170), 1.5))
            for i in np.nonzero(seen)[0]:
                on = hover == (k, int(i))
                rad = (7 if on else 4.5) if i < 23 else (5 if on else 2.2)
                p.setBrush(accent if on else QColor("#ff5c8a" if i < 23 else "#ffd166"))
                p.drawEllipse(QPointF(*px[i]), rad, rad)
        if hover is not None:
            k, t = hover
            who = f"Person {k + 1}: " if len(self._skeleton) > 1 else ""
            text = who + (point_name(t) if isinstance(t, int) else line_name(*t))
            p.setFont(theme.mono_font(11))
            fm = p.fontMetrics()
            pts, _seen = self._skeleton[k]
            px = self._skel_px(r, pts)
            at = px[t] if isinstance(t, int) else (px[t[0]] + px[t[1]]) / 2
            box = QRectF(at[0] + 14, at[1] - 26, fm.horizontalAdvance(text) + 14, 20)
            p.fillRect(box, QColor(0, 0, 0, 190))
            p.setPen(accent)
            p.drawText(box, Qt.AlignCenter, text)
        p.setFont(theme.mono_font(11))
        hint = "Punkt oder Linie anklicken = Kästchen dort · Esc = ausblenden"
        tag = QRectF(r.x() + 8, r.bottom() - 28, p.fontMetrics().horizontalAdvance(hint) + 16, 20)
        p.fillRect(tag, QColor(0, 0, 0, 170))
        p.setPen(theme.qcolor(theme.TEXT))
        p.drawText(tag, Qt.AlignCenter, hint)

    def set_track_overlay(self, boxes) -> None:
        """Where the tracked areas are in the current frame (None = show the marked ones)."""
        self._overlay = [tuple(b) for b in boxes] if boxes is not None else None
        self.update()

    # ------------------------------------------------------------ frames
    def set_frame(self, frame_bgr: np.ndarray | None) -> None:
        if frame_bgr is None:
            self._img = None
        else:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb.shape
            self._img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.update()

    def set_roi(self, roi) -> None:
        self._roi = tuple(roi) if roi else None
        self.update()

    def roi(self):
        return self._roi

    # ------------------------------------------------------------ geometry
    def _image_rect(self) -> QRectF:
        if self._img is None:
            return QRectF(self.rect())
        iw, ih = self._img.width(), self._img.height()
        scale = min(self.width() / iw, self.height() / ih) * self._zoom
        w, h = iw * scale, ih * scale
        if self._zoom <= 1.0:
            return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        x = self.width() / 2 - self._zc[0] * w
        y = self.height() / 2 - self._zc[1] * h
        # keep the picture filling the view where it can
        x = min(0.0, max(self.width() - w, x)) if w > self.width() else (self.width() - w) / 2
        y = min(0.0, max(self.height() - h, y)) if h > self.height() else (self.height() - h) / 2
        return QRectF(x, y, w, h)

    def reset_zoom(self) -> None:
        self._zoom, self._zc = 1.0, (0.5, 0.5)
        self.update()

    def _zoom_at(self, pos: QPointF, factor: float) -> None:
        """Zoom by factor keeping the image point under pos where it is."""
        before = self._to_norm(pos)
        self._zoom = float(min(MAX_ZOOM, max(1.0, self._zoom * factor)))
        if self._zoom <= 1.0:
            self._zc = (0.5, 0.5)
        else:
            r = self._image_rect()
            # choose the centre so that `before` lands under the cursor again
            cx = before.x() - (pos.x() - self.width() / 2) / r.width()
            cy = before.y() - (pos.y() - self.height() / 2) / r.height()
            self._zc = (cx, cy)
            r = self._image_rect()                         # store the clamped centre
            self._zc = ((self.width() / 2 - r.x()) / r.width(), (self.height() / 2 - r.y()) / r.height())
        self.update()

    def _to_norm(self, p: QPointF) -> QPointF:
        r = self._image_rect()
        return QPointF(min(max((p.x() - r.x()) / r.width(), 0), 1),
                       min(max((p.y() - r.y()) / r.height(), 0), 1))

    # ------------------------------------------------------------ painting
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), theme.qcolor(theme.CARD))
        if self._img is None:
            self._paint_empty(p)
            return
        r = self._image_rect()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(r, self._img)
        if self._zoom > 1.0 and self.mode != "play":
            p.setFont(theme.mono_font(11))
            tag = QRectF(self.width() - 92, 8, 84, 20)
            p.fillRect(tag, QColor(0, 0, 0, 150))
            p.setPen(theme.qcolor(theme.TEXT))
            p.drawText(tag, Qt.AlignCenter, f"Zoom {self._zoom * 100:.0f} %")
        if self._countdown is not None:
            self._paint_countdown(p, r)
        if self.mode == "play":                     # player: just the video
            return
        if self.mode == "track":
            self._paint_tracks(p, r)
            if self._skeleton:
                self._paint_skeleton(p, r)
            return
        rect = None
        if self._drag_rect is not None:
            rect = self._drag_rect
        elif self._roi:
            x, y, w, h = self._roi
            rect = QRectF(r.x() + x * r.width(), r.y() + y * r.height(), w * r.width(), h * r.height())
        if rect is not None:
            # dim what is outside the analysed area instead of drawing a loud frame
            shade = QPainterPath()
            shade.addRect(r)
            hole = QPainterPath()
            hole.addRect(rect)
            p.fillPath(shade - hole, QColor(0, 0, 0, 140))
            p.setPen(QPen(theme.qcolor(theme.TEXT, 0.7), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            p.setFont(theme.mono_font(11))
            p.setPen(theme.qcolor(theme.TEXT_2))
            p.drawText(QRectF(rect.x() + 8, rect.y() + 4, 200, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       "Analysebereich")

    def _paint_countdown(self, p: QPainter, r: QRectF) -> None:
        d = max(56.0, min(r.width(), r.height()) * 0.28)
        c = QPointF(r.center().x(), r.center().y())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawEllipse(c, d / 2, d / 2)
        p.setPen(QPen(theme.qcolor(theme.ACCENT), 3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, d / 2 - 2, d / 2 - 2)
        f = p.font()
        f.setPixelSize(int(d * 0.45))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(c.x() - d / 2, c.y() - d / 2, d, d), Qt.AlignCenter, str(self._countdown))
        f.setPixelSize(max(12, int(d * 0.12)))
        f.setBold(False)
        p.setFont(f)
        text = "Gleich geht's los"
        tw = p.fontMetrics().horizontalAdvance(text) + 20
        th = p.fontMetrics().height() + 8
        box = QRectF(c.x() - tw / 2, c.y() + d / 2 + 8, tw, th)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(box, th / 2, th / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(box, Qt.AlignCenter, text)

    def _norm_rect(self, r: QRectF, box) -> QRectF:
        x, y, w, h = box
        return QRectF(r.x() + x * r.width(), r.y() + y * r.height(), w * r.width(), h * r.height())

    # ------------------------------------------------------------ rotated areas
    def _poly(self, r: QRectF, box) -> tuple[list[QPointF], QPointF, float]:
        """Corners (widget px) of a normalized box (x, y, w, h[, angle[, lost]])."""
        rect = self._norm_rect(r, box[:4])
        ang = math.radians(box[4]) if len(box) > 4 and box[4] is not None else 0.0
        c = rect.center()
        hw, hh = rect.width() / 2, rect.height() / 2
        ca, sa = math.cos(ang), math.sin(ang)
        pts = [QPointF(c.x() + dx * ca - dy * sa, c.y() + dx * sa + dy * ca)
               for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
        return pts, c, ang

    def _handle(self, r: QRectF, box) -> QPointF:
        """Turn handle: a little above the middle of the top edge."""
        pts, c, ang = self._poly(r, box)
        hh = self._norm_rect(r, box[:4]).height() / 2 + 16
        return QPointF(c.x() + hh * math.sin(ang), c.y() - hh * math.cos(ang))

    def _contains(self, r: QRectF, box, p: QPointF) -> bool:
        pts, c, ang = self._poly(r, box)
        rect = self._norm_rect(r, box[:4])
        dx, dy = p.x() - c.x(), p.y() - c.y()
        lx = dx * math.cos(ang) + dy * math.sin(ang)
        ly = -dx * math.sin(ang) + dy * math.cos(ang)
        return abs(lx) <= rect.width() / 2 and abs(ly) <= rect.height() / 2

    def _outline_widget(self, r: QRectF, box, outline) -> list[QPointF]:
        """Outline points (box's own coordinates) in widget px, turned with the box."""
        rect = self._norm_rect(r, box[:4])
        ang = math.radians(box[4]) if len(box) > 4 and box[4] is not None else 0.0
        c = rect.center()
        ca, sa = math.cos(ang), math.sin(ang)
        out = []
        for u, v in outline:
            dx, dy = u * rect.width(), v * rect.height()
            out.append(QPointF(c.x() + dx * ca - dy * sa, c.y() + dx * sa + dy * ca))
        return out

    def _paint_outline(self, p: QPainter, r: QRectF, box, outline, color: QColor, analysed: bool) -> None:
        """The outline, and inside along its edge the band that is left out (red hatching)."""
        pts = self._outline_widget(r, box, outline)
        path = QPainterPath()
        path.addPolygon(QPolygonF(pts))
        path.closeSubpath()
        img_w = self._img.width() if self._img is not None else 1
        aw = max(1, self.analysis_width)
        bw = box[2] * aw
        bh = box[3] * aw * (self._img.height() / max(1, img_w) if self._img is not None else 0.5625)
        band = outline_inset(bw, bh) * r.width() / aw          # analysis px -> widget px
        p.save()
        p.setClipPath(path)
        hatch = QColor("#e5534b")
        hatch.setAlpha(110)
        p.setPen(QPen(hatch, 2 * band))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.restore()
        p.setPen(QPen(color, 2 if analysed else 1.8))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _paint_stroke(self, p: QPainter, r: QRectF, line, live: bool = False) -> None:
        a = QPointF(r.x() + line[0] * r.width(), r.y() + line[1] * r.height())
        b = QPointF(r.x() + line[2] * r.width(), r.y() + line[3] * r.height())
        color = QColor(STROKE_COLOR)
        p.setPen(QPen(color, 2.5, Qt.DashLine if live else Qt.SolidLine, Qt.RoundCap))
        p.drawLine(a, b)
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        head = []
        for d in (2.6, -2.6):
            head.append(QPointF(b.x() - 14 * math.cos(ang + d / 6), b.y() - 14 * math.sin(ang + d / 6)))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([b] + head))
        p.drawEllipse(a, 3.5, 3.5)
        p.setFont(theme.mono_font(11))
        tag = QRectF(b.x() + 6, b.y() - 9, 40, 18)
        p.fillRect(tag, QColor(0, 0, 0, 150))
        p.setPen(color)
        p.drawText(tag, Qt.AlignCenter, "raus")

    def _area_near(self, pos: QPointF, slack: float) -> int | None:
        """The area under pos, or the nearest one within `slack` px of its edge."""
        boxes = self._grab_boxes()
        r = self._image_rect()
        best, best_d = None, slack
        for i in reversed(range(len(boxes))):
            if self._contains(r, boxes[i], pos):
                return i
            _pts, c, ang = self._poly(r, boxes[i])
            rect = self._norm_rect(r, boxes[i][:4])
            dx, dy = pos.x() - c.x(), pos.y() - c.y()
            lx = abs(dx * math.cos(ang) + dy * math.sin(ang)) - rect.width() / 2
            ly = abs(-dx * math.sin(ang) + dy * math.cos(ang)) - rect.height() / 2
            d = math.hypot(max(lx, 0.0), max(ly, 0.0))
            if d <= best_d:
                best, best_d = i, d
        return best

    def _paint_tracks(self, p: QPainter, r: QRectF) -> None:
        analysed = self._overlay is not None
        boxes = self._overlay if analysed else self._tracks
        p.setFont(theme.mono_font(11))
        for i, box in enumerate(boxes):
            pts, _c, _ang = self._poly(r, box)
            lost = len(box) > 5 and bool(box[5])
            color = QColor("#e5534b") if lost else QColor(TRACK_COLORS[i % len(TRACK_COLORS)])
            outline = self._outlines[i] if i < len(self._outlines) else ()
            if outline:
                self._paint_outline(p, r, box, outline, color, analysed)
                frame = QColor(color)
                frame.setAlpha(110)
                p.setPen(QPen(frame, 1, Qt.DotLine))
            else:
                p.setPen(QPen(color, 2 if analysed else 1.5, Qt.SolidLine if analysed else Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(QPolygonF(pts))
            top = min(pts, key=lambda q: q.y())
            text = f"{i + 1} {ROLE_NAMES[i % len(ROLE_NAMES)]}" + ("?" if lost else "")
            tw = p.fontMetrics().horizontalAdvance(text) + 10
            tag = QRectF(top.x() - tw / 2, max(r.y(), top.y() - 24), tw, 18)
            p.fillRect(tag, color)
            p.setPen(QColor(theme.ON_ACCENT))
            p.drawText(tag, Qt.AlignCenter, text)
            if analysed or not self.editable:                  # can be grabbed to put it right
                p.setBrush(color)
                for q in pts:
                    p.drawRect(QRectF(q.x() - 2.5, q.y() - 2.5, 5, 5))
                p.setBrush(Qt.NoBrush)
            if not analysed and self.editable:                 # turn handle and corners
                h = self._handle(r, box)
                p.setPen(QPen(color, 1))
                p.drawLine(QPointF((pts[0].x() + pts[1].x()) / 2, (pts[0].y() + pts[1].y()) / 2), h)
                p.setBrush(color)
                p.drawEllipse(h, 4.5, 4.5)
                for q in pts:
                    p.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))
        if self._drag_rect is not None:
            p.setPen(QPen(theme.qcolor(theme.TEXT, 0.8), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._drag_rect)
        if self._stroke and self._arrow is None:
            self._paint_stroke(p, r, self._stroke)
        if self._arrow is not None:
            a, b = (self._to_norm(q) for q in self._arrow)
            self._paint_stroke(p, r, (a.x(), a.y(), b.x(), b.y()), live=True)
        if self._lasso:
            p.setPen(QPen(theme.qcolor(theme.TEXT, 0.9), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF(self._lasso))
        if self.draw_mode != "box" and not self._lasso and self._arrow is None:
            hint = ("Umranden: mit gedrückter Maustaste eng um das Toy bzw. den Körperteil fahren"
                    if self.draw_mode == "outline" else
                    "Hub-Richtung: Pfeil entlang der Stoßrichtung ziehen, Spitze = raus")
            tw = p.fontMetrics().horizontalAdvance(hint) + 16
            p.fillRect(QRectF(r.x() + 8, r.bottom() - 32, tw, 24), QColor(0, 0, 0, 160))
            p.setPen(theme.qcolor(theme.TEXT))
            p.drawText(QRectF(r.x() + 16, r.bottom() - 32, tw, 24), Qt.AlignLeft | Qt.AlignVCenter, hint)
        if not boxes and self._drag_rect is None and not self._lasso:
            p.setPen(theme.qcolor(theme.TEXT))
            p.fillRect(QRectF(r.x() + 8, r.y() + 8, 330, 24), QColor(0, 0, 0, 150))
            p.drawText(QRectF(r.x() + 16, r.y() + 8, 330, 24), Qt.AlignLeft | Qt.AlignVCenter,
                       "Tracking: Bereiche aufziehen, die verfolgt werden")

    def _set_angle(self, i: int, angle: float) -> None:
        angle = (angle + 180) % 360 - 180
        b = list(self._tracks[i][:4]) + [round(angle, 1)]
        self._tracks[i] = tuple(b)
        self.update()

    def _paint_empty(self, p: QPainter):
        cx, cy = self.width() / 2, self.height() / 2
        p.drawPixmap(int(cx - 16), int(cy - 56), theme.icon_pixmap("film", theme.MUTED, 32))
        f = p.font()
        f.setPixelSize(15)
        p.setFont(f)
        p.setPen(theme.qcolor(theme.TEXT))
        p.drawText(QRectF(0, cy - 12, self.width(), 20), Qt.AlignCenter, "Kein Video geladen")
        f.setPixelSize(13)
        p.setFont(f)
        p.setPen(theme.qcolor(theme.TEXT_2))
        p.drawText(QRectF(0, cy + 12, self.width(), 20), Qt.AlignCenter,
                   "Video öffnen oder hierher ziehen")
        _keycaps(p, cx, cy + 48, ["Strg", "O"])

    # ------------------------------------------------------------ mouse
    def _grab_boxes(self) -> list:
        """The areas shown, which can be grabbed: marked ones or tracked ones."""
        if self.mode != "track" or self._img is None:
            return []
        if self._overlay is not None:
            return [tuple(b[:4]) + ((b[4] if len(b) > 4 else 0.0),) for b in self._overlay]
        return list(self._tracks)

    def _hit(self, pos: QPointF) -> tuple[str, int, int] | None:
        """What a press at pos grabs: ("rotate"|"resize"|"move", area, corner)."""
        boxes = self._grab_boxes()
        if not boxes:
            return None
        r = self._image_rect()

        def near(q: QPointF) -> bool:
            return (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2 <= GRAB * GRAB

        if self._can_edit():
            for i, box in enumerate(boxes):
                if near(self._handle(r, box)):
                    return ("rotate", i, -1)
        for i, box in enumerate(boxes):
            pts, _c, _a = self._poly(r, box)
            for k, q in enumerate(pts):
                if near(q):
                    return ("resize", i, k)
        for i in reversed(range(len(boxes))):
            if self._contains(r, boxes[i], pos):
                return ("move", i, -1)
        return None

    def _start_correction(self) -> None:
        """Grabbing an area away from a section start: edit a copy of the shown areas."""
        if self._can_edit():
            return
        self._correcting = [self._overlay]
        self._tracks = self._grab_boxes()
        self._overlay = None

    def mousePressEvent(self, ev):
        if self._img is None or self.mode == "play":
            return
        if ev.button() == Qt.MiddleButton and self._zoom > 1.0:     # move the zoomed picture
            self._pan_from = (ev.position(), self._zc)
            self.setCursor(QCursor(Qt.ClosedHandCursor))
            return
        if ev.button() == Qt.RightButton:
            if self.mode == "track":
                if self._lasso is not None or self._arrow is not None:     # cancel drawing
                    self._lasso = self._arrow = None
                    self.update()
                    return
                i = self._area_near(ev.position(), MENU_SLACK)
                if i is not None:
                    self.areaMenuRequested.emit(i, ev.globalPosition().toPoint())
                else:
                    self.sectionMenuRequested.emit(ev.globalPosition().toPoint())
                return
            else:
                self._roi = None
                self.roiChanged.emit(None)
            self.update()
        elif ev.button() == Qt.LeftButton:
            if self.mode == "track" and self._skeleton:
                pick = self._skel_hit(ev.position())
                if pick is not None:
                    self.set_skeleton(None)
                    self.skeletonPicked.emit(pick[0], pick[1])
                    return
            if self.mode == "track" and self.draw_mode == "stroke":
                self._arrow = (ev.position(), ev.position())
                return
            hit = self._hit(ev.position())
            if self.mode == "track" and self.draw_mode == "outline" and (hit is None or hit[0] == "move"):
                # drawing an outline may start inside another area (a toy in front of the body)
                self._lasso = [ev.position()]
                return
            if hit is not None:
                kind, i, corner = hit
                self._start_correction()
                if kind == "rotate":
                    self._rotating = i
                elif kind == "resize":
                    pts, _c, _a = self._poly(self._image_rect(), self._tracks[i])
                    self._edit = ("resize", i, pts[(corner + 2) % 4])     # the opposite corner stays
                else:
                    self._edit = ("move", i, (ev.position(), self._tracks[i]))
                return
            self._drag_start = ev.position()
            self._drag_rect = QRectF(self._drag_start, self._drag_start)

    def _angle(self, i: int) -> float:
        box = self._tracks[i]
        return float(box[4]) if len(box) > 4 and box[4] is not None else 0.0

    def _move_to(self, i: int, start: QPointF, orig, pos: QPointF) -> None:
        r = self._image_rect()
        x, y, w, h = orig[:4]
        dx = (pos.x() - start.x()) / r.width()
        dy = (pos.y() - start.y()) / r.height()
        cx = min(max(x + w / 2 + dx, 0.0), 1.0)                 # the centre stays in the frame
        cy = min(max(y + h / 2 + dy, 0.0), 1.0)
        self._tracks[i] = (round(cx - w / 2, 4), round(cy - h / 2, 4), w, h, self._angle(i))
        self.update()

    def _resize_to(self, i: int, fixed: QPointF, pos: QPointF) -> None:
        """Opposite corner fixed, dragged corner under the mouse, angle kept."""
        r = self._image_rect()
        ang = math.radians(self._angle(i))
        ca, sa = math.cos(ang), math.sin(ang)
        vx, vy = pos.x() - fixed.x(), pos.y() - fixed.y()
        lx, ly = vx * ca + vy * sa, -vx * sa + vy * ca           # in the area's own directions
        min_w, min_h = MIN_SIZE * r.width(), MIN_SIZE * r.height()
        lx = math.copysign(max(abs(lx), min_w), lx or 1.0)
        ly = math.copysign(max(abs(ly), min_h), ly or 1.0)
        cx = fixed.x() + (lx * ca - ly * sa) / 2
        cy = fixed.y() + (lx * sa + ly * ca) / 2
        w, h = abs(lx) / r.width(), abs(ly) / r.height()
        x = (cx - r.x()) / r.width() - w / 2
        y = (cy - r.y()) / r.height() - h / 2
        self._tracks[i] = (round(x, 4), round(y, 4), round(w, 4), round(h, 4), self._angle(i))
        self.update()

    def mouseMoveEvent(self, ev):
        if self._lasso is not None:
            q = ev.position()
            last = self._lasso[-1]
            if (q.x() - last.x()) ** 2 + (q.y() - last.y()) ** 2 >= 4:
                r = self._image_rect()
                self._lasso.append(QPointF(min(max(q.x(), r.left()), r.right()),
                                           min(max(q.y(), r.top()), r.bottom())))
                self.update()
            return
        if self._arrow is not None:
            self._arrow = (self._arrow[0], ev.position())
            self.update()
            return
        if self._pan_from is not None:
            start, (cx, cy) = self._pan_from
            r = self._image_rect()
            self._zc = (cx - (ev.position().x() - start.x()) / r.width(),
                        cy - (ev.position().y() - start.y()) / r.height())
            r = self._image_rect()
            self._zc = ((self.width() / 2 - r.x()) / r.width(), (self.height() / 2 - r.y()) / r.height())
            self.update()
            return
        if self._rotating is not None:
            r = self._image_rect()
            _pts, c, _a = self._poly(r, self._tracks[self._rotating])
            p = ev.position()
            ang = math.degrees(math.atan2(p.x() - c.x(), -(p.y() - c.y())))
            if ev.modifiers() & Qt.ShiftModifier:
                ang = round(ang / 15) * 15                       # Shift: 15-degree steps
            self._set_angle(self._rotating, ang)
            return
        if self._edit is not None:
            kind, i, data = self._edit
            if kind == "move":
                self._move_to(i, data[0], data[1], ev.position())
            else:
                self._resize_to(i, data, ev.position())
            return
        if self._drag_start is not None:
            self._drag_rect = QRectF(self._drag_start, ev.position()).normalized() & self._image_rect()
            self.update()
            return
        if self._skeleton:
            pick = self._skel_hit(ev.position())
            if pick != self._skel_hover:
                self._skel_hover = pick
                self.update()
            if pick is not None:
                self.setCursor(QCursor(Qt.PointingHandCursor))
                return
        hit = self._hit(ev.position()) if self._img is not None else None
        shape = {"rotate": Qt.PointingHandCursor, "move": Qt.SizeAllCursor}.get(hit[0]) if hit else None
        if hit and hit[0] == "resize":
            shape = Qt.SizeFDiagCursor if hit[2] in (0, 2) else Qt.SizeBDiagCursor
        if shape is None:
            self.unsetCursor()
        else:
            self.setCursor(QCursor(shape))

    def mouseReleaseEvent(self, ev):
        if self._lasso is not None:
            pts, self._lasso = self._lasso, None
            norm = [(q.x(), q.y()) for q in (self._to_norm(q) for q in pts)]
            aspect = self._img.width() / max(1, self._img.height()) if self._img is not None else 16 / 9
            got = lasso_to_box(norm, aspect)
            if got is not None:
                self.outlineDrawn.emit(got[0], got[1])
            self.update()
            return
        if self._arrow is not None:
            a, b = (self._to_norm(q) for q in self._arrow)
            self._arrow = None
            r = self._image_rect()
            if math.hypot((b.x() - a.x()) * r.width(), (b.y() - a.y()) * r.height()) >= 12:
                self.strokeDrawn.emit((round(a.x(), 4), round(a.y(), 4), round(b.x(), 4), round(b.y(), 4)))
            self.update()
            return
        if self._pan_from is not None:
            self._pan_from = None
            self.unsetCursor()
            return
        if self._rotating is not None:
            i, self._rotating = self._rotating, None
            self.boxRotated.emit(i, self._angle(i))
            return
        if self._edit is not None:
            kind, i, data = self._edit
            self._edit = None
            correcting, self._correcting = self._correcting, None
            if kind == "move" and self._tracks[i] == tuple(data[1][:4]) + (self._angle(i),):
                if correcting is not None:                       # just a click: as before
                    self._overlay = correcting[0]
                    self.update()
                return
            (self.boxEdited if correcting is None else self.boxCorrected).emit(
                i, tuple(self._tracks[i]))
            return
        if self._drag_start is None or ev.button() != Qt.LeftButton:
            return
        a, b = self._to_norm(self._drag_start), self._to_norm(ev.position())
        self._drag_start = self._drag_rect = None
        x0, x1 = sorted((a.x(), b.x()))
        y0, y1 = sorted((a.y(), b.y()))
        if x1 - x0 > MIN_SIZE and y1 - y0 > MIN_SIZE:
            box = (x0, y0, x1 - x0, y1 - y0)
            if self.mode == "track":
                self.boxDrawn.emit(box)
            else:
                self._roi = box
                self.roiChanged.emit(self._roi)
        self.update()

    def wheelEvent(self, ev):
        """Strg+wheel zooms the picture (to mark small things); the wheel over a
        marked area turns it (5 degrees, with Shift 1 degree)."""
        if ev.modifiers() & Qt.ControlModifier and self._img is not None and self.mode != "play":
            d = ev.angleDelta().y() or ev.angleDelta().x()
            if d:
                self._zoom_at(ev.position(), 1.25 ** (d / 120))
            ev.accept()
            return
        boxes = self._grab_boxes()
        if boxes:
            r = self._image_rect()
            for i, box in enumerate(boxes):
                if self._contains(r, box, ev.position()):
                    d = ev.angleDelta().y() or ev.angleDelta().x()
                    if not d:
                        return
                    step = 1 if ev.modifiers() & Qt.ShiftModifier else 5
                    cur = box[4] if len(box) > 4 else 0.0
                    ang = (cur + (step if d > 0 else -step) + 180) % 360 - 180
                    if self._can_edit():
                        self._set_angle(i, ang)
                        self.boxRotated.emit(i, float(self._tracks[i][4]))
                    else:                                        # away from a start: a correction
                        self.boxCorrected.emit(i, tuple(box[:4]) + (round(ang, 1),))
                    ev.accept()
                    return
        super().wheelEvent(ev)


def _keycaps(p: QPainter, cx: float, cy: float, keys: list[str]) -> None:
    p.setFont(theme.mono_font(11))
    fm = p.fontMetrics()
    widths = [max(20, fm.horizontalAdvance(k) + 12) for k in keys]
    total = sum(widths) + 4 * (len(keys) - 1)
    x = cx - total / 2
    for k, w in zip(keys, widths):
        r = QRectF(x, cy - 10, w, 20)
        p.setPen(QPen(theme.qcolor(theme.TEXT, 0.12), 1))
        p.setBrush(theme.qcolor(theme.RAISED))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(theme.qcolor(theme.TEXT_2))
        p.drawText(r, Qt.AlignCenter, k)
        x += w + 4
