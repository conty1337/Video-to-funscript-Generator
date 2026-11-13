"""Languages and accent colours."""
import ast
import json
import os
import re
import string
import subprocess
import sys
from pathlib import Path

import pytest

from vtf import i18n
from vtf.locale_en import EN

SRC = Path(__file__).resolve().parents[1] / "src"
KEYSEQ = re.compile(r"^(Ctrl|Shift|Alt)\+|^(Space|Esc|Left|Right|Up|Down|PgUp|PgDown)$")


def _texts_in_code() -> set[str]:
    """Every literal given to tr() or plural() in the program."""
    out = set()
    for p in (SRC / "vtf").rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                args = n.args[:1] if n.func.id == "tr" else n.args[1:3] if n.func.id == "plural" else []
                out |= {a.value for a in args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    return out


def _fields(s: str) -> list[str]:
    return sorted(f"{name}:{spec}!{conv}" for _t, name, spec, conv in string.Formatter().parse(s) if name is not None)


@pytest.fixture
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("de")


def test_every_text_has_an_english_translation():
    from vtf import patterns as P
    names = ({p.name for p in P.BUILTIN} | {p.category for p in P.BUILTIN}
             | {p.name for p in P.BUILTIN_PROGRAMS} | {"Eigene"})
    missing = sorted(t for t in _texts_in_code() | names if t not in EN and not KEYSEQ.search(t))
    assert not missing, f"{len(missing)} texts without English: {missing[:10]}"


def test_translations_keep_the_fields():
    """{0}, {1:.0f} … must survive the translation, or the text breaks at runtime."""
    bad = [k for k, v in EN.items() if _fields(k) != _fields(v)]
    assert not bad, bad[:10]
    leftovers = [v for v in EN.values() if re.search(r"[äöüÄÖÜß]", v)]
    assert not leftovers, leftovers[:10]


def test_tr_plural_dec(english):
    assert i18n.tr("Abspielen") == "Play"
    assert i18n.tr("{0} Punkte erzeugt", 12) == "12 points created"
    assert i18n.tr("gibt es nicht im Katalog {0}", 3) == "gibt es nicht im Katalog 3"
    assert i18n.plural(1, "{0} Stelle prüfen: {1}", "{0} Stellen prüfen: {1}", "x") == "Check 1 spot: x"
    assert i18n.plural(3, "{0} Stelle prüfen: {1}", "{0} Stellen prüfen: {1}", "x") == "Check 3 spots: x"
    assert i18n.dec("1.5 s") == "1.5 s"
    assert i18n.num(10500) == "10,500"
    i18n.set_language("de")
    assert i18n.num(10500) == "10.500"
    assert i18n.tr("Abspielen") == "Abspielen"
    assert i18n.dec("1.5 s") == "1,5 s"


def test_language_choice_is_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert i18n.chosen() == "system"
    i18n.save_ui(language="en")
    i18n.save_ui(accent="#123456")
    assert i18n.chosen() == "en"
    assert json.loads(i18n.ui_file().read_text()) == {"language": "en", "accent": "#123456"}
    i18n.ui_file().write_text("{kaputt")
    assert i18n.chosen() == "system"


def test_system_language(monkeypatch):
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert i18n.system_language() == "de"
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    assert i18n.system_language() == "en"


def test_phone_page_in_english_and_accent(english):
    from vtf import remote
    for de, _en in remote.PAGE_EN:
        assert de in remote.PAGE, de                 # the page still contains every piece
    page = remote.page("#5b9cf5")
    assert 'lang="en"' in page and ">Connect</button>" in page and "Verbinden" not in page
    assert "#e5a043" not in page and "#5b9cf5" in page and "rgba(91,156,245," in page
    i18n.set_language("de")
    assert remote.page().count("Fernbedienung") >= 2


def test_builtin_patterns_show_translated_names(english):
    from vtf import patterns as P
    p = P.BUILTIN[0]
    assert p.label == EN[p.name] and p.name in [x.name for x in P.BUILTIN]
    own = P.Pattern("Gleichmäßig")                 # a user pattern keeps its name as typed
    assert own.label == "Gleichmäßig"
    assert P.BUILTIN_PROGRAMS[0].label == EN[P.BUILTIN_PROGRAMS[0].name]


def test_accent_colours():
    pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
    from vtf.gui import theme
    try:
        for _label, color in theme.ACCENTS.values():
            assert theme.set_accent(color) == color and theme.ON_ACCENT == theme.BG   # dark text fits
        assert theme.set_accent("#202a60") == "#202a60" and theme.ON_ACCENT == "#ffffff"
        assert theme.set_accent("kein-farbwert") == theme.DEFAULT_ACCENT
        assert "#202a60" not in theme.build_qss()
        theme.set_accent("#202a60")
        assert "#202a60" in theme.build_qss()
    finally:
        theme.set_accent(theme.DEFAULT_ACCENT)


def test_accent_changes_the_app(tmp_path):
    """Runs in its own process: restyling the app re-polishes every widget, and the GUI
    tests before this one leave thousands behind (minutes instead of a second)."""
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    code = r"""
from PySide6 import QtWidgets
from vtf import i18n
from vtf.gui import theme
from vtf.gui.look_ui import Look
app = QtWidgets.QApplication([])
theme.apply(app)

class Win(QtWidgets.QMainWindow):
    said = []

    def _status(self, text):
        self.said.append(text)

w = Win()
btn = theme.accent_icon(QtWidgets.QPushButton(), "play", "Start")
look = Look(w)
blue = theme.ACCENTS["blue"][1]
look.set_accent(blue)
assert theme.ACCENT == blue and blue in app.styleSheet()
assert i18n.load_ui()["accent"] == blue
assert w.said[-1] == "Akzentfarbe: Blau", w.said
assert btn.text() == "Start" and not btn.icon().isNull()
look.set_accent("#3a1f5c")                   # own colour
assert theme.ON_ACCENT == "#ffffff"
QtWidgets.QColorDialog.exec = lambda self: 0  # cancelled
look.pick_accent()
assert theme.ACCENT == "#3a1f5c"
print("ok")
"""
    env = dict(os.environ, VTF_LANG="de", QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path / "c"),
               XDG_CACHE_HOME=str(tmp_path / "cache"), PYTHONPATH=str(SRC))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert "ok" in r.stdout, r.stdout + r.stderr


