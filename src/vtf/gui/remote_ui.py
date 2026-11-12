"""Phone remote in the GUI: settings dialog, server lifetime, commands to the pattern control."""

from __future__ import annotations

import bisect
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
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

from ..funscript import position_at
from ..i18n import tr
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
                self.error = tr("Port {0} nicht verfügbar: {1}", self.port, e)
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
        st["device"] = tr("Gerät: {0}", dev.name) if dev is not None and dev.connected else tr("Kein Gerät verbunden")
        st["safety"] = self.win.devices.safety_label.text()
        st["video"] = self._video_state()
        self._state = st

    VIDEO_BEFORE_MS = 3000          # script window on the phone around the playhead
    VIDEO_AFTER_MS = 12000
    MAX_ITEMS = 500

    def _video_state(self) -> dict:
        """Video player for the phone: what plays, where, how long still, the playlist."""
        w = self.win
        pl = w.player.playlist
        loaded = bool(getattr(w, "cap", None))
        dur = w.frame_count / w.fps * 1000 if loaded and w.fps else 0.0
        playing = loaded and w.timer.isActive()
        pos = (w._media_ms() if playing else w._cur_ms()) if loaded else 0.0
        pos = max(0.0, min(dur, pos))
        acts = w.stroke_actions() if loaded else []
        curve = []
        if acts:
            t0, t1 = pos - self.VIDEO_BEFORE_MS, pos + self.VIDEO_AFTER_MS
            span = t1 - t0
            times = [a.at for a in acts]
            i, j = bisect.bisect_left(times, t0), bisect.bisect_right(times, t1)
            pts = [(t0, position_at(acts, t0))] + [(a.at, a.pos) for a in acts[i:j]] + [(t1, position_at(acts, t1))]
            curve = [[round((t - t0) / span, 4), round(v / 100, 3)] for t, v in pts if v is not None]
        return {
            "title": Path(w.video_path).stem if loaded and w.video_path else "",
            "playing": playing, "pos": round(pos), "dur": round(dur), "script": bool(acts),
            "items": [Path(x).stem for x in pl.items[:self.MAX_ITEMS]], "current": pl.current,
            "player": w.player_mode, "curve": curve,
            "now": self.VIDEO_BEFORE_MS / (self.VIDEO_BEFORE_MS + self.VIDEO_AFTER_MS),
        }

    def _video_command(self, c: dict) -> None:
        w = self.win
        cmd = c.get("cmd")
        pl = w.player.playlist
        if cmd == "vplay":
            try:
                w.play_index(int(c.get("index", -1)))
            except (TypeError, ValueError):
                pass
        elif cmd == "vtoggle":
            if getattr(w, "cap", None):
                w.toggle_play()
            elif pl.items:
                w.play_index(pl.current if 0 <= pl.current < len(pl.items) else 0)
        elif cmd == "vnext":
            w.play_next()
        elif cmd == "vprev":
            w.play_prev()
        elif cmd in ("vseek", "vskip") and getattr(w, "cap", None):
            try:
                ms = float(c.get("ms", 0))
            except (TypeError, ValueError):
                return
            if cmd == "vskip":
                ms += w._media_ms() if w.timer.isActive() else w._cur_ms()
            dur = w.frame_count / w.fps * 1000
            w.seek_ms(int(max(0.0, min(dur - 1, ms))))

    def _handle(self, c: dict) -> None:
        self.win.devices.touch()
        if c.get("cmd") == "estop":
            self.win.emergency_stop()
        elif str(c.get("cmd", "")).startswith("v"):
            self._video_command(c)
        else:
            self.win.ensure_patterns().remote_command(c)
        self._snapshot()


def qr_pixmap(text: str, module_px: int = 6):
    """QR code of `text` as a pixmap (dark on white with the quiet zone), None without segno."""
    try:
        import segno
    except ImportError:
        return None
    rows = [list(r) for r in segno.make(text, error="m", micro=False).matrix_iter(scale=1, border=4)]
    n = len(rows)
    img = QImage(n * module_px, n * module_px, QImage.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0))
    for y, row in enumerate(rows):
        for x, dark in enumerate(row):
            if dark:
                p.drawRect(x * module_px, y * module_px, module_px, module_px)
    p.end()
    return QPixmap.fromImage(img)


class RemoteDialog(QDialog):
    def __init__(self, ctl: RemoteController, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle(tr("Handy-Fernbedienung"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        cap = QLabel(tr("Steuere Muster, Abläufe und Zufall vom Handy aus. Handy und PC im selben WLAN, keine App nötig: Adresse im Browser öffnen, PIN eingeben."))
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        cap.setMinimumWidth(460)
        lay.addWidget(cap)
        self.on = QCheckBox(tr("Fernbedienung an"))
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
        form.addRow(self._label(tr("Adresse")), self.addr)
        self.pin = QLabel()
        self.pin.setFont(theme.mono_font(14))
        self.pin.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pin_btn = QPushButton(tr("Neue PIN"))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.pin, 1)
        row.addWidget(self.pin_btn)
        form.addRow(self._label(tr("PIN")), row)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(ctl.port)
        self.port.setMinimumWidth(120)
        prow = QHBoxLayout()
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(self.port)
        prow.addStretch(1)
        form.addRow(self._label(tr("Port")), prow)
        lay.addLayout(form)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setToolTip(tr("Mit der Handy-Kamera scannen: öffnet die Fernbedienung, die PIN ist schon drin"))
        self.qr_cap = QLabel(tr("Mit der Handy-Kamera scannen – die PIN ist schon drin"))
        self.qr_cap.setObjectName("caption")
        self.qr_cap.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.qr)
        lay.addWidget(self.qr_cap)
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
        self.addr.setText("\n".join(self.ctl.urls()) if on else tr("aus"))
        self.pin.setText(self.ctl.pin)
        urls = self.ctl.urls() if on else []
        pix = qr_pixmap(f"{urls[0]}#pin={self.ctl.pin}") if urls else None
        self.qr.setVisible(pix is not None)
        self.qr_cap.setVisible(pix is not None)
        if pix is not None:
            self.qr.setPixmap(pix)
        self.err.setText(self.ctl.error)
        self.err.setVisible(bool(self.ctl.error))
        self.hint.setText(tr("Klappt es nicht, lässt die Firewall den Port evtl. nicht durch. Einmalig im Terminal:\nsudo firewall-cmd --add-port={0}/tcp --permanent\nsudo firewall-cmd --reload", self.ctl.port))
        if self.ctl.error:
            self.on.blockSignals(True)
            self.on.setChecked(False)
            self.on.blockSignals(False)
