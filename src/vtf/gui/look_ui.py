"""View menu entries for the language and the accent colour.

The accent changes at once (also while the colour picker is open). The language is
read once at start (i18n), so changing it offers a restart.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QColorDialog, QMenu, QMessageBox

from .. import i18n, updater
from ..i18n import tr
from . import theme

PREVIEW_MS = 80          # colour picker: repaint the app at most this often


def swatch(color: str, px: int = 14) -> QIcon:
    pm = QPixmap(px * 2, px * 2)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(theme.HAIRLINE))
    p.setBrush(QColor(color))
    p.drawEllipse(2, 2, px * 2 - 4, px * 2 - 4)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)


class LookMenus:
    """Adds "Sprache" and "Akzentfarbe" to a menu of the main window."""

    def __init__(self, win, menu: QMenu):
        self.win = win
        lang = menu.addMenu(tr("Sprache"))
        group = QActionGroup(lang)
        for code, label in (("system", tr("Wie das System")), *i18n.LANGUAGES.items()):
            a = QAction(label, lang, checkable=True)
            a.setChecked(i18n.chosen() == code)
            a.triggered.connect(lambda _on=False, c=code: self.set_language(c))
            group.addAction(a)
            lang.addAction(a)
        self.lang_menu = lang
        self.accent_menu = menu.addMenu(tr("Akzentfarbe"))
        self.accent_menu.aboutToShow.connect(self._fill_accents)
        self._fill_accents()

    # ------------------------------------------------------------ accent
    def _fill_accents(self) -> None:
        m = self.accent_menu
        m.clear()
        presets = [c for _label, c in theme.ACCENTS.values()]
        for label, color in theme.ACCENTS.values():
            a = m.addAction(swatch(color), label)
            a.setCheckable(True)
            a.setChecked(theme.ACCENT == color)
            a.triggered.connect(lambda _on=False, c=color: self.set_accent(c))
        m.addSeparator()
        own = theme.ACCENT not in presets
        a = m.addAction(swatch(theme.ACCENT) if own else QIcon(), tr("Eigene Farbe…"))
        a.setCheckable(True)
        a.setChecked(own)
        a.triggered.connect(lambda _on=False: self.pick_accent())

    def set_accent(self, color: str) -> None:
        used = theme.change_accent(QApplication.instance(), color)
        i18n.save_ui(accent=used)
        name = next((label for label, c in theme.ACCENTS.values() if c == used), used)
        self.win._status(tr("Akzentfarbe: {0}", name))

    def pick_accent(self) -> None:
        """Colour picker with live preview; Cancel puts the old colour back."""
        app = QApplication.instance()
        old = theme.ACCENT
        dlg = QColorDialog(QColor(old), self.win)
        dlg.setWindowTitle(tr("Eigene Akzentfarbe"))
        dlg.setOption(QColorDialog.DontUseNativeDialog)      # the native one sends no live colour
        pending: list[str] = []
        timer = QTimer(dlg, singleShot=True, interval=PREVIEW_MS)
        timer.timeout.connect(lambda: pending and theme.change_accent(app, pending[-1]))

        def preview(c: QColor):
            pending.append(c.name())
            timer.start()
        dlg.currentColorChanged.connect(preview)
        if dlg.exec():
            timer.stop()
            self.set_accent(dlg.selectedColor().name())
        else:
            timer.stop()
            theme.change_accent(app, old)

    # ------------------------------------------------------------ language
    def set_language(self, code: str) -> None:
        if code == i18n.chosen():
            return
        i18n.save_ui(language=code)
        # said in both languages: the new one is not loaded yet
        text = ("Die Sprache wechselt nach einem Neustart.\nThe language changes after a restart.")
        appimage = updater.running_appimage()
        if appimage is None:
            QMessageBox.information(self.win, tr("Sprache"), text)
            return
        ask = text + "\n\nJetzt neu starten? / Restart now?"
        if (QMessageBox.question(self.win, tr("Sprache"), ask) == QMessageBox.Yes
                and self.win.prepare_restart()):
            updater.restart(appimage)
