"""Drive test (stopwatch): the device drives full strokes at one speed step of the
Fredorch F21S, the user presses Space when it arrives. The measured travel times show
how far the real device differs from the speed table in Intiface's driver; the result
can be copied (to tune the F21S timing from real measurements)."""

from __future__ import annotations

from statistics import mean

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..i18n import dec, tr
from . import theme

#: travel time of a full stroke (15 steps) per speed step in Intiface's fredorch driver
FULL_STROKE_MS = {1: 8048, 2: 7068, 3: 4442, 4: 3708, 5: 2668, 6: 1930, 7: 1800, 8: 1520, 9: 878, 10: 618}
ROUNDS = 4
LEAD_MS = 2500           # glide to the bottom and settle first


def sent_ms(step: int) -> int:
    """Duration to send so the driver picks exactly this step for a full stroke
    (it takes the slowest step whose time is below the duration)."""
    return FULL_STROKE_MS[step] + 1


def report(results: dict) -> str:
    """One line per measured step, for copying."""
    return "\n".join(f"Stufe {s}: Intiface {FULL_STROKE_MS[s]} ms, gemessen {mean(t):.0f} ms "
                     f"({', '.join(str(round(x)) for x in t)})"
                     for s, t in sorted(results.items(), reverse=True))


class DriveTestDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle(tr("Fahrtest"))
        self.results: dict[int, list[float]] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        text = QLabel(tr("Stoppuhr für die Geschwindigkeitsstufen des F21S: Hubbereich 0 bis 100 einstellen, eine Stufe wählen, Start. Das Gerät fährt volle Hübe; drück jedes Mal die Leertaste, sobald es oben bzw. unten ankommt. Stufe 10 und 9 zuerst (Vergleich), dann die langsamen. Zum Schluss „Ergebnis kopieren“ und weitergeben."))
        text.setWordWrap(True)
        lay.addWidget(text)
        row = QHBoxLayout()
        self.step = QComboBox()
        for s in sorted(FULL_STROKE_MS, reverse=True):
            self.step.addItem(tr("Stufe {0} (laut Intiface {1} s)", s, dec(f"{FULL_STROKE_MS[s] / 1000:.2f}")), s)
        row.addWidget(self.step, 1)
        self.start_btn = theme.accent_icon(QPushButton(), "play", tr("Start"))
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start)
        row.addWidget(self.start_btn)
        stop = QPushButton(theme.icon("x"), tr("Stopp (Esc)"))
        stop.clicked.connect(self.stop)
        row.addWidget(stop)
        lay.addLayout(row)
        self.tap_btn = QPushButton(tr("Angekommen (Leertaste)"))
        self.tap_btn.setStyleSheet("min-height: 120px; font-size: 16px;")   # big target to hit
        self.tap_btn.clicked.connect(self.tap)
        lay.addWidget(self.tap_btn, 1)
        QShortcut(QKeySequence(Qt.Key_Space), self, self.tap)
        self.state = QLabel()
        self.state.setObjectName("meta")
        lay.addWidget(self.state)
        self.summary = QLabel()
        self.summary.setFont(theme.mono_font(12))
        lay.addWidget(self.summary)
        copy = QPushButton(tr("Ergebnis kopieren"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(report(self.results)))
        lay.addWidget(copy)
        self.clock = QElapsedTimer()
        self.clock.start()
        self.lead = QTimer(self, singleShot=True)
        self.lead.timeout.connect(self._stroke)
        self._sent_at: float | None = None
        self._times: list[float] = []
        self._pos = 0.0

    @property
    def device(self):
        dev = self.ctl.device
        return dev if dev is not None and dev.connected else None

    def _move(self, pos: float, ms: int):
        if self.device is not None:
            self.device.move(self.ctl.sync.settings.map(pos), ms)

    def start(self):
        if self.device is None:
            self.state.setText(tr("Kein Gerät verbunden."))
            return
        self.ctl.sync.stop()
        self._times, self._pos, self._sent_at = [], 0.0, None
        self._move(0.0, LEAD_MS - 1000)
        self.state.setText(tr("Fährt nach unten…"))
        self.lead.start(LEAD_MS)

    def _stroke(self):
        """Next full stroke to the other end at the chosen step."""
        self._pos = 100.0 - self._pos
        self._move(self._pos, sent_ms(self.step.currentData()))
        self._sent_at = float(self.clock.elapsed())
        self.state.setText(tr("Hub {0} von {1}: Leertaste, sobald es ankommt", len(self._times) + 1, ROUNDS))

    def tap(self):
        if self._sent_at is None:
            return
        self._times.append(float(self.clock.elapsed()) - self._sent_at)
        self._sent_at = None
        if len(self._times) < ROUNDS:
            self._stroke()
            return
        self.results[self.step.currentData()] = self._times
        self.summary.setText(report(self.results))
        self.state.setText(tr("Fertig. Nächste Stufe wählen und Start."))
        self.step.setCurrentIndex(min(self.step.count() - 1, self.step.currentIndex() + 1))

    def stop(self):
        self.lead.stop()
        self._sent_at = None
        self.state.setText("")
        if self.device is not None:
            try:
                self.device.stop()
            except Exception:
                pass

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.stop()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def done(self, r):
        self.stop()
        super().done(r)
