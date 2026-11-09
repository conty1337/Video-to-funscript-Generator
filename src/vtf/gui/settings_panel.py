"""Settings form for analysis and script generation."""

from __future__ import annotations

from dataclasses import asdict, fields

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..analysis import AnalysisSettings
from ..generator import GeneratorSettings
from . import theme
from .tracking_ui import TrackingBox

AXES = [("Vertikal", "vertical"), ("Horizontal", "horizontal"), ("Automatisch", "auto")]
HWACCEL = [("Automatisch", "auto"), ("VAAPI, AMD-GPU", "vaapi"), ("Software", "none")]


def _slider(lo, hi, val, suffix=""):
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    lbl = QLabel(f"{val}{suffix}")
    lbl.setObjectName("value")
    lbl.setFont(theme.mono_font(12))
    lbl.setFixedWidth(64)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s.valueChanged.connect(lambda v: lbl.setText(f"{v}{suffix}"))
    lay.addWidget(s, 1)
    lay.addWidget(lbl)
    return box, s


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setContentsMargins(0, 0, 0, 0)
    f.setHorizontalSpacing(12)
    f.setVerticalSpacing(8)
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return f


def _row(form: QFormLayout, text: str, widget: QWidget) -> None:
    lbl = QLabel(text)
    lbl.setObjectName("formLabel")
    lbl.setMinimumWidth(104)
    form.addRow(lbl, widget)


def _section(lay: QVBoxLayout, title: str, caption: str | None = None) -> None:
    t = QLabel(title)
    t.setObjectName("section")
    lay.addWidget(t)
    if caption:
        c = QLabel(caption)
        c.setObjectName("caption")
        c.setWordWrap(True)
        lay.addWidget(c)
    lay.addSpacing(4)


METHODS = [("Bewegung im Bild", "flow"), ("Tracking", "track")]


def _fmt_ms(ms: float) -> str:
    s = ms / 1000
    return f"{int(s // 60)}:{s % 60:04.1f}"


