"""All findings of the script check in one window: jump there, tick off as fine."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import tr
from ..quality import describe
from ..quality import summary as issue_summary


class IssuesDialog(QDialog):
    """Stays open next to the editor (not modal): fix a place, the list updates."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(tr("Stellen zum Prüfen"))
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemActivated.connect(self._jump)
        self.list.itemDoubleClicked.connect(self._jump)
        lay.addWidget(self.list, 1)
        note = QLabel(tr("Doppelklick springt hin. „Passt so“ blendet eine Stelle aus, bis sich das "
                         "Script dort ändert – dann taucht sie zur erneuten Prüfung wieder auf."))
        note.setObjectName("caption")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        self.jump_btn = QPushButton(tr("Hinspringen"))
        self.jump_btn.clicked.connect(lambda: self._jump(self.list.currentItem()))
        self.ok_btn = QPushButton(tr("Passt so"))
        self.ok_btn.setToolTip(tr("Ausgewählte Stellen abhaken (Entf)"))
        self.ok_btn.setShortcut(Qt.Key.Key_Delete)
        self.ok_btn.clicked.connect(self._tick)
        self.back_btn = QPushButton(tr("Abgehakte zurückholen"))
        self.back_btn.clicked.connect(self.win.untick_issues)
        close = QPushButton(tr("Schließen"))
        close.clicked.connect(self.close)
        for b in (self.jump_btn, self.ok_btn, self.back_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

    def refresh(self):
        cur = self.list.currentRow()
        self.list.clear()
        for it in self.win.issues:
            item = QListWidgetItem(describe(it))
            item.setData(Qt.ItemDataRole.UserRole, it)
            self.list.addItem(item)
        n = len(self.win._checked)
        self.head.setText(issue_summary(self.win.issues) + (tr(" · {0} abgehakt", n) if n else ""))
        self.back_btn.setEnabled(bool(n))
        if self.list.count():
            self.list.setCurrentRow(min(max(cur, 0), self.list.count() - 1))

    def _jump(self, item):
        if item is not None:
            self.win._show_issue(item.data(Qt.ItemDataRole.UserRole))

    def _tick(self):
        items = [i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()]
        if items:
            self.win.tick_issues(items)
