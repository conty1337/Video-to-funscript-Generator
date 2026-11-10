"""Device connection in the GUI: dialog, background connect, sync settings."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..device import Device, DeviceError, SimulatedDevice
from ..device.buttplug import DEFAULT_URL, VIBE_MODES, ButtplugDevice
from ..device.sync import ScriptSync, SyncSettings
from ..device.tcode import DEFAULT_PORT as TCODE_PORT
from ..device.tcode import AxisProxy, TCodeDevice
from ..safety import SafetyGuard, SafetySettings
from . import theme

KINDS = [("Simulation (ohne Gerät)", "sim"), ("Intiface Central (Bluetooth)", "intiface"),
         ("OSR2/SR6 u.a. (TCode, USB oder WLAN)", "tcode")]


def default_address(kind: str) -> str:
    return TCODE_PORT if kind == "tcode" else DEFAULT_URL


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
        self.axis_syncs: dict = {}
        self._axis_actions: dict = {}
        self._connecting = False
        self.status = QLabel("Gerät · nicht verbunden")
        self.status.setFont(theme.mono_font(11))
        window.statusBar().addPermanentWidget(self.status)
        self.message.connect(self._on_message)
        # safety timer: everything that moves the device registers as a source
        self.sources: list = []
        self._paused_by_safety: list = []
        self.safety = SafetyGuard(self._load_safety())
        self.safety.touch(self._ms())
        self.safety_label = QLabel()
        self.safety_label.setFont(theme.mono_font(11))
        self.safety_label.hide()
        window.statusBar().addPermanentWidget(self.safety_label)
        self._safety_timer = QTimer(self)
        self._safety_timer.setInterval(1000)
        self._safety_timer.timeout.connect(self._safety_tick)
        self._safety_timer.start()
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

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

    def _load_safety(self) -> SafetySettings:
        s = SafetySettings()
        for key in SafetySettings.KEYS:
            v = self.qs.value(f"safety/{key}")
            if v is None:
                continue
            if key == "enabled":
                s.enabled = str(v).lower() == "true"
            else:
                try:
                    setattr(s, key, max(0, int(v)))
                except (TypeError, ValueError):
                    pass
        return s

    def save_safety(self) -> None:
        for key in SafetySettings.KEYS:
            v = getattr(self.safety.settings, key)
            self.qs.setValue(f"safety/{key}", ("true" if v else "false") if key == "enabled" else v)

    # ------------------------------------------------------------ safety timer
    @staticmethod
    def _ms() -> float:
        return time.monotonic() * 1000

    def register_source(self, src) -> None:
        """src: safety_active() -> bool, safety_pause() -> bool, safety_resume()."""
        if src not in self.sources:
            self.sources.append(src)

    def touch(self) -> None:
        self.safety.touch(self._ms())

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.Wheel):
            self.safety.touch(self._ms())
        return False

    def _safety_tick(self) -> None:
        now = self._ms()
        active = any(_call(src, "safety_active") for src in self.sources)
        ev = self.safety.update(now, active)
        if ev is not None:
            if ev[0] == "stop":
                self._paused_by_safety = []
                self.win.emergency_stop()
                self.message.emit(ev[1])
            elif ev[0] == "break":
                self._paused_by_safety = [src for src in self.sources if _call(src, "safety_pause")]
                self.sync.stop()
                self.message.emit(f"Sicherheits-Timer: {ev[1]} s Pause, danach geht es weiter")
            elif ev[0] == "resume":
                for src in self._paused_by_safety:
                    _call(src, "safety_resume")
                self._paused_by_safety = []
                self.message.emit("Pause vorbei, weiter geht's")
        text = self.safety.status(now)
        self.safety_label.setText(text)
        self.safety_label.setVisible(bool(text))

    def save_sync(self) -> None:
        s = self.sync.settings
        for key in ("offset_ms", "pos_min", "pos_max", "max_speed", "invert"):
            self.qs.setValue(f"device/{key}", getattr(s, key))

    # ------------------------------------------------------------ connection
    def connect_device(self, kind: str, url: str) -> None:
        if self._connecting:
            return
        self.disconnect_device()
        if kind == "sim":
            dev: Device = SimulatedDevice()
        elif kind == "tcode":
            dev = TCodeDevice(url or TCODE_PORT)
            self.qs.setValue("device/tcode", url)
        else:
            dev = ButtplugDevice(url or DEFAULT_URL)
            dev.vibe_mode = str(self.qs.value("device/vibe", "speed"))
            self.qs.setValue("device/url", url)
        dev.add_listener(self.message.emit)
        self.qs.setValue("device/kind", kind)
        self._connecting = True
        self.message.emit("Verbinde…")

        def run():
            try:
                dev.connect()
                if isinstance(dev, ButtplugDevice) and not any(d.usable for d in dev.devices.values()):
                    dev.start_scanning()
                self.device = dev
                self.sync.set_device(dev)
                self._make_axis_syncs(dev)
            except DeviceError as e:
                self.message.emit(f"Fehler: {e}")
            finally:
                self._connecting = False
                self.changed.emit()

        threading.Thread(target=run, name="device-connect", daemon=True).start()

    def _make_axis_syncs(self, dev) -> None:
        """Multi-axis devices (TCode): one ScriptSync per extra axis."""
        self.axis_syncs = {}
        for ax in getattr(dev, "axes", ())[1:]:
            self.axis_syncs[ax] = ScriptSync(AxisProxy(dev, ax), SyncSettings())
            self.axis_syncs[ax].set_actions(self._axis_actions.get(ax, []))

    def set_axes(self, axes: dict) -> None:
        """All script axes (L1, R0, …) for multi-axis devices; the stroke goes through set_actions."""
        self._axis_actions = {k: list(v) for k, v in axes.items() if k != "L0"}
        for ax, sync in self.axis_syncs.items():
            sync.set_actions(self._axis_actions.get(ax, []))

    def set_vibe_mode(self, mode: str) -> None:
        self.qs.setValue("device/vibe", mode)
        if isinstance(self.device, ButtplugDevice):
            self.device.vibe_mode = mode

    def disconnect_device(self) -> None:
        dev, self.device = self.device, None
        self.sync.set_device(None)
        self.axis_syncs = {}
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
            main = self.sync.settings
            for sync in self.axis_syncs.values():
                sync.settings.offset_ms, sync.settings.max_speed = main.offset_ms, main.max_speed
                sync.tick(media_ms, playing)
        except DeviceError as e:
            self.message.emit(f"Fehler: {e}")

    def device_position(self) -> float | None:
        return self.device.current_position() if self.device is not None else None

    def emergency_stop(self) -> None:
        self.sync.stop()
        for sync in self.axis_syncs.values():
            sync.stop()

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

    def open_wizard(self) -> None:
        from .setup_wizard import SetupWizard

        SetupWizard(self, self.win).exec()
        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None             # rebuilt with the new values next time

    def open_dialog(self) -> None:
        if self.dialog is None:
            self.dialog = DeviceDialog(self, self.win)
            self.changed.connect(self.dialog.refresh)
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()


def _call(obj, name: str):
    fn = getattr(obj, name, None)
    try:
        return fn() if fn is not None else None
    except Exception:
        return None


INPUT_H = 34
BUTTON_H = 36


class DeviceDialog(QDialog):
    def __init__(self, ctl: DeviceController, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Gerät")
        # the content scrolls when the screen is too low for it; the window opens as tall as it needs
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.body = QWidget()
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(8)

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
        f.setVerticalSpacing(12)
        self.kind = QComboBox()
        for label, val in KINDS:
            self.kind.addItem(label, val)
        i = self.kind.findData(ctl.qs.value("device/kind", "sim"))
        self.kind.setCurrentIndex(max(0, i))
        f.addRow(self._label("Art"), self.kind)
        self.url = QLineEdit(self._address_for(self.kind.currentData()))
        self.url.setFont(theme.mono_font(12))
        self.url_label = self._label("Intiface-Adresse")
        self._shown_kind = None
        f.addRow(self.url_label, self.url)
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
        self.wizard_btn = QPushButton("Einrichtungs-Assistent…")
        self.wizard_btn.setToolTip("Schritt für Schritt: Verbindung, Richtung, Hubbereich, Höchsttempo, Verzögerung")
        self.wizard_btn.clicked.connect(ctl.open_wizard)
        lay.addWidget(self.wizard_btn)

        lay.addSpacing(16)
        lay.addWidget(theme.section_title("Wiedergabe", self.reset_playback,
                                          "Zeitversatz, Tempo, Hubbereich, Richtung und Vibration auf Standard"))
        f = QFormLayout()
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(12)
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
        self.vibe = QComboBox()
        for code, label in VIBE_MODES:
            self.vibe.addItem(label, code)
        self.vibe.setCurrentIndex(max(0, self.vibe.findData(ctl.qs.value("device/vibe", "speed"))))
        self.vibe.setToolTip("Nur für Vibratoren und andere Geräte ohne Hub: woraus die Stärke wird")
        self.vibe.currentIndexChanged.connect(lambda _i: ctl.set_vibe_mode(self.vibe.currentData()))
        f.addRow(self._label("Vibration folgt"), self.vibe)
        lay.addLayout(f)

        lay.addSpacing(16)
        lay.addWidget(theme.section_title("Sicherheit", self.reset_safety, "Sicherheits-Timer auf Standard"))
        g = ctl.safety.settings
        self.safe_on = QCheckBox("Sicherheits-Timer an")
        self.safe_on.setChecked(g.enabled)
        lay.addWidget(self.safe_on)
        f = QFormLayout()
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(12)
        self.safe_max = self._minutes(g.max_minutes, 600)
        self.safe_idle = self._minutes(g.idle_minutes, 240)
        self.safe_every = self._minutes(g.break_every, 240)
        self.safe_secs = QSpinBox()
        self.safe_secs.setRange(5, 600)
        self.safe_secs.setSuffix(" s")
        self.safe_secs.setValue(max(5, g.break_seconds))
        f.addRow(self._label("Höchstens"), self._row(self.safe_max, "am Stück, dann Stopp"))
        f.addRow(self._label("Ohne Bedienung"), self._row(self.safe_idle, "dann Stopp"))
        f.addRow(self._label("Pause alle"), self._row(self.safe_every, "für", self.safe_secs))
        self.safe_form = QWidget()
        f.setContentsMargins(0, 0, 0, 0)
        self.safe_form.setLayout(f)
        lay.addWidget(self.safe_form)
        cap = QLabel("0 = aus. Gezählt wird nur, solange sich das Gerät bewegt (Video, Muster, Ablauf, "
                     "Musik, anderer Player). Bedienen heißt Taste, Maus, Controller oder Handy.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        lay.addWidget(cap)
        for w in (self.safe_max, self.safe_idle, self.safe_every, self.safe_secs):
            w.valueChanged.connect(self._apply_safety)
        self.safe_on.toggled.connect(self._apply_safety)
        self.safe_form.setEnabled(g.enabled)

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
        self.scroll.setWidget(self.body)
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
        self.invert.setMinimumHeight(28)
        self.safe_on.setMinimumHeight(28)
        for sl in (self.offset[0], self.speed[0]):
            sl.setFixedHeight(INPUT_H)

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
        theme.double_click_reset(self.offset[1], SyncSettings().offset_ms)
        theme.double_click_reset(self.speed[1], int(SyncSettings().max_speed))
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

    @staticmethod
    def _minutes(v: int, maximum: int) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(0, maximum)
        sp.setSuffix(" min")
        sp.setSpecialValueText("aus")
        sp.setValue(v)
        sp.setFixedHeight(INPUT_H)
        return sp

    @staticmethod
    def _row(*items) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        for x in items:
            if isinstance(x, str):
                lb = QLabel(x)
                lb.setObjectName("caption")
                lay.addWidget(lb)
            else:
                lay.addWidget(x)
        lay.addStretch(1)
        return w

    def reset_playback(self):
        d = SyncSettings()
        for w, v in ((self.offset[1], d.offset_ms), (self.speed[1], int(d.max_speed)), (self.pmin, d.pos_min),
                     (self.pmax, d.pos_max)):
            w.setValue(v)
        self.invert.setChecked(d.invert)
        self.vibe.setCurrentIndex(max(0, self.vibe.findData(VIBE_MODES[0][0])))
        self._apply()

    def reset_safety(self):
        d = SafetySettings()
        for w, v in ((self.safe_max, d.max_minutes), (self.safe_idle, d.idle_minutes),
                     (self.safe_every, d.break_every), (self.safe_secs, d.break_seconds)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.safe_on.setChecked(d.enabled)
        self._apply_safety()

    def _apply_safety(self):
        g = self.ctl.safety.settings
        g.enabled = self.safe_on.isChecked()
        g.max_minutes = self.safe_max.value()
        g.idle_minutes = self.safe_idle.value()
        g.break_every = self.safe_every.value()
        g.break_seconds = self.safe_secs.value()
        self.safe_form.setEnabled(g.enabled)
        self.ctl.touch()
        self.ctl.save_safety()

    def _apply(self):
        s = self.ctl.sync.settings
        s.offset_ms = self.offset[1].value()
        s.max_speed = float(self.speed[1].value())
        s.pos_min, s.pos_max = self.pmin.value(), self.pmax.value()
        s.invert = self.invert.isChecked()
        self.ctl.save_sync()

    def _address_for(self, kind: str) -> str:
        key = "device/tcode" if kind == "tcode" else "device/url"
        return str(self.ctl.qs.value(key, "") or "") or default_address(kind)

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

    def showEvent(self, ev):
        super().showEvent(ev)
        if not getattr(self, "_sized", False):
            self._sized = True
            self._fit_to_content()

    def _fit_to_content(self) -> None:
        """As wide as the content needs (plus room for the scroll bar), as tall as it needs up to the screen."""
        lay = self.body.layout()
        lay.activate()
        bar = self.scroll.verticalScrollBar().sizeHint().width()
        w = max(self.body.minimumSizeHint().width(), self.body.sizeHint().width()) + bar
        h = lay.totalHeightForWidth(w - bar) if lay.hasHeightForWidth() else self.body.sizeHint().height()
        scr = self.screen()
        if scr is not None:
            h = min(h, scr.availableGeometry().height() - 60)
        self.setMinimumWidth(w)
        self.setMinimumHeight(min(h, 360))
        self.resize(w, h)

    def refresh(self):
        dev = self.ctl.device
        connected = dev is not None and dev.connected
        kind = self.kind.currentData()
        self.url.setEnabled(kind in ("intiface", "tcode") and not connected)
        if not connected and kind != self._shown_kind:
            self._shown_kind = kind
            self.url.setText(self._address_for(kind))
            self.url_label.setText("Port / Adresse" if kind == "tcode" else "Intiface-Adresse")
        self.kind.setEnabled(not connected)
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        self.scan_btn.setEnabled(connected and isinstance(dev, ButtplugDevice))
        for b in (self.up_btn, self.down_btn, self.stop_btn):
            b.setEnabled(connected)
        self.device_box.clear()
        if isinstance(dev, ButtplugDevice) and connected:
            for d in dev.devices.values():
                self.device_box.addItem(d.name + ("" if d.is_linear else "  (Vibration)" if d.scalars
                                                  else "  (nicht steuerbar)"), d.index)
            if dev.selected is not None:
                self.device_box.setCurrentIndex(max(0, self.device_box.findData(dev.selected)))
        elif connected:
            self.device_box.addItem(dev.name, None)
        self.device_box.setEnabled(self.device_box.count() > 1)
