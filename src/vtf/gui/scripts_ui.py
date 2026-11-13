"""Where the app keeps its files: scripts (next to the video and/or in a script folder) and the
video data (<video>.vtf.json: boxes, sections, calibration, cuts, bookmarks)."""

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


class _FolderRow(QHBoxLayout):
    """Label, the chosen folder (or what happens without one), Choose…, clear."""

    def __init__(self, title: str, empty: str, clear_tip: str, on_change):
        super().__init__()
        self.setSpacing(8)
        self.empty = empty
        self.on_change = on_change
        self.folder: Path | None = None
        self.path_label = QLabel()
        self.path_label.setObjectName("meta")
        self.path_label.setFont(theme.mono_font(12))
        self.path_label.setWordWrap(True)
        pick = QPushButton(theme.icon("folder-open"), tr("Wählen…"))
        pick.clicked.connect(self._pick)
        self.clear_btn = QPushButton(theme.icon("x"), "")
        self.clear_btn.setToolTip(clear_tip)
        self.clear_btn.clicked.connect(lambda: self.set_folder(None))
        self.title = title
        lab = QLabel(title)
        lab.setMinimumWidth(150)
        self.addWidget(lab)
        self.addWidget(self.path_label, 1)
        self.addWidget(pick)
        self.addWidget(self.clear_btn)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self.path_label.window(), self.title, str(self.folder or Path.home()))
        if d:
            self.set_folder(Path(d))

    def set_folder(self, folder: Path | None, notify: bool = True):
        self.folder = folder
        self.path_label.setText(str(folder) if folder else self.empty)
        self.clear_btn.setEnabled(folder is not None)
        if notify:
            self.on_change()


class ScriptFolderDialog(QDialog):
    """Changes apply at once (scriptdir.save_settings)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Speicherorte"))
        self.setMinimumWidth(560)
        sd = scriptdir.settings()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        lay.setSizeConstraint(QLayout.SetMinimumSize)

        lay.addWidget(theme.section_title(tr("Scripts")))
        cap = QLabel(tr("Ohne Script-Ordner liegt jedes Script neben seinem Video, so finden es die meisten "
                        "Player. Mit Script-Ordner landen alle Scripts zusätzlich (oder nur) dort. Bibliothek, "
                        "Player, Stapel und andere Player suchen erst neben dem Video, dann im Script-Ordner "
                        "und seinen Unterordnern."))
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        lay.addWidget(cap)
        self.scripts = _FolderRow(tr("Script-Ordner"), tr("keiner – Scripts liegen neben dem Video"),
                                  tr("Kein Script-Ordner: Scripts nur neben dem Video"), self._store)
        lay.addLayout(self.scripts)
        self.next_to = QCheckBox(tr("Zusätzlich neben dem Video speichern"))
        self.next_to.setToolTip(tr("Die meisten Player (und andere Programme) suchen das Script neben dem Video"))
        self.next_to.setChecked(sd.next_to_video)
        self.next_to.toggled.connect(lambda _on: self._store())
        lay.addWidget(self.next_to)

        lay.addSpacing(8)
        lay.addWidget(theme.section_title(tr("Video-Daten")))
        cap2 = QLabel(tr("Tracking-Kästchen, Abschnitte, Kalibrierung, Schnitte und Lesezeichen stehen in "
                         "<Video>.vtf.json. Mit einem Ordner für Video-Daten liegt diese Datei dort statt "
                         "neben dem Video, so bleibt der Video-Ordner aufgeräumt. Vorhandene Dateien neben "
                         "dem Video werden beim nächsten Speichern in den Ordner verschoben."))
        cap2.setObjectName("caption")
        cap2.setWordWrap(True)
        lay.addWidget(cap2)
        self.data = _FolderRow(tr("Ordner für Video-Daten"), tr("keiner – neben dem Video"),
                               tr("Kein Ordner: Video-Daten neben dem Video"), self._store)
        lay.addLayout(self.data)

        foot = QHBoxLayout()
        foot.addStretch(1)
        close = QPushButton(tr("Schließen"))
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        lay.addLayout(foot)
        self.scripts.set_folder(sd.folder, notify=False)
        self.data.set_folder(sd.data_folder, notify=False)
        self._refresh()

    @property
    def folder(self) -> Path | None:
        return self.scripts.folder

    def _set_folder(self, folder: Path | None):
        self.scripts.set_folder(folder)

    def _store(self):
        try:
            scriptdir.save_settings(self.scripts.folder, self.next_to.isChecked(), self.data.folder)
        except OSError:
            pass
        self._refresh()

    def _refresh(self):
        self.next_to.setEnabled(self.scripts.folder is not None)


def describe() -> str:
    """One line for the library and the status bar: where Ctrl+S saves now."""
    sd = scriptdir.settings()
    if not sd.active:
        return tr("Scripts: neben dem Video")
    if sd.next_to_video:
        return tr("Scripts: neben dem Video und in {0}", sd.folder)
    return tr("Scripts: in {0}", sd.folder)
