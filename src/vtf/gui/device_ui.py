"""Device connection in the GUI: dialog, background connect, sync settings."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLayout,
                               QLineEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..device import Device, DeviceError, SimulatedDevice
from ..device.buttplug import DEFAULT_URL, ButtplugDevice
from ..device.sync import ScriptSync, SyncSettings
from . import theme

KINDS = [("Simulation (ohne Gerät)", "sim"), ("Intiface Central (Bluetooth)", "intiface")]


class DeviceController(QObject):
    message = Signal(str)          # status text from any thread
    changed = Signal()             # connection state / device list changed

    def __init__(self, window, settings):
        super().__init__(window)
        self.win = window
        self.qs = settings
        self.device: Device | None = None
        self.sync = ScriptSync(None, self._load_sync())
        self.dialog: DeviceDialog | None = None
        self._connecting = False
        self.status = QLabel("Gerät · nicht verbunden")
        self.status.setFont(theme.mono_font(11))
        window.statusBar().addPermanentWidget(self.status)
        self.message.connect(self._on_message)

    # ------------------------------------------------------------ settings
    def _load_sync(self) -> SyncSettings:
        s = SyncSettings()
        for key, typ in (("offset_ms", int), ("pos_min", int), ("pos_max", int), ("max_speed", float)):
            v = self.qs.value(f"device/{key}")
            if v is not None:
                try:
                    setattr(s, key, typ(v))
                except (TypeError, ValueError):
                    pass
        s.invert = str(self.qs.value("device/invert", "false")).lower() == "true"
        return s

    def save_sync(self) -> None:
        s = self.sync.settings
        for key in ("offset_ms", "pos_min", "pos_max", "max_speed", "invert"):
            self.qs.setValue(f"device/{key}", getattr(s, key))

    # ------------------------------------------------------------ connection
    def connect_device(self, kind: str, url: str) -> None:
        if self._connecting:
            return
        self.disconnect_device()
        dev: Device = SimulatedDevice() if kind == "sim" else ButtplugDevice(url or DEFAULT_URL)
        dev.add_listener(self.message.emit)
        self.qs.setValue("device/kind", kind)
        self.qs.setValue("device/url", url)
        self._connecting = True
        self.message.emit("Verbinde…")

        def run():
            try:
                dev.connect()
                if isinstance(dev, ButtplugDevice) and not any(d.is_linear for d in dev.devices.values()):
                    dev.start_scanning()
                self.device = dev
                self.sync.set_device(dev)
            except DeviceError as e:
                self.message.emit(f"Fehler: {e}")
            finally:
                self._connecting = False
                self.changed.emit()

        threading.Thread(target=run, name="device-connect", daemon=True).start()

    def disconnect_device(self) -> None:
        dev, self.device = self.device, None
        self.sync.set_device(None)
        if dev is not None:
            try:
                dev.disconnect()
            except DeviceError:
                pass
        self.changed.emit()

    # ------------------------------------------------------------ playback
    def set_actions(self, actions) -> None:
        self.sync.set_actions(actions)

    def tick(self, media_ms: float, playing: bool) -> None:
        try:
            self.sync.tick(media_ms, playing)
        except DeviceError as e:
            self.message.emit(f"Fehler: {e}")

    def device_position(self) -> float | None:
        return self.device.current_position() if self.device is not None else None

    def emergency_stop(self) -> None:
        self.sync.stop()

    def test_move(self, pos: float) -> None:
        if self.device is not None and self.device.connected:
            self.sync.stop()
            self.device.move(self.sync.settings.map(pos), 800)

    def shutdown(self) -> None:
        self.disconnect_device()

    # ------------------------------------------------------------ ui
    def _on_message(self, msg: str) -> None:
        connected = self.device is not None and self.device.connected
        if connected:
            name = self.device.name
            if isinstance(self.device, ButtplugDevice) and self.device.selected in self.device.devices:
                name = self.device.devices[self.device.selected].name
            self.status.setText(f"Gerät · {name}")
        else:
            self.status.setText("Gerät · nicht verbunden")
        self.status.setToolTip(msg)
        self.win._status(msg)
        if self.dialog is not None:
            self.dialog.log(msg)
            self.dialog.refresh()

    def open_dialog(self) -> None:
        if self.dialog is None:
            self.dialog = DeviceDialog(self, self.win)
            self.changed.connect(self.dialog.refresh)
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()


INPUT_H = 34
BUTTON_H = 36


class DeviceDialog(QDialog):
    def __init__(self, ctl: DeviceController, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Gerät")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(8)
        # the window can never be shrunk below what the content needs
        lay.setSizeConstraint(QLayout.SetMinimumSize)

        title = QLabel("Verbindung")
        title.setObjectName("section")
        lay.addWidget(title)
        cap = QLabel("Für den Fredorch F21S: Intiface Central starten, Server starten, dann hier verbinden. "
                     "Die Simulation zeigt auf der Hubschiene, wie sich das Gerät bewegen würde.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        cap.setMinimumWidth(412)          # 460 px dialog minus margins
        lay.addWidget(cap)
        f = QFormLayout()
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        self.kind = QComboBox()
        for label, val in KINDS:
            self.kind.addItem(label, val)
        i = self.kind.findData(ctl.qs.value("device/kind", "sim"))
        self.kind.setCurrentIndex(max(0, i))
        f.addRow(self._label("Art"), self.kind)
        self.url = QLineEdit(str(ctl.qs.value("device/url", DEFAULT_URL) or DEFAULT_URL))
        self.url.setFont(theme.mono_font(12))
        f.addRow(self._label("Intiface-Adresse"), self.url)
        self.device_box = QComboBox()
        f.addRow(self._label("Gerät"), self.device_box)
        lay.addLayout(f)
        row = QHBoxLayout()
        self.connect_btn = QPushButton(theme.icon("bluetooth", theme.ON_ACCENT), "Verbinden")
        self.connect_btn.setObjectName("primary")
        self.scan_btn = QPushButton("Geräte suchen")
        self.disconnect_btn = QPushButton("Trennen")
        row.addWidget(self.connect_btn)
        row.addWidget(self.scan_btn)
        row.addWidget(self.disconnect_btn)
        lay.addLayout(row)

        lay.addSpacing(16)
        title = QLabel("Wiedergabe")
        title.setObjectName("section")
        lay.addWidget(title)
        f = QFormLayout()
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        s = ctl.sync.settings
        self.offset = self._slider(-300, 600, s.offset_ms, " ms")
        self.offset[1].setToolTip("Positiv = Gerät bekommt Befehle früher (gleicht die Bluetooth-Verzögerung aus)")
        f.addRow(self._label("Zeitversatz"), self.offset[0])
        self.speed = self._slider(50, 1000, int(s.max_speed), " /s")
        self.speed[1].setToolTip("Höchstgeschwindigkeit in Positionen pro Sekunde. "
                                 "F21S: voller Hub frühestens in ca. 200 ms = 500 /s")
        f.addRow(self._label("Max. Tempo"), self.speed[0])
        rng = QWidget()
        rl = QHBoxLayout(rng)
        rl.setContentsMargins(0, 0, 0, 0)
        self.pmin, self.pmax = QSpinBox(), QSpinBox()
        for sp, v in ((self.pmin, s.pos_min), (self.pmax, s.pos_max)):
            sp.setRange(0, 100)
            sp.setValue(v)
            sp.setButtonSymbols(QSpinBox.NoButtons)
            sp.setAlignment(Qt.AlignRight)
            sp.setFont(theme.mono_font(12))
        bis = QLabel("bis")
        bis.setObjectName("caption")
        rl.addWidget(self.pmin, 1)
        rl.addWidget(bis)
        rl.addWidget(self.pmax, 1)
        f.addRow(self._label("Hubbereich"), rng)
        self.invert = QCheckBox("Richtung umkehren")
        self.invert.setChecked(s.invert)
        f.addRow(QLabel(), self.invert)
        lay.addLayout(f)

        lay.addSpacing(16)
        title = QLabel("Test")
        title.setObjectName("section")
        lay.addWidget(title)
        row = QHBoxLayout()
        self.up_btn = QPushButton(theme.icon("arrow-up"), "Oben")
        self.down_btn = QPushButton(theme.icon("arrow-down"), "Unten")
        self.stop_btn = QPushButton(theme.icon("x"), "Stopp  (Esc)")
        for b in (self.up_btn, self.down_btn, self.stop_btn):
            row.addWidget(b)
        lay.addLayout(row)
        self.log_label = QLabel()
        self.log_label.setObjectName("meta")
        self.log_label.setWordWrap(True)
        self.log_label.setFont(theme.mono_font(11))
        self.log_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        # room for three lines, so a status message does not resize the dialog
        self.log_label.setMinimumHeight(self.log_label.fontMetrics().lineSpacing() * 3 + 4)
        lay.addWidget(self.log_label)
        lay.addStretch(1)
        # fixed control heights, set after the stylesheet has been applied
        # (QSS min-height would otherwise override them on first show)
        self.ensurePolished()
        for w in self.findChildren(QWidget):
            w.ensurePolished()
        rng.setFixedHeight(INPUT_H)
        for w in (self.kind, self.url, self.device_box, self.pmin, self.pmax):
            w.setFixedHeight(INPUT_H)
        for b in (self.connect_btn, self.scan_btn, self.disconnect_btn,
                  self.up_btn, self.down_btn, self.stop_btn):
            b.setFixedHeight(BUTTON_H)
        self.invert.setMinimumHeight(24)
        for sl in (self.offset[0], self.speed[0]):
            sl.setMinimumHeight(24)

        self.connect_btn.clicked.connect(lambda: ctl.connect_device(self.kind.currentData(), self.url.text().strip()))
        self.disconnect_btn.clicked.connect(ctl.disconnect_device)
        self.scan_btn.clicked.connect(self._scan)
        self.device_box.activated.connect(self._select)
        self.kind.currentIndexChanged.connect(self.refresh)
        self.up_btn.clicked.connect(lambda: ctl.test_move(100))
        self.down_btn.clicked.connect(lambda: ctl.test_move(0))
        self.stop_btn.clicked.connect(ctl.emergency_stop)
        for w in (self.offset[1], self.speed[1]):
            w.valueChanged.connect(self._apply)
        for w in (self.pmin, self.pmax):
            w.valueChanged.connect(self._apply)
        self.invert.toggled.connect(self._apply)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _label(text):
        lbl = QLabel(text)
        lbl.setObjectName("formLabel")
        lbl.setMinimumWidth(112)
        lbl.setFixedHeight(INPUT_H)       # same height as the field: label sits centred
        lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return lbl

    @staticmethod
    def _slider(lo, hi, val, suffix):
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        lbl = QLabel(f"{val}{suffix}")
        lbl.setObjectName("value")
        lbl.setFont(theme.mono_font(12))
        lbl.setFixedWidth(72)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        s.valueChanged.connect(lambda v: lbl.setText(f"{v}{suffix}"))
        lay.addWidget(s, 1)
        lay.addWidget(lbl)
        return box, s

    def _apply(self):
        s = self.ctl.sync.settings
        s.offset_ms = self.offset[1].value()
        s.max_speed = float(self.speed[1].value())
        s.pos_min, s.pos_max = self.pmin.value(), self.pmax.value()
        s.invert = self.invert.isChecked()
        self.ctl.save_sync()

    def _scan(self):
        dev = self.ctl.device
        if isinstance(dev, ButtplugDevice) and dev.connected:
            try:
                dev.start_scanning()
            except DeviceError as e:
                self.log(f"Fehler: {e}")

    def _select(self, i: int):
        dev = self.ctl.device
        idx = self.device_box.itemData(i)
        if isinstance(dev, ButtplugDevice) and idx is not None:
            dev.select(idx)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:      # Esc also means emergency stop here
            self.ctl.emergency_stop()
        super().keyPressEvent(ev)

    def log(self, msg: str):
        self.log_label.setText(msg)

    def refresh(self):
        dev = self.ctl.device
        connected = dev is not None and dev.connected
        intiface = self.kind.currentData() == "intiface"
        self.url.setEnabled(intiface and not connected)
        self.kind.setEnabled(not connected)
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        self.scan_btn.setEnabled(connected and isinstance(dev, ButtplugDevice))
        for b in (self.up_btn, self.down_btn, self.stop_btn):
            b.setEnabled(connected)
        self.device_box.clear()
        if isinstance(dev, ButtplugDevice) and connected:
            for d in dev.devices.values():
                self.device_box.addItem(d.name + ("" if d.is_linear else "  (keine Positionssteuerung)"), d.index)
            if dev.selected is not None:
                self.device_box.setCurrentIndex(max(0, self.device_box.findData(dev.selected)))
        elif connected:
            self.device_box.addItem(dev.name, None)
        self.device_box.setEnabled(self.device_box.count() > 1)
