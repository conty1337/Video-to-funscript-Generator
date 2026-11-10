"""Safety timer: stop after a while, stop when nobody touches anything, short breaks.

Switched off by default. All limits are optional (0 = off). The guard only counts
time while something drives the device (video, pattern, program, music, other
player); it does not move anything itself, it tells the caller what to do.
"""

from __future__ import annotations

from dataclasses import dataclass

SESSION_GAP_MS = 5 * 60_000      # this long without motion starts a new session


@dataclass
class SafetySettings:
    enabled: bool = False
    max_minutes: int = 30         # stop after this much motion in one go (0 = off)
    idle_minutes: int = 10        # stop when nothing was touched this long (0 = off)
    break_every: int = 0          # short pause every N minutes of motion (0 = off)
    break_seconds: int = 30

    KEYS = ("enabled", "max_minutes", "idle_minutes", "break_every", "break_seconds")


class SafetyGuard:
    def __init__(self, settings: SafetySettings | None = None):
        self.settings = settings or SafetySettings()
        self.active_ms = 0.0          # motion in this session
        self.since_break_ms = 0.0
        self.last_input = 0.0
        self.break_until: float | None = None
        self._last: float | None = None
        self._idle_since: float | None = None

    def touch(self, now: float) -> None:
        """The user did something (key, mouse, controller, phone)."""
        self.last_input = now

    def reset(self) -> None:
        self.active_ms = self.since_break_ms = 0.0
        self.break_until = None
        self._idle_since = None

    @property
    def on_break(self) -> bool:
        return self.break_until is not None

    def update(self, now: float, active: bool) -> tuple | None:
        """Call regularly. Returns ("stop", reason), ("break", seconds), ("resume",) or None."""
        dt = 0.0 if self._last is None else max(0.0, now - self._last)
        self._last = now
        s = self.settings
        if not s.enabled:
            self.reset()
            return None
        if self.break_until is not None:
            if now >= self.break_until:
                self.break_until = None
                self.last_input = now          # coming back from a break counts as being there
                return ("resume",)
            return None
        if not active:
            if self._idle_since is None:
                self._idle_since = now
            elif now - self._idle_since >= SESSION_GAP_MS:
                self.active_ms = self.since_break_ms = 0.0
            return None
        self._idle_since = None
        self.active_ms += dt
        self.since_break_ms += dt
        if s.max_minutes > 0 and self.active_ms >= s.max_minutes * 60_000:
            self.reset()
            return ("stop", f"Sicherheits-Timer: nach {s.max_minutes} min gestoppt")
        if s.idle_minutes > 0 and now - self.last_input >= s.idle_minutes * 60_000:
            self.reset()
            return ("stop", f"Sicherheits-Timer: {s.idle_minutes} min nichts bedient, gestoppt")
        if s.break_every > 0 and self.since_break_ms >= s.break_every * 60_000:
            self.since_break_ms = 0.0
            self.break_until = now + max(5, s.break_seconds) * 1000
            return ("break", max(5, s.break_seconds))
        return None

    def status(self, now: float) -> str:
        """Short text for the status bar ("" when off)."""
        s = self.settings
        if not s.enabled:
            return ""
        if self.break_until is not None:
            left = max(0, int((self.break_until - now) / 1000))
            return f"Pause · noch {left} s"
        parts = []
        if s.max_minutes > 0 and self.active_ms > 0:
            left = max(0.0, s.max_minutes * 60_000 - self.active_ms) / 60_000
            parts.append(f"Stopp in {left:.0f} min")
        return "Sicherheit an" + (" · " + parts[0] if parts else "")
