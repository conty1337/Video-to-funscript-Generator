"""Other players in the GUI: connect to mpv/VLC/DeoVR/HereSphere and drive the device."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from .. import players as PL
from ..device.sync import ScriptSync
from ..funscript import Funscript
from . import theme

HINTS = {
    "mpv": "mpv so starten:  mpv --input-ipc-server=/tmp/mpvsocket video.mp4\n"
           "Oder dauerhaft in ~/.config/mpv/mpv.conf die Zeile  input-ipc-server=/tmp/mpvsocket  eintragen.",
    "vlc": "In VLC: Werkzeuge → Einstellungen → unten „Alle“ → Schnittstellen → Hauptschnittstellen → „Web“ "
           "anhaken, dann unter Hauptschnittstellen → Lua ein Passwort setzen und VLC neu starten. "
           "VLC meldet die Zeit nur grob, mpv ist genauer.",
    "deovr": "In DeoVR: Einstellungen → „Remote control“ einschalten. Adresse = IP der Brille im WLAN.",
    "heresphere": "In HereSphere: Einstellungen → „DeoVR Remote“ einschalten. Adresse = IP der Brille im WLAN.",
}


class ExternalSync(QObject):
    changed = Signal()

    POLL_S = 0.1

    def __init__(self, window, ctl, settings):
        super().__init__(window)
        self.win = window
        self.ctl = ctl
        self.qs = settings
        self.sync = ScriptSync(ctl.device, ctl.sync.settings)
        self.player = None
        self.clock = PL.SmoothClock()
        self.status = "Nicht verbunden"
        self.path = ""
        self.script = ""
        self.points = 0
        self._latest = None           # (state, wall_ms)
        self._error = ""
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)
        ctl.changed.connect(self._device_changed)
        ctl.register_source(self)

    @property
    def running(self) -> bool:
        return self.player is not None

    def folders(self) -> list[str]:
        return [f for f in str(self.qs.value("players/folder", "") or "").split(";") if f]

    # ------------------------------------------------------------ connection
    def connect_player(self, kind: str, **kw) -> str:
        self.disconnect_player()
        player = PL.make_player(kind, **kw)
        try:
            player.connect()
        except PL.PlayerError as e:
            self.status = str(e)
            self.changed.emit()
            return self.status
        if hasattr(self.win, "pause") and self.win.timer.isActive():
            self.win.pause()
        self.win._stop_patterns()
        self.player = player
        self.clock = PL.SmoothClock(1200 if kind == "vlc" else 400)
        self.path = self.script = ""
        self._error = ""
        self._latest = None
        self.status = f"Verbunden mit {player.name}, warte auf ein Video…"
        self._thread = threading.Thread(target=self._poll_loop, args=(player,), name="player-poll", daemon=True)
        self._thread.start()
        self.timer.start()
        self.changed.emit()
        return self.status

    def disconnect_player(self) -> None:
        p, self.player = self.player, None
        self.timer.stop()
        self.sync.stop()
        if p is not None:
            p.close()
            self.status = "Getrennt"
            self.changed.emit()

    def _poll_loop(self, player) -> None:
        while self.player is player:
            try:
                st = player.poll()
                with self._lock:
                    self._latest = (st, time.monotonic() * 1000)
            except PL.PlayerError as e:
                with self._lock:
                    self._error = str(e)
                return
            time.sleep(self.POLL_S)

    def _device_changed(self):
        if self.ctl.device is not self.sync.device:
            self.sync.set_device(self.ctl.device)

    # ------------------------------------------------------------ playback
    def _tick(self) -> None:
        with self._lock:
            latest, self._latest = self._latest, None
            err = self._error
        if err:
            self.disconnect_player()
            self.status = err
            self.changed.emit()
            return
        now = time.monotonic() * 1000
        if latest is not None:
            st, wall = latest
            self.clock.report(st.time_ms, st.playing, wall)
            if st.path != self.path:
                self._load(st.path)
        if not self.script:
            if self.sync._playing:
                self.sync.stop()
            return
        self.sync.tick(self.clock.now(now), self.clock.playing)
        if hasattr(self.win, "rail"):
            self.win.rail.set_device_position(self.ctl.device_position())

    def _load(self, path: str) -> None:
        self.path = path
        self.script, self.points = "", 0
        self.sync.stop()
        if not path:
            self.status = f"{self.player.name}: kein Video"
        else:
            found = PL.find_script(path, self.folders())
            name = PL.media_name(path)
            if found:
                try:
                    acts = Funscript.load(found).actions
                except (OSError, ValueError) as e:
                    self.status = f"{name}: Script nicht lesbar ({e})"
                else:
                    self.sync.set_actions(acts)
                    self.script, self.points = found, len(acts)
                    self.status = f"{name}: Script gefunden ({len(acts)} Punkte)"
            else:
                self.status = f"{name}: kein Script gefunden (neben dem Video oder im Script-Ordner)"
        self.changed.emit()

    def emergency_stop(self) -> None:
        if self.running:
            self.disconnect_player()
            self.status = "Not-Aus: vom Player getrennt"
            self.changed.emit()

    # safety timer source
    def safety_active(self) -> bool:
        return self.running and bool(self.script) and self.clock.playing

    def safety_pause(self) -> bool:
        if self.safety_active():
            self.disconnect_player()
            return True
        return False

    def safety_resume(self) -> None:
        pass                          # the user starts again in the player

    def shutdown(self) -> None:
        self.disconnect_player()


class PlayersDialog(QDialog):
    def __init__(self, ext: ExternalSync, parent=None):
        super().__init__(parent)
        self.ext = ext
        qs = ext.qs
        self.setWindowTitle("Andere Player")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        cap = QLabel("Spielt Scripts zu Videos ab, die du in einem anderen Player schaust. Das Script wird neben "
                     "dem Video gesucht (gleicher Name, .funscript) oder im Script-Ordner.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        cap.setMinimumWidth(460)
        lay.addWidget(cap)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.kind = QComboBox()
        for code, label in PL.KINDS:
            self.kind.addItem(label, code)
        self.kind.setCurrentIndex(max(0, self.kind.findData(qs.value("players/kind", "mpv"))))
        form.addRow("Player", self.kind)
        lay.addLayout(form)
        self.pages = QStackedWidget()
        # mpv
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(0, 0, 0, 0)
        self.mpv_socket = QLineEdit(str(qs.value("players/mpv", PL.DEFAULT_MPV_SOCKET)))
        f.addRow("Socket", self.mpv_socket)
        self.pages.addWidget(w)
        # vlc
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(0, 0, 0, 0)
        self.vlc_addr = QLineEdit(str(qs.value("players/vlc", PL.DEFAULT_VLC)))
        self.vlc_pw = QLineEdit(str(qs.value("players/vlc_pw", "")))
        self.vlc_pw.setEchoMode(QLineEdit.Password)
        f.addRow("Adresse", self.vlc_addr)
        f.addRow("Passwort", self.vlc_pw)
        self.pages.addWidget(w)
        # deovr / heresphere
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(0, 0, 0, 0)
        self.host = QLineEdit(str(qs.value("players/host", "192.168.0.")))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(qs.value("players/port", PL.DEOVR_PORT)))
        f.addRow("Adresse", self.host)
        f.addRow("Port", self.port)
        self.pages.addWidget(w)
        lay.addWidget(self.pages)
        self.hint = QLabel()
        self.hint.setObjectName("caption")
        self.hint.setWordWrap(True)
        self.hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.hint)
        row = QHBoxLayout()
        self.folder = QLineEdit(";".join(ext.folders()))
        self.folder.setPlaceholderText("Script-Ordner (optional)")
        pick = QPushButton("Ordner…")
        pick.clicked.connect(self._pick)
        row.addWidget(self.folder, 1)
        row.addWidget(pick)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.connect_btn = QPushButton(theme.icon("play", theme.ON_ACCENT), "Verbinden")
        self.connect_btn.setObjectName("primary")
        self.disc_btn = QPushButton("Trennen")
        row.addWidget(self.connect_btn)
        row.addWidget(self.disc_btn)
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setObjectName("meta")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.kind.currentIndexChanged.connect(self._kind)
        self.connect_btn.clicked.connect(self._connect)
        self.disc_btn.clicked.connect(ext.disconnect_player)
        ext.changed.connect(self.refresh)
        self._kind()
        self.refresh()

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Script-Ordner")
        if d:
            self.folder.setText(d)

    def _kind(self, *_):
        k = self.kind.currentData()
        self.pages.setCurrentIndex({"mpv": 0, "vlc": 1}.get(k, 2))
        self.hint.setText(HINTS[k])
        if k in ("deovr", "heresphere") and self.port.value() == 0:
            self.port.setValue(PL.DEOVR_PORT)

    def _connect(self):
        qs = self.ext.qs
        k = self.kind.currentData()
        for key, val in (("kind", k), ("mpv", self.mpv_socket.text()), ("vlc", self.vlc_addr.text()),
                         ("vlc_pw", self.vlc_pw.text()), ("host", self.host.text()), ("port", self.port.value()),
                         ("folder", self.folder.text().strip())):
            qs.setValue(f"players/{key}", val)
        self.ext.connect_player(k, mpv_socket=self.mpv_socket.text().strip(), vlc_address=self.vlc_addr.text(),
                                vlc_password=self.vlc_pw.text(), host=self.host.text(), port=self.port.value())

    def refresh(self):
        self.status.setText(self.ext.status)
        self.connect_btn.setEnabled(not self.ext.running)
        self.disc_btn.setEnabled(self.ext.running)
