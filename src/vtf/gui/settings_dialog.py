"""One window for the app's settings (Datei → Einstellungen…, Strg+,).

Settings that are used while working stay where they are too (analysis options in the left
bar, the playlist options, the device window); this window changes the very same values by
driving those widgets, so both places always agree.
"""

from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, i18n
from ..device.buttplug import VIBE_MODES
from ..i18n import tr
from . import theme
from .device_ui import DeviceDialog, SafetyBox
from .look_ui import swatch
from .scripts_ui import ScriptFolderDialog

PAGES = ("general", "storage", "analysis", "player", "device")


def _mirror_check(src) -> QCheckBox:
    """A checkbox standing for `src` (a QCheckBox or a checkable QAction)."""
    cb = QCheckBox(src.text())
    cb.setToolTip(src.toolTip())
    cb.setChecked(src.isChecked())
    cb.setEnabled(src.isEnabled())
    cb.toggled.connect(src.setChecked)
    return cb


def _mirror_combo(src: QComboBox) -> QComboBox:
    box = QComboBox()
    for i in range(src.count()):
        box.addItem(src.itemIcon(i), src.itemText(i), src.itemData(i))
    box.setCurrentIndex(src.currentIndex())
    box.setToolTip(src.toolTip())
    box.currentIndexChanged.connect(src.setCurrentIndex)
    return box


def _mirror_spin(src: QSpinBox) -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(src.minimum(), src.maximum())
    sp.setSuffix(src.suffix())
    sp.setValue(src.value())
    sp.setToolTip(src.toolTip())
    sp.valueChanged.connect(src.setValue)
    return sp


