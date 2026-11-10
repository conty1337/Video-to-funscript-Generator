"""Game controllers on Linux through the joystick interface (/dev/input/js*).

No extra library: the kernel sends 8-byte events (time, value, type, number).
Xbox, PlayStation and most other pads use the same layout here:
buttons 0 A/✕, 1 B/○, 2 X/□, 3 Y/△, 4 LB/L1, 5 RB/R1, 7 Start/Options;
axes 0/1 left stick, 3/4 right stick, 6/7 D-pad (−1, 0, 1).
"""

from __future__ import annotations

import array
import fcntl
import glob
import os
import select
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass

EVENT = struct.Struct("<IhBB")          # time ms, value, type, number
JS_BUTTON, JS_AXIS, JS_INIT = 0x01, 0x02, 0x80
JSIOCGNAME = 0x80006A13 + (128 << 16)   # _IOC(_IOC_READ, 'j', 0x13, 128)

BTN_A, BTN_B, BTN_X, BTN_Y, BTN_LB, BTN_RB, BTN_START = 0, 1, 2, 3, 4, 5, 7
AX_LX, AX_LY, AX_RX, AX_RY, AX_DX, AX_DY = 0, 1, 3, 4, 6, 7


@dataclass
class Event:
    kind: str          # "button" | "axis"
    number: int
    value: float       # button: 1 pressed / 0 released; axis: -1..1
    init: bool = False  # state sent once on open, not a real press


def parse(data: bytes) -> list[Event]:
    out = []
    for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
        _t, value, typ, num = EVENT.unpack_from(data, i)
        init = bool(typ & JS_INIT)
        typ &= ~JS_INIT
        if typ == JS_BUTTON:
            out.append(Event("button", num, 1.0 if value else 0.0, init))
        elif typ == JS_AXIS:
            out.append(Event("axis", num, max(-1.0, value / 32767), init))
    return out


def devices() -> list[str]:
    return sorted(glob.glob("/dev/input/js*"))


def device_name(fd: int) -> str:
    buf = array.array("B", [0] * 128)
    try:
        fcntl.ioctl(fd, JSIOCGNAME, buf)
        return buf.tobytes().split(b"\0", 1)[0].decode(errors="replace") or "Controller"
    except OSError:
        return "Controller"


class GamepadReader:
    """Reads the first controller in a background thread and calls `on_event`
    (from that thread) and `on_state(name or None)` when one comes or goes.
    Looks for a controller again every 2 s while none is there."""

    def __init__(self, on_event: Callable[[Event], None], on_state: Callable[[str | None], None],
                 paths: Callable[[], list[str]] = devices):
        self.on_event = on_event
        self.on_state = on_state
        self.paths = paths
        self.name: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="gamepad", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            fd = None
            for path in self.paths():
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                    break
                except OSError:
                    continue
            if fd is None:
                self._stop.wait(2.0)
                continue
            self.name = device_name(fd)
            self.on_state(self.name)
            try:
                self._read(fd)
            finally:
                os.close(fd)
                self.name = None
                self.on_state(None)
            self._stop.wait(1.0)

    def _read(self, fd: int) -> None:
        rest = b""
        while not self._stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            try:
                data = os.read(fd, EVENT.size * 32)
            except BlockingIOError:
                continue
            except OSError:
                return                      # unplugged
            if not data:
                return
            data = rest + data
            cut = len(data) - len(data) % EVENT.size
            rest = data[cut:]
            for ev in parse(data[:cut]):
                self.on_event(ev)
