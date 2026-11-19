"""Built-in Intiface Engine (Buttplug server without a window, BSD licence).

The AppImage carries the newest engine (fetched at build time). When the app connects
to a local Intiface address where nothing listens yet, it starts the engine there; a
running Intiface Central is used as before.
"""

from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

NAME = "intiface-engine"
START_TIMEOUT_S = 8.0
LOCAL = {"127.0.0.1", "localhost", "::1"}


def engine_path() -> Path | None:
    """The engine next to the frozen app (AppImage), from VTF_INTIFACE_ENGINE, or on PATH."""
    own = os.environ.get("VTF_INTIFACE_ENGINE")
    if own:
        return Path(own) if os.access(own, os.X_OK) else None
    if getattr(sys, "frozen", False):
        p = Path(sys.executable).with_name(NAME)
        if os.access(p, os.X_OK):
            return p
    found = shutil.which(NAME)
    return Path(found) if found else None


def listening(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure(url: str) -> subprocess.Popen | None:
    """Start the engine for a local `url` nobody serves yet. Returns the process it
    started (to stop later), or None: remote address, server already there, no engine."""
    u = urlparse(url)
    host, port = u.hostname or "", u.port or 12345
    if host not in LOCAL or listening(host, port):
        return None
    path = engine_path()
    if path is None:
        return None
    proc = subprocess.Popen([str(path), "--websocket-port", str(port), "--use-bluetooth-le"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(stop, proc)
    end = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < end and proc.poll() is None:
        if listening(host, port):
            return proc
        time.sleep(0.1)
    stop(proc)          # exited or never listened: the connect error tells the user
    return None


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
