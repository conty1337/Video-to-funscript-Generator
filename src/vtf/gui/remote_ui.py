"""Phone remote in the GUI: settings dialog, server lifetime, commands to the pattern control."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..remote import DEFAULT_PORT, RemoteServer, new_pin
from . import theme


class RemoteController(QObject):
    command = Signal(object)          # from the server thread, handled in the GUI thread

    def __init__(self, window, settings):
        super().__init__(window)
        self.win = window
        self.qs = settings
        self.pin = str(self.qs.value("remote/pin", "") or "") or new_pin()
        self.qs.setValue("remote/pin", self.pin)
        try:
            self.port = int(self.qs.value("remote/port", DEFAULT_PORT))
        except (TypeError, ValueError):
            self.port = DEFAULT_PORT
        self.server: RemoteServer | None = None
        self.error = ""
        self._state: dict = {}
        self.command.connect(self._handle, Qt.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self._snapshot)
        if str(self.qs.value("remote/enabled", "false")).lower() == "true":
            self.set_enabled(True)

    @property
    def enabled(self) -> bool:
        return self.server is not None and self.server.running

    def set_enabled(self, on: bool) -> None:
        self.qs.setValue("remote/enabled", "true" if on else "false")
        if on and not self.enabled:
            self.error = ""
            self.server = RemoteServer(lambda: self._state, self.command.emit, self.pin, self.port)
            try:
                self.server.start()
            except OSError as e:
                self.server = None
                self.error = f"Port {self.port} nicht verfügbar: {e}"
                return
            self._snapshot()
            self.timer.start()
        elif not on and self.server is not None:
            self.server.stop()
            self.server = None
            self.timer.stop()

    def new_pin(self) -> None:
        self.pin = new_pin()
        self.qs.setValue("remote/pin", self.pin)
        if self.server is not None:
            self.server.pin = self.pin

    def set_port(self, port: int) -> None:
        self.port = port
        self.qs.setValue("remote/port", port)
        if self.enabled:
            self.set_enabled(False)
            self.set_enabled(True)

    def urls(self) -> list[str]:
        return self.server.urls() if self.server is not None else []

    def shutdown(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None

    # ------------------------------------------------------------ state and commands
    def _snapshot(self) -> None:
        d = self.win.ensure_patterns()
        st = d.remote_state()
        dev = self.win.devices.device
        st["device"] = f"Gerät: {dev.name}" if dev is not None and dev.connected else "Kein Gerät verbunden"
        st["safety"] = self.win.devices.safety_label.text()
        self._state = st

    def _handle(self, c: dict) -> None:
        self.win.devices.touch()
        if c.get("cmd") == "estop":
            self.win.emergency_stop()
        else:
            self.win.ensure_patterns().remote_command(c)
        self._snapshot()


class RemoteDialog(QDialog):
    def __init__(self, ctl: RemoteController, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Handy-Fernbedienung")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        cap = QLabel("Steuere Muster, Abläufe und Zufall vom Handy aus. Handy und PC im selben WLAN, "
                     "keine App nötig: Adresse im Browser öffnen, PIN eingeben.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        cap.setMinimumWidth(460)
        lay.addWidget(cap)
        self.on = QCheckBox("Fernbedienung an")
        self.on.setChecked(ctl.enabled)
        self.on.setMinimumHeight(28)
        lay.addWidget(self.on)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.addr = QLabel()
        self.addr.setFont(theme.mono_font(14))
        self.addr.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow(self._label("Adresse"), self.addr)
        self.pin = QLabel()
        self.pin.setFont(theme.mono_font(14))
        self.pin.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pin_btn = QPushButton("Neue PIN")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.pin, 1)
        row.addWidget(self.pin_btn)
        form.addRow(self._label("PIN"), row)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(ctl.port)
        self.port.setMinimumWidth(120)
        prow = QHBoxLayout()
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(self.port)
        prow.addStretch(1)
        form.addRow(self._label("Port"), prow)
        lay.addLayout(form)
        self.err = QLabel()
        self.err.setObjectName("caption")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)
        self.hint = QLabel()
        self.hint.setObjectName("caption")
        self.hint.setWordWrap(True)
        self.hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.hint)
        self.on.toggled.connect(self._toggled)
        self.pin_btn.clicked.connect(self._new_pin)
        self.port.editingFinished.connect(lambda: (ctl.set_port(self.port.value()), self.refresh()))
        self.refresh()

    @staticmethod
    def _label(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("formLabel")
        lb.setMinimumWidth(72)
        return lb

    def _toggled(self, on: bool):
        self.ctl.set_enabled(on)
        self.refresh()

    def _new_pin(self):
        self.ctl.new_pin()
        self.refresh()

    def refresh(self):
        on = self.ctl.enabled
        self.addr.setText("\n".join(self.ctl.urls()) if on else "aus")
        self.pin.setText(self.ctl.pin)
        self.err.setText(self.ctl.error)
        self.err.setVisible(bool(self.ctl.error))
        self.hint.setText("Klappt es nicht, lässt die Firewall den Port evtl. nicht durch. Einmalig im Terminal:\n"
                          f"sudo firewall-cmd --add-port={self.ctl.port}/tcp --permanent && "
                          "sudo firewall-cmd --reload")
        if self.ctl.error:
            self.on.blockSignals(True)
            self.on.setChecked(False)
            self.on.blockSignals(False)
