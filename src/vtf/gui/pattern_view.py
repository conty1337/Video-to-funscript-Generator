"""Pictures of stroke patterns: thumbnails, block icons and a large preview."""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt
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
        self.highlight = None               # block index or (first, last)
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

    def set_highlight(self, idx) -> None:
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
        hl = self.highlight
        if isinstance(hl, int):
            hl = (hl, hl)
        if hl is not None and self._ranges and 0 <= hl[0] <= hl[1] < len(self._ranges):
            a, b = self._ranges[hl[0]][0], self._ranges[hl[1]][1]
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
            x = r.left() + r.width() * self.phase   # phase over all runs shown
            p.setPen(QPen(QColor(theme.TEXT), 1.5))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))


def _fmt_min(ms: float) -> str:
    s = int(round(ms / 1000))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class ProgramView(QWidget):
    """Timeline of a program: one block per step (width = duration) with the
    pattern's shape at its depth, and a playhead with elapsed/remaining time."""

    def __init__(self, lookup, parent=None):
        super().__init__(parent)
        self.lookup = lookup                  # name -> Pattern | None
        self.program: P.Program | None = None
        self.elapsed: float | None = None
        self.selected: int | None = None
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(480, 160)

    def set_program(self, program: P.Program | None) -> None:
        self.program = program
        self.update()

    def set_elapsed(self, ms: float | None) -> None:
        self.elapsed = ms
        self.update()

    def set_selected(self, i: int | None) -> None:
        self.selected = i
        self.update()

    def _rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(12, 26, -12, -40)

    def paintEvent(self, _ev):
        """The steps are drawn once into a cached picture; per frame only the playhead."""
        prog = self.program
        cur = None
        if prog is not None and prog.steps and self.elapsed is not None:
            cur = max(i for i, t in enumerate(prog.starts()) if self.elapsed >= t)
        sig = None if prog is None else tuple((st.pattern, st.minutes, st.bpm, st.depth, st.first, st.last)
                                              for st in prog.steps)
        key = (self.width(), self.height(), self.devicePixelRatioF(), sig, cur, self.selected)
        if getattr(self, "_key", None) != key:
            ratio = self.devicePixelRatioF()
            pm = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
            pm.setDevicePixelRatio(ratio)
            q = QPainter(pm)
            q.setRenderHint(QPainter.Antialiasing)
            self._paint_static(q, cur)
            q.end()
            self._cache, self._key = pm, key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        if prog is not None and prog.steps and prog.total_ms > 0 and self.elapsed is not None:
            r = self._rect()
            x = r.left() + r.width() * min(1.0, self.elapsed / prog.total_ms)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(theme.TEXT), 1.5))
            p.drawLine(QPointF(x, r.top() - 2), QPointF(x, r.bottom() + 2))

    def _paint_static(self, p: QPainter, cur: int | None) -> None:
        p.fillRect(QRectF(0, 0, self.width(), self.height()), QColor(theme.CARD))
        r = self._rect()
        p.setFont(theme.mono_font(10))
        prog = self.program
        if prog is None or not prog.steps or prog.total_ms <= 0:
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, "Keine Schritte – Schritt hinzufügen")
            return
        total = prog.total_ms
        starts = prog.starts()
        for i, st in enumerate(prog.steps):
            x0 = r.left() + r.width() * starts[i] / total
            x1 = r.left() + r.width() * (starts[i] + st.minutes * 60_000) / total
            box = QRectF(x0 + 1, r.top(), max(1.0, x1 - x0 - 2), r.height())
            on = i == cur or i == self.selected
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.RAISED) if on else theme.qcolor(theme.TEXT, 0.03))
            p.drawRoundedRect(box, 4, 4)
            pat = self.lookup(st.pattern)
            if pat is not None and pat.blocks:
                part = st.part(pat)
                one = P.cycle_ms(part, st.bpm)
                fit = max(1, int(box.width() / (5 * max(1, len(P.segments(part, st.bpm))))))
                cycles = max(1, min(fit, int(st.minutes * 60_000 / one) if one > 0 else 1))
                pts = P.preview(part, st.bpm, cycles)
                p.save()
                p.setClipRect(box)
                draw_curve(p, box.adjusted(3, 4, -3, -4), pts, 100 - st.depth, 100,
                           color=theme.ACCENT if on else theme.TEXT_2, width=1.2)
                p.restore()
            p.setPen(QColor(theme.TEXT if on else theme.TEXT_2))
            p.drawText(QRectF(x0 + 3, self.rect().top() + 4, x1 - x0 - 6, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(st.pattern, Qt.ElideRight, int(max(0, x1 - x0 - 6))))
            p.setPen(QColor(theme.MUTED))
            w = int(max(0, x1 - x0 - 6))
            for k, lab in enumerate((f"{st.minutes:g} min".replace(".", ","), f"{st.bpm}/min · {st.depth} %")):
                p.drawText(QRectF(x0 + 3, r.bottom() + 3 + 15 * k, x1 - x0 - 6, 15), Qt.AlignLeft | Qt.AlignVCenter,
                           p.fontMetrics().elidedText(lab, Qt.ElideRight, w))


def _window(pts, t0: float, t1: float) -> list:
    """Points inside t0..t1, cut exactly at both edges (no clipping needed, which is slow
    with antialiasing)."""
    out = []
    for i, (t, v) in enumerate(pts):
        if t < t0:
            continue
        if not out and i > 0:
            pt, pv = pts[i - 1]
            out.append((t0, pv + (v - pv) * (t0 - pt) / ((t - pt) or 1)))
        if t > t1:
            if out:
                pt, pv = out[-1]
                out.append((t1, pv + (v - pv) * (t1 - pt) / ((t - pt) or 1)))
            break
        out.append((t, v))
    return out


class LiveStrip(QWidget):
    """What the device does, in real scale, scrolling: the last seconds on the left
    (what was played), the coming seconds on the right (what is planned), the
    playhead in between. Fixed pixels per second, so a bigger window shows more
    time instead of stretching. Only the visible seconds are kept."""

    PX_PER_S = 90.0
    PAST = 0.3                              # share of the width left of the playhead

    def __init__(self, parent=None):
        super().__init__(parent)
        self.now = 0.0
        self.past: list = []                # (ms, pos)
        self.future: list = []              # (ms, pos)
        self.marks: list = []               # (ms, label): step changes
        self.hint = ""
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(480, 200)

    def window_ms(self) -> tuple[float, float]:
        """Visible time before and after the playhead."""
        span = max(1.0, (self.width() - 52) / self.PX_PER_S * 1000)
        return span * self.PAST, span * (1 - self.PAST)

    def set_data(self, now: float, past, future, marks=(), hint: str = "") -> None:
        self.now, self.past, self.future, self.marks, self.hint = now, list(past), list(future), list(marks), hint
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.CARD))
        r = QRectF(self.rect()).adjusted(40, 20, -12, -12)
        before, after = self.window_ms()
        t0 = self.now - before
        px = r.width() / (before + after)

        def x(t):
            return r.left() + (t - t0) * px

        def y(v):
            return r.bottom() - r.height() * max(0.0, min(100.0, v)) / 100

        p.setFont(theme.mono_font(10))
        for v in (0, 50, 100):
            p.setPen(QPen(theme.qcolor(theme.TEXT, 0.06), 1))
            p.drawLine(QPointF(r.left(), y(v)), QPointF(r.right(), y(v)))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(0, y(v) - 8, 34, 16), Qt.AlignRight | Qt.AlignVCenter, str(v))
        # seconds ticks
        p.setPen(QPen(theme.qcolor(theme.TEXT, 0.04), 1))
        first = int(t0 // 1000 + 1) * 1000
        for t in range(first, int(self.now + after) + 1, 1000):
            p.drawLine(QPointF(x(t), r.top()), QPointF(x(t), r.bottom()))
        for t, label in self.marks:                  # step changes
            if t0 <= t <= self.now + after:
                p.setPen(QPen(theme.qcolor(theme.TEXT, 0.25), 1, Qt.DashLine))
                p.drawLine(QPointF(x(t), r.top() - 4), QPointF(x(t), r.bottom()))
                p.setPen(QColor(theme.TEXT_2))
                p.drawText(QRectF(x(t) + 4, 2, 260, 16), Qt.AlignLeft | Qt.AlignVCenter, label)
        t1 = self.now + after
        past = self.past if self.past else []
        future_from = self.now if self.past else t0       # while playing: planned starts at the playhead
        for pts, color, width in ((_window(past, t0, self.now), theme.TEXT_2, 1.5),
                                  (_window(self.future, future_from, t1), theme.ACCENT, 2.0)):
            if len(pts) < 2:
                continue
            pen = QPen(QColor(color), width)
            pen.setCapStyle(Qt.SquareCap)          # round caps cost ~60x more when antialiased
            p.setPen(pen)
            # single segments: an antialiased zigzag polyline is about 100x slower to stroke
            q = [QPointF(x(t), y(v)) for t, v in pts]
            p.drawLines([QLineF(a, b) for a, b in zip(q, q[1:])])
        xn = x(self.now)
        p.setPen(QPen(QColor(theme.TEXT), 1.5))
        p.drawLine(QPointF(xn, r.top() - 4), QPointF(xn, r.bottom()))
        if self.hint:
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, self.hint)
