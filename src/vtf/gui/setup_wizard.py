"""Setup assistant for the device: connection, direction, stroke range, top speed, delay."""

from __future__ import annotations

import math
import os
import struct
import tempfile
import wave

from PySide6.QtCore import QElapsedTimer, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..device.buttplug import ButtplugDevice
from ..device.sync import ScriptSync
from ..funscript import Action
from . import theme
from .device_ui import KINDS

#: full stroke times to try (ms), slow to fast
SPEED_LEVELS = (600, 500, 400, 350, 300, 250, 220, 200, 180, 160, 140)
BEAT_MS = 1000


def speed_for(full_stroke_ms: int) -> float:
    """Positions per second for a full stroke in that time."""
    return 100_000 / full_stroke_ms


def metronome_actions(start_ms: float, beats: int, beat_ms: int = BEAT_MS) -> list[Action]:
    """Bottom (0) exactly on every beat, top (100) halfway between."""
    out = []
    for k in range(beats):
        t = start_ms + k * beat_ms
        out.append(Action(int(round(t)), 0))
        out.append(Action(int(round(t + beat_ms / 2)), 100))
    return out


def click_wav() -> str | None:
    """A short click as a WAV file (for the beat), or None."""
    path = os.path.join(tempfile.gettempdir(), "vtf-click.wav")
    if os.path.exists(path):
        return path
    try:
        rate = 22050
        frames = bytearray()
        for i in range(int(rate * 0.03)):
            v = math.sin(2 * math.pi * 1800 * i / rate) * math.exp(-i / (rate * 0.006))
            frames += struct.pack("<h", int(v * 20000))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
        return path
    except OSError:
        return None


