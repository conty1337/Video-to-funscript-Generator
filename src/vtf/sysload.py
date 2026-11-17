"""Load shown in the status bar: the whole CPU, the graphics card (AMD) and how busy its video
decoder is for this app (and the ffmpeg it starts). Linux only; what cannot be read is None."""

from __future__ import annotations

import glob
import os
import time

DEC_KEYS = ("drm-engine-dec", "drm-engine-video", "drm-engine-vcn")   # AMD, Intel, newer AMD


def cpu_times(text: str) -> tuple[int, int] | None:
    """(busy, total) jiffies from /proc/stat."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:9]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            return sum(v) - idle, sum(v)
    return None


def decode_ns(fdinfo: str) -> tuple[str, int] | None:
    """(client, ns the video decoder worked for it) from one DRM fdinfo; None if it is none."""
    keys, ns = {}, 0
    for line in fdinfo.splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        keys[k] = v
        if k.startswith(DEC_KEYS) and v.endswith("ns"):
            ns += int(v.split()[0])
    if "drm-client-id" not in keys:
        return None
    return f"{keys.get('drm-pdev', '')}/{keys['drm-client-id']}", ns


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _family(root: int) -> list[int]:
    """This process and everything it started (the ffmpeg reading the video)."""
    kids: dict[int, list[int]] = {}
    for d in glob.glob("/proc/[0-9]*/stat"):
        s = _read(d)
        try:
            pid, ppid = int(s.split()[0]), int(s[s.rindex(")") + 2:].split()[1])
        except (ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(pid)
    out, todo = [], [root]
    while todo:
        p = todo.pop()
        out.append(p)
        todo += kids.get(p, [])
    return out


def _decode_total() -> int | None:
    clients: dict[str, int] = {}
    for pid in _family(os.getpid()):
        for fd in glob.glob(f"/proc/{pid}/fd/*"):
            try:
                if not os.readlink(fd).startswith("/dev/dri/"):
                    continue
            except OSError:
                continue
            got = decode_ns(_read(f"/proc/{pid}/fdinfo/{os.path.basename(fd)}"))
            if got:
                clients[got[0]] = max(clients.get(got[0], 0), got[1])
    return sum(clients.values()) if clients else None


def gpu_busy() -> int | None:
    vals = []
    for p in glob.glob("/sys/class/drm/card*/device/gpu_busy_percent"):
        try:
            vals.append(int(_read(p).strip()))
        except ValueError:
            pass
    return max(vals) if vals else None


class Load:
    """Call sample() now and then: (cpu %, gpu %, decode %) since the last call."""

    def __init__(self):
        self._cpu = cpu_times(_read("/proc/stat"))
        self._dec = _decode_total()
        self._t = time.monotonic()

    def sample(self) -> tuple[float | None, int | None, float | None]:
        now, cpu, dec = time.monotonic(), cpu_times(_read("/proc/stat")), _decode_total()
        cpu_pct = dec_pct = None
        if cpu and self._cpu and cpu[1] > self._cpu[1]:
            cpu_pct = 100.0 * (cpu[0] - self._cpu[0]) / (cpu[1] - self._cpu[1])
        if dec is not None:
            # a client that went away (ffmpeg ended) leaves the sum smaller: count from 0
            old = self._dec if self._dec is not None and self._dec <= dec else 0
            dt = max(1e-3, now - self._t)
            dec_pct = min(100.0, (dec - old) / (dt * 1e9) * 100.0) if old else 0.0
        self._cpu, self._dec, self._t = cpu, dec, now
        return cpu_pct, gpu_busy(), dec_pct
