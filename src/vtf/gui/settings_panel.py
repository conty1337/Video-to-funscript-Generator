"""Settings form for analysis and script generation."""

from __future__ import annotations

from dataclasses import asdict, fields

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..analysis import AnalysisSettings
from ..generator import GeneratorSettings

AXES = [("Vertikal", "vertical"), ("Horizontal", "horizontal"), ("Automatisch", "auto")]
HWACCEL = [("Automatisch", "auto"), ("VAAPI (AMD)", "vaapi"), ("Aus (Software)", "none")]


def _slider(lo, hi, val, suffix=""):
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    lbl = QLabel()
    lbl.setMinimumWidth(56)
    lbl.setText(f"{val}{suffix}")
    s.valueChanged.connect(lambda v: lbl.setText(f"{v}{suffix}"))
    lay.addWidget(s, 1)
    lay.addWidget(lbl)
    return box, s


class SettingsPanel(QWidget):
    generatorChanged = Signal()   # cheap: regenerate script
    analysisChanged = Signal()    # requires a new analysis

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)

        g = QGroupBox("Script")
        f = QFormLayout(g)
        self.axis = QComboBox()
        for label, val in AXES:
            self.axis.addItem(label, val)
        f.addRow("Bewegungsrichtung", self.axis)
        box, self.sensitivity = _slider(0, 100, 60, " %")
        f.addRow("Empfindlichkeit", box)
        box, self.smoothing = _slider(0, 500, 100, " ms")
        f.addRow("Glättung", box)
        box, self.min_interval = _slider(0, 1000, 120, " ms")
        f.addRow("Mindestabstand", box)
        box, self.drift = _slider(500, 10000, 2500, " ms")
        f.addRow("Drift-Filter", box)
        box, self.norm = _slider(0, 20000, 5000, " ms")
        self.norm.setToolTip("Fenster für die lokale Amplituden-Normalisierung (0 = global)")
        f.addRow("Normalisierung", box)
        rng = QWidget()
        rl = QHBoxLayout(rng)
        rl.setContentsMargins(0, 0, 0, 0)
        self.pos_min = QSpinBox()
        self.pos_max = QSpinBox()
        for sp, v in ((self.pos_min, 0), (self.pos_max, 100)):
            sp.setRange(0, 100)
            sp.setValue(v)
            rl.addWidget(sp)
        f.addRow("Bewegungsumfang", rng)
        self.invert = QCheckBox("Umkehren")
        self.full_stroke = QCheckBox("Immer voller Hub")
        f.addRow(self.invert)
        f.addRow(self.full_stroke)
        lay.addWidget(g)

        g = QGroupBox("Analyse (erfordert neue Analyse)")
        f = QFormLayout(g)
        self.width = QComboBox()
        for w in (160, 240, 320, 480, 640):
            self.width.addItem(f"{w} px", w)
        self.width.setCurrentIndex(2)
        f.addRow("Auflösung", self.width)
        self.step = QSpinBox()
        self.step.setRange(1, 6)
        self.step.setToolTip("Nur jeden n-ten Frame analysieren (schneller, ungenauer)")
        f.addRow("Frame-Schritt", self.step)
        self.cam = QCheckBox("Kamerabewegung kompensieren")
        self.cam.setChecked(True)
        f.addRow(self.cam)
        self.opencl = QCheckBox("GPU (OpenCL)")
        f.addRow(self.opencl)
        self.hwaccel = QComboBox()
        for label, val in HWACCEL:
            self.hwaccel.addItem(label, val)
        f.addRow("Hardware-Dekodierung", self.hwaccel)
        self.roi_label = QLabel("ROI: ganzes Bild")
        self.roi_label.setWordWrap(True)
        f.addRow(self.roi_label)
        lay.addWidget(g)
        lay.addStretch(1)

        for w in (self.sensitivity, self.smoothing, self.min_interval, self.drift, self.norm):
            w.valueChanged.connect(self.generatorChanged)
        for w in (self.pos_min, self.pos_max):
            w.valueChanged.connect(self.generatorChanged)
        self.axis.currentIndexChanged.connect(self.generatorChanged)
        self.invert.toggled.connect(self.generatorChanged)
        self.full_stroke.toggled.connect(self.generatorChanged)
        self.width.currentIndexChanged.connect(self.analysisChanged)
        self.step.valueChanged.connect(self.analysisChanged)
        self.cam.toggled.connect(self.analysisChanged)
        self._roi = None

    # ------------------------------------------------------------ roi
    def set_roi(self, roi):
        self._roi = tuple(round(v, 4) for v in roi) if roi else None
        self.roi_label.setText("ROI: ganzes Bild" if not roi else
                               "ROI: x={:.0%} y={:.0%} b={:.0%} h={:.0%}".format(*roi))
        self.analysisChanged.emit()

    # ------------------------------------------------------------ settings
    def generator_settings(self) -> GeneratorSettings:
        return GeneratorSettings(
            axis=self.axis.currentData(), invert=self.invert.isChecked(),
            sensitivity=self.sensitivity.value(), smoothing_ms=self.smoothing.value(),
            drift_window_ms=self.drift.value(), normalize_window_ms=self.norm.value(),
            min_interval_ms=self.min_interval.value(), pos_min=self.pos_min.value(),
            pos_max=self.pos_max.value(), full_stroke=self.full_stroke.isChecked())

    def analysis_settings(self) -> AnalysisSettings:
        return AnalysisSettings(
            width=self.width.currentData(), roi=self._roi, frame_step=self.step.value(),
            compensate_camera=self.cam.isChecked(), use_opencl=self.opencl.isChecked(),
            hwaccel=self.hwaccel.currentData())

    def to_dict(self) -> dict:
        a = asdict(self.analysis_settings())
        a.pop("roi")
        return {"generator": asdict(self.generator_settings()), "analysis": a}

    def load_dict(self, d: dict) -> None:
        gen = {f.name for f in fields(GeneratorSettings)}
        g = {k: v for k, v in (d.get("generator") or {}).items() if k in gen}
        a = d.get("analysis") or {}
        widgets = [self.axis, self.sensitivity, self.smoothing, self.min_interval, self.drift, self.norm,
                   self.pos_min, self.pos_max, self.invert, self.full_stroke, self.width, self.step,
                   self.cam, self.opencl, self.hwaccel]
        for w in widgets:
            w.blockSignals(True)
        try:
            _set_combo(self.axis, g.get("axis"))
            for key, w in (("sensitivity", self.sensitivity), ("smoothing_ms", self.smoothing),
                           ("min_interval_ms", self.min_interval), ("drift_window_ms", self.drift),
                           ("normalize_window_ms", self.norm), ("pos_min", self.pos_min),
                           ("pos_max", self.pos_max)):
                if key in g:
                    w.setValue(int(g[key]))
            self.invert.setChecked(bool(g.get("invert", False)))
            self.full_stroke.setChecked(bool(g.get("full_stroke", False)))
            _set_combo(self.width, a.get("width"))
            self.step.setValue(int(a.get("frame_step", 1)))
            self.cam.setChecked(bool(a.get("compensate_camera", True)))
            self.opencl.setChecked(bool(a.get("use_opencl", False)))
            _set_combo(self.hwaccel, a.get("hwaccel"))
        finally:
            for w in widgets:
                w.blockSignals(False)
        # slider labels are updated via valueChanged; refresh them once
        for s in (self.sensitivity, self.smoothing, self.min_interval, self.drift, self.norm):
            s.valueChanged.emit(s.value())


def _set_combo(combo: QComboBox, value) -> None:
    if value is None:
        return
    i = combo.findData(value)
    if i >= 0:
        combo.setCurrentIndex(i)
