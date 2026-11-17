"""Named script settings ("Maschine seitlich", "Reiten" …): saved once, loaded with one click.
In the data folder when one is set (the laptop finds them too), else in the config folder."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .scriptdir import settings


def profiles_file() -> Path:
    data = settings().data_folder
    if data is not None:
        return data / "profiles.json"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "video-to-funscript" / "profiles.json"


def load() -> dict[str, dict]:
    try:
        d = json.loads(profiles_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _write(d: dict) -> None:
    path = profiles_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def save(name: str, generator: dict) -> None:
    d = load()
    d[name] = dict(generator)
    _write(d)


def delete(name: str) -> None:
    d = load()
    if d.pop(name, None) is not None:
        _write(d)
