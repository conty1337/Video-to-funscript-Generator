"""Drive test (stopwatch): the device drives full strokes at one speed step of the
Fredorch F21S, the user presses Space when it arrives. "Übernehmen" turns the measured
times into a correction per step (device/fredorch.py) that playback then uses; the
copied report shows the numbers and which step the app would pick, for checking."""

from __future__ import annotations

from statistics import mean

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..device import fredorch
from ..i18n import dec, tr
from . import theme

#: travel time of a full stroke (15 steps) per speed step in Intiface's fredorch driver
FULL_STROKE_MS = {s: fredorch.FULL_STROKE[s - 1] for s in range(1, 11)}
SAMPLE_MS = (500, 1000, 1500, 2000, 2500, 3000, 4000, 6000)    # full strokes shown in the report
ROUNDS = 4
LEAD_MS = 2500           # glide to the bottom and settle first


def sent_ms(step: int) -> int:
    """Duration to send so the driver picks exactly this step for a full stroke
    (it takes the slowest step whose time is below the duration)."""
    return FULL_STROKE_MS[step] + 1


def report(results: dict, saved: dict | None = None) -> str:
    """Measurements, the correction they give and what the app would pick, for copying."""
    reaction, factors = fredorch.factors_from(results)
    lines = [f"Reaktionszeit: {reaction:.0f} ms (aus Stufe 10/9)",
             "Stufe  Intiface  gemessen  echt  Faktor  Einzelwerte"]
    for s, t in sorted(results.items(), reverse=True):
        f = factors.get(s)
        real = mean(t) - reaction
        lines.append(f"{s:>5}  {FULL_STROKE_MS[s]:>8}  {mean(t):>8.0f}  {real:>4.0f}  "
                     f"{f'{f:.2f}' if f else '   -':>6}  {', '.join(str(round(x)) for x in t)}")
    use = saved if saved is not None else factors
    lines.append("Übernommen: " + (", ".join(f"{s}:{f:.2f}" for s, f in sorted(use.items())) if saved else "nein"))
    lines.append("Voller Hub soll -> Stufe, echte Zeit, gesendet")
    for d in SAMPLE_MS:
        speed, sent = fredorch.pick(fredorch.POSITIONS, d, use)
        if speed:
            lines.append(f"{d:>5} ms -> Stufe {speed}, {fredorch.real_ms(fredorch.POSITIONS, speed, use):.0f} ms, {sent} ms")
        else:
            lines.append(f"{d:>5} ms -> wie Intiface, {d} ms")
    return "\n".join(lines)


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
        btns = QHBoxLayout()
        take = QPushButton(tr("Übernehmen"))
        take.setToolTip(tr("Aus den Messungen eine Korrektur pro Stufe berechnen, speichern und beim Abspielen nutzen"))
        take.clicked.connect(self.take)
        off = QPushButton(tr("Korrektur aus"))
        off.clicked.connect(self.clear)
        copy = QPushButton(tr("Ergebnis kopieren"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self._report()))
        for b in (take, off, copy):
            btns.addWidget(b)
        lay.addLayout(btns)
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
        self.summary.setText(self._report())
        self.state.setText(tr("Fertig. Nächste Stufe wählen und Start."))
        self.step.setCurrentIndex(min(self.step.count() - 1, self.step.currentIndex() + 1))

    def _report(self) -> str:
        saved = self.ctl.fred_factors() if hasattr(self.ctl, "fred_factors") else {}
        return report(self.results, saved)

    def take(self):
        _reaction, factors = fredorch.factors_from(self.results)
        if not factors:
            self.state.setText(tr("Erst langsame Stufen messen (und Stufe 10 oder 9 als Vergleich)."))
            return
        self.ctl.set_fred_factors(factors)
        self.summary.setText(self._report())
        self.state.setText(tr("Korrektur übernommen, gilt ab jetzt beim Abspielen."))

    def clear(self):
        self.ctl.set_fred_factors({})
        self.summary.setText(self._report())
        self.state.setText(tr("Korrektur aus: wieder wie Intiface."))

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
