"""Batch processing dialog."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ..batch import find_videos, is_analyzed
from ..i18n import tr
from ..prep import is_prepared
from . import theme
from .workers import BatchWorker


class BatchDialog(QDialog):
    def __init__(self, settings_panel, folder: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Stapelverarbeitung"))
        self.resize(640, 560)
        self.panel = settings_panel
        self.worker: BatchWorker | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(8)
        title = QLabel(tr("Ordner stapelweise verarbeiten"))
        title.setObjectName("section")
        lay.addWidget(title)
        cap = QLabel(tr("Verwendet die aktuellen Einstellungen inklusive Analysebereich. Videos, die im Editor mit Tracking vorbereitet wurden (<Video>.vtf.json), nutzen ihre markierten Bereiche, Abschnitte und Kalibrierung."))
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        lay.addWidget(cap)
        lay.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.folder = QLineEdit(folder)
        self.folder.setPlaceholderText(tr("Videoordner"))
        self.folder.textChanged.connect(self._count)
        browse = QPushButton(theme.icon("folder-open"), tr("Wählen"))
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        self.info = QLabel()
        self.info.setObjectName("meta")
        self.info.setFont(theme.mono_font(12))
        lay.addWidget(self.info)
        lay.addSpacing(4)
        self.recursive = QCheckBox(tr("Unterordner einbeziehen"))
        self.recursive.setChecked(True)
        self.recursive.toggled.connect(self._count)
        self.overwrite = QCheckBox(tr("Bereits analysierte Videos neu erstellen"))
        lay.addWidget(self.recursive)
        lay.addWidget(self.overwrite)
        lay.addSpacing(16)
        self.file_label = QLabel(tr("Gesamt"))
        self.file_label.setObjectName("formLabel")
        self.total = QProgressBar()
        self.total.setTextVisible(False)
        self.current = QProgressBar()
        self.current.setTextVisible(False)
        cur_label = QLabel(tr("Aktuelles Video"))
        cur_label.setObjectName("formLabel")
        lay.addWidget(self.file_label)
        lay.addWidget(self.total)
        lay.addSpacing(4)
        lay.addWidget(cur_label)
        lay.addWidget(self.current)
        lay.addSpacing(12)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono_font(12))
        lay.addWidget(self.log, 1)
        lay.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        close = QPushButton(tr("Schließen"))
        close.clicked.connect(self.close)
        self.stop_btn = QPushButton(tr("Abbrechen"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        self.start_btn = QPushButton(tr("Starten"))
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        for b in (close, self.stop_btn, self.start_btn):
            row.addWidget(b)
        lay.addLayout(row)
        self._count()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, tr("Videoordner wählen"), self.folder.text())
        if d:
            self.folder.setText(d)

    def _count(self):
        f = self.folder.text().strip()
        try:
            vids = find_videos(f, self.recursive.isChecked()) if f else []
        except OSError:
            vids = []
        done = sum(is_analyzed(v) for v in vids)
        prep = sum(is_prepared(v) for v in vids)
        self.info.setText(tr("{0} Videos · {1} bereits analysiert · {2} offen · {3} mit Tracking vorbereitet", len(vids), done, len(vids) - done, prep))

    def _start(self):
        f = self.folder.text().strip()
        if not f:
            return
        self.log.clear()
        # tracking areas belong to one video: prepared videos bring their own (<video>.vtf.json),
        # all others use motion in the image
        a = dataclasses.replace(self.panel.analysis_settings(), mode="flow", track_boxes=(),
                                track_sections=())
        self.worker = BatchWorker(f, a, self.panel.generator_settings(),
                                  self.recursive.isChecked(), self.overwrite.isChecked())
        self.worker.fileStarted.connect(self._file)
        self.worker.progress.connect(lambda p: self.current.setValue(int(p * 100)))
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.done.connect(self._done)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker.start()

    def _file(self, i, n, path):
        self.total.setMaximum(n)
        self.total.setValue(i)
        self.current.setValue(0)
        self.file_label.setText(tr("Gesamt · {0} von {1} · {2}", i + 1, n, Path(path).name))

    def _stop(self):
        if self.worker:
            self.worker.stop()

    def _done(self, res):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if res is not None:
            self.total.setValue(self.total.maximum())
            self.log.appendPlainText(
                tr("\nFertig: {0}, übersprungen: {1}, Fehler: {2}", len(res.done), len(res.skipped), len(res.failed))
                + (tr(" (abgebrochen)") if res.cancelled else ""))
        self.worker = None
        self._count()

    def closeEvent(self, ev):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(10000)
        super().closeEvent(ev)
