"""Intiface Central / Buttplug client (Buttplug message spec v3 over WebSocket).

Intiface Central (free, open source, https://intiface.com) talks Bluetooth to
the device; this client only speaks JSON over ws://127.0.0.1:12345. That keeps
the Bluetooth details out of this program and works with every device
Buttplug supports, including the Fredorch F21S (protocol "fredorch").
"""

from __future__ import annotations

import itertools
import json
import threading
from dataclasses import dataclass, field

from .base import Device, DeviceError

DEFAULT_URL = "ws://127.0.0.1:12345"
CLIENT_NAME = "Video-to-Funscript Generator"
SPEC_VERSION = 3
REPLY_TIMEOUT = 5.0


@dataclass
class RemoteDevice:
    index: int
    name: str
    linear_features: int = 0          # number of LinearCmd actuators
    step_count: int = 0
    messages: dict = field(default_factory=dict)
    scalars: list = field(default_factory=list)    # ActuatorType per ScalarCmd feature (Vibrate, …)

    @property
    def is_linear(self) -> bool:
        return self.linear_features > 0

    @property
    def is_vibe(self) -> bool:
        return not self.is_linear and bool(self.scalars)

    @property
    def usable(self) -> bool:
        return self.is_linear or bool(self.scalars)


def _parse_device(d: dict) -> RemoteDevice:
    msgs = d.get("DeviceMessages") or {}
    linear = msgs.get("LinearCmd") or []
    steps = int(linear[0].get("StepCount", 0)) if linear else 0
    scalars = [str(x.get("ActuatorType") or "Vibrate") for x in (msgs.get("ScalarCmd") or [])]
    return RemoteDevice(int(d["DeviceIndex"]), str(d.get("DeviceDisplayName") or d.get("DeviceName") or "?"),
                        len(linear), steps, msgs, scalars)


#: how a vibrating (or rotating) device follows the script
VIBE_MODES = (("speed", "Tempo (schnelle Hübe = stark)"), ("depth", "Tiefe (tief = stark)"))
VIBE_FULL_SPEED = 400.0               # positions per second that mean full strength


def vibe_strength(mode: str, last_pos: float | None, pos: float, duration_ms: int) -> float:
    """0..1 strength for a stroke from last_pos to pos in duration_ms."""
    if mode == "depth":
        return max(0.0, min(1.0, 1 - pos / 100))
    if last_pos is None or duration_ms <= 0:
        return 0.3
    speed = abs(pos - last_pos) / duration_ms * 1000
    return max(0.0, min(1.0, speed / VIBE_FULL_SPEED))


