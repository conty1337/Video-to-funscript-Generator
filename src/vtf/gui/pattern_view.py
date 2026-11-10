"""Pictures of stroke patterns: thumbnails, block icons and a large preview."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import patterns as P
from . import theme


def draw_curve(p: QPainter, rect: QRectF, pts, lo: float = 0, hi: float = 100, color: str = theme.ACCENT,
               width: float = 1.6, fill: bool = True) -> None:
    """Draw points (ms, 0..1) into rect; positions mapped to the stroke range lo..hi."""
    if len(pts) < 2:
        return
    t_end = pts[-1][0] or 1.0
    lo, hi = sorted((lo, hi))

    def xy(t, f):
        v = (lo + f * (hi - lo)) / 100
        return QPointF(rect.left() + rect.width() * t / t_end, rect.bottom() - rect.height() * v)

    path = QPainterPath(xy(*pts[0]))
    for t, f in pts[1:]:
        path.lineTo(xy(t, f))
    if fill:
        area = QPainterPath(path)
        area.lineTo(rect.right(), rect.bottom())
        area.lineTo(rect.left(), rect.bottom())
        area.closeSubpath()
        p.fillPath(area, theme.qcolor(color, 0.12))
    pen = QPen(QColor(color), width)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawPath(path)


def curve_pixmap(pts, w: int, h: int, color: str = theme.ACCENT, ratio: float = 2.0) -> QPixmap:
    pm = QPixmap(int(w * ratio), int(h * ratio))
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(0, 0, w, h)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.CARD))
    p.drawRoundedRect(r, 4, 4)
    draw_curve(p, r.adjusted(4, 4, -4, -4), pts, color=color, width=1.4)
    p.end()
    return pm


def pattern_icon(pattern: P.Pattern, w: int = 96, h: int = 32) -> QIcon:
    """A thumbnail: two runs of the pattern (short patterns repeated to fill)."""
    one = P.cycle_ms(pattern, 60)
    cycles = max(2, min(8, int(4000 / one))) if one > 0 else 1
    return QIcon(curve_pixmap(P.preview(pattern, 60, cycles), w, h))


def block_icon(block: P.Block, w: int = 56, h: int = 28) -> QIcon:
    pat = P.Pattern("", [block])
    if block.kind == "stroke":
        pts = P.preview(pat, 60, 1)
    else:                          # show the way there: start in the middle
        t, pts = 0.0, [(0.0, 0.5)]
        for d, f in P._segments(pat, 60, 0.5):
            t += d
            pts.append((t, f))
    return QIcon(curve_pixmap(pts, w, h))


class PatternView(QWidget):
    """Large preview: a few runs of the pattern in the stroke range, with a moving
    playhead while it plays and the selected block highlighted."""

    def __init__(self, parent=None, cycles: int = 2):
        super().__init__(parent)
        self.pattern: P.Pattern | None = None
        self.cycles = cycles
        self.bpm, self.lo, self.hi = 60.0, 0, 100
        self.phase: float | None = None
        self.highlight: int | None = None
        self._pts: list = []
        self._ranges: list = []
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(480, 180)

    def set_pattern(self, pattern: P.Pattern | None, bpm: float | None = None, lo=None, hi=None) -> None:
        self.pattern = pattern
        if bpm is not None:
            self.bpm = bpm
        if lo is not None:
            self.lo, self.hi = lo, hi
        self._pts = P.preview(pattern, self.bpm, self.cycles) if pattern else []
        self._ranges = P.block_ranges(pattern, self.bpm) if pattern else []
        self.update()

    def set_phase(self, phase: float | None) -> None:
        if phase != self.phase:
            self.phase = phase
            self.update()

    def set_highlight(self, idx: int | None) -> None:
        self.highlight = idx
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.CARD))
        r = QRectF(self.rect()).adjusted(40, 12, -12, -24)
        # stroke range guide
        p.setFont(theme.mono_font(10))
        for v in (0, 50, 100):
            y = r.bottom() - r.height() * v / 100
            p.setPen(QPen(theme.qcolor(theme.TEXT, 0.06), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(0, y - 8, 34, 16), Qt.AlignRight | Qt.AlignVCenter, str(v))
        lo, hi = sorted((self.lo, self.hi))
        band = QRectF(r.left(), r.bottom() - r.height() * hi / 100, r.width(), r.height() * (hi - lo) / 100)
        p.fillRect(band, theme.qcolor(theme.TEXT, 0.025))
        if not self._pts:
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, "Kein Muster – Bausteine hinzufügen")
            return
        total = self._pts[-1][0] or 1.0
        one = total / self.cycles
        if self.highlight is not None and 0 <= self.highlight < len(self._ranges):
            a, b = self._ranges[self.highlight]
            for c in range(self.cycles):
                x0 = r.left() + r.width() * (c * one + a) / total
                x1 = r.left() + r.width() * (c * one + b) / total
                p.fillRect(QRectF(x0, r.top(), max(2.0, x1 - x0), r.height()), theme.qcolor(theme.ACCENT, 0.10))
        for c in range(1, self.cycles):               # run boundaries
            x = r.left() + r.width() * c / self.cycles
            p.setPen(QPen(theme.qcolor(theme.TEXT, 0.12), 1, Qt.DashLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        draw_curve(p, r, self._pts, self.lo, self.hi, width=2.0)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 16), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self.cycles} Durchläufe · je {one / 1000:.1f} s".replace(".", ","))
        if self.phase is not None:
            x = r.left() + r.width() * self.phase / self.cycles
            p.setPen(QPen(QColor(theme.TEXT), 1.5))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
