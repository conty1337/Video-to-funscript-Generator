"""Drive a device from a funscript while the video plays.

Principle (as in common funscript players): whenever playback passes an
action, the device is told to travel to the *next* action's position, taking
exactly the time until that action. A positive offset sends commands earlier
to compensate Bluetooth latency.

Safety rules: on pause the device stops; after a seek or resume it first
glides to the current script position before following again; strokes faster
than ``max_speed`` are shortened (not delayed), so the device never falls
behind the video.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from ..funscript import Action, position_at
from .base import Device

APPROACH_MS = 500          # glide time after start/seek
SEEK_JUMP_MS = 400         # media time jump that counts as a seek


@dataclass
class SyncSettings:
    offset_ms: int = 0         # >0: device commands earlier (latency compensation)
    pos_min: int = 0           # stroke range on the device
    pos_max: int = 100
    invert: bool = False
    max_speed: float = 500.0   # position units per second (F21S: 100 in ~200 ms)

    def map(self, pos: float) -> float:
        p = 100 - pos if self.invert else pos
        lo, hi = sorted((self.pos_min, self.pos_max))
        return lo + max(0.0, min(100.0, p)) * (hi - lo) / 100


class ScriptSync:
    def __init__(self, device: Device | None = None, settings: SyncSettings | None = None):
        self.device = device
        self.settings = settings or SyncSettings()
        self.actions: list[Action] = []
        self._times: list[int] = []
        self._playing = False
        self._last_t: float | None = None
        self._target_idx: int | None = None
        self._resume_at: float | None = None
        self._cmd_pos: float | None = None
        self.enabled = True

    # ------------------------------------------------------------ setup
    def set_device(self, device: Device | None) -> None:
        self.stop()
        self.device = device
        self._reset()

    def set_actions(self, actions: list[Action]) -> None:
        self.actions = list(actions)
        self._times = [a.at for a in self.actions]
        self._target_idx = None

    def _reset(self) -> None:
        self._last_t = None
        self._target_idx = None
        self._resume_at = None

    def _ready(self) -> bool:
        return self.enabled and self.device is not None and self.device.connected and bool(self.actions)

    # ------------------------------------------------------------ control
    def stop(self) -> None:
        """Stop motion now (pause, seek while paused, close, emergency stop)."""
        if self.device is not None and self.device.connected:
            try:
                self.device.stop()
            except Exception:
                pass
        self._playing = False
        self._reset()

    def tick(self, media_ms: float, playing: bool) -> None:
        if not playing:
            if self._playing:
                self.stop()
            return
        if not self._ready():
            self._playing = playing
            return
        s = self.settings
        t = media_ms + s.offset_ms
        jumped = self._last_t is not None and (t < self._last_t - 50 or t - self._last_t > SEEK_JUMP_MS)
        if not self._playing or jumped or self._last_t is None:
            self._approach(t)
        self._playing = True
        self._last_t = t
        if self._resume_at is not None:
            if t < self._resume_at:
                return
            self._resume_at = None
        idx = bisect.bisect_right(self._times, t)
        if idx >= len(self.actions) or idx == self._target_idx:
            return
        self._target_idx = idx
        target = self.actions[idx]
        duration = max(1, int(target.at - t))
        self._command(s.map(target.pos), duration)

    # ------------------------------------------------------------ helpers
    def _approach(self, t: float) -> None:
        pos = position_at(self.actions, t)
        if pos is None:
            return
        self._target_idx = None
        self._resume_at = t + APPROACH_MS
        self._cmd_pos = None
        self.device.move(self.settings.map(pos), APPROACH_MS)
        self._cmd_pos = self.settings.map(pos)

    def _command(self, pos: float, duration_ms: int) -> None:
        cur = self.device.current_position()
        if cur is None:
            cur = self._cmd_pos
        if cur is not None and self.settings.max_speed > 0:
            max_delta = self.settings.max_speed * duration_ms / 1000
            delta = pos - cur
            if abs(delta) > max_delta:
                pos = cur + max_delta * (1 if delta > 0 else -1)
        self.device.move(pos, duration_ms)
        self._cmd_pos = pos
