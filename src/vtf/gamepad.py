"""Game controllers on Linux, without an extra library.

Two kernel interfaces:

- evdev (/dev/input/event*): what KDE, Steam and SDL use. Only devices with real
  gamepad buttons (A/B/X/Y) count, so keyboards and mice that also announce a
  joystick interface (some gaming keyboards do) are left out.
- joystick (/dev/input/js*): the older interface, used when evdev finds nothing.

Both are turned into one layout (Xbox naming, PlayStation and Switch pads the same):
buttons 0 A/✕, 1 B/○, 2 X/□, 3 Y/△, 4 LB/L1, 5 RB/R1, 6 Back, 7 Start;
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
from dataclasses import dataclass, field

BTN_A, BTN_B, BTN_X, BTN_Y, BTN_LB, BTN_RB, BTN_BACK, BTN_START = 0, 1, 2, 3, 4, 5, 6, 7
AX_LX, AX_LY, AX_RX, AX_RY, AX_DX, AX_DY = 0, 1, 3, 4, 6, 7


@dataclass
class Event:
    kind: str          # "button" | "axis"
    number: int
    value: float       # button: 1 pressed / 0 released; axis: -1..1
    init: bool = False  # state sent once on open, not a real press


def _ioc_read(type_: str, nr: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (ord(type_) << 8) | nr


def _ioctl_bytes(fd: int, req: int, size: int) -> bytes | None:
    buf = array.array("B", [0] * size)
    try:
        fcntl.ioctl(fd, req, buf)
    except OSError:
        return None
    return buf.tobytes()


# ------------------------------------------------------------ joystick interface (js)
JS_EVENT = struct.Struct("<IhBB")         # time ms, value, type, number
JS_BUTTON, JS_AXIS, JS_INIT = 0x01, 0x02, 0x80
EVENT = JS_EVENT                          # (old name)
JSIOCGNAME = _ioc_read("j", 0x13, 128)
JSIOCGAXES = _ioc_read("j", 0x11, 1)
JSIOCGBUTTONS = _ioc_read("j", 0x12, 1)

NOT_A_PAD = ("keyboard", "mouse", "touchpad", "trackpad", "consumer control", "system control")


def parse(data: bytes) -> list[Event]:
    """Events of the joystick interface (8 bytes each)."""
    out = []
    for i in range(0, len(data) - JS_EVENT.size + 1, JS_EVENT.size):
        _t, value, typ, num = JS_EVENT.unpack_from(data, i)
        init = bool(typ & JS_INIT)
        typ &= ~JS_INIT
        if typ == JS_BUTTON:
            out.append(Event("button", num, 1.0 if value else 0.0, init))
        elif typ == JS_AXIS:
            out.append(Event("axis", num, max(-1.0, value / 32767), init))
    return out


def looks_like_pad(name: str) -> bool:
    low = name.casefold()
    return not any(word in low for word in NOT_A_PAD)


# ------------------------------------------------------------ evdev
EV_EVENT = struct.Struct("llHHi")         # timeval, type, code, value (native)
EV_KEY, EV_ABS = 0x01, 0x03
EVIOCGNAME = _ioc_read("E", 0x06, 256)
EVIOCGBIT_KEY = _ioc_read("E", 0x20 + EV_KEY, 96)


def EVIOCGABS(axis: int) -> int:          # noqa: N802 (kernel name)
    return _ioc_read("E", 0x40 + axis, 24)


EV_BUTTONS = {0x130: BTN_A, 0x131: BTN_B, 0x134: BTN_X, 0x133: BTN_Y, 0x136: BTN_LB, 0x137: BTN_RB,
              0x13A: BTN_BACK, 0x13B: BTN_START}
EV_AXES = {0x00: AX_LX, 0x01: AX_LY, 0x03: AX_RX, 0x04: AX_RY, 0x10: AX_DX, 0x11: AX_DY}
EV_DPAD = {0x220: (AX_DY, -1.0), 0x221: (AX_DY, 1.0), 0x222: (AX_DX, -1.0), 0x223: (AX_DX, 1.0)}
GAMEPAD_KEYS = (0x130, 0x131)             # BTN_SOUTH, BTN_EAST: a real gamepad has them


def _has_bit(bits: bytes, n: int) -> bool:
    return len(bits) > n // 8 and bool(bits[n // 8] & (1 << (n % 8)))


@dataclass
class Pad:
    """One opened controller."""

    fd: int
    name: str
    kind: str                                 # "evdev" | "js"
    ranges: dict = field(default_factory=dict)   # evdev axis -> (min, max)
    rest: bytes = b""

    @property
    def size(self) -> int:
        return EV_EVENT.size if self.kind == "evdev" else JS_EVENT.size

    def norm(self, axis: int, value: int) -> float:
        lo, hi = self.ranges.get(axis, (-32768, 32767))
        if hi - lo <= 2:                      # hats: already -1, 0, 1
            return float(max(-1, min(1, value)))
        return max(-1.0, min(1.0, 2 * (value - lo) / (hi - lo) - 1))

    def events(self, data: bytes) -> list[Event]:
        data = self.rest + data
        cut = len(data) - len(data) % self.size
        self.rest = data[cut:]
        if self.kind == "js":
            return parse(data[:cut])
        return self.evdev_events(data[:cut])

    def evdev_events(self, data: bytes) -> list[Event]:
        out = []
        for i in range(0, len(data) - EV_EVENT.size + 1, EV_EVENT.size):
            _s, _us, typ, code, value = EV_EVENT.unpack_from(data, i)
            if typ == EV_KEY and code in EV_BUTTONS and value in (0, 1):
                out.append(Event("button", EV_BUTTONS[code], float(value)))
            elif typ == EV_KEY and code in EV_DPAD and value in (0, 1):
                axis, v = EV_DPAD[code]
                out.append(Event("axis", axis, v if value else 0.0))
            elif typ == EV_ABS and code in EV_AXES:
                out.append(Event("axis", EV_AXES[code], self.norm(code, value)))
        return out

    def initial(self) -> list[Event]:
        """Where the sticks are right now (evdev), as init events."""
        out = []
        for code, axis in EV_AXES.items():
            if code in self.ranges:
                info = _ioctl_bytes(self.fd, EVIOCGABS(code), 24)
                if info:
                    value = struct.unpack("6i", info)[0]
                    out.append(Event("axis", axis, self.norm(code, value), True))
        return out


def open_evdev(path: str) -> Pad | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    keys = _ioctl_bytes(fd, EVIOCGBIT_KEY, 96)
    if not keys or not any(_has_bit(keys, k) for k in GAMEPAD_KEYS):
        os.close(fd)
        return None
    raw = _ioctl_bytes(fd, EVIOCGNAME, 256) or b""
    name = raw.split(b"\0", 1)[0].decode(errors="replace") or "Controller"
    ranges = {}
    for code in EV_AXES:
        info = _ioctl_bytes(fd, EVIOCGABS(code), 24)
        if info:
            _v, lo, hi, *_ = struct.unpack("6i", info)
            if hi > lo:
                ranges[code] = (lo, hi)
    return Pad(fd, name, "evdev", ranges)


def open_js(path: str) -> Pad | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    raw = _ioctl_bytes(fd, JSIOCGNAME, 128)
    name = (raw or b"").split(b"\0", 1)[0].decode(errors="replace") or "Controller"
    buttons = _ioctl_bytes(fd, JSIOCGBUTTONS, 1)
    if not looks_like_pad(name) or (buttons is not None and buttons[0] < 4):
        os.close(fd)
        return None
    return Pad(fd, name, "js")


def find_pads() -> list[Pad]:
    """All controllers: evdev gamepads, or (if there are none) joystick devices
    that are not keyboards or mice."""
    pads = [p for p in (open_evdev(x) for x in sorted(glob.glob("/dev/input/event*"))) if p]
    if pads:
        return pads
    return [p for p in (open_js(x) for x in sorted(glob.glob("/dev/input/js*"))) if p]


def devices() -> list[str]:                  # (old helper, joystick paths)
    return sorted(glob.glob("/dev/input/js*"))


class GamepadReader:
    """Reads all controllers in a background thread and calls `on_event` (from that
    thread) and `on_state(names or None)` when controllers come or go. Looks for
    controllers again every 2 s while none is there."""

    def __init__(self, on_event: Callable[[Event], None], on_state: Callable[[str | None], None],
                 paths: Callable[[], list[str]] | None = None, finder: Callable[[], list[Pad]] | None = None):
        self.on_event = on_event
        self.on_state = on_state
        if finder is None and paths is not None:     # explicit joystick paths (tests, special setups)
            finder = lambda: [p for p in (open_js(x) for x in paths()) if p]   # noqa: E731
        self.finder = finder or find_pads
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
            try:
                pads = self.finder()
            except OSError:
                pads = []
            if not pads:
                self._stop.wait(2.0)
                continue
            self.name = ", ".join(dict.fromkeys(p.name for p in pads))
            self.on_state(self.name)
            for p in pads:
                if p.kind == "evdev":
                    for ev in p.initial():
                        self.on_event(ev)
            try:
                self._read(pads)
            finally:
                for p in pads:
                    try:
                        os.close(p.fd)
                    except OSError:
                        pass
                self.name = None
                self.on_state(None)
            self._stop.wait(1.0)

    def _read(self, pads: list[Pad]) -> None:
        """Until every controller is gone (or stop)."""
        while pads and not self._stop.is_set():
            ready, _, _ = select.select([p.fd for p in pads], [], [], 0.2)
            for p in [p for p in pads if p.fd in ready]:
                try:
                    data = os.read(p.fd, p.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    data = b""
                if not data:                     # unplugged
                    pads.remove(p)
                    try:
                        os.close(p.fd)
                    except OSError:
                        pass
                    continue
                for ev in p.events(data):
                    self.on_event(ev)
