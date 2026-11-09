"""Video timeline: click anywhere to jump there and keep dragging.

A plain QSlider only moves one page per click, so the handle had to be hit
exactly. Here a left click moves the handle under the cursor first and then
hands the press to QSlider, which sees a press on the handle and starts a drag.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class TimelineSlider(QSlider):
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.wheel_step = 30            # frames per wheel notch (set to fps = 1 s)
        self._wheel_acc = 0
        self.setCursor(Qt.PointingHandCursor)
        self.marks: list[int] = []      # bookmarks (frame numbers), drawn as small ticks

    def set_marks(self, frames) -> None:
        self.marks = sorted(int(f) for f in frames)
        self.update()

    def x_of(self, v: int) -> float:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        lo = groove.x() + handle.width() / 2
        span = max(1.0, groove.width() - handle.width())
        rng = max(1, self.maximum() - self.minimum())
        return lo + (v - self.minimum()) / rng * span

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self.marks:
            return
        p = QPainter(self)
        p.setPen(QPen(QColor(190, 165, 240), 2))
        for f in self.marks:
            x = self.x_of(f)
            p.drawLine(QPointF(x, 1), QPointF(x, 6))

    def value_at(self, x: float) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        lo = groove.x() + handle.width() / 2
        span = max(1.0, groove.width() - handle.width())
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                              round(x - lo), int(span), opt.upsideDown)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.isEnabled():
            v = self.value_at(ev.position().x())
            if v != self.value():
                self.setValue(v)
                self.sliderMoved.emit(v)
        super().mousePressEvent(ev)

    def wheelEvent(self, ev):
        d = ev.angleDelta().y() or ev.angleDelta().x()
        if not d or not self.isEnabled():
            return
        # a mouse wheel sends 120 per notch, a touchpad many small deltas: add them up
        self._wheel_acc += d
        notches = int(self._wheel_acc / 120)
        ev.accept()
        if not notches:
            return
        self._wheel_acc -= notches * 120
        v = self.value() + notches * self.wheel_step
        v = max(self.minimum(), min(self.maximum(), v))
        if v != self.value():
            self.setValue(v)
            self.sliderMoved.emit(v)