class SettingsPanel(QWidget):
    generatorChanged = Signal()   # cheap: regenerate script
    analysisChanged = Signal()    # requires a new analysis
    methodChanged = Signal(str)   # "flow" or "track"

    def __init__(self, parent=None):
        super().__init__(parent)
        # two pages, placed by the main window: analysis on the left (method,
        # tracking, options), the script settings on the right
        self.script_page = QWidget()
        self.analysis_page = QWidget()
        for page in (self.script_page, self.analysis_page):
            page.setObjectName("sidebar")
        lay = QVBoxLayout(self.script_page)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(4)

        _section(lay, "Script", "Wirkt sofort auf die Kurve")
        f = _form()
        self.axis = QComboBox()
        for label, val in AXES:
            self.axis.addItem(label, val)
        _row(f, "Richtung", self.axis)
        box, self.sensitivity = _slider(0, 100, 60, " %")
        _row(f, "Empfindlichkeit", box)
        box, self.smoothing = _slider(0, 500, 100, " ms")
        _row(f, "Glättung", box)
        box, self.min_interval = _slider(0, 1000, 120, " ms")
        _row(f, "Mindestabstand", box)
        box, self.drift = _slider(500, 10000, 2500, " ms")
        self.drift.setToolTip("Entfernt langsames Wegdriften der Position")
        _row(f, "Drift-Filter", box)
        box, self.norm = _slider(0, 20000, 5000, " ms")
        self.norm.setToolTip("Fenster für die lokale Amplituden-Normalisierung, 0 = ganzes Video")
        _row(f, "Normalisierung", box)
        box, self.max_speed = _slider(0, 1000, 0, " /s")
        lbl = box.findChild(QLabel)

        def fmt(v: int) -> str:
            return "aus" if v == 0 else f"{v} /s"

        lbl.setText(fmt(0))
        self.max_speed.valueChanged.connect(lambda v: lbl.setText(fmt(v)))
        self.max_speed.setSingleStep(10)
        self.max_speed.setPageStep(50)
        self.max_speed.setToolTip("Höchstes Tempo in Positionen pro Sekunde: schnellere Hübe werden beim "
                                  "Erzeugen gekürzt. F21S: voller Hub frühestens in ca. 200 ms = 500 /s. "
                                  "0 = aus")
        _row(f, "Tempo-Grenze", box)
        rng = QWidget()
        rl = QHBoxLayout(rng)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        self.pos_min = QSpinBox()
        self.pos_max = QSpinBox()
        dash = QLabel("bis")
        dash.setObjectName("caption")
        for sp, v in ((self.pos_min, 0), (self.pos_max, 100)):
            sp.setRange(0, 100)
            sp.setValue(v)
            sp.setButtonSymbols(QSpinBox.NoButtons)
            sp.setAlignment(Qt.AlignRight)
            sp.setFont(theme.mono_font(12))
        rl.addWidget(self.pos_min, 1)
        rl.addWidget(dash)
        rl.addWidget(self.pos_max, 1)
        _row(f, "Hubbereich", rng)
        lay.addLayout(f)
        lay.addSpacing(4)
        self.invert = QCheckBox("Richtung umkehren")
        self.full_stroke = QCheckBox("Immer voller Hub")
        lay.addWidget(self.invert)
        lay.addWidget(self.full_stroke)
        lay.addStretch(1)

        lay = QVBoxLayout(self.analysis_page)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(4)
        _section(lay, "Analyse", "Änderungen wirken nach erneuter Analyse")
        f = _form()
        self.method = QComboBox()
        for label, val in METHODS:
            self.method.addItem(label, val)
        self.method.setToolTip("Bewegung im Bild: erkennt die stärkste Bewegung automatisch.\n"
                               "Tracking: verfolgt Bereiche, die du im Video markierst (z.B. Körperteile).")
        self.method.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.method.setMinimumContentsLength(8)
        _row(f, "Methode", self.method)
        lay.addLayout(f)
        self.tracking = TrackingBox()
        lay.addWidget(self.tracking)
        self.roi_label = QLabel()
        self.roi_label.setObjectName("caption")
        self.roi_label.setWordWrap(True)
        lay.addSpacing(8)
        lay.addWidget(self.roi_label)
        lay.addSpacing(16)
        # rarely changed options: folded away
        self.options_btn = QToolButton()
        self.options_btn.setObjectName("disclosure")
        self.options_btn.setCheckable(True)
        self.options_btn.setText("Optionen")
        self.options_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.options_btn.setIcon(theme.icon("chevron-right", theme.TEXT_2, 16))
        lay.addWidget(self.options_btn)
        self.options = QWidget()
        ol = QVBoxLayout(self.options)
        ol.setContentsMargins(0, 8, 0, 0)
        ol.setSpacing(4)
        lay.addWidget(self.options)
        f = _form()
        self.width = QComboBox()
        for w in (160, 240, 320, 480, 640):
            self.width.addItem(f"{w} px", w)
        self.width.setCurrentIndex(2)
        _row(f, "Auflösung", self.width)
        self.step = QSpinBox()
        self.step.setRange(1, 6)
        self.step.setButtonSymbols(QSpinBox.NoButtons)
        self.step.setFont(theme.mono_font(12))
        self.step.setAlignment(Qt.AlignRight)
        self.step.setToolTip("Nur jeden n-ten Frame analysieren, schneller und ungenauer")
        _row(f, "Frame-Schritt", self.step)
        self.hwaccel = QComboBox()
        for label, val in HWACCEL:
            self.hwaccel.addItem(label, val)
        _row(f, "Dekodierung", self.hwaccel)
        ol.addLayout(f)
        ol.addSpacing(4)
        self.cam = QCheckBox("Kamerabewegung ausgleichen")
        self.cam.setChecked(True)
        self.opencl = QCheckBox("GPU über OpenCL")
        self.turn = QCheckBox("Kästchen drehen und skalieren mit")
        self.turn.setChecked(True)
        self.turn.setToolTip("Tracking: die Kästchen folgen auch Drehung und Größe (Körper). Aus: sie "
                             "verschieben sich nur, besser für Maschinen und starre Teile")
        ol.addWidget(self.cam)
        ol.addWidget(self.turn)
        ol.addWidget(self.opencl)
        self.options.setVisible(False)
        self.options_btn.toggled.connect(self._options_toggled)
        lay.addStretch(1)
        self._roi = None
        self._update_roi_label()
        self.tracking.analysisChanged.connect(self.analysisChanged)
        self.tracking.generatorChanged.connect(self.generatorChanged)

        for w in (self.sensitivity, self.smoothing, self.min_interval, self.drift, self.norm, self.max_speed):
            w.valueChanged.connect(self.generatorChanged)
        for w in (self.pos_min, self.pos_max):
            w.valueChanged.connect(self.generatorChanged)
        self.axis.currentIndexChanged.connect(self.generatorChanged)
        self.invert.toggled.connect(self.generatorChanged)
        self.full_stroke.toggled.connect(self.generatorChanged)
        self.width.currentIndexChanged.connect(self.analysisChanged)
        self.step.valueChanged.connect(self.analysisChanged)
        self.cam.toggled.connect(self.analysisChanged)
        self.turn.toggled.connect(self.analysisChanged)
        self.method.currentIndexChanged.connect(self._method_changed)

    def _options_toggled(self, on: bool) -> None:
        self.options.setVisible(on)
        self.options_btn.setIcon(theme.icon("chevron-down" if on else "chevron-right", theme.TEXT_2, 16))

    @property
    def mode(self) -> str:
        return self.method.currentData()

    @property
    def sections(self) -> list:
        """Tracking sections (only meaningful in mode "track")."""
        return list(self.tracking.sections) if self.mode == "track" else []

    def _method_changed(self):
        self._update_roi_label()
        self.methodChanged.emit(self.mode)
        self.analysisChanged.emit()

    # ------------------------------------------------------------ roi
    def set_roi(self, roi):
        self._roi = tuple(round(v, 4) for v in roi) if roi else None
        self._update_roi_label()
        self.analysisChanged.emit()

    def _update_roi_label(self):
        track = self.mode == "track"
        self.roi_label.setVisible(not track)
        self.tracking.setVisible(track)
        if track:
            return
        if not self._roi:
            self.roi_label.setText("Analysebereich: ganzes Bild. Rechteck im Video aufziehen, um ihn einzugrenzen.")
        else:
            x, y, w, h = self._roi
            self.roi_label.setText(f"Analysebereich: {w:.0%} × {h:.0%} ab {x:.0%}, {y:.0%}. "
                                   "Rechtsklick im Video setzt ihn zurück.")

    # ------------------------------------------------------------ settings
    def generator_settings(self) -> GeneratorSettings:
        return GeneratorSettings(
            axis=self.axis.currentData(), invert=self.invert.isChecked(),
            sensitivity=self.sensitivity.value(), smoothing_ms=self.smoothing.value(),
            drift_window_ms=self.drift.value(), normalize_window_ms=self.norm.value(),
            min_interval_ms=self.min_interval.value(), pos_min=self.pos_min.value(),
            pos_max=self.pos_max.value(), full_stroke=self.full_stroke.isChecked(),
            max_speed=self.max_speed.value())

    def analysis_settings(self) -> AnalysisSettings:
        return AnalysisSettings(
            width=self.width.currentData(), roi=self._roi, frame_step=self.step.value(),
            compensate_camera=self.cam.isChecked(), use_opencl=self.opencl.isChecked(),
            hwaccel=self.hwaccel.currentData(), mode=self.mode,
            track_sections=tuple(self.sections), track_turn=self.turn.isChecked())

    def to_dict(self) -> dict:
        a = asdict(self.analysis_settings())
        for k in ("roi", "track_boxes", "track_start_ms", "track_sections"):     # belong to one video
            a.pop(k)
        return {"generator": asdict(self.generator_settings()), "analysis": a}

    def load_dict(self, d: dict) -> None:
        gen = {f.name for f in fields(GeneratorSettings)}
        g = {k: v for k, v in (d.get("generator") or {}).items() if k in gen}
        a = d.get("analysis") or {}
        widgets = [self.axis, self.sensitivity, self.smoothing, self.min_interval, self.drift, self.norm,
                   self.pos_min, self.pos_max, self.invert, self.full_stroke, self.width, self.step,
                   self.cam, self.opencl, self.hwaccel, self.method, self.max_speed, self.turn]
        for w in widgets:
            w.blockSignals(True)
        try:
            _set_combo(self.axis, g.get("axis"))
            for key, w in (("sensitivity", self.sensitivity), ("smoothing_ms", self.smoothing),
                           ("min_interval_ms", self.min_interval), ("drift_window_ms", self.drift),
                           ("normalize_window_ms", self.norm), ("pos_min", self.pos_min),
                           ("pos_max", self.pos_max), ("max_speed", self.max_speed)):
                if key in g:
                    w.setValue(int(g[key]))
            self.invert.setChecked(bool(g.get("invert", False)))
            self.full_stroke.setChecked(bool(g.get("full_stroke", False)))
            _set_combo(self.width, a.get("width"))
            self.step.setValue(int(a.get("frame_step", 1)))
            self.cam.setChecked(bool(a.get("compensate_camera", True)))
            self.turn.setChecked(bool(a.get("track_turn", True)))
            self.opencl.setChecked(bool(a.get("use_opencl", False)))
            _set_combo(self.hwaccel, a.get("hwaccel"))
            _set_combo(self.method, a.get("mode"))
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._update_roi_label()
        self.methodChanged.emit(self.mode)
        # slider labels are updated via valueChanged; refresh them once
        for s in (self.sensitivity, self.smoothing, self.min_interval, self.drift, self.norm, self.max_speed):
            s.valueChanged.emit(s.value())


def _set_combo(combo: QComboBox, value) -> None:
    if value is None:
        return
    i = combo.findData(value)
    if i >= 0:
        combo.setCurrentIndex(i)