def test_language_choice_restarts(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from vtf.gui.look_ui import Look
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a: shown.append(a[2]))
    w = QtWidgets.QMainWindow()
    Look(w).set_language("en")
    assert i18n.chosen() == "en" and "restart" in shown[0]
    w.deleteLater()


def test_whole_window_in_english(tmp_path):
    """Start the main window in English in its own process: every dialog builds, the
    texts are English."""
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    code = r"""
import sys
from PySide6.QtWidgets import QApplication
from vtf.gui import theme
from vtf.gui.main_window import MainWindow
app = QApplication([])
theme.apply(app)
w = MainWindow()
titles = [a.text() for a in w.menuBar().actions()]
assert "&File" in titles and "&Edit" in titles and "&View" in titles, titles
assert w.analyze_btn.text() == "Analyse", w.analyze_btn.text()
from vtf.gui.library_dialog import LibraryDialog
from vtf.gui.stats_dialog import StatsDialog
from vtf.gui.pattern_dialog import PatternDialog
from vtf.gui.settings_dialog import SettingsDialog
assert SettingsDialog(w).nav.item(0).text() == "General"
assert LibraryDialog(w.qsettings, w).windowTitle() == "Library"
StatsDialog(w)
d = PatternDialog(w.devices, w)
assert d.windowTitle() == "Pattern control", d.windowTitle()
d.hide()
w.dirty = False
print("ok")
"""
    env = dict(os.environ, VTF_LANG="en", QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path / "c"),
               XDG_CACHE_HOME=str(tmp_path / "cache"), PYTHONPATH=str(SRC), VTF_NO_UPDATE="1")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert "ok" in r.stdout, r.stdout + r.stderr