class Flash(QWidget):
    """Big circle that lights up on the beat."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on = False
        self.setMinimumSize(120, 120)

    def set_on(self, on: bool):
        if on != self.on:
            self.on = on
            self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = min(self.width(), self.height()) / 2 - 8
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT) if self.on else QColor(theme.RAISED))
        p.drawEllipse(QPointF(self.width() / 2, self.height() / 2), r, r)


class SetupWizard(QDialog):
    PAGES = ("Verbinden", "Richtung", "Hubbereich", "Höchsttempo", "Verzögerung", "Fertig")

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.s = ctl.sync.settings
        self.setWindowTitle("Gerät einrichten")
        self.resize(620, 520)
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetMinimumSize)    # see theme.DialogFitter
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        self.steps = QLabel()
        self.steps.setObjectName("caption")
        lay.addWidget(self.steps)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.status = QLabel()
        self.status.setObjectName("meta")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        nav = QHBoxLayout()
        self.stop_btn = QPushButton(theme.icon("x"), "Stopp (Esc)")
        self.stop_btn.clicked.connect(self.stop_motion)
        self.back_btn = QPushButton("Zurück")
        self.next_btn = QPushButton("Weiter")
        self.next_btn.setObjectName("primary")
        self.back_btn.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.stop_btn)
        nav.addStretch(1)
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)

        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._tick)
        self._strokes: list[tuple[float, float, int]] = []    # (time, position, duration) still to send
        self._metro: ScriptSync | None = None
        self._last_beat = -1
        self.sound = None
        wav = click_wav()
        if wav:
            try:
                from PySide6.QtMultimedia import QSoundEffect
                self.sound = QSoundEffect(self)
                self.sound.setSource(QUrl.fromLocalFile(wav))
                self.sound.setVolume(0.6)
            except Exception:
                self.sound = None

        self._page_connect()
        self._page_direction()
        self._page_range()
        self._page_speed()
        self._page_delay()
        self._page_done()
        ctl.message.connect(self._message)
        ctl.changed.connect(self._refresh)
        self.go(0)

    # ------------------------------------------------------------ helpers
    def _page(self, title: str, text: str) -> QVBoxLayout:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("section")
        v.addWidget(t)
        c = QLabel(text)
        c.setObjectName("caption")
        c.setWordWrap(True)
        v.addWidget(c)
        self.stack.addWidget(w)
        return v

    def _now(self) -> float:
        return float(self.clock.elapsed())

    @property
    def device(self):
        dev = self.ctl.device
        return dev if dev is not None and dev.connected else None

    def go(self, i: int):
        self.stop_motion()
        i = max(0, min(self.stack.count() - 1, i))
        self.stack.setCurrentIndex(i)
        self.steps.setText("  ›  ".join(f"<b>{p}</b>" if k == i else p for k, p in enumerate(self.PAGES)))
        self.back_btn.setEnabled(i > 0)
        self.next_btn.setText("Speichern und schließen" if i == self.stack.count() - 1 else "Weiter")
        if i == len(self.PAGES) - 1:
            self._summary()
        self._refresh()

    def _next(self):
        i = self.stack.currentIndex()
        if i == self.stack.count() - 1:
            self.ctl.save_sync()
            self.accept()
        else:
            self.go(i + 1)

    def _message(self, msg: str):
        self.status.setText(msg)
        self._refresh()

    def _refresh(self, *_):
        connected = self.device is not None
        if self.stack.currentIndex() == 0:
            self.next_btn.setEnabled(connected)
            self.connect_btn.setEnabled(not connected)
            self.dev_box.clear()
            dev = self.device
            if isinstance(dev, ButtplugDevice):
                for d in dev.devices.values():
                    self.dev_box.addItem(d.name + ("" if d.is_linear else " (keine Positionssteuerung)"), d.index)
                if dev.selected is not None:
                    self.dev_box.setCurrentIndex(max(0, self.dev_box.findData(dev.selected)))
            elif dev is not None:
                self.dev_box.addItem(dev.name, None)
            self.dev_box.setEnabled(self.dev_box.count() > 1)
            self.found.setText(f"Verbunden: {dev.name}" if connected else "Noch nicht verbunden.")
        else:
            self.next_btn.setEnabled(True)
        for b in self.findChildren(QPushButton):
            if b.property("needs_device"):
                b.setEnabled(connected)

    def _btn(self, text: str, fn, icon: str | None = None) -> QPushButton:
        b = QPushButton(theme.icon(icon), text) if icon else QPushButton(text)
        b.setProperty("needs_device", True)
        b.clicked.connect(fn)
        return b

    # ------------------------------------------------------------ motion
    def move(self, pos: float, ms: int = 800):
        """Move to a script position (mapped through range/direction)."""
        if self.device is not None:
            self.ctl.sync.stop()
            self.device.move(self.s.map(pos), ms)

    def stop_motion(self):
        self._strokes = []
        self.timer.stop()
        self._metro = None
        if hasattr(self, "metro_btn"):
            self.metro_btn.setText("Takt starten")
            self.flash.set_on(False)
        if self.device is not None:
            try:
                self.device.stop()
            except Exception:
                pass

    def _tick(self):
        now = self._now()
        while self._strokes and self._strokes[0][0] <= now:
            _t, pos, ms = self._strokes.pop(0)
            if self.device is not None:
                self.device.move(self.s.map(pos), ms)
        if self._metro is not None:
            self._metro.tick(now - self._metro_start, True)
            beat = int((now - self._metro_start) // BEAT_MS)
            phase = (now - self._metro_start) % BEAT_MS
            self.flash.set_on(phase < 120)
            if beat != self._last_beat:
                self._last_beat = beat
                if self.sound is not None and self.click.isChecked():
                    self.sound.play()
        elif not self._strokes:
            self.timer.stop()

    # ------------------------------------------------------------ pages
    def _page_connect(self):
        v = self._page("1. Verbinden", "Für den Fredorch F21S: Intiface Central starten und „Start Server“ "
                       "klicken, Gerät einschalten (Handy-App getrennt), dann hier verbinden. Ohne Gerät geht "
                       "alles auch mit der Simulation.")
        self.kind = QComboBox()
        for label, val in KINDS:
            self.kind.addItem(label, val)
        self.kind.setCurrentIndex(max(0, self.kind.findData(self.ctl.qs.value("device/kind", "intiface"))))
        self.url = QLineEdit()
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()
        self.connect_btn = QPushButton(theme.icon("bluetooth", theme.ON_ACCENT), "Verbinden")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(
            lambda: self.ctl.connect_device(self.kind.currentData(), self.url.text().strip()))
        v.addWidget(self.kind)
        v.addWidget(self.url)
        v.addWidget(self.connect_btn)
        self.dev_box = QComboBox()
        self.dev_box.activated.connect(self._select_device)
        v.addWidget(self.dev_box)
        self.found = QLabel()
        v.addWidget(self.found)
        v.addStretch(1)

    def _kind_changed(self, *_):
        from .device_ui import default_address

        kind = self.kind.currentData()
        key = "device/tcode" if kind == "tcode" else "device/url"
        self.url.setText(str(self.ctl.qs.value(key, "") or "") or default_address(kind))
        self.url.setEnabled(kind != "sim")

    def _select_device(self, i: int):
        dev = self.device
        if isinstance(dev, ButtplugDevice) and self.dev_box.itemData(i) is not None:
            dev.select(self.dev_box.itemData(i))

    def _page_direction(self):
        v = self._page("2. Richtung", "Drück „Oben“. Das Gerät soll ganz nach oben bzw. außen fahren. Fährt "
                       "es stattdessen nach unten bzw. innen, setz den Haken bei „Richtung umkehren“.")
        row = QHBoxLayout()
        row.addWidget(self._btn("Oben", lambda: self.move(100), "arrow-up"))
        row.addWidget(self._btn("Unten", lambda: self.move(0), "arrow-down"))
        row.addWidget(self._btn("Mitte", lambda: self.move(50)))
        v.addLayout(row)
        self.invert = QCheckBox("Richtung umkehren")
        self.invert.setChecked(self.s.invert)
        self.invert.toggled.connect(lambda on: (setattr(self.s, "invert", on), self.move(100)))
        v.addWidget(self.invert)
        v.addStretch(1)

    def _page_range(self):
        v = self._page("3. Hubbereich", "Welcher Teil des Weges genutzt wird. Beim Verschieben fährt das Gerät "
                       "an die Stelle, so siehst du sofort, wo die Grenze liegt. Ganz durch: 0 bis 100.")
        self.r_lo, self.r_hi = QSlider(Qt.Horizontal), QSlider(Qt.Horizontal)
        self.r_lo_val, self.r_hi_val = QLabel(), QLabel()
        for sl, val, lab, text in ((self.r_lo, self.s.pos_min, self.r_lo_val, "Unten"),
                                   (self.r_hi, self.s.pos_max, self.r_hi_val, "Oben")):
            sl.setRange(0, 100)
            sl.setValue(val)
            lab.setObjectName("value")
            lab.setFixedWidth(48)
            row = QHBoxLayout()
            name = QLabel(text)
            name.setObjectName("formLabel")
            name.setFixedWidth(64)
            row.addWidget(name)
            row.addWidget(sl, 1)
            row.addWidget(lab)
            v.addLayout(row)
        self.r_lo.valueChanged.connect(lambda x: self._range_changed(0))
        self.r_hi.valueChanged.connect(lambda x: self._range_changed(100))
        self._range_labels()
        v.addStretch(1)

    def _range_labels(self):
        self.r_lo_val.setText(str(self.r_lo.value()))
        self.r_hi_val.setText(str(self.r_hi.value()))

    def _range_changed(self, show: int):
        if self.r_hi.value() <= self.r_lo.value():
            if show == 0:
                self.r_lo.setValue(self.r_hi.value() - 1)
            else:
                self.r_hi.setValue(self.r_lo.value() + 1)
            return
        self.s.pos_min, self.s.pos_max = self.r_lo.value(), self.r_hi.value()
        self._range_labels()
        self.move(show, 400)

    def _page_speed(self):
        v = self._page("4. Höchsttempo", "„Probieren“ macht 4 volle Hübe im gewählten Tempo. Werde schneller, "
                       "bis das Gerät nicht mehr ganz hin und her kommt oder ruckelt, dann eine Stufe zurück und "
                       "„Übernehmen“. Der F21S schafft etwa 200 ms.")
        cur = min(range(len(SPEED_LEVELS)), key=lambda i: abs(speed_for(SPEED_LEVELS[i]) - self.s.max_speed))
        self.level = cur
        self.speed_label = QLabel()
        self.speed_label.setObjectName("value")
        self.speed_label.setFont(theme.mono_font(13))
        v.addWidget(self.speed_label)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Langsamer", clicked=lambda: self._level(-1)))
        row.addWidget(QPushButton("Schneller", clicked=lambda: self._level(1)))
        row.addWidget(self._btn("Probieren", self.try_speed, "play"))
        row.addWidget(QPushButton("Übernehmen", clicked=self.take_speed))
        v.addLayout(row)
        self.speed_taken = QLabel()
        self.speed_taken.setObjectName("caption")
        v.addWidget(self.speed_taken)
        self._level(0)
        v.addStretch(1)

    def _level(self, d: int):
        self.level = max(0, min(len(SPEED_LEVELS) - 1, self.level + d))
        ms = SPEED_LEVELS[self.level]
        self.speed_label.setText(f"Voller Hub in {ms} ms  =  {speed_for(ms):.0f} Positionen/s")
        self.speed_taken.setText(f"Eingestellt: {self.s.max_speed:.0f} /s")

    def try_speed(self):
        if self.device is None:
            return
        ms = SPEED_LEVELS[self.level]
        now = self._now()
        self._strokes = [(now + k * ms, 0 if k % 2 == 0 else 100, ms) for k in range(8)]
        self._strokes.append((now + 8 * ms + 100, 100, 400))
        self.timer.start()

    def take_speed(self):
        self.s.max_speed = float(round(speed_for(SPEED_LEVELS[self.level])))
        self._level(0)

    def _page_delay(self):
        v = self._page("5. Verzögerung", "Das Gerät soll genau dann ganz unten sein, wenn der Kreis aufleuchtet "
                       "(und es klickt). Kommt es zu spät, den Regler nach rechts schieben, kommt es zu früh, "
                       "nach links. Bluetooth braucht meist 50–150 ms.")
        row = QHBoxLayout()
        self.flash = Flash()
        row.addWidget(self.flash, 1)
        col = QVBoxLayout()
        self.metro_btn = self._btn("Takt starten", self.toggle_metro, "play")
        col.addWidget(self.metro_btn)
        self.click = QCheckBox("Klick-Ton")
        self.click.setChecked(self.sound is not None)
        self.click.setEnabled(self.sound is not None)
        col.addWidget(self.click)
        col.addStretch(1)
        row.addLayout(col)
        v.addLayout(row, 1)
        orow = QHBoxLayout()
        self.offset = QSlider(Qt.Horizontal)
        self.offset.setRange(-300, 600)
        self.offset.setSingleStep(10)
        self.offset.setValue(self.s.offset_ms)
        self.offset_val = QLabel()
        self.offset_val.setObjectName("value")
        self.offset_val.setFixedWidth(72)
        name = QLabel("Zeitversatz")
        name.setObjectName("formLabel")
        orow.addWidget(name)
        orow.addWidget(self.offset, 1)
        orow.addWidget(self.offset_val)
        v.addLayout(orow)
        self.offset.valueChanged.connect(self._offset_changed)
        self._offset_changed(self.offset.value())

    def _offset_changed(self, v: int):
        self.s.offset_ms = v
        self.offset_val.setText(f"{v:+d} ms")

    def toggle_metro(self):
        if self._metro is not None:
            self.stop_motion()
            return
        if self.device is None:
            return
        self._metro_start = self._now() + 600
        self._metro = ScriptSync(self.device, self.s)
        self._metro.set_actions(metronome_actions(0, 600))
        self._last_beat = -1
        self.metro_btn.setText("Takt stoppen")
        self.timer.start()

    def _page_done(self):
        v = self._page("Fertig", "Diese Werte werden gespeichert. Du kannst sie später im Fenster „Gerät“ "
                       "ändern oder den Assistenten erneut starten.")
        self.summary = QLabel()
        self.summary.setFont(theme.mono_font(12))
        v.addWidget(self.summary)
        v.addStretch(1)

    def _summary(self):
        s = self.s
        self.summary.setText(f"Richtung      {'umgekehrt' if s.invert else 'normal'}\n"
                             f"Hubbereich    {s.pos_min} bis {s.pos_max}\n"
                             f"Höchsttempo   {s.max_speed:.0f} /s\n"
                             f"Zeitversatz   {s.offset_ms:+d} ms")

    # ------------------------------------------------------------ closing
    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.stop_motion()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def done(self, r):
        self.stop_motion()
        self.ctl.save_sync()
        super().done(r)
