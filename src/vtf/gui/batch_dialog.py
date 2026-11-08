"""Batch processing dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)

from ..batch import find_videos, is_analyzed
from .workers import BatchWorker


class BatchDialog(QDialog):
    def __init__(self, settings_panel, folder: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stapelverarbeitung")
        self.resize(640, 460)
        self.panel = settings_panel
        self.worker: BatchWorker | None = None

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.folder = QLineEdit(folder)
        self.folder.setPlaceholderText("Videoordner…")
        self.folder.textChanged.connect(self._count)
        browse = QPushButton("Durchsuchen…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        self.recursive = QCheckBox("Unterordner einbeziehen")
        self.recursive.setChecked(True)
        self.recursive.toggled.connect(self._count)
        self.overwrite = QCheckBox("Bereits analysierte Videos neu erstellen (sonst überspringen)")
        lay.addWidget(self.recursive)
        lay.addWidget(self.overwrite)
        self.info = QLabel()
        lay.addWidget(self.info)
        lay.addWidget(QLabel("Es werden die aktuellen Einstellungen (inkl. ROI) verwendet."))
        self.file_label = QLabel()
        self.total = QProgressBar()
        self.current = QProgressBar()
        lay.addWidget(self.file_label)
        lay.addWidget(QLabel("Gesamt:"))
        lay.addWidget(self.total)
        lay.addWidget(QLabel("Aktuelles Video:"))
        lay.addWidget(self.current)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton("Abbrechen")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        close = QPushButton("Schließen")
        close.clicked.connect(self.close)
        for b in (self.start_btn, self.stop_btn, close):
            row.addWidget(b)
        lay.addLayout(row)
        self._count()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Videoordner wählen", self.folder.text())
        if d:
            self.folder.setText(d)

    def _count(self):
        f = self.folder.text().strip()
        try:
            vids = find_videos(f, self.recursive.isChecked()) if f else []
        except OSError:
            vids = []
        done = sum(is_analyzed(v) for v in vids)
        self.info.setText(f"{len(vids)} Videos, davon {done} bereits analysiert.")

    def _start(self):
        f = self.folder.text().strip()
        if not f:
            return
        self.log.clear()
        self.worker = BatchWorker(f, self.panel.analysis_settings(), self.panel.generator_settings(),
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
        self.file_label.setText(f"[{i + 1}/{n}] {path}")

    def _stop(self):
        if self.worker:
            self.worker.stop()

    def _done(self, res):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if res is not None:
            self.total.setValue(self.total.maximum())
            self.log.appendPlainText(
                f"\nFertig: {len(res.done)}, übersprungen: {len(res.skipped)}, Fehler: {len(res.failed)}"
                + (" (abgebrochen)" if res.cancelled else ""))
        self.worker = None
        self._count()

    def closeEvent(self, ev):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(10000)
        super().closeEvent(ev)
