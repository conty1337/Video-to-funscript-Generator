"""Drive test: full strokes with fixed times, a circle lights up when the device should
arrive. The user notes per time whether it is on time and goes all the way; the result
can be copied (to tune the timing of devices like the F21S from real measurements)."""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import dec, tr
from . import theme
from .setup_wizard import Flash

DURATIONS = (500, 1000, 1500, 2000, 3000)      # ms per full stroke
STROKES = 6
LEAD_MS = 1200                                 # glide to the bottom first
FLASH_MS = 150
VERDICTS = ((tr("pünktlich"), "ok"), (tr("zu früh, wartet"), "früh"), (tr("zu spät"), "spät"))


def plan(start_ms: float, duration_ms: int, strokes: int = STROKES) -> list[tuple[float, float, int]]:
    """(send time, position, duration): bottom first, then full strokes back to back."""
    out = [(start_ms, 0.0, LEAD_MS - 200)]
    for k in range(strokes):
        out.append((start_ms + LEAD_MS + k * duration_ms, 100.0 if k % 2 == 0 else 0.0, duration_ms))
    return out


def report(results: dict) -> str:
    """One line per tested time, for copying."""
    return "\n".join(f"{d} ms: {v}, {'ganz durch' if full else 'NICHT ganz durch'}"
                     for d, (v, full) in sorted(results.items()))


class DriveTestDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle(tr("Fahrtest"))
        self.results: dict[int, tuple[str, bool]] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        text = QLabel(tr("Fährt volle Hübe in der gewählten Zeit. Der Kreis leuchtet, wenn das Gerät oben bzw. unten sein soll. Danach eintragen, was du gesehen hast, und für die nächste Zeit wiederholen. Zum Schluss „Ergebnis kopieren“ und weitergeben."))
        text.setWordWrap(True)
        lay.addWidget(text)
        row = QHBoxLayout()
        self.duration = QComboBox()
        for d in DURATIONS:
            self.duration.addItem(tr("{0} s pro Hub", dec(f"{d / 1000:g}")), d)
        row.addWidget(self.duration, 1)
        self.start_btn = theme.accent_icon(QPushButton(), "play", tr("Start"))
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start)
        row.addWidget(self.start_btn)
        stop = QPushButton(theme.icon("x"), tr("Stopp (Esc)"))
        stop.clicked.connect(self.stop)
        row.addWidget(stop)
        lay.addLayout(row)
        self.flash = Flash()
        lay.addWidget(self.flash, 1)
        seen = QHBoxLayout()
        self.verdict = QComboBox()
        for label, key in VERDICTS:
            self.verdict.addItem(label, key)
        self.full = QCheckBox(tr("fährt ganz durch"))
        self.full.setChecked(True)
        take = QPushButton(tr("Eintragen"))
        take.clicked.connect(self.take)
        seen.addWidget(self.verdict, 1)
        seen.addWidget(self.full)
        seen.addWidget(take)
        lay.addLayout(seen)
        self.summary = QLabel()
        self.summary.setFont(theme.mono_font(12))
        lay.addWidget(self.summary)
        copy = QPushButton(tr("Ergebnis kopieren"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(report(self.results)))
        lay.addWidget(copy)
        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self, interval=10)
        self.timer.timeout.connect(self._tick)
        self._todo: list[tuple[float, float, int]] = []
        self._flashes: list[float] = []

    @property
    def device(self):
        dev = self.ctl.device
        return dev if dev is not None and dev.connected else None

    def start(self):
        if self.device is None:
            self.summary.setText(tr("Kein Gerät verbunden."))
            return
        self.ctl.sync.stop()
        d = self.duration.currentData()
        now = float(self.clock.elapsed())
        self._todo = plan(now, d)
        self._flashes = [now + LEAD_MS + (k + 1) * d for k in range(STROKES)]
        self.timer.start()

    def stop(self):
        self._todo, self._flashes = [], []
        self.timer.stop()
        self.flash.set_on(False)
        if self.device is not None:
            try:
                self.device.stop()
            except Exception:
                pass

    def take(self):
        self.results[self.duration.currentData()] = (self.verdict.currentData(), self.full.isChecked())
        self.summary.setText(report(self.results))
        self.duration.setCurrentIndex(min(self.duration.count() - 1, self.duration.currentIndex() + 1))

    def _tick(self):
        now = float(self.clock.elapsed())
        while self._todo and self._todo[0][0] <= now:
            _t, pos, ms = self._todo.pop(0)
            if self.device is not None:
                self.device.move(self.ctl.sync.settings.map(pos), ms)
        self.flash.set_on(any(f <= now < f + FLASH_MS for f in self._flashes))
        if not self._todo and (not self._flashes or now > self._flashes[-1] + FLASH_MS):
            self.timer.stop()
            self.flash.set_on(False)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.stop()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def done(self, r):
        self.stop()
        super().done(r)
