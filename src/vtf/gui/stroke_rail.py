"""Stroke rail: the script position at the playhead, drawn like the linear
actuator it will drive. The product's signature detail."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

THUMB_H = 12
PAD_T, PAD_B = 24, 32


class StrokeRail(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(48)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._pos: float | None = None
        self.setToolTip("Script-Position an der aktuellen Stelle (0–100)")

    def set_position(self, pos: float | None) -> None:
        if pos != self._pos:
            self._pos = pos
            self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), theme.qcolor(theme.CARD))
        mono = theme.mono_font(11)
        p.setFont(mono)
        cx = self.width() / 2
        top, bottom = PAD_T, self.height() - PAD_B
        track = QRectF(cx - 2, top, 4, bottom - top)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(theme.RAISED))
        p.drawRoundedRect(track, 2, 2)
        # scale ticks every 25
        p.setBrush(theme.qcolor(theme.MUTED, 0.5))
        for v in (0, 25, 50, 75, 100):
            y = bottom - v / 100 * (bottom - top)
            p.drawRect(QRectF(cx + 6, y - 0.5, 4 if v % 50 else 6, 1))
        p.setPen(theme.qcolor(theme.MUTED))
        p.drawText(QRectF(0, 4, self.width(), 16), Qt.AlignCenter, "100")
        if self._pos is None:
            p.drawText(QRectF(0, self.height() - 24, self.width(), 16), Qt.AlignCenter, "–")
            return
        y = bottom - self._pos / 100 * (bottom - top)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(theme.ACCENT, 0.35))
        p.drawRoundedRect(QRectF(cx - 2, y, 4, bottom - y), 2, 2)
        p.setBrush(theme.qcolor(theme.ACCENT))
        p.drawRoundedRect(QRectF(cx - 10, y - THUMB_H / 2, 20, THUMB_H), 4, 4)
        p.setPen(theme.qcolor(theme.TEXT))
        p.drawText(QRectF(0, self.height() - 24, self.width(), 16), Qt.AlignCenter, f"{self._pos:.0f}")
