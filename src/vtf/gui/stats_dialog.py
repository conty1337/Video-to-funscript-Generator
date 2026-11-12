"""Session statistics: playing time, strokes, favourite videos and patterns (only on
this computer)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .. import stats as S
from . import theme

PERIODS = (("Heute", 1), ("7 Tage", 7), ("30 Tage", 30), ("Gesamt", None))


class StatsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Statistik")
        self.setMinimumWidth(560)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 24, 24, 24)
        self.lay.setSpacing(16)
        self.body = QVBoxLayout()
        self.body.setSpacing(16)
        self.lay.addLayout(self.body)
        foot = QHBoxLayout()
        note = QLabel("Nur auf diesem Rechner gespeichert, nichts wird verschickt")
        note.setObjectName("caption")
        reset = QPushButton("Zurücksetzen")
        reset.clicked.connect(self._reset)
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        foot.addWidget(note, 1)
        foot.addWidget(reset)
        foot.addWidget(close)
        self.lay.addLayout(foot)
        self.refresh()

    def _clear(self):
        while self.body.count():
            it = self.body.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    c = lay.takeAt(0).widget()
                    if c is not None:
                        c.deleteLater()

    def refresh(self) -> None:
        self._clear()
        data = S.load()
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        for c, (label, _d) in enumerate(PERIODS, start=1):
            h = QLabel(label)
            h.setObjectName("formLabel")
            h.setAlignment(Qt.AlignRight)
            grid.addWidget(h, 0, c)
        sums = [S.summary(data, d) for _l, d in PERIODS]
        rows = (("Spielzeit", lambda s: S.fmt_duration(s["seconds"])),
                ("Hübe", lambda s: f"{s['strokes']:,}".replace(",", ".")),
                ("Tage", lambda s: str(s["days"])))
        for r, (label, fn) in enumerate(rows, start=1):
            lb = QLabel(label)
            lb.setObjectName("formLabel")
            grid.addWidget(lb, r, 0)
            for c, s in enumerate(sums, start=1):
                v = QLabel(fn(s))
                v.setFont(theme.mono_font(13))
                v.setAlignment(Qt.AlignRight)
                grid.addWidget(v, r, c)
        self.body.addLayout(grid)
        total = sums[-1]
        for title, items in (("Lieblingsvideos", total["videos"]), ("Lieblingsmuster", total["patterns"])):
            head = QLabel(title)
            head.setObjectName("section")
            self.body.addWidget(head)
            if not items:
                empty = QLabel("Noch nichts gespielt")
                empty.setObjectName("caption")
                self.body.addWidget(empty)
                continue
            top = QGridLayout()
            top.setHorizontalSpacing(16)
            top.setVerticalSpacing(4)
            for r, (name, sec) in enumerate(items[:5]):
                n = QLabel(name)
                t = QLabel(S.fmt_duration(sec))
                t.setFont(theme.mono_font(12))
                t.setObjectName("meta")
                t.setAlignment(Qt.AlignRight)
                top.addWidget(n, r, 0)
                top.addWidget(t, r, 1)
            top.setColumnStretch(0, 1)
            self.body.addLayout(top)

    def _reset(self):
        if QMessageBox.question(self, "Statistik zurücksetzen",
                                "Alle gespeicherten Zeiten und Zählungen löschen?") == QMessageBox.Yes:
            S.reset()
            self.refresh()
