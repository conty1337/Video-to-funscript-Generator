"""Video preview widget.

Mode "roi":   drag a rectangle = analysis area, right click removes it.
Mode "track": drag up to three rectangles = areas to track (body parts,
              objects), right click removes the section. Marked areas can be
              moved (drag inside), resized (drag a corner) and turned (round
              handle or mouse wheel). After the analysis the tracked areas are
              drawn where they are in the current frame.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
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

from . import theme

TRACK_COLORS = ("#e5a043", "#5fb3f0", "#9ad06b")   # area 1, 2, 3
MAX_TRACKS = 3
MIN_SIZE = 0.03          # smallest area side, as a fraction of the frame
GRAB = 8                 # px around a handle that still grabs it


class VideoView(QWidget):
    roiChanged = Signal(object)  # normalized (x, y, w, h) or None
    boxDrawn = Signal(tuple)      # mode "track": a new area (x, y, w, h), normalized
    boxRotated = Signal(int, float)  # mode "track": marked area i turned to angle (degrees)
    boxEdited = Signal(int, tuple)   # mode "track": marked area i moved/resized to (x, y, w, h, angle)
    tracksCleared = Signal()      # mode "track": right click

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
        self.setMouseTracking(True)
        self.set_mode("roi")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.setToolTip("Rechteck aufziehen = Analysebereich (ROI) festlegen, Rechtsklick = ROI entfernen"
                        if mode == "roi" else
                        "Bis zu 3 Bereiche aufziehen, die verfolgt werden (z.B. die beiden Körperteile). "
                        "Das startet einen Tracking-Abschnitt an dieser Stelle. Kästchen innen ziehen = "
                        "verschieben, an einer Ecke = Größe, runder Griff oder Mausrad = drehen. "
                        "Rechtsklick löscht den Abschnitt an dieser Stelle")
        self.update()

    def set_tracks(self, boxes) -> None:
        self._tracks = [tuple(b) for b in boxes][:MAX_TRACKS]
        self.update()

    def tracks(self) -> list:
        return list(self._tracks)

    def set_editable(self, on: bool) -> None:
        """Whether the marked areas shown can be changed (at a section start or a correction)."""
        self.editable = bool(on)
        self.update()

    def _can_edit(self) -> bool:
        return self.mode == "track" and self._overlay is None and self.editable

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
        scale = min(self.width() / iw, self.height() / ih)
        w, h = iw * scale, ih * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

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
        if self.mode == "track":
            self._paint_tracks(p, r)
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

    def _paint_tracks(self, p: QPainter, r: QRectF) -> None:
        analysed = self._overlay is not None
        boxes = self._overlay if analysed else self._tracks
        p.setFont(theme.mono_font(11))
        for i, box in enumerate(boxes):
            pts, _c, _ang = self._poly(r, box)
            lost = len(box) > 5 and bool(box[5])
            color = QColor("#e5534b") if lost else QColor(TRACK_COLORS[i % len(TRACK_COLORS)])
            p.setPen(QPen(color, 2 if analysed else 1.5, Qt.SolidLine if analysed else Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(QPolygonF(pts))
            top = min(pts, key=lambda q: q.y())
            tag = QRectF(top.x() - 11, max(r.y(), top.y() - 24), 22 if not lost else 30, 18)
            p.fillRect(tag, color)
            p.setPen(QColor(theme.ON_ACCENT))
            p.drawText(tag, Qt.AlignCenter, f"{i + 1}" + ("?" if lost else ""))
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
        if not boxes and self._drag_rect is None:
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
    def _hit(self, pos: QPointF) -> tuple[str, int, int] | None:
        """What a press at pos grabs: ("rotate"|"resize"|"move", area, corner)."""
        if not self._can_edit():
            return None
        r = self._image_rect()

        def near(q: QPointF) -> bool:
            return (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2 <= GRAB * GRAB

        for i, box in enumerate(self._tracks):
            if near(self._handle(r, box)):
                return ("rotate", i, -1)
        for i, box in enumerate(self._tracks):
            pts, _c, _a = self._poly(r, box)
            for k, q in enumerate(pts):
                if near(q):
                    return ("resize", i, k)
        for i in reversed(range(len(self._tracks))):
            if self._contains(r, self._tracks[i], pos):
                return ("move", i, -1)
        return None

    def mousePressEvent(self, ev):
        if self._img is None:
            return
        if ev.button() == Qt.RightButton:
            if self.mode == "track":
                self.tracksCleared.emit()
            else:
                self._roi = None
                self.roiChanged.emit(None)
            self.update()
        elif ev.button() == Qt.LeftButton:
            hit = self._hit(ev.position())
            if hit is not None:
                kind, i, corner = hit
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
        hit = self._hit(ev.position()) if self._img is not None else None
        shape = {"rotate": Qt.PointingHandCursor, "move": Qt.SizeAllCursor}.get(hit[0]) if hit else None
        if hit and hit[0] == "resize":
            shape = Qt.SizeFDiagCursor if hit[2] in (0, 2) else Qt.SizeBDiagCursor
        if shape is None:
            self.unsetCursor()
        else:
            self.setCursor(QCursor(shape))

    def mouseReleaseEvent(self, ev):
        if self._rotating is not None:
            i, self._rotating = self._rotating, None
            self.boxRotated.emit(i, self._angle(i))
            return
        if self._edit is not None:
            kind, i, data = self._edit
            self._edit = None
            if kind == "move" and self._tracks[i] == tuple(data[1][:4]) + (self._angle(i),):
                return                                           # just a click
            self.boxEdited.emit(i, tuple(self._tracks[i]))
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
        """Mouse wheel over a marked area turns it (5 degrees, with Shift 1 degree)."""
        if self._can_edit() and self._tracks:
            r = self._image_rect()
            for i, box in enumerate(self._tracks):
                if self._contains(r, box, ev.position()):
                    d = ev.angleDelta().y() or ev.angleDelta().x()
                    if not d:
                        return
                    step = 1 if ev.modifiers() & Qt.ShiftModifier else 5
                    cur = box[4] if len(box) > 4 else 0.0
                    self._set_angle(i, cur + (step if d > 0 else -step))
                    self.boxRotated.emit(i, float(self._tracks[i][4]))
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
