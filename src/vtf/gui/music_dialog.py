"""Dialog: make the script of a range from the beats in the video's sound."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .rhythm_dialog import _secs


class MusicScriptDialog(QDialog):
    def __init__(self, start_ms: float, end_ms: float, duration_ms: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Script aus Musik"))
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetMinimumSize)    # see theme.DialogFitter
        info = QLabel(tr("Sucht die Schläge in der Tonspur und setzt auf jeden Schlag einen Hub nach unten. Gut für Videos, bei denen die Musik den Takt vorgibt."))
        info.setObjectName("caption")
        info.setWordWrap(True)
        info.setMinimumWidth(380)
        lay.addWidget(info)
        form = QFormLayout()
        dur = duration_ms / 1000
        self.start = _secs(start_ms / 1000, dur)
        self.end = _secs(end_ms / 1000, dur)
        rng = QWidget()
        rl = QHBoxLayout(rng)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.start)
        rl.addWidget(QLabel(tr("bis")))
        rl.addWidget(self.end)
        form.addRow(tr("Bereich"), rng)
        self.every = QComboBox()
        for n, label in ((1, tr("Jeder Schlag")), (2, tr("Jeder 2. Schlag")), (4, tr("Jeder 4. Schlag"))):
            self.every.addItem(label, n)
        form.addRow(tr("Hub auf"), self.every)
        self.sens = QSlider()
        self.sens.setOrientation(self.sens.orientation().Horizontal)
        self.sens.setRange(0, 100)
        self.sens.setValue(50)
        form.addRow(tr("Empfindlich"), self.sens)
        depth = QWidget()
        dl = QHBoxLayout(depth)
        dl.setContentsMargins(0, 0, 0, 0)
        self.lo, self.hi = QSpinBox(), QSpinBox()
        for sp, v in ((self.lo, 0), (self.hi, 100)):
            sp.setRange(0, 100)
            sp.setValue(v)
        dl.addWidget(self.lo)
        dl.addWidget(QLabel(tr("bis")))
        dl.addWidget(self.hi)
        form.addRow(tr("Tiefe"), depth)
        self.offset = QSpinBox()
        self.offset.setRange(-500, 500)
        self.offset.setSingleStep(10)
        self.offset.setSuffix(" ms")
        self.offset.setToolTip(tr("Negativ = Hübe etwas vor dem Schlag"))
        form.addRow(tr("Versatz"), self.offset)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(tr("Erzeugen"))
        bb.button(QDialogButtonBox.Cancel).setText(tr("Abbrechen"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {"start_ms": self.start.value() * 1000, "end_ms": self.end.value() * 1000,
                "every": self.every.currentData(), "sensitivity": self.sens.value() / 100,
                "lo": self.lo.value(), "hi": self.hi.value(), "offset_ms": self.offset.value()}
