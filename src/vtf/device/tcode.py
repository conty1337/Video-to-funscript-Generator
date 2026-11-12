"""TCode devices (OSR2, SR6 and other open-source strokers) over USB or WLAN (UDP).

TCode is a short text command per axis: "L0" + position digits + "I" + duration,
e.g. "L09999I500" = stroke to the top in 500 ms. The extra axes (L1 surge,
L2 sway, R0 twist, R1 roll, R2 pitch) take the same form. USB is a serial port at
115200 baud, set up with termios (no extra library).
"""

from __future__ import annotations

import os
import socket

from ..i18n import tr
from .base import Device, DeviceError

DEFAULT_PORT = "/dev/ttyUSB0"
AXES = ("L0", "L1", "L2", "R0", "R1", "R2")


def command(axis: str, position: float, duration_ms: int) -> str:
    v = int(round(max(0.0, min(100.0, float(position))) / 100 * 9999))
    return f"{axis}{v:04d}I{max(0, int(duration_ms))}"


class TCodeDevice(Device):
    """address: a serial port (/dev/ttyUSB0, /dev/ttyACM0) or udp://host:port."""

    def __init__(self, address: str = DEFAULT_PORT):
        super().__init__()
        self.address = address.strip() or DEFAULT_PORT
        self.name = tr("TCode-Gerät")
        self._fd: int | None = None
        self._sock: socket.socket | None = None
        self._udp: tuple[str, int] | None = None
        self.axes = AXES

    @property
    def connected(self) -> bool:
        return self._fd is not None or self._sock is not None

    def connect(self) -> None:
        if self.address.startswith("udp://"):
            host, _, port = self.address[6:].rpartition(":")
            try:
                self._udp = (host or "127.0.0.1", int(port))
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except (ValueError, OSError) as e:
                raise DeviceError(tr("Ungültige Adresse {0} (Beispiel: udp://192.168.0.50:8000)", self.address)) from e
            self.name = tr("TCode über WLAN ({0})", self._udp[0])
        else:
            try:
                fd = os.open(self.address, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            except OSError as e:
                hint = tr(" Ist der Benutzer in der Gruppe „dialout“?") if e.errno == 13 else ""
                raise DeviceError(tr("{0} lässt sich nicht öffnen: {1}.{2}", self.address, e.strerror, hint)) from e
            try:
                import termios
                import tty
            except ImportError:            # not on Linux: write without port setup
                termios = None
            if termios is not None and os.isatty(fd):
                try:
                    tty.setraw(fd)
                    attrs = termios.tcgetattr(fd)
                    attrs[4] = attrs[5] = termios.B115200
                    termios.tcsetattr(fd, termios.TCSANOW, attrs)
                except (OSError, termios.error):
                    pass
            self._fd = fd
            self.name = tr("TCode über USB ({0})", os.path.basename(self.address))
        self._emit(f"Verbunden: {self.name}")

    def disconnect(self) -> None:
        if not self.connected:
            return
        try:
            self.stop()
        except DeviceError:
            pass
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._emit(tr("Getrennt"))

    def write(self, line: str) -> None:
        data = (line + "\n").encode()
        try:
            with self._lock:
                if self._sock is not None and self._udp is not None:
                    self._sock.sendto(data, self._udp)
                elif self._fd is not None:
                    os.write(self._fd, data)
                else:
                    raise DeviceError(tr("nicht verbunden"))
        except OSError as e:
            raise DeviceError(tr("TCode-Gerät: {0}", e)) from e

    def move(self, position: float, duration_ms: int) -> None:
        self.write(command("L0", position, duration_ms))

    def move_axis(self, axis: str, position: float, duration_ms: int) -> None:
        if axis in self.axes:
            self.write(command(axis, position, duration_ms))

    def stop(self) -> None:
        if self.connected:
            self.write("DSTOP")


class AxisProxy(Device):
    """One extra axis of a multi-axis device, so it can be driven by its own ScriptSync."""

    def __init__(self, device: Device, axis: str):
        super().__init__()
        self.device = device
        self.axis = axis
        self.name = f"{device.name} {axis}"

    @property
    def connected(self) -> bool:
        return self.device.connected

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def move(self, position: float, duration_ms: int) -> None:
        self.device.move_axis(self.axis, position, duration_ms)

    def stop(self) -> None:
        pass                          # the main axis stops the whole device
