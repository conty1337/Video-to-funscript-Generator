"""Session statistics, kept only on this computer: how long was played, how many
strokes, which videos and which patterns. No Qt.

Stored per day in ~/.config/video-to-funscript/stats.json:
{"days": {"2026-11-12": {"seconds": 1234.5, "strokes": 812,
                         "videos": {"name": seconds}, "patterns": {"name": seconds}}}}
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

FLUSH_S = 30.0          # write at least this often while recording


def stats_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "video-to-funscript" / "stats.json"


def load() -> dict:
    try:
        d = json.loads(stats_file().read_text(encoding="utf-8"))
        return d if isinstance(d.get("days"), dict) else {"days": {}}
    except (OSError, ValueError, AttributeError):
        return {"days": {}}


def _save(data: dict) -> None:
    f = stats_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def reset() -> None:
    try:
        stats_file().unlink()
    except FileNotFoundError:
        pass


class Recorder:
    """Collects playing time in memory and adds it to the file now and then."""

    def __init__(self, today=None, clock=None):
        self._today = today or (lambda: _dt.date.today().isoformat())
        self._clock = clock or __import__("time").monotonic
        self._buf: dict = {}
        self._last_flush = self._clock()

    def add(self, kind: str, name: str, seconds: float, strokes: int = 0) -> None:
        """kind "video" or "pattern"; seconds played, strokes done meanwhile."""
        if seconds <= 0 and strokes <= 0:
            return
        day = self._buf.setdefault(self._today(), {"seconds": 0.0, "strokes": 0, "videos": {}, "patterns": {}})
        day["seconds"] += max(0.0, seconds)
        day["strokes"] += max(0, int(strokes))
        bucket = day["videos" if kind == "video" else "patterns"]
        bucket[name] = bucket.get(name, 0.0) + max(0.0, seconds)
        if self._clock() - self._last_flush >= FLUSH_S:
            self.flush()

    def flush(self) -> None:
        self._last_flush = self._clock()
        if not self._buf:
            return
        data = load()
        for day, add in self._buf.items():
            d = data["days"].setdefault(day, {"seconds": 0.0, "strokes": 0, "videos": {}, "patterns": {}})
            d["seconds"] = float(d.get("seconds", 0)) + add["seconds"]
            d["strokes"] = int(d.get("strokes", 0)) + add["strokes"]
            for key in ("videos", "patterns"):
                tgt = d.setdefault(key, {})
                for name, s in add[key].items():
                    tgt[name] = float(tgt.get(name, 0)) + s
        self._buf = {}
        try:
            _save(data)
        except OSError:
            pass


def summary(data: dict | None = None, days: int | None = None, today: str | None = None) -> dict:
    """Totals over the last `days` days (None = all): seconds, strokes, days played,
    favourite videos and patterns (name, seconds), most first."""
    data = data if data is not None else load()
    today_d = _dt.date.fromisoformat(today) if today else _dt.date.today()
    out = {"seconds": 0.0, "strokes": 0, "days": 0, "videos": {}, "patterns": {}}
    for day, d in data.get("days", {}).items():
        try:
            age = (today_d - _dt.date.fromisoformat(day)).days
        except ValueError:
            continue
        if days is not None and not 0 <= age < days:
            continue
        out["seconds"] += float(d.get("seconds", 0))
        out["strokes"] += int(d.get("strokes", 0))
        out["days"] += 1 if float(d.get("seconds", 0)) > 0 else 0
        for key in ("videos", "patterns"):
            for name, s in d.get(key, {}).items():
                out[key][name] = out[key].get(name, 0.0) + float(s)
    for key in ("videos", "patterns"):
        out[key] = sorted(out[key].items(), key=lambda kv: -kv[1])
    return out


def fmt_duration(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"
