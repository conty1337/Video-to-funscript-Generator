"""Other players: read what mpv, VLC, DeoVR or HereSphere are playing and where.

- mpv: JSON IPC over a socket (start mpv with --input-ipc-server=/tmp/mpvsocket)
- VLC: web interface (status.json, needs a password set in VLC)
- DeoVR / HereSphere: DeoVR remote control over TCP (port 23554)

Each player reports (file, time, playing). A smoothed clock fills the gaps between
reports, so the device can follow the script as with the built-in player.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

KINDS = (("mpv", "mpv"), ("vlc", "VLC"), ("deovr", "DeoVR"), ("heresphere", "HereSphere"))
DEFAULT_MPV_SOCKET = "/tmp/mpvsocket"
DEFAULT_VLC = "127.0.0.1:8080"
DEOVR_PORT = 23554


class PlayerError(Exception):
    pass


@dataclass
class PlayerState:
    path: str = ""                # file path or URL of what is playing
    time_ms: float = 0.0
    playing: bool = False
    duration_ms: float = 0.0


# ------------------------------------------------------------ players
class MpvPlayer:
    name = "mpv"

    def __init__(self, socket_path: str = DEFAULT_MPV_SOCKET):
        self.socket_path = socket_path
        self.sock: socket.socket | None = None
        self._buf = b""

    def connect(self) -> None:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(self.socket_path)
        except OSError as e:
            raise PlayerError(f"mpv nicht erreichbar über {self.socket_path}. mpv mit "
                              f"--input-ipc-server={self.socket_path} starten.") from e
        self.sock = s

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def poll(self) -> PlayerState:
        if self.sock is None:
            raise PlayerError("nicht verbunden")
        props = ("time-pos", "pause", "path", "duration")
        msg = b"".join(json.dumps({"command": ["get_property", p], "request_id": i + 1}).encode() + b"\n"
                       for i, p in enumerate(props))
        try:
            self.sock.sendall(msg)
            got: dict[int, object] = {}
            deadline = time.monotonic() + 1.0
            while len(got) < len(props) and time.monotonic() < deadline:
                while b"\n" in self._buf:
                    line, self._buf = self._buf.split(b"\n", 1)
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and "request_id" in r:
                        got[int(r["request_id"])] = r.get("data")
                if len(got) < len(props):
                    data = self.sock.recv(65536)
                    if not data:
                        raise PlayerError("mpv wurde beendet")
                    self._buf += data
        except OSError as e:
            raise PlayerError(f"Verbindung zu mpv verloren: {e}") from e
        t, pause, path, dur = (got.get(i + 1) for i in range(len(props)))
        return PlayerState(str(path or ""), float(t or 0) * 1000, path is not None and t is not None and not pause,
                           float(dur or 0) * 1000)


class VlcPlayer:
    name = "VLC"

    def __init__(self, address: str = DEFAULT_VLC, password: str = ""):
        self.address = address.strip() or DEFAULT_VLC
        self.password = password
        self._uri_for: str | None = None
        self._uri = ""

    def _get(self, what: str) -> dict:
        req = urllib.request.Request(f"http://{self.address}/requests/{what}")
        token = base64.b64encode(f":{self.password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=1.0) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise PlayerError("VLC: falsches Passwort (Einstellungen → Lua → Passwort)") from e
            raise PlayerError(f"VLC antwortet mit {e.code}") from e
        except (OSError, ValueError) as e:
            raise PlayerError(f"VLC nicht erreichbar unter {self.address}. Weboberfläche in VLC an?") from e

    def connect(self) -> None:
        self._get("status.json")

    def close(self) -> None:
        pass

    def poll(self) -> PlayerState:
        st = self._get("status.json")
        state = st.get("state")
        length = float(st.get("length") or 0)
        pos = float(st.get("position") or 0)
        t = pos * length if length > 0 else float(st.get("time") or 0)
        meta = ((st.get("information") or {}).get("category") or {}).get("meta") or {}
        name = str(meta.get("filename") or "")
        if name and name != self._uri_for:            # the full path is only in the playlist
            self._uri_for = name
            self._uri = self._current_uri() or name
        return PlayerState(self._uri if name else "", t * 1000, state == "playing", length * 1000)

    def _current_uri(self) -> str:
        try:
            tree = self._get("playlist.json")
        except PlayerError:
            return ""
        stack = [tree]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("current") == "current" and node.get("uri"):
                    return str(node["uri"])
                stack.extend(node.get("children") or [])
        return ""


class DeoVrPlayer:
    """DeoVR remote control (HereSphere speaks the same): 4-byte length + JSON."""

    def __init__(self, host: str, port: int = DEOVR_PORT, name: str = "DeoVR"):
        self.host = host.strip() or "127.0.0.1"
        self.port = port
        self.name = name
        self.sock: socket.socket | None = None
        self.state = PlayerState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._error = ""
        self._last_ping = 0.0

    def connect(self) -> None:
        try:
            s = socket.create_connection((self.host, self.port), timeout=3.0)
        except OSError as e:
            raise PlayerError(f"{self.name} nicht erreichbar unter {self.host}:{self.port}. "
                              "Fernsteuerung im Player eingeschaltet?") from e
        s.settimeout(0.5)
        self.sock = s
        self._error = ""
        self._thread = threading.Thread(target=self._read, args=(s,), name="deovr", daemon=True)
        self._thread.start()

    def close(self) -> None:
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def _read(self, s: socket.socket) -> None:
        buf = b""
        while self.sock is s:
            try:
                data = s.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                self._error = str(e)
                return
            if not data:
                self._error = f"{self.name} hat die Verbindung beendet"
                return
            buf += data
            while len(buf) >= 4:
                n = struct.unpack("<I", buf[:4])[0]
                if len(buf) < 4 + n:
                    break
                body, buf = buf[4:4 + n], buf[4 + n:]
                if n:
                    self._message(body)

    def _message(self, body: bytes) -> None:
        try:
            m = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(m, dict):
            return
        with self._lock:
            st = self.state
            if "path" in m:
                st.path = str(m.get("path") or "")
            if "currentTime" in m:
                st.time_ms = float(m.get("currentTime") or 0) * 1000
            if "duration" in m:
                st.duration_ms = float(m.get("duration") or 0) * 1000
            if "playerState" in m:
                st.playing = int(m.get("playerState") or 0) == 0     # 0 = playing, 1 = paused
            self._stamp = time.monotonic()

    def poll(self) -> PlayerState:
        if self._error or self.sock is None:
            raise PlayerError(self._error or "nicht verbunden")
        now = time.monotonic()
        if now - self._last_ping >= 1.0:                # keep-alive: an empty message
            self._last_ping = now
            try:
                self.sock.sendall(struct.pack("<I", 0))
            except OSError as e:
                raise PlayerError(f"Verbindung zu {self.name} verloren: {e}") from e
        with self._lock:
            return PlayerState(self.state.path, self.state.time_ms, self.state.playing, self.state.duration_ms)


def make_player(kind: str, mpv_socket: str = DEFAULT_MPV_SOCKET, vlc_address: str = DEFAULT_VLC,
                vlc_password: str = "", host: str = "127.0.0.1", port: int = DEOVR_PORT):
    if kind == "mpv":
        return MpvPlayer(mpv_socket)
    if kind == "vlc":
        return VlcPlayer(vlc_address, vlc_password)
    return DeoVrPlayer(host, port, "HereSphere" if kind == "heresphere" else "DeoVR")


# ------------------------------------------------------------ clock and scripts
class SmoothClock:
    """Media time between reports: runs on with the wall clock, jumps on seeks,
    corrects small drift gently."""

    def __init__(self, jump_ms: float = 400.0):
        self.jump_ms = jump_ms
        self.anchor_media = 0.0
        self.anchor_wall = 0.0
        self.playing = False

    def report(self, media_ms: float, playing: bool, wall_ms: float) -> None:
        est = self.now(wall_ms)
        if playing != self.playing or not playing or abs(media_ms - est) > self.jump_ms:
            self.anchor_media, self.anchor_wall = media_ms, wall_ms
        else:
            self.anchor_media = est + 0.2 * (media_ms - est)
            self.anchor_wall = wall_ms
        self.playing = playing

    def now(self, wall_ms: float) -> float:
        return self.anchor_media + (wall_ms - self.anchor_wall if self.playing else 0.0)


def media_name(path: str) -> str:
    """File name of a path or URL (URL-decoded, without query)."""
    p = urllib.parse.urlparse(path)
    raw = p.path if p.scheme else path
    return os.path.basename(urllib.parse.unquote(raw))


def local_path(path: str) -> str | None:
    p = urllib.parse.urlparse(path)
    if p.scheme == "file":
        return urllib.parse.unquote(p.path)
    if not p.scheme and path:
        return path
    return None


def find_script(path: str, folders: list[str] | None = None, max_files: int = 20000) -> str | None:
    """The funscript for a played file: next to it, or by name in the script folders."""
    name = media_name(path)
    if not name:
        return None
    stem = os.path.splitext(name)[0]
    loc = local_path(path)
    if loc:
        cand = os.path.splitext(loc)[0] + ".funscript"
        if os.path.isfile(cand):
            return cand
    want = (stem + ".funscript").casefold()
    seen = 0
    for folder in folders or []:
        if not folder or not os.path.isdir(folder):
            continue
        for root, _dirs, files in os.walk(folder):
            for f in files:
                seen += 1
                if f.casefold() == want:
                    return str(Path(root) / f)
            if seen > max_files:
                break
    return None
