"""Drive test (stopwatch): the device drives full strokes at one speed step of the
Fredorch F21S, the user presses Space when it arrives. "Übernehmen" turns the measured
times into a correction per step (device/fredorch.py) that playback then uses; the
copied report shows the numbers and which step the app would pick, for checking."""

from __future__ import annotations

import json
from statistics import mean

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

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
    use = saved or factors          # nothing applied yet: preview what "Übernehmen" would give
    lines.append("Übernommen: " + (", ".join(f"{s}:{f:.2f}" for s, f in sorted(saved.items())) if saved else "nein"))
    lines.append("Voller Hub soll -> Stufe, echte Zeit, gesendet" + ("" if saved else " (Vorschau)"))
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
        self.resize(760, 820)
        self.results = self._load()           # earlier measurements stay for comparison
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
        self.tap_btn.setStyleSheet("min-height: 80px; font-size: 16px;")   # big target to hit
        self.tap_btn.clicked.connect(self.tap)
        lay.addWidget(self.tap_btn)
        QShortcut(QKeySequence(Qt.Key_Space), self, self.tap)
        self.state = QLabel()
        self.state.setObjectName("meta")
        lay.addWidget(self.state)
        self.summary = QPlainTextEdit(readOnly=True)       # scrolls, and the text can be selected
        self.summary.setFont(theme.mono_font(12))
        self.summary.setMinimumHeight(300)
        lay.addWidget(self.summary, 2)
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
        if self.results:
            self.summary.setPlainText(self._report())

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
        if hasattr(self.device, "fred_factors"):
            self.device.fred_factors = {}          # measure the plain steps, not the correction
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
        self._save()
        self.summary.setPlainText(self._report())
        self.state.setText(tr("Fertig. Nächste Stufe wählen und Start."))
        self.step.setCurrentIndex(min(self.step.count() - 1, self.step.currentIndex() + 1))

    def _load(self) -> dict[int, list[float]]:
        qs = getattr(self.ctl, "qs", None)
        try:
            raw = json.loads(str(qs.value("device/fred_measured", "{}"))) if qs is not None else {}
            return {int(k): [float(x) for x in v] for k, v in raw.items()}
        except (ValueError, TypeError, AttributeError):
            return {}

    def _save(self) -> None:
        qs = getattr(self.ctl, "qs", None)
        if qs is not None:
            qs.setValue("device/fred_measured", json.dumps(self.results))

    def _report(self) -> str:
        saved = self.ctl.fred_factors() if hasattr(self.ctl, "fred_factors") else {}
        return report(self.results, saved)

    def take(self):
        _reaction, factors = fredorch.factors_from(self.results)
        if not factors:
            self.state.setText(tr("Erst langsame Stufen messen (und Stufe 10 oder 9 als Vergleich)."))
            return
        saved = self.ctl.fred_factors() if hasattr(self.ctl, "fred_factors") else {}
        self.ctl.set_fred_factors({**saved, **factors})     # steps measured again replace only their own
        self.summary.setPlainText(self._report())
        self.state.setText(tr("Korrektur übernommen, gilt ab jetzt beim Abspielen."))

    def clear(self):
        self.ctl.set_fred_factors({})
        self.summary.setPlainText(self._report())
        self.state.setText(tr("Korrektur aus: wieder wie Intiface."))

    def stop(self):
        self.lead.stop()
        if hasattr(self.device, "fred_factors") and hasattr(self.ctl, "fred_factors"):
            self.device.fred_factors = self.ctl.fred_factors()      # correction back on
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


#: tempo test: depth in % -> distance in steps of the F21S
DEPTHS = {100: 15, 75: 11, 50: 8, 25: 4}
TEMPO_START, TEMPO_STEP, TEMPO_MAX = 60, 10, 300      # strokes (up and down) per minute
PER_LEVEL = 8            # moves per tempo level, then it gets faster


def per_min(ms: float) -> float:
    """Strokes (up and down) per minute when one direction takes `ms`."""
    return 30_000 / ms


def limits_report(limits: dict[int, float]) -> str:
    lines = ["Tiefe  Stufen  Tabelle  gemessen  (Hübe/min, höchstens)"]
    for depth, dist in DEPTHS.items():
        got = f"{per_min(limits[dist]):>8.0f}" if dist in limits else "       -"
        lines.append(f"{depth:>4} %  {dist:>6}  {per_min(fredorch.SPEED_MATRIX[dist - 1][-1]):>7.0f}  {got}")
    lines.append("Schnellere Stellen fährt das Gerät kürzer, statt zu zappeln.")
    return "\n".join(lines)


