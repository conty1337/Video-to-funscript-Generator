"""Update handling in the GUI: quiet check on start, background install, restart button."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QMessageBox, QPushButton

from .. import __version__, updater
from .workers import UpdateWorker

CHECK_DELAY_MS = 4000


class UpdateController(QObject):
    def __init__(self, window, settings):
        super().__init__(window)
        self.win = window
        self.settings = settings
        self.worker: UpdateWorker | None = None
        self.release: updater.Release | None = None
        self.installed_path: Path | None = None
        self.interactive = False

        self.button = QPushButton()
        self.button.setObjectName("statusAction")
        self.button.setFlat(True)
        self.button.setVisible(False)
        self.button.clicked.connect(self._button_clicked)
        window.statusBar().addPermanentWidget(self.button)

        self.auto_action = QAction("Automatisch aktualisieren", window)
        self.auto_action.setCheckable(True)
        self.auto_action.setChecked(self.auto_enabled())
        self.auto_action.toggled.connect(lambda on: self.settings.setValue("auto_update", on))

        if self.auto_enabled() and updater.running_appimage() and not os.environ.get("VTF_NO_UPDATE"):
            QTimer.singleShot(CHECK_DELAY_MS, lambda: self.check(interactive=False))

    def auto_enabled(self) -> bool:
        v = self.settings.value("auto_update", True)
        return v if isinstance(v, bool) else str(v).lower() == "true"

    def add_menu_entries(self, menu) -> None:
        menu.addSeparator()
        a = QAction("Nach Updates suchen", self.win)
        a.triggered.connect(lambda: self.check(interactive=True))
        menu.addAction(a)
        menu.addAction(self.auto_action)
        info = QAction(f"Version {__version__}", self.win)
        info.setEnabled(False)
        menu.addAction(info)

    # ------------------------------------------------------------ check
    def check(self, interactive: bool) -> None:
        if self.worker and self.worker.isRunning():
            return
        if self.installed_path:
            if interactive:
                self._ask_restart()
            return
        self.interactive = interactive
        self.worker = UpdateWorker("check")
        self.worker.checked.connect(self._checked)
        self.worker.failed.connect(self._check_failed)
        self.worker.start()

    def _checked(self, release):
        if release is None:
            if self.interactive:
                QMessageBox.information(self.win, "Updates", f"Version {__version__} ist aktuell.")
            return
        self.release = release
        if updater.can_self_update() and (self.auto_enabled() or self.interactive):
            self._install()
        else:
            self._show(f"Version {release.version} verfügbar")
            if self.interactive:
                self._button_clicked()

    def _check_failed(self, msg: str):
        if self.interactive:
            QMessageBox.warning(self.win, "Updates",
                                f"Die Suche nach Updates ist fehlgeschlagen:\n{msg}")

    # ------------------------------------------------------------ install
    def _install(self):
        self._show(f"Lade Version {self.release.version}  0 %", enabled=False)
        self.worker = UpdateWorker("install", self.release)
        self.worker.progress.connect(
            lambda p: self.button.setText(f"Lade Version {self.release.version} {p * 100:3.0f} %"))
        self.worker.installed.connect(self._installed)
        self.worker.failed.connect(self._install_failed)
        self.worker.start()

    def _installed(self, path: str):
        self.installed_path = Path(path)
        self._show(f"Version {self.release.version} installiert · Neu starten")
        if self.interactive:
            self._ask_restart()

    def _install_failed(self, msg: str):
        self._show(f"Version {self.release.version} verfügbar")
        if self.interactive:
            QMessageBox.warning(self.win, "Update", f"Das Update konnte nicht installiert werden:\n{msg}")

    # ------------------------------------------------------------ ui
    def _show(self, text: str, enabled: bool = True):
        self.button.setText(text)
        self.button.setEnabled(enabled)
        self.button.setVisible(True)

    def _button_clicked(self):
        if self.installed_path:
            self._ask_restart()
        elif self.release and updater.can_self_update():
            self.interactive = True
            self._install()
        elif self.release:
            where = ("Diese Installation kann sich nicht selbst aktualisieren "
                     "(kein AppImage oder keine Schreibrechte im Ordner).")
            r = QMessageBox.question(self.win, "Update verfügbar",
                                     f"Version {self.release.version} ist verfügbar.\n{where}\n\n"
                                     "Download-Seite öffnen?")
            if r == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl(self.release.page_url))

    def _ask_restart(self):
        notes = (self.release.notes[:600] + "…") if self.release and len(self.release.notes) > 600 else (
            self.release.notes if self.release else "")
        text = f"Version {self.release.version} ist installiert. Jetzt neu starten?"
        if notes:
            text += f"\n\nNeu in dieser Version:\n{notes}"
        if QMessageBox.question(self.win, "Update installiert", text) == QMessageBox.Yes:
            self.restart_now()

    def restart_now(self):
        if not self.win.prepare_restart():
            return
        updater.restart(self.installed_path)

    def shutdown(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
