"""Video preview widget with ROI selection (drag a rectangle)."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class VideoView(QWidget):
    roiChanged = Signal(object)  # normalized (x, y, w, h) or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._img: QImage | None = None
        self._roi: tuple[float, float, float, float] | None = None
        self._drag_start: QPointF | None = None
        self._drag_rect: QRectF | None = None
        self.setMouseTracking(True)
        self.setToolTip("Rechteck aufziehen = Analysebereich (ROI) festlegen, Rechtsklick = ROI entfernen")

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
        p.fillRect(self.rect(), QColor(20, 20, 24))
        if self._img is None:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, "Kein Video geladen\n(Datei → Video öffnen)")
            return
        r = self._image_rect()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(r, self._img)
        rect = None
        if self._drag_rect is not None:
            rect = self._drag_rect
        elif self._roi:
            x, y, w, h = self._roi
            rect = QRectF(r.x() + x * r.width(), r.y() + y * r.height(), w * r.width(), h * r.height())
        if rect is not None:
            p.setPen(QPen(QColor(0, 220, 120), 2, Qt.DashLine))
            p.drawRect(rect)

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, ev):
        if self._img is None:
            return
        if ev.button() == Qt.RightButton:
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
            self._roi = (x0, y0, x1 - x0, y1 - y0)
            self.roiChanged.emit(self._roi)
        self.update()