class TempoTestDialog(QDialog):
    """Tempo test: full or partial strokes get faster step by step; the user presses Space
    as soon as the device judders or no longer reaches the end. The last clean tempo per
    depth is saved and playback never asks for more (device/fredorch.py: fit)."""

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle(tr("Tempo-Test"))
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        text = QLabel(tr("Hubbereich 0 bis 100 einstellen, Tiefe wählen, Start. Das Gerät fährt immer schneller; drück die Leertaste, sobald es zappelt oder nicht mehr ganz durchfährt. Das letzte saubere Tempo wird gespeichert, schnellere Stellen fährt es danach kürzer."))
        text.setWordWrap(True)
        lay.addWidget(text)
        row = QHBoxLayout()
        self.depth = QComboBox()
        for depth, dist in DEPTHS.items():
            self.depth.addItem(tr("Tiefe {0} % ({1} Stufen)", depth, dist), dist)
        row.addWidget(self.depth, 1)
        start = theme.accent_icon(QPushButton(), "play", tr("Start"))
        start.setObjectName("primary")
        start.clicked.connect(self.start)
        row.addWidget(start)
        stop = QPushButton(theme.icon("x"), tr("Stopp (Esc)"))
        stop.clicked.connect(self.stop)
        row.addWidget(stop)
        lay.addLayout(row)
        self.tap_btn = QPushButton(tr("Zappelt (Leertaste)"))
        self.tap_btn.setStyleSheet("min-height: 80px; font-size: 16px;")
        self.tap_btn.clicked.connect(self.tap)
        lay.addWidget(self.tap_btn)
        QShortcut(QKeySequence(Qt.Key_Space), self, self.tap)
        self.state = QLabel()
        self.state.setObjectName("meta")
        lay.addWidget(self.state)
        self.summary = QPlainTextEdit(readOnly=True)
        self.summary.setFont(theme.mono_font(12))
        lay.addWidget(self.summary, 1)
        btns = QHBoxLayout()
        reset = QPushButton(tr("Grenzen zurücksetzen"))
        reset.setToolTip(tr("Wieder nur die schnellste Stufe der Intiface-Tabelle als Grenze"))
        reset.clicked.connect(self.reset)
        copy = QPushButton(tr("Ergebnis kopieren"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.summary.toPlainText()))
        btns.addWidget(reset)
        btns.addWidget(copy)
        lay.addLayout(btns)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self._stroke)
        self.bpm = 0
        self._moves = 0
        self._pos = 0.0
        self.summary.setPlainText(limits_report(self.ctl.fred_limits()))

    @property
    def device(self):
        dev = self.ctl.device
        return dev if dev is not None and dev.connected else None

    def start(self):
        if self.device is None:
            self.state.setText(tr("Kein Gerät verbunden."))
            return
        self.ctl.sync.stop()
        if hasattr(self.device, "fred_limits"):
            self.device.fred_limits = None         # let it try, that is the point
        self.bpm, self._moves, self._pos = TEMPO_START, 0, 0.0
        self.device.move(self.ctl.sync.settings.map(0.0), LEAD_MS - 1000)
        self.state.setText(tr("Fährt nach unten…"))
        self.timer.start(LEAD_MS)

    def _stroke(self):
        if self.device is None:
            return
        if self._moves == PER_LEVEL:
            self._moves = 0
            self.bpm += TEMPO_STEP
            if self.bpm > TEMPO_MAX:            # never juddered: the fastest tempo is fine
                self._save(TEMPO_MAX)
                return
        half = per_min(self.bpm)                # ms per direction
        self._pos = self.depth.currentData() * 100 / fredorch.POSITIONS if self._pos == 0 else 0.0
        self.device.move(self.ctl.sync.settings.map(self._pos), int(half))
        self._moves += 1
        self.state.setText(tr("{0} Hübe/min: Leertaste, sobald es zappelt", self.bpm))
        self.timer.start(int(half))

    def tap(self):
        if self.bpm and self.timer.isActive():
            self._save(max(TEMPO_START, self.bpm - TEMPO_STEP))   # the level before was still clean

    def _save(self, bpm: int):
        limits = {**self.ctl.fred_limits(), self.depth.currentData(): per_min(bpm)}
        self.stop()
        self.ctl.set_fred_limits(limits)
        self.summary.setPlainText(limits_report(limits))
        self.state.setText(tr("Gespeichert: höchstens {0} Hübe/min bei dieser Tiefe. Nächste Tiefe wählen und Start.", bpm))
        self.depth.setCurrentIndex(min(self.depth.count() - 1, self.depth.currentIndex() + 1))

    def reset(self):
        self.ctl.set_fred_limits({})
        self.summary.setPlainText(limits_report({}))
        self.state.setText(tr("Grenzen zurückgesetzt: wieder wie die Intiface-Tabelle."))

    def stop(self):
        self.timer.stop()
        self.bpm = 0
        if hasattr(self.device, "fred_limits"):
            self.device.fred_limits = self.ctl.fred_limits()     # limit back on
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