class ButtplugDevice(Device):
    """Controls the first (or chosen) linear device found by Intiface Central."""

    def __init__(self, url: str = DEFAULT_URL):
        super().__init__()
        self.url = url
        self.name = "Intiface Central"
        self.devices: dict[int, RemoteDevice] = {}
        self.selected: int | None = None
        self._ws = None
        self._cm = None
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._pinger: threading.Thread | None = None
        self.server_name = ""
        self.vibe_mode = "speed"
        self._vibe_last: float | None = None
        self._vibe_sent = -1.0

    # ------------------------------------------------------------ connection
    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._stop.is_set()

    def connect(self) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError as e:  # pragma: no cover - dependency is declared
            raise DeviceError("Python-Paket 'websockets' fehlt") from e
        try:
            self._cm = connect(self.url, open_timeout=5, close_timeout=2, max_size=2**22)
            self._ws = self._cm.__enter__() if hasattr(self._cm, "__enter__") else self._cm
        except Exception as e:
            self._ws = None
            raise DeviceError(f"Intiface Central nicht erreichbar unter {self.url}. "
                              "Läuft Intiface und ist der Server gestartet?") from e
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, name="buttplug-reader", daemon=True)
        self._reader.start()
        info = self._request({"RequestServerInfo": {"ClientName": CLIENT_NAME, "MessageVersion": SPEC_VERSION}})
        if "ServerInfo" not in info:
            self.disconnect()
            raise DeviceError(f"Unerwartete Antwort von Intiface: {info}")
        si = info["ServerInfo"]
        self.server_name = si.get("ServerName", "Intiface")
        ping = int(si.get("MaxPingTime") or 0)
        if ping > 0:
            self._pinger = threading.Thread(target=self._ping_loop, args=(ping / 1000 / 2,),
                                            name="buttplug-ping", daemon=True)
            self._pinger.start()
        lst = self._request({"RequestDeviceList": {}})
        for d in (lst.get("DeviceList") or {}).get("Devices", []):
            self._add_device(d)
        self._emit(f"Verbunden mit {self.server_name}")

    def disconnect(self) -> None:
        if self._ws is None:
            return
        try:
            self.stop()
        except DeviceError:
            pass
        self._stop.set()
        try:
            self._ws.close()
        except Exception:
            pass
        self._ws = None
        self.devices.clear()
        self.selected = None
        self._emit("Getrennt")

    # ------------------------------------------------------------ scanning
    def start_scanning(self) -> None:
        self._check(self._request({"StartScanning": {}}))
        self._emit("Suche Geräte… (Gerät einschalten)")

    def stop_scanning(self) -> None:
        if self.connected:
            self._check(self._request({"StopScanning": {}}))

    def select(self, index: int) -> None:
        if index in self.devices:
            self.selected = index
            self._emit(f"Gerät gewählt: {self.devices[index].name}")

    # ------------------------------------------------------------ motion
    def move(self, position: float, duration_ms: int) -> None:
        dev = self._device()
        if dev is None:
            return
        if not dev.is_linear:
            self._vibrate(dev, float(position), duration_ms)
            return
        pos = max(0.0, min(100.0, float(position))) / 100
        vectors = [{"Index": i, "Duration": max(0, int(duration_ms)), "Position": round(pos, 4)}
                   for i in range(dev.linear_features)]
        # fire and forget: waiting for "Ok" would add a full round trip of latency
        self._send({"LinearCmd": {"DeviceIndex": dev.index, "Vectors": vectors}})

    def _vibrate(self, dev: RemoteDevice, position: float, duration_ms: int) -> None:
        v = round(vibe_strength(self.vibe_mode, self._vibe_last, position, duration_ms), 2)
        self._vibe_last = position
        if abs(v - self._vibe_sent) < 0.02:
            return
        self._vibe_sent = v
        scalars = [{"Index": i, "Scalar": v, "ActuatorType": t} for i, t in enumerate(dev.scalars)]
        self._send({"ScalarCmd": {"DeviceIndex": dev.index, "Scalars": scalars}})

    def stop(self) -> None:
        self._vibe_last, self._vibe_sent = None, -1.0
        if not self.connected:
            return
        dev = self._device()
        if dev is not None:
            self._send({"StopDeviceCmd": {"DeviceIndex": dev.index}})
        else:
            self._send({"StopAllDevices": {}})

    # ------------------------------------------------------------ internals
    def _device(self) -> RemoteDevice | None:
        if not self.connected or self.selected is None:
            return None
        return self.devices.get(self.selected)

    def _add_device(self, d: dict) -> None:
        dev = _parse_device(d)
        self.devices[dev.index] = dev
        current = self.devices.get(self.selected) if self.selected is not None else None
        if dev.is_linear and (current is None or not current.is_linear):
            self.selected = dev.index         # a stroker wins over a vibrator
        elif dev.usable and current is None:
            self.selected = dev.index
        kind = ("Hub-Gerät" if dev.is_linear else "Vibration/Drehung" if dev.scalars
                else "ohne Steuerung")
        self._emit(f"Gefunden: {dev.name} ({kind})")

    def _send(self, msg: dict) -> int:
        if self._ws is None:
            raise DeviceError("nicht verbunden")
        mid = next(self._ids)
        name = next(iter(msg))
        body = dict(msg[name], Id=mid)
        try:
            with self._send_lock:
                self._ws.send(json.dumps([{name: body}]))
        except Exception as e:
            self._connection_lost(e)
            raise DeviceError(f"Verbindung zu Intiface verloren: {e}") from e
        return mid

    def _request(self, msg: dict) -> dict:
        ev, box = threading.Event(), []
        mid = next(self._ids)
        self._pending[mid] = (ev, box)
        name = next(iter(msg))
        try:
            with self._send_lock:
                if self._ws is None:
                    raise DeviceError("nicht verbunden")
                self._ws.send(json.dumps([{name: dict(msg[name], Id=mid)}]))
            if not ev.wait(REPLY_TIMEOUT):
                raise DeviceError(f"Keine Antwort von Intiface auf {name}")
        except DeviceError:
            raise
        except Exception as e:
            self._connection_lost(e)
            raise DeviceError(f"Verbindung zu Intiface verloren: {e}") from e
        finally:
            self._pending.pop(mid, None)
        if not box:
            raise DeviceError("Verbindung zu Intiface verloren")
        return box[0]

    @staticmethod
    def _check(reply: dict) -> None:
        if "Error" in reply:
            raise DeviceError(reply["Error"].get("ErrorMessage", "Fehler von Intiface"))

    def _read_loop(self) -> None:
        ws = self._ws
        while not self._stop.is_set():
            try:
                raw = ws.recv()
            except Exception as e:
                if not self._stop.is_set():
                    self._connection_lost(e)
                return
            try:
                messages = json.loads(raw)
            except ValueError:
                continue
            for m in messages if isinstance(messages, list) else [messages]:
                self._handle(m)

    def _handle(self, m: dict) -> None:
        name, body = next(iter(m.items()))
        mid = int(body.get("Id", 0)) if isinstance(body, dict) else 0
        if mid and mid in self._pending:
            ev, box = self._pending[mid]
            box.append(m)
            ev.set()
            return
        if name == "DeviceAdded":
            self._add_device(body)
        elif name == "DeviceRemoved":
            dev = self.devices.pop(int(body.get("DeviceIndex", -1)), None)
            if dev:
                if self.selected == dev.index:
                    self.selected = next((d.index for d in self.devices.values() if d.is_linear),
                                         next((d.index for d in self.devices.values() if d.usable), None))
                self._emit(f"Gerät getrennt: {dev.name}")
        elif name == "ScanningFinished":
            self._emit("Suche beendet")
        elif name == "Error" and mid:
            self._emit(f"Intiface: {body.get('ErrorMessage')}")

    def _ping_loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self._send({"Ping": {}})
            except DeviceError:
                return

    def _connection_lost(self, err: Exception) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._ws = None
        self.devices.clear()
        self.selected = None
        for ev, _box in list(self._pending.values()):
            ev.set()
        self._emit(f"Verbindung zu Intiface verloren ({err})")