def _caption(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("caption")
    lab.setWordWrap(True)
    return lab


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setHorizontalSpacing(12)
    f.setVerticalSpacing(12)
    return f


class SettingsDialog(QDialog):
    def __init__(self, win, page: str = "general"):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(tr("Einstellungen"))
        self.resize(860, 600)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(190)
        self.stack = QStackedWidget()
        builds = {"general": (tr("Allgemein"), self._general), "storage": (tr("Speicherorte"), self._storage),
                  "analysis": (tr("Analyse"), self._analysis), "player": (tr("Player"), self._player),
                  "device": (tr("Gerät"), self._device)}
        for key in PAGES:
            title, build = builds[key]
            self.nav.addItem(title)
            body = QWidget()
            lay = QVBoxLayout(body)
            lay.setContentsMargins(24, 24, 24, 24)
            lay.setSpacing(12)
            build(lay)
            lay.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(body)
            self.stack.addWidget(scroll)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 16, 16)    # the pages bring their own 24 px
        right.addWidget(self.stack, 1)
        foot = QHBoxLayout()
        foot.addStretch(1)
        close = QPushButton(tr("Schließen"))
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        right.addLayout(foot)
        outer.addWidget(self.nav)
        outer.addLayout(right, 1)
        self.nav.setCurrentRow(PAGES.index(page) if page in PAGES else 0)

    # ------------------------------------------------------------ pages
    def _general(self, lay: QVBoxLayout) -> None:
        win = self.win
        lay.addWidget(theme.section_title(tr("Darstellung")))
        f = _form()
        self.language = QComboBox()
        for code, label in (("system", tr("Wie das System")), *i18n.LANGUAGES.items()):
            self.language.addItem(label, code)
        self.language.setCurrentIndex(max(0, self.language.findData(i18n.chosen())))
        self.language.currentIndexChanged.connect(lambda _i: win.look.set_language(self.language.currentData()))
        f.addRow(DeviceDialog._label(tr("Sprache")), self.language)
        self.accent = QComboBox()
        self._fill_accents()
        self.accent.activated.connect(self._accent_chosen)
        f.addRow(DeviceDialog._label(tr("Akzentfarbe")), self.accent)
        lay.addLayout(f)

        lay.addSpacing(8)
        lay.addWidget(theme.section_title(tr("Updates")))
        self.auto_update = _mirror_check(win.updates.auto_action)
        lay.addWidget(self.auto_update)
        row = QHBoxLayout()
        check = QPushButton(theme.icon("rotate-ccw"), tr("Jetzt nach Updates suchen"))
        check.clicked.connect(lambda: win.updates.check(interactive=True))
        row.addWidget(check)
        row.addWidget(_caption(tr("Version {0}", __version__)))
        row.addStretch(1)
        lay.addLayout(row)

        lay.addSpacing(8)
        lay.addWidget(theme.section_title(tr("Startmenü")))
        self.menu_entry = _mirror_check(win.desktop_action)
        lay.addWidget(self.menu_entry)

    def _fill_accents(self) -> None:
        a = self.accent
        a.blockSignals(True)
        a.clear()
        presets = [c for _label, c in theme.ACCENTS.values()]
        for label, color in theme.ACCENTS.values():
            a.addItem(swatch(color), label, color)
        own = theme.ACCENT not in presets
        a.addItem(swatch(theme.ACCENT) if own else QIcon(), tr("Eigene Farbe…"), "")
        a.setCurrentIndex(a.count() - 1 if own else a.findData(theme.ACCENT))
        a.blockSignals(False)

    def _accent_chosen(self, i: int) -> None:
        color = self.accent.itemData(i)
        if color:
            self.win.look.set_accent(color)
        else:
            self.win.look.pick_accent()
        self._fill_accents()

    def _storage(self, lay: QVBoxLayout) -> None:
        self.folders = ScriptFolderDialog(self, embedded=True)
        lay.addWidget(self.folders)

    def _analysis(self, lay: QVBoxLayout) -> None:
        p = self.win.panel
        lay.addWidget(theme.section_title(tr("Analyse")))
        f = _form()
        self.hwaccel = _mirror_combo(p.hwaccel)
        f.addRow(DeviceDialog._label(tr("Dekodierung")), self.hwaccel)
        lay.addLayout(f)
        self.opencl = _mirror_check(p.opencl)
        lay.addWidget(self.opencl)
        row = QHBoxLayout()
        test = QPushButton(tr("Dekodierung testen…"))
        test.clicked.connect(self.win.test_decoding)
        row.addWidget(test)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_caption(tr("Gilt ab der nächsten Analyse. Dieselben Einstellungen stehen auch in der "
                                  "linken Leiste unter „Optionen“.")))

    def _player(self, lay: QVBoxLayout) -> None:
        pp = self.win.player
        lay.addWidget(theme.section_title(tr("Abspielen")))
        self.intro = _mirror_check(pp.intro_cb)
        self.countdown = _mirror_check(pp.countdown_cb)
        self.countdown_s = _mirror_spin(pp.countdown_s)
        self.cuts = _mirror_check(pp.cuts_cb)
        self.quiet = _mirror_check(pp.quiet_cb)
        for w in (self.intro, self.countdown):
            lay.addWidget(w)
        f = _form()
        f.addRow(DeviceDialog._label(tr("Countdown-Länge")), DeviceDialog._row(self.countdown_s))
        lay.addLayout(f)
        for w in (self.cuts, self.quiet):
            lay.addWidget(w)
        lay.addWidget(_caption(tr("Dieselben Einstellungen stehen auch unter der Playlist.")))

    def _device(self, lay: QVBoxLayout) -> None:
        ctl = self.win.devices
        lay.addWidget(theme.section_title(tr("Gerät")))
        f = _form()
        self.vibe = QComboBox()
        for code, label in VIBE_MODES:
            self.vibe.addItem(label, code)
        self.vibe.setCurrentIndex(max(0, self.vibe.findData(ctl.qs.value("device/vibe", "speed"))))
        self.vibe.setToolTip(tr("Nur für Vibratoren und andere Geräte ohne Hub: woraus die Stärke wird"))
        self.vibe.currentIndexChanged.connect(lambda _i: ctl.set_vibe_mode(self.vibe.currentData()))
        f.addRow(DeviceDialog._label(tr("Vibration folgt")), self.vibe)
        lay.addLayout(f)
        lay.addSpacing(8)
        self.safety = SafetyBox(ctl)
        lay.addWidget(self.safety)
        lay.addWidget(_caption(tr("Verbinden, Zeitversatz, Tempo und Hubbereich: im Fenster „Gerät“ (Strg+D).")))
