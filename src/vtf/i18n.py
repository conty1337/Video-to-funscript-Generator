"""Languages of the interface. No Qt.

The German text in the code is the key: tr("Abspielen") gives "Play" in English. Text with
values uses numbered fields, tr("{0} Punkte", n), so a translation can move them around.
The language is read once, before the first text is shown, from
~/.config/video-to-funscript/ui.json (written by the settings), or VTF_LANG for tests.
A change takes effect after a restart.
"""

from __future__ import annotations

import json
import locale
import os
from pathlib import Path

LANGUAGES = {"de": "Deutsch", "en": "English"}
DEFAULT = "de"

_lang: str | None = None
_catalog: dict[str, str] = {}


def ui_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "video-to-funscript" / "ui.json"


def load_ui() -> dict:
    try:
        d = json.loads(ui_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ui(**values) -> None:
    """Merge the given keys into ui.json (language, accent)."""
    d = load_ui()
    d.update(values)
    f = ui_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


def system_language() -> str:
    """German if the system speaks German, otherwise English."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        v = os.environ.get(var, "")
        if v and v not in ("C", "POSIX"):
            return "de" if v.lower().startswith("de") else "en"
    try:
        loc = locale.getlocale()[0] or ""
    except ValueError:
        loc = ""
    return "de" if loc.lower().startswith("de") or not loc else "en"


def chosen() -> str:
    """What the settings say: a language code or "system"."""
    v = load_ui().get("language", "system")
    return v if v in LANGUAGES or v == "system" else "system"


def current() -> str:
    global _lang
    if _lang is None:
        env = os.environ.get("VTF_LANG", "")
        c = env if env in LANGUAGES else chosen()
        set_language(system_language() if c == "system" else c)
    return _lang


def set_language(code: str) -> None:
    """Switch the catalog (used at start; widgets already built keep their text)."""
    global _lang, _catalog
    _lang = code if code in LANGUAGES else DEFAULT
    _catalog = {}
    if _lang == "en":
        from .locale_en import EN
        _catalog = EN


def tr(text: str, *args) -> str:
    if _lang is None:
        current()
    s = _catalog.get(text, text)
    if args:
        try:
            return s.format(*args)
        except (IndexError, KeyError, ValueError):
            return text.format(*args)
    return s


def plural(n: int, one: str, many: str, *args) -> str:
    """tr() of the singular or plural text; the count is field {0}."""
    return tr(one if n == 1 else many, n, *args)


def dec(text: str) -> str:
    """Decimal comma in German, point in English."""
    return text.replace(".", ",") if current() == "de" else text
