"""Dialog: fill a time range with a regular rhythm (script template)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..editing import PATTERNS


def _secs(v: float, maximum: float) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setDecimals(1)
    sp.setRange(0.0, max(0.1, maximum))
    sp.setSingleStep(0.5)
    sp.setSuffix(" s")
    sp.setValue(v)
    return sp


class RhythmDialog(QDialog):
    def __init__(self, start_ms: float, end_ms: float, duration_ms: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rhythmus einfügen")
        lay = QVBoxLayout(self)
        info = QLabel("Ersetzt die Punkte im Bereich durch einen gleichmäßigen Rhythmus.")
        info.setObjectName("caption")
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QFormLayout()
        dur = duration_ms / 1000
        self.start = _secs(start_ms / 1000, dur)
        self.end = _secs(end_ms / 1000, dur)
        rng = QWidget()
        rl = QHBoxLayout(rng)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.start)
        rl.addWidget(QLabel("bis"))
        rl.addWidget(self.end)
        form.addRow("Bereich", rng)
        self.bpm = QSpinBox()
        self.bpm.setRange(10, 300)
        self.bpm.setValue(60)
        self.bpm.setSuffix(" Hübe/min")
        form.addRow("Tempo", self.bpm)
        self.pattern = QComboBox()
        for code, label in PATTERNS:
            self.pattern.addItem(label, code)
        form.addRow("Muster", self.pattern)
        depth = QWidget()
        dl = QHBoxLayout(depth)
        dl.setContentsMargins(0, 0, 0, 0)
        self.lo, self.hi = QSpinBox(), QSpinBox()
        for sp, v in ((self.lo, 10), (self.hi, 90)):
            sp.setRange(0, 100)
            sp.setValue(v)
        dl.addWidget(self.lo)
        dl.addWidget(QLabel("bis"))
        dl.addWidget(self.hi)
        form.addRow("Tiefe", depth)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Einfügen")
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {"start_ms": self.start.value() * 1000, "end_ms": self.end.value() * 1000,
                "bpm": self.bpm.value(), "lo": self.lo.value(), "hi": self.hi.value(),
                "pattern": self.pattern.currentData()}
