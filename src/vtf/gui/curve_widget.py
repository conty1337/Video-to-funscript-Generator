"""Analysis curve + editable funscript actions.

Mouse:
  * left click on empty space      -> seek
  * left drag on empty space       -> scroll the time axis (also middle drag)
  * left drag on a point           -> move point(s): all selected points move together
  * Shift/Ctrl + drag on empty     -> selection rectangle; Ctrl+click toggles a point
  * double click                   -> add point
  * right click on a point / Del   -> delete point
  * wheel                          -> zoom time axis
  * Shift+wheel, horizontal wheel  -> scroll (touchpad: two-finger swipe)
  * scroll bar below the plot      -> scroll
"""

from __future__ import annotations

import bisect
from typing import ClassVar

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QScrollBar, QSizePolicy, QWidget

from .. import editing
from ..funscript import Action
from . import theme

HIT_PX = 7
SCROLL_H = 10
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 40, 16, 12, 24 + SCROLL_H + 6
PAN_THRESHOLD_PX = 4


class CurveWidget(QWidget):
    seekRequested = Signal(int)        # ms
    actionsEdited = Signal()           # user changed actions
    editStarted = Signal()             # before a change (for undo)
    selectionChanged = Signal(int)     # number of selected points
    clipboard: ClassVar[list] = []     # copied points, shared by all curves (editing.Clip)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.t_ms = np.zeros(0)
        self.signal = np.zeros(0)
        self.actions: list[Action] = []
        self.duration_ms = 10_000.0
        self.view_ms = 20_000.0        # visible time span
        self.start_ms = 0.0
        self.playhead_ms = 0.0
        self.follow = True
        self.editable = True           # False in player mode: view, seek and scroll only
        self.sections: list[tuple] = []  # tracking: (start, end, calib_in_ms, calib_out_ms)
        self._sel: set[int] = set()          # selected action indices
        self._drag_from: QPointF | None = None
        self._drag_base: list[Action] | None = None
        self._dragging = False
        self._band: QRectF | None = None      # selection rectangle (widget coordinates)
        self._band_add = False
        self._press: QPointF | None = None     # press on empty space (click or pan)
        self._press_button = Qt.NoButton
        self._pan_from = 0.0
        self._panning = False
        self.scrollbar = QScrollBar(Qt.Horizontal, self)
        self.scrollbar.setObjectName("curveScroll")
        self.scrollbar.setCursor(Qt.ArrowCursor)
        self.scrollbar.valueChanged.connect(self._scrolled)
        self._sync_scrollbar()

    # ------------------------------------------------------------ data
    def set_data(self, t_ms: np.ndarray, signal: np.ndarray, actions: list[Action], duration_ms: float):
        self.t_ms, self.signal = t_ms, signal
        self.actions = actions
        self.duration_ms = max(duration_ms, 1000.0)
        self.view_ms = min(self.view_ms, self.duration_ms)
        self._sel = set()
        self._clamp_view()
        self.update()

    def set_sections(self, sections) -> None:
        self.sections = list(sections)
        self.update()

    def set_actions(self, actions: list[Action]):
        self.actions = actions
        self._sel = set()
        self.update()

    def set_playhead(self, ms: float):
        self.playhead_ms = ms
        if self.follow and not (self.start_ms <= ms <= self.start_ms + self.view_ms * 0.9):
            self.start_ms = ms - self.view_ms * 0.1
            self._clamp_view()
        self.update()

    # ------------------------------------------------------------ mapping
    def _plot(self) -> QRectF:
        return QRectF(MARGIN_L, MARGIN_T, max(1, self.width() - MARGIN_L - MARGIN_R),
                      max(1, self.height() - MARGIN_T - MARGIN_B))

    def _x(self, ms: float) -> float:
        r = self._plot()
        return r.left() + (ms - self.start_ms) / self.view_ms * r.width()

    def _y(self, pos01: float) -> float:
        r = self._plot()
        return r.bottom() - pos01 * r.height()

    def _ms(self, x: float) -> float:
        r = self._plot()
        return self.start_ms + (x - r.left()) / r.width() * self.view_ms

    def _pos(self, y: float) -> int:
        r = self._plot()
        return int(round(min(max((r.bottom() - y) / r.height(), 0.0), 1.0) * 100))

    def _clamp_view(self):
        self.view_ms = float(min(max(self.view_ms, 1000.0), max(self.duration_ms, 1000.0)))
        self.start_ms = float(min(max(self.start_ms, 0.0), max(0.0, self.duration_ms - self.view_ms)))
        self._sync_scrollbar()

    # ------------------------------------------------------------ scrolling
    def _sync_scrollbar(self):
        sb = getattr(self, "scrollbar", None)
        if sb is None:
            return
        sb.blockSignals(True)
        sb.setRange(0, int(max(0.0, self.duration_ms - self.view_ms)))
        sb.setPageStep(int(self.view_ms))
        sb.setSingleStep(max(1, int(self.view_ms * 0.05)))
        sb.setValue(int(self.start_ms))
        sb.blockSignals(False)
        sb.setVisible(sb.maximum() > 0)

    def _scrolled(self, v: int):
        self.follow = False
        self.start_ms = float(v)
        self.update()

    def scroll_by(self, ms: float):
        """Move the visible window (positive = later)."""
        self.follow = False
        self.start_ms += ms
        self._clamp_view()
        self.update()

    def resizeEvent(self, ev):
        r = self._plot()
        self.scrollbar.setGeometry(int(r.left()), self.height() - SCROLL_H - 2, int(r.width()), SCROLL_H)
        super().resizeEvent(ev)

    def _hit(self, p: QPointF) -> int | None:
        best, best_d = None, HIT_PX ** 2
        lo = bisect.bisect_left([a.at for a in self.actions], self._ms(p.x() - HIT_PX))
        for i in range(lo, len(self.actions)):
            a = self.actions[i]
            x = self._x(a.at)
            if x > p.x() + HIT_PX:
                break
            d = (x - p.x()) ** 2 + (self._y(a.pos / 100) - p.y()) ** 2
            if d <= best_d:
                best, best_d = i, d
        return best

    # ------------------------------------------------------------ painting
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), theme.qcolor(theme.CARD))
        r = self._plot()
        p.setFont(theme.mono_font(11))
        for v in (0, 25, 50, 75, 100):
            y = self._y(v / 100)
            p.setPen(theme.qcolor(theme.TEXT, 0.08 if v in (0, 100) else 0.04))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(theme.qcolor(theme.MUTED))
            p.drawText(QRectF(0, y - 8, MARGIN_L - 8, 16), Qt.AlignRight | Qt.AlignVCenter, str(v))
        # time grid
        step = next(s for s in (500, 1000, 2000, 5000, 10000, 30000, 60000, 300000, 600000, 10**9)
                    if self.view_ms / s <= 12)
        t = (self.start_ms // step) * step
        while t <= self.start_ms + self.view_ms:
            x = self._x(t)
            if x >= r.left():
                p.setPen(theme.qcolor(theme.TEXT, 0.04))
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                p.setPen(theme.qcolor(theme.MUTED))
                p.drawText(QPointF(x + 4, r.bottom() + 16), _fmt(t))
            t += step
        p.setClipRect(r)
        # tracking sections: light bands, calibration moments as labelled lines
        for a, b, cin, cout in self.sections:
            x0, x1 = self._x(a), self._x(b)
            if x1 < r.left() or x0 > r.right():
                continue
            p.fillRect(QRectF(x0, r.top(), x1 - x0, r.height()), theme.qcolor(theme.ACCENT, 0.05))
            p.setPen(QPen(theme.qcolor(theme.ACCENT, 0.35), 1))
            p.drawLine(QPointF(x0, r.top()), QPointF(x0, r.bottom()))
            for ms, label, y in ((cin, "drin", r.bottom() - 4), (cout, "raus", r.top() + 12)):
                if ms is None:
                    continue
                x = self._x(ms)
                p.setPen(QPen(theme.qcolor(theme.TEXT_2, 0.8), 1, Qt.DashLine))
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                p.setPen(theme.qcolor(theme.TEXT_2))
                p.drawText(QPointF(x + 4, y), label)
        # analysis curve
        if len(self.t_ms):
            i0 = max(0, int(np.searchsorted(self.t_ms, self.start_ms)) - 1)
            i1 = min(len(self.t_ms), int(np.searchsorted(self.t_ms, self.start_ms + self.view_ms)) + 2)
            stride = max(1, (i1 - i0) // int(r.width() * 2))
            path = QPainterPath()
            for k, i in enumerate(range(i0, i1, stride)):
                pt = QPointF(self._x(self.t_ms[i]), self._y(float(self.signal[i])))
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            p.setPen(QPen(theme.qcolor(theme.TEXT_2, 0.35), 1))
            p.drawPath(path)
        # actions
        vis = [i for i, a in enumerate(self.actions)
               if self.start_ms - 5000 <= a.at <= self.start_ms + self.view_ms + 5000]
        if vis:
            path = QPainterPath()
            for k, i in enumerate(vis):
                a = self.actions[i]
                pt = QPointF(self._x(a.at), self._y(a.pos / 100))
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            p.setPen(QPen(theme.qcolor(theme.ACCENT), 1.5))
            p.drawPath(path)
            if self.view_ms < 120_000:
                for i in vis:
                    a = self.actions[i]
                    sel = i in self._sel
                    p.setPen(Qt.NoPen)
                    p.setBrush(theme.qcolor(theme.TEXT) if sel else theme.qcolor(theme.ACCENT))
                    p.drawEllipse(QPointF(self._x(a.at), self._y(a.pos / 100)), 4 if sel else 2.5, 4 if sel else 2.5)
        # selection rectangle
        if self._band is not None:
            p.setPen(QPen(theme.qcolor(theme.ACCENT, 0.8), 1, Qt.DashLine))
            p.setBrush(theme.qcolor(theme.ACCENT, 0.08))
            p.drawRect(self._band.normalized())
        # playhead
        x = self._x(self.playhead_ms)
        p.setPen(QPen(theme.qcolor(theme.TEXT, 0.9), 1))
        p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

    # ------------------------------------------------------------ selection
    @property
    def selection(self) -> set[int]:
        return set(self._sel)

    def set_selection(self, sel) -> None:
        self._sel = {i for i in sel if 0 <= i < len(self.actions)}
        self.selectionChanged.emit(len(self._sel))
        self.update()

    def select_all(self) -> None:
        self.set_selection(range(len(self.actions)))

    def apply(self, fn, *args) -> bool:
        """Run an editing.* function on the selection, with undo; False if nothing selected."""
        if not self.editable or not self._sel:
            return False
        self.editStarted.emit()
        self.actions = fn(self.actions, set(self._sel), *args)
        self.actionsEdited.emit()
        self.update()
        return True

    def delete_selection(self) -> bool:
        if not self.apply(editing.delete):
            return False
        self.set_selection(())
        return True

    def copy_selection(self) -> int:
        clip = editing.copy(self.actions, self._sel)
        if clip:
            CurveWidget.clipboard = clip
        return len(clip)

    def paste(self, at_ms: float | None = None) -> int:
        if not self.editable or not CurveWidget.clipboard:
            return 0
        self.editStarted.emit()
        at = self.playhead_ms if at_ms is None else at_ms
        self.actions, sel = editing.paste(self.actions, CurveWidget.clipboard, int(at), self.duration_ms)
        self.actionsEdited.emit()
        self.set_selection(sel)
        return len(sel)

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, ev):
        pos = ev.position()
        mods = ev.modifiers()
        hit = self._hit(pos) if self.editable else None
        if ev.button() == Qt.RightButton:
            if hit is not None:
                if hit in self._sel and len(self._sel) > 1:
                    self.delete_selection()
                else:
                    self.editStarted.emit()
                    self.actions = editing.delete(self.actions, {hit})
                    self._sel = set()
                    self.actionsEdited.emit()
                    self.update()
            return
        if ev.button() == Qt.LeftButton and hit is None and self.editable and mods & (Qt.ShiftModifier | Qt.ControlModifier):
            # Shift/Strg + drag on empty space: selection rectangle
            self._band = QRectF(pos, pos)
            self._band_add = bool(mods & Qt.ControlModifier) or bool(mods & Qt.ShiftModifier)
            return
        if ev.button() == Qt.MiddleButton or (ev.button() == Qt.LeftButton and hit is None):
            # empty space: a click seeks (on release), a drag scrolls
            self._press, self._press_button = pos, ev.button()
            self._pan_from = self.start_ms
            self._panning = ev.button() == Qt.MiddleButton
            if self._panning:
                self.setCursor(Qt.ClosedHandCursor)
            return
        if ev.button() != Qt.LeftButton:
            return
        if mods & Qt.ControlModifier:              # Strg+click: toggle one point
            self.set_selection(self._sel ^ {hit})
            return
        if hit not in self._sel:
            self.set_selection((self._sel | {hit}) if mods & Qt.ShiftModifier else {hit})
        # drag: all selected points move together
        self._drag_from = pos
        self._drag_base = list(self.actions)
        self._dragging = False
        self.update()

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._band is not None:
            self._band.setBottomRight(pos)
            self.update()
            return
        if self._press is not None:
            dx = pos.x() - self._press.x()
            if not self._panning and abs(dx) >= PAN_THRESHOLD_PX:
                self._panning = True
                self.setCursor(Qt.ClosedHandCursor)
            if self._panning:
                self.follow = False
                self.start_ms = self._pan_from - dx / self._plot().width() * self.view_ms
                self._clamp_view()
                self.update()
            return
        if self._drag_from is None:
            over = self.editable and self._hit(pos) is not None
            self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
            return
        r = self._plot()
        dt = (pos.x() - self._drag_from.x()) / r.width() * self.view_ms
        dpos = -(pos.y() - self._drag_from.y()) / r.height() * 100
        if not self._dragging:
            if abs(pos.x() - self._drag_from.x()) + abs(pos.y() - self._drag_from.y()) < 2:
                return
            self._dragging = True
            self.editStarted.emit()
        self.actions = editing.move(self._drag_base, self._sel, dt, dpos, self.duration_ms)
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._band is not None:
            b = self._band.normalized()
            found = editing.in_rect(self.actions, self._ms(b.left()), self._ms(b.right()),
                                    self._pos(b.bottom()), self._pos(b.top()))
            self._band = None
            self.set_selection((self._sel | found) if self._band_add else found)
            return
        if self._press is not None:
            if not self._panning and self._press_button == Qt.LeftButton:
                if self._sel:
                    self.set_selection(())
                self.seekRequested.emit(int(max(0, min(self._ms(self._press.x()), self.duration_ms))))
                self.update()
            self._press, self._panning = None, False
            self.setCursor(Qt.ArrowCursor)
            return
        if self._drag_from is not None:
            moved = self._dragging
            self._drag_from, self._drag_base, self._dragging = None, None, False
            if moved:
                self.actionsEdited.emit()

    def mouseDoubleClickEvent(self, ev):
        if not self.editable or ev.button() != Qt.LeftButton or self._hit(ev.position()) is not None:
            return
        at = int(max(0, min(self._ms(ev.position().x()), self.duration_ms)))
        times = [a.at for a in self.actions]
        i = bisect.bisect_left(times, at)
        if i < len(times) and times[i] == at:
            return
        self.editStarted.emit()
        self.actions.insert(i, Action(at, self._pos(ev.position().y())))
        self.actionsEdited.emit()
        self.set_selection({i})

    def wheelEvent(self, ev):
        ad, px = ev.angleDelta(), ev.pixelDelta()
        horizontal = abs(ad.x()) > abs(ad.y())
        if horizontal or ev.modifiers() & Qt.ShiftModifier:
            # touchpad swipe (pixel deltas) moves with the fingers, a wheel by 15 % per notch
            if horizontal and not px.isNull():
                self.start_ms -= px.x() / self._plot().width() * self.view_ms
            else:
                d = ad.x() if horizontal else (ad.y() or ad.x())
                self.start_ms -= d / 120 * self.view_ms * 0.15
        else:
            d = ad.y()
            if not d:
                return
            anchor = self._ms(ev.position().x())
            factor = 0.8 ** (d / 120)        # proportional: smooth on touchpads too
            self.view_ms *= factor
            self._clamp_view()
            self.start_ms = anchor - (anchor - self.start_ms) * factor
        self.follow = False
        self._clamp_view()
        self.update()

    def keyPressEvent(self, ev):
        if self.editable and ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._sel:
            self.delete_selection()
        else:
            super().keyPressEvent(ev)


def _fmt(ms: float) -> str:
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}" if ms % 1000 == 0 else f"{s // 60}:{s % 60:02d}.{int(ms % 1000) // 100}"
