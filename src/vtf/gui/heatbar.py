"""Heatmap: a thin bar under the timeline, coloured by how fast the script moves
(dark = still/slow, then green, yellow, red = fast)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..funscript import Action
from ..i18n import tr
from . import theme

#: (speed in positions per second, colour) - interpolated in between
STOPS = [(0, (40, 40, 44)), (100, (40, 90, 160)), (250, (60, 170, 90)),
         (400, (230, 200, 60)), (550, (235, 120, 50)), (700, (220, 60, 60))]


def heat_color(speed: float) -> QColor:
    if speed <= STOPS[0][0]:
        return QColor(*STOPS[0][1])
    for (s0, c0), (s1, c1) in zip(STOPS, STOPS[1:]):
        if speed <= s1:
            t = (speed - s0) / (s1 - s0)
            return QColor(*(int(a + (b - a) * t) for a, b in zip(c0, c1)))
    return QColor(*STOPS[-1][1])


def bucket_speeds(actions: list[Action], duration_ms: float, buckets: int) -> list[float]:
    """Fastest step touching each of `buckets` equal time slices (positions per second)."""
    out = [0.0] * max(1, buckets)
    if len(actions) < 2 or duration_ms <= 0:
        return out
    n = len(out)
    for a, b in zip(actions, actions[1:]):
        dt = max(1, b.at - a.at)
        v = abs(b.pos - a.pos) * 1000.0 / dt
        i0 = max(0, min(n - 1, int(a.at / duration_ms * n)))
        i1 = max(0, min(n - 1, int(b.at / duration_ms * n)))
        for i in range(i0, i1 + 1):
            out[i] = max(out[i], v)
    return out


def heatmap_image(actions: list[Action], duration_ms: float, width: int = 1600, height: int = 120) -> QImage:
    """The script as a picture to share: tempo colours over time (like the heatmap under the
    timeline) with the stroke curve drawn thinly over them."""
    img = QImage(width, height, QImage.Format.Format_RGB32)
    img.fill(QColor(*STOPS[0][1]))
    if len(actions) < 2 or duration_ms <= 0:
        return img
    p = QPainter(img)
    for x, v in enumerate(bucket_speeds(actions, duration_ms, width)):
        if v > 0:
            p.fillRect(x, 0, 1, height, heat_color(v))
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(255, 255, 255, 150), 1.2))
    path = QPainterPath()
    for k, a in enumerate(actions):
        pt = QPointF(a.at / duration_ms * (width - 1), 4 + (1 - a.pos / 100) * (height - 9))
        path.moveTo(pt) if k == 0 else path.lineTo(pt)
    p.drawPath(path)
    p.end()
    return img


class HeatBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.actions: list[Action] = []
        self.duration_ms = 0.0
        self.inset = 6               # match the slider groove (half the handle width)
        self.setToolTip(tr("Heatmap: Tempo des Scripts – dunkel = langsam, grün, gelb, rot = schnell"))

    def set_actions(self, actions: list[Action], duration_ms: float) -> None:
        self.actions = list(actions)
        self.duration_ms = float(duration_ms)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        r = QRectF(self.inset, 1, max(1, self.width() - 2 * self.inset), self.height() - 2)
        p.fillRect(r, theme.qcolor(theme.RAISED))
        if len(self.actions) < 2 or self.duration_ms <= 0:
            return
        n = max(1, int(r.width() / 2))           # 2 px per slice
        speeds = bucket_speeds(self.actions, self.duration_ms, n)
        w = r.width() / n
        p.setPen(Qt.NoPen)
        for i, v in enumerate(speeds):
            if v <= 0:
                continue
            p.fillRect(QRectF(r.x() + i * w, r.y(), w + 0.5, r.height()), heat_color(v))
