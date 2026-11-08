"""Simulated device that behaves like the Fredorch F21S.

The real machine knows only 16 positions (0..15) and needs about 200 ms for a
full stroke at top speed (Buttplug's fredorch speed table). The simulator
quantizes and rate-limits the same way, so a script that looks smooth here
will also be feasible on the device.
"""

from __future__ import annotations

import time

from .base import Device

STEPS = 15                 # positions 0..15
MIN_FULL_STROKE_MS = 200   # fastest full stroke


class SimulatedDevice(Device):
    name = "Simulation (Fredorch F21S)"

    def __init__(self, clock=time.monotonic):
        super().__init__()
        self._clock = clock
        self._connected = False
        self._from = 0.0
        self._to = 0.0
        self._t0 = 0.0
        self._t1 = 0.0
        self.commands: list[tuple[float, int]] = []   # for tests / debugging

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        self._connected = True
        self._emit("Simulation verbunden")

    def disconnect(self) -> None:
        self.stop()
        self._connected = False
        self._emit("Simulation getrennt")

    @staticmethod
    def quantize(position: float) -> float:
        step = round(max(0.0, min(100.0, position)) / 100 * STEPS)
        return step * 100 / STEPS

    def move(self, position: float, duration_ms: int) -> None:
        if not self._connected:
            return
        with self._lock:
            now = self._clock()
            start = self._pos_at(now)
            target = self.quantize(position)
            dist = abs(target - start) / 100
            duration = max(int(duration_ms), int(dist * MIN_FULL_STROKE_MS))
            self._from, self._to = start, target
            self._t0, self._t1 = now, now + duration / 1000
            self.commands.append((target, duration))

    def stop(self) -> None:
        with self._lock:
            now = self._clock()
            p = self._pos_at(now)
            self._from = self._to = p
            self._t0 = self._t1 = now

    def _pos_at(self, now: float) -> float:
        if now >= self._t1 or self._t1 <= self._t0:
            return self._to
        f = (now - self._t0) / (self._t1 - self._t0)
        return self._from + (self._to - self._from) * f

    def current_position(self) -> float | None:
        if not self._connected:
            return None
        with self._lock:
            return self._pos_at(self._clock())
