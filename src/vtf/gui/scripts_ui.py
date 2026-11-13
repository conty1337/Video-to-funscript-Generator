"""Where scripts are saved and looked for: next to the video and/or in one script folder."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
)

from .. import scriptdir
from ..i18n import tr
from . import theme


class ScriptFolderDialog(QDialog):
    """Changes apply at once (scriptdir.save_settings)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Speicherort für Scripts"))
        self.setMinimumWidth(520)
        sd = scriptdir.settings()
        self.folder: Path | None = sd.folder

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        cap = QLabel(tr("Ohne Script-Ordner liegt jedes Script neben seinem Video, so finden es die meisten "
                        "Player. Mit Script-Ordner landen alle Scripts zusätzlich (oder nur) dort. Bibliothek, "
                        "Player, Stapel und andere Player suchen erst neben dem Video, dann im Script-Ordner "
                        "und seinen Unterordnern."))
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        lay.addWidget(cap)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.path_label = QLabel()
        self.path_label.setObjectName("meta")
        self.path_label.setFont(theme.mono_font(12))
        self.path_label.setWordWrap(True)
        pick = QPushButton(theme.icon("folder-open"), tr("Wählen…"))
        pick.clicked.connect(self._pick)
        self.clear_btn = QPushButton(theme.icon("x"), "")
        self.clear_btn.setToolTip(tr("Kein Script-Ordner: Scripts nur neben dem Video"))
        self.clear_btn.clicked.connect(lambda: self._set_folder(None))
        row.addWidget(QLabel(tr("Script-Ordner")))
        row.addWidget(self.path_label, 1)
        row.addWidget(pick)
        row.addWidget(self.clear_btn)
        lay.addLayout(row)

        self.next_to = QCheckBox(tr("Zusätzlich neben dem Video speichern"))
        self.next_to.setToolTip(tr("Die meisten Player (und andere Programme) suchen das Script neben dem Video"))
        self.next_to.setChecked(sd.next_to_video)
        self.next_to.toggled.connect(lambda _on: self._store())
        lay.addWidget(self.next_to)

        foot = QHBoxLayout()
        foot.addStretch(1)
        close = QPushButton(tr("Schließen"))
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        lay.addLayout(foot)
        self._refresh()

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, tr("Script-Ordner"), str(self.folder or Path.home()))
        if d:
            self._set_folder(Path(d))

    def _set_folder(self, folder: Path | None):
        self.folder = folder
        self._store()

    def _store(self):
        try:
            scriptdir.save_settings(self.folder, self.next_to.isChecked())
        except OSError:
            pass
        self._refresh()

    def _refresh(self):
        has = self.folder is not None
        self.path_label.setText(str(self.folder) if has else tr("keiner – Scripts liegen neben dem Video"))
        self.clear_btn.setEnabled(has)
        self.next_to.setEnabled(has)


def describe() -> str:
    """One line for menus and the library: where Ctrl+S saves now."""
    sd = scriptdir.settings()
    if not sd.active:
        return tr("Scripts: neben dem Video")
    if sd.next_to_video:
        return tr("Scripts: neben dem Video und in {0}", sd.folder)
    return tr("Scripts: in {0}", sd.folder)
