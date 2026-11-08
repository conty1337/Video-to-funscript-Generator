"""Common interface for stroker devices."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Callable


class DeviceError(Exception):
    pass


class Device(ABC):
    """A linear device. Positions are 0..100 (like funscript), durations in ms.

    Implementations must be thread-safe: the GUI calls ``move``/``stop`` from the
    main thread while connections may run in background threads.
    """

    name: str = "Gerät"

    def __init__(self):
        self._listeners: list[Callable[[str], None]] = []
        self._lock = threading.Lock()

    # status messages for the UI ("verbunden", "getrennt", errors)
    def add_listener(self, fn: Callable[[str], None]) -> None:
        self._listeners.append(fn)

    def _emit(self, msg: str) -> None:
        for fn in list(self._listeners):
            try:
                fn(msg)
            except Exception:
                pass

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def move(self, position: float, duration_ms: int) -> None:
        """Move to ``position`` (0..100) within ``duration_ms``."""

    @abstractmethod
    def stop(self) -> None:
        """Stop all motion immediately."""

    def current_position(self) -> float | None:
        """Best estimate of the physical position (simulator only), else None."""
        return None
