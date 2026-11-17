"""Einstellungen vorschlagen: tries script settings on the finished analysis in the background,
shows the best three next to the current ones, one is taken over with a click."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import tr
from .workers import SuggestWorker


def describe(r) -> str:
    score, g, issues, spm = r
    return tr("Empfindlichkeit {0} %, Glättung {1} ms, Mindestabstand {2} ms\n{3} Stellen zum Prüfen · {4} Hübe/min · Wertung {5}",
              g.sensitivity, g.smoothing_ms, g.min_interval_ms, issues, round(spm), round(score * 100))


class SuggestDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(tr("Einstellungen vorschlagen"))
        self.resize(520, 380)
        lay = QVBoxLayout(self)
        self.head = QLabel(tr("Probiere Einstellungen an der fertigen Analyse aus …"))
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        lay.addWidget(self.bar)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._take())
        lay.addWidget(self.list, 1)
        note = QLabel(tr("Bewertet wird, wie gut die Hübe der Bewegung folgen und wie wenig die Prüfung findet. "
                         "Eine richtige Kurve zum Vergleichen gibt es nicht: ein Vorschlag, kein Urteil. "
                         "Übernommen wirkt er wie die Regler."))
        note.setObjectName("caption")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        self.take_btn = QPushButton(tr("Übernehmen"))
        self.take_btn.setEnabled(False)
        self.take_btn.clicked.connect(self._take)
        close = QPushButton(tr("Schließen"))
        close.clicked.connect(self.reject)
        row.addWidget(self.take_btn)
        row.addWidget(close)
        lay.addLayout(row)
        p = win.panel
        self.worker = SuggestWorker(win.motion, p.generator_settings(), p.sections, win.cuts)
        self.worker.setParent(self)            # Qt owns it with the dialog (not Python at exit)
        self.worker.progress.connect(lambda f: self.bar.setValue(int(f * 100)))
        self.worker.done.connect(self._show)
        self.worker.failed.connect(lambda e: self.head.setText(tr("Fehler: {0}", e)))
        self.worker.start()

    def _show(self, res):
        best, current = res
        self.bar.hide()
        self.head.setText(tr("Jetzt: {0}", describe(current).replace("\n", " · ")))
        for r in best:
            item = QListWidgetItem(describe(r))
            item.setData(Qt.ItemDataRole.UserRole, r[1])
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
            self.take_btn.setEnabled(True)

    def _take(self):
        item = self.list.currentItem()
        if item is None:
            return
        g = item.data(Qt.ItemDataRole.UserRole)
        p = self.win.panel
        d = p.to_dict()
        d["generator"].update(sensitivity=g.sensitivity, smoothing_ms=g.smoothing_ms, min_interval_ms=g.min_interval_ms)
        p.load_dict(d)
        p.generatorChanged.emit()
        self.accept()

    def done(self, r):
        self.worker.stop()
        self.worker.wait(5000)
        super().done(r)
