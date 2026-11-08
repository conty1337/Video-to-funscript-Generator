"""Video preview widget.

Mode "roi":   drag a rectangle = analysis area, right click removes it.
Mode "track": drag up to three rectangles = areas to track (body parts,
              objects), right click removes them all. After the analysis the
              tracked areas are drawn where they are in the current frame.
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme


TRACK_COLORS = ("#e5a043", "#5fb3f0", "#9ad06b")   # area 1, 2, 3
MAX_TRACKS = 3


class VideoView(QWidget):
    roiChanged = Signal(object)  # normalized (x, y, w, h) or None
    tracksChanged = Signal(list)  # normalized boxes [(x, y, w, h), ...] in mode "track"

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
        self._overlay: list[tuple[float, float, float, float]] | None = None
        self.setMouseTracking(True)
        self.set_mode("roi")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.setToolTip("Rechteck aufziehen = Analysebereich (ROI) festlegen, Rechtsklick = ROI entfernen"
                        if mode == "roi" else
                        "Bis zu 3 Bereiche aufziehen, die verfolgt werden (z.B. Hüfte, Oberschenkel). "
                        "Rechtsklick entfernt alle")
        self.update()

    def set_tracks(self, boxes) -> None:
        self._tracks = [tuple(b) for b in boxes][:MAX_TRACKS]
        self.update()

    def tracks(self) -> list:
        return list(self._tracks)

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

    def _paint_tracks(self, p: QPainter, r: QRectF) -> None:
        boxes = self._overlay if self._overlay is not None else self._tracks
        p.setFont(theme.mono_font(11))
        for i, box in enumerate(boxes):
            rect = self._norm_rect(r, box)
            color = QColor(TRACK_COLORS[i % len(TRACK_COLORS)])
            p.setPen(QPen(color, 2 if self._overlay is not None else 1.5,
                          Qt.SolidLine if self._overlay is not None else Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            tag = QRectF(rect.x(), rect.y() - 18 if rect.y() - 18 >= r.y() else rect.y(), 22, 18)
            p.fillRect(tag, color)
            p.setPen(QColor(theme.ON_ACCENT))
            p.drawText(tag, Qt.AlignCenter, str(i + 1))
        if self._drag_rect is not None:
            p.setPen(QPen(theme.qcolor(theme.TEXT, 0.8), 1, Qt.DashLine))
            p.drawRect(self._drag_rect)
        if not boxes and self._drag_rect is None:
            p.setPen(theme.qcolor(theme.TEXT))
            p.fillRect(QRectF(r.x() + 8, r.y() + 8, 330, 24), QColor(0, 0, 0, 150))
            p.drawText(QRectF(r.x() + 16, r.y() + 8, 330, 24), Qt.AlignLeft | Qt.AlignVCenter,
                       "Tracking: Bereiche aufziehen, die verfolgt werden")

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
    def mousePressEvent(self, ev):
        if self._img is None:
            return
        if ev.button() == Qt.RightButton:
            if self.mode == "track":
                self._tracks = []
                self._overlay = None
                self.tracksChanged.emit([])
            else:
                self._roi = None
                self.roiChanged.emit(None)
            self.update()
        elif ev.button() == Qt.LeftButton:
            self._drag_start = ev.position()
            self._drag_rect = QRectF(self._drag_start, self._drag_start)

    def mouseMoveEvent(self, ev):
        if self._drag_start is not None:
            self._drag_rect = QRectF(self._drag_start, ev.position()).normalized() & self._image_rect()
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag_start is None or ev.button() != Qt.LeftButton:
            return
        a, b = self._to_norm(self._drag_start), self._to_norm(ev.position())
        self._drag_start = self._drag_rect = None
        x0, x1 = sorted((a.x(), b.x()))
        y0, y1 = sorted((a.y(), b.y()))
        if x1 - x0 > 0.03 and y1 - y0 > 0.03:
            box = (x0, y0, x1 - x0, y1 - y0)
            if self.mode == "track":
                if len(self._tracks) >= MAX_TRACKS:
                    self._tracks = self._tracks[1:]
                self._tracks.append(box)
                self._overlay = None
                self.tracksChanged.emit(list(self._tracks))
            else:
                self._roi = box
                self.roiChanged.emit(self._roi)
        self.update()


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
