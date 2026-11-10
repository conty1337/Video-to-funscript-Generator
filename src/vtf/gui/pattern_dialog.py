"""Pattern control: play stroke patterns on the device, build and save own patterns."""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QElapsedTimer, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import gamepad as G
from .. import music as M
from .. import patterns as P
from ..device.sync import ScriptSync
from . import theme
from .pattern_view import PatternView, ProgramView, _fmt_min, block_icon, pattern_icon

ROLE = Qt.UserRole + 1


def _hrow(*widgets, stretch=False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for x in widgets:
        if isinstance(x, str):
            lb = QLabel(x)
            lb.setObjectName("formLabel")
            lay.addWidget(lb)
        else:
            lay.addWidget(x)
    if stretch:
        lay.addStretch(1)
    return w


def _pct_spin(v: int) -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(0, 100)
    sp.setSuffix(" %")
    sp.setValue(v)
    return sp


def _speed_spin(v: float) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setDecimals(2)
    sp.setRange(P.MIN_SPEED, P.MAX_SPEED)
    sp.setSingleStep(0.1)
    sp.setSuffix(" ×")
    sp.setValue(v)
    return sp


class GamepadBridge(QObject):
    """Hands controller events from the reader thread to the GUI thread."""

    event = Signal(object)
    state = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.reader = G.GamepadReader(self.event.emit, self.state.emit)


class PatternDialog(QDialog):
    """Library of patterns (presets and own ones) with pictures, one depth and one
    tempo (kept, changeable on the fly by mouse, keys or controller), start/stop."""

    TICK_MS = 20
    APPLY_MS = 120          # live changes reach the device at most this often
    DEADZONE = 0.25
    STICK_DEPTH = 60.0      # % per second at full stick
    STICK_TEMPO = 60.0      # strokes/min per second at full stick
    STEP_DEPTH, STEP_TEMPO = 5, 5

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.qs = ctl.qs
        self.setWindowTitle("Muster-Steuerung")
        self.resize(1040, 640)
        self.user: list[P.Pattern] = P.load_user()
        self.sync = ScriptSync(ctl.device, ctl.sync.settings)
        self.runner = P.PatternRunner(self.sync)
        self.programs: list[P.Program] = P.load_programs()
        self.prunner = P.ProgramRunner(self.sync, self.find_pattern)
        self._offset_f = [0.0, 0.0]          # program: tempo, depth offsets (float for the sticks)
        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self)
        self.timer.setInterval(self.TICK_MS)
        self.timer.timeout.connect(self._tick)
        ctl.changed.connect(self._device_changed)
        ctl.register_source(self)
        self._resume = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- library
        left = QWidget()
        left.setObjectName("sidebar")
        left.setFixedWidth(320)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 16, 8, 16)
        ll.setSpacing(8)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        self.mode_btns = []
        self._mode = 0
        for i, text in enumerate(("Muster", "Abläufe", "Zufall", "Musik")):
            b = QPushButton(text)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=i: self.set_mode(k))
            mode_row.addWidget(b)
            self.mode_btns.append(b)
        mode_row.addStretch(1)
        ll.addLayout(mode_row)
        self.left_stack = QStackedWidget()
        ll.addWidget(self.left_stack, 1)
        page = QWidget()
        ll = QVBoxLayout(page)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        self.left_stack.addWidget(page)
        self.filter = QComboBox()
        self.filter.addItem("Alle", "")
        for cat in dict.fromkeys(p.category for p in P.BUILTIN):
            self.filter.addItem(cat, cat)
        self.filter.addItem("Eigene", "Eigene")
        self.filter.currentIndexChanged.connect(self._fill)
        ll.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setObjectName("playlist")
        self.list.setIconSize(QSize(96, 32))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda _i: self.toggle())
        ll.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.new_btn = QPushButton(theme.icon("plus"), "Neu")
        self.edit_btn = QPushButton(theme.icon("pencil"), "Bearbeiten")
        self.del_btn = QPushButton(theme.icon("trash"), "")
        self.del_btn.setToolTip("Eigenes Muster löschen")
        self.del_btn.setFixedWidth(40)
        for b in (self.new_btn, self.edit_btn, self.del_btn):
            row.addWidget(b)
        ll.addLayout(row)
        self.new_btn.clicked.connect(self.new_pattern)
        self.edit_btn.clicked.connect(self.edit_pattern)
        self.del_btn.clicked.connect(self.delete_pattern)

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        self.prog_list = QListWidget()
        self.prog_list.setObjectName("playlist")
        self.prog_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.prog_list.currentItemChanged.connect(self._prog_selected)
        self.prog_list.itemDoubleClicked.connect(lambda _i: self.toggle())
        pl.addWidget(self.prog_list, 1)
        row = QHBoxLayout()
        self.pnew_btn = QPushButton(theme.icon("plus"), "Neu")
        self.pedit_btn = QPushButton(theme.icon("pencil"), "Bearbeiten")
        self.pdel_btn = QPushButton(theme.icon("trash"), "")
        self.pdel_btn.setToolTip("Eigenen Ablauf löschen")
        self.pdel_btn.setFixedWidth(40)
        for b in (self.pnew_btn, self.pedit_btn, self.pdel_btn):
            row.addWidget(b)
        pl.addLayout(row)
        self.pnew_btn.clicked.connect(self.new_program)
        self.pedit_btn.clicked.connect(self.edit_program)
        self.pdel_btn.clicked.connect(self.delete_program)
        self.left_stack.addWidget(page)

        page = QWidget()
        rl = QVBoxLayout(page)
        rl.setContentsMargins(0, 8, 0, 0)
        rl.setSpacing(8)
        cap = QLabel("Wechselt Muster, Tempo und Tiefe von selbst. Jeder Abschnitt wird erst gewürfelt, wenn er "
                     "drankommt. Alles hier wirkt sofort, auch beim Abspielen.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        rl.addWidget(cap)
        rf = QFormLayout()
        rf.setHorizontalSpacing(8)
        rf.setVerticalSpacing(8)
        self.mood = QComboBox()
        for code, (label, *_rest) in P.MOODS.items():
            self.mood.addItem(label, code)
        rf.addRow(self._label("Stimmung"), self.mood)
        self.r_bpm0, self.r_bpm1 = QSpinBox(), QSpinBox()
        for sp in (self.r_bpm0, self.r_bpm1):
            sp.setRange(10, 240)
            sp.setSingleStep(5)
        rf.addRow(self._label("Tempo"), _hrow(self.r_bpm0, "bis", self.r_bpm1))
        self.r_d0, self.r_d1 = QSpinBox(), QSpinBox()
        for sp in (self.r_d0, self.r_d1):
            sp.setRange(10, 100)
            sp.setSingleStep(5)
            sp.setSuffix(" %")
        rf.addRow(self._label("Tiefe"), _hrow(self.r_d0, "bis", self.r_d1))
        self.r_m0, self.r_m1 = QDoubleSpinBox(), QDoubleSpinBox()
        for sp in (self.r_m0, self.r_m1):
            sp.setRange(0.25, 30)
            sp.setDecimals(2)
            sp.setSingleStep(0.5)
            sp.setSuffix(" min")
        rf.addRow(self._label("Wechsel"), _hrow(self.r_m0, "bis", self.r_m1))
        rl.addLayout(rf)
        self.r_own = QCheckBox("Eigene Muster mit einbeziehen")
        rl.addWidget(self.r_own)
        self.reroll_btn = QPushButton(theme.icon("shuffle"), "Neu würfeln")
        self.reroll_btn.clicked.connect(lambda: self.reroll(restart=True))
        rl.addWidget(self.reroll_btn)
        rl.addStretch(1)
        self.left_stack.addWidget(page)

        page = QWidget()
        ml = QVBoxLayout(page)
        ml.setContentsMargins(0, 8, 0, 0)
        ml.setSpacing(8)
        cap = QLabel("Das Gerät folgt dem Takt der Musik, die gerade auf dem PC läuft (Spotify, YouTube, …) "
                     "oder die das Mikrofon hört.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        ml.addWidget(cap)
        mf = QFormLayout()
        mf.setHorizontalSpacing(8)
        mf.setVerticalSpacing(8)
        self.m_source = QComboBox()
        for code, label in M.SOURCES:
            self.m_source.addItem(label, code)
        self.m_source.setCurrentIndex(max(0, self.m_source.findData(str(self._setting("m_source", "system")))))
        mf.addRow(self._label("Quelle"), self.m_source)
        self.m_every = QComboBox()
        for n, label in ((1, "Jeder Schlag"), (2, "Jeder 2. Schlag"), (4, "Jeder 4. Schlag")):
            self.m_every.addItem(label, n)
        self.m_every.setCurrentIndex(max(0, self.m_every.findData(int(self._setting("m_every", 1)))))
        mf.addRow(self._label("Hub auf"), self.m_every)
        self.m_sens = QSlider(Qt.Horizontal)
        self.m_sens.setRange(0, 100)
        self.m_sens.setValue(int(self._setting("m_sens", 50)))
        mf.addRow(self._label("Empfindlich"), self.m_sens)
        ml.addLayout(mf)
        self.m_level = QCheckBox("Tiefe folgt der Lautstärke")
        self.m_level.setChecked(str(self.qs.value("pattern/m_level", "true")).lower() == "true")
        ml.addWidget(self.m_level)
        ml.addStretch(1)
        self.left_stack.addWidget(page)
        for w in (self.m_source, self.m_every):
            w.currentIndexChanged.connect(self._music_settings)
        self.m_sens.valueChanged.connect(self._music_settings)
        self.m_level.toggled.connect(self._music_settings)
        self.random_prog: P.Program | None = None
        r = self._random_settings()
        self.mood.setCurrentIndex(max(0, self.mood.findData(r["mood"])))
        for sp, v in ((self.r_bpm0, r["bpm0"]), (self.r_bpm1, r["bpm1"]), (self.r_d0, r["d0"]),
                      (self.r_d1, r["d1"]), (self.r_m0, r["m0"]), (self.r_m1, r["m1"])):
            sp.setValue(v)
        self.r_own.setChecked(r["own"])
        self.mood.currentIndexChanged.connect(self._mood_changed)
        for w in (self.r_bpm0, self.r_bpm1, self.r_d0, self.r_d1, self.r_m0, self.r_m1):
            w.valueChanged.connect(self._random_changed)
        self.r_own.toggled.connect(self._random_changed)
        root.addWidget(left)

        # ---- preview and control
        outer = QVBoxLayout()
        outer.setContentsMargins(24, 16, 24, 20)
        outer.setSpacing(10)
        self.right_stack = QStackedWidget()
        outer.addWidget(self.right_stack, 1)
        page = QWidget()
        right = QVBoxLayout(page)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)
        self.right_stack.addWidget(page)
        self.title = QLabel("")
        self.title.setObjectName("section")
        right.addWidget(self.title)
        self.info = QLabel("")
        self.info.setObjectName("caption")
        right.addWidget(self.info)
        self.view = PatternView(cycles=2)
        right.addWidget(self.view, 1)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        bpm = int(self._setting("bpm", 60))
        self.bpm = QSlider(Qt.Horizontal)
        self.bpm.setRange(10, 240)
        self.bpm.setValue(bpm)
        self.bpm_val = QLabel()
        self.bpm_val.setObjectName("value")
        self.bpm_val.setFont(theme.mono_font(12))
        self.bpm_val.setMinimumWidth(96)
        form.addRow(self._label("Tempo"), _hrow(self.bpm, self.bpm_val))
        self.depth = QSlider(Qt.Horizontal)
        self.depth.setRange(10, 100)
        self.depth.setValue(int(self._setting("depth", 100)))
        self.depth_val = QLabel()
        self.depth_val.setObjectName("value")
        self.depth_val.setFont(theme.mono_font(12))
        self.depth_val.setMinimumWidth(96)
        form.addRow(self._label("Tiefe"), _hrow(self.depth, self.depth_val))
        right.addLayout(form)
        cap = QLabel("Tiefe und Tempo bleiben gespeichert und gelten für alle Muster. Ändern geht jederzeit, "
                     "auch beim Abspielen: ↑/↓ Tiefe, ←/→ Tempo, Leertaste Start/Stopp, Bild↑/↓ Muster. "
                     "Hubbereich und Tempo-Limit aus „Gerät“ gelten zusätzlich.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        right.addWidget(cap)

        # program page
        page = QWidget()
        pr = QVBoxLayout(page)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(10)
        self.ptitle = QLabel("")
        self.ptitle.setObjectName("section")
        pr.addWidget(self.ptitle)
        self.pinfo = QLabel("")
        self.pinfo.setObjectName("caption")
        pr.addWidget(self.pinfo)
        self.pview = ProgramView(self.find_pattern)
        pr.addWidget(self.pview, 1)
        self.pnow = QLabel("")
        self.pnow.setObjectName("value")
        self.pnow.setFont(theme.mono_font(12))
        pr.addWidget(self.pnow)
        row = QHBoxLayout()
        self.prev_btn = QPushButton(theme.icon("skip-back"), "Schritt zurück")
        self.next_btn = QPushButton(theme.icon("skip-forward"), "Nächster Schritt")
        self.prev_btn.clicked.connect(lambda: self.step_pattern(-1))
        self.next_btn.clicked.connect(lambda: self.step_pattern(1))
        self.offset_label = QLabel("")
        self.offset_label.setObjectName("caption")
        self.reset_btn = QPushButton("Anpassung zurücksetzen")
        self.reset_btn.clicked.connect(self.reset_offsets)
        for w in (self.prev_btn, self.next_btn):
            row.addWidget(w)
        row.addSpacing(12)
        row.addWidget(self.offset_label, 1)
        row.addWidget(self.reset_btn)
        pr.addLayout(row)
        cap = QLabel()
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        self.pcap = cap
        pr.addWidget(cap)
        self.right_stack.addWidget(page)

        page = QWidget()
        mr = QVBoxLayout(page)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.setSpacing(10)
        t = QLabel("Musik")
        t.setObjectName("section")
        mr.addWidget(t)
        from .setup_wizard import Flash
        self.m_flash = Flash()
        mr.addWidget(self.m_flash, 1)
        self.m_info = QLabel("Start drücken und Musik abspielen.")
        self.m_info.setObjectName("value")
        self.m_info.setFont(theme.mono_font(13))
        self.m_info.setAlignment(Qt.AlignCenter)
        mr.addWidget(self.m_info)
        mform = QFormLayout()
        mform.setHorizontalSpacing(12)
        self.m_depth = QSlider(Qt.Horizontal)
        self.m_depth.setRange(10, 100)
        self.m_depth.setValue(int(self._setting("m_depth", 100)))
        self.m_depth_val = QLabel()
        self.m_depth_val.setObjectName("value")
        self.m_depth_val.setFont(theme.mono_font(12))
        self.m_depth_val.setMinimumWidth(96)
        mform.addRow(self._label("Tiefe"), _hrow(self.m_depth, self.m_depth_val))
        mr.addLayout(mform)
        cap = QLabel("Das Gerät ist auf den vorhergesagten Schlägen unten. Passt der Takt nicht genau, im Fenster "
                     "„Gerät“ den Zeitversatz ändern. ↑/↓ ändert die Tiefe.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        mr.addWidget(cap)
        self.right_stack.addWidget(page)
        self.m_depth.valueChanged.connect(self._music_settings)
        self.m_depth_val.setText(f"{self.m_depth.value()} %")
        self.music = M.MusicDriver(self.sync)
        self.capture: M.AudioCapture | None = None
        self.tracker: M.BeatTracker | None = None
        self._beats: deque = deque()
        self._stream_base: float | None = None
        self._stream_samples = 0
        self._music_error = ""
        self._last_flash = -1e9
        right = outer

        self.pad_label = QLabel()
        self.pad_label.setObjectName("caption")
        self.pad_label.setWordWrap(True)
        right.addWidget(self.pad_label)

        row = QHBoxLayout()
        self.device_label = QLabel()
        self.device_label.setObjectName("caption")
        row.addWidget(self.device_label, 1)
        self.connect_btn = QPushButton(theme.icon("bluetooth"), "Gerät…")
        self.connect_btn.clicked.connect(ctl.open_dialog)
        row.addWidget(self.connect_btn)
        self.play_btn = QPushButton(theme.icon("play", theme.ON_ACCENT), "Start")
        self.play_btn.setObjectName("primary")
        self.play_btn.setMinimumWidth(120)
        self.play_btn.clicked.connect(self.toggle)
        row.addWidget(self.play_btn)
        right.addLayout(row)
        root.addLayout(outer, 1)

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(self.APPLY_MS)
        self._apply_timer.timeout.connect(self._apply_live)
        self.bpm.valueChanged.connect(self._params_changed)
        self.depth.valueChanged.connect(self._params_changed)
        for keys, fn in ((Qt.Key_Up, lambda: self.nudge(depth=self.STEP_DEPTH)),
                         (Qt.Key_Down, lambda: self.nudge(depth=-self.STEP_DEPTH)),
                         (Qt.Key_Right, lambda: self.nudge(tempo=self.STEP_TEMPO)),
                         (Qt.Key_Left, lambda: self.nudge(tempo=-self.STEP_TEMPO)),
                         (Qt.Key_Space, self.toggle),
                         (Qt.Key_PageDown, lambda: self.step_pattern(1)),
                         (Qt.Key_PageUp, lambda: self.step_pattern(-1))):
            QShortcut(QKeySequence(keys), self, fn)

        # controller: sticks change smoothly, D-pad in steps, buttons start/stop/switch
        self._axes: dict[int, float] = {}
        self._depth_f = float(self.depth.value())
        self._tempo_f = float(self.bpm.value())
        self.pad_timer = QTimer(self)
        self.pad_timer.setInterval(50)
        self.pad_timer.timeout.connect(self._pad_tick)
        self.pad = GamepadBridge(self)
        self.pad.event.connect(self.pad_event)
        self.pad.state.connect(self.pad_state)
        self.pad_state(None)
        self._fill()
        self._select_name(str(self._setting("last", P.BUILTIN[0].name)))
        self._fill_programs()
        self._select_program(str(self._setting("last_program", P.BUILTIN_PROGRAMS[0].name)))
        self._params_changed()
        self._update_offsets()
        self._device_changed()
        self.set_mode(int(self._setting("mode", 0)))

    # ------------------------------------------------------------ helpers
    def _label(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("formLabel")
        return lb

    def _setting(self, key: str, default):
        v = self.qs.value(f"pattern/{key}")
        if v is None:
            return default
        try:
            return type(default)(v)
        except (TypeError, ValueError):
            return default

    def _now(self) -> float:
        return float(self.clock.elapsed())

    def all_patterns(self) -> list[P.Pattern]:
        return P.BUILTIN + self.user

    def find_pattern(self, name: str) -> P.Pattern | None:
        for p in self.user + P.BUILTIN:
            if p.name == name:
                return p
        return None

    def all_programs(self) -> list[P.Program]:
        return self.programs + P.BUILTIN_PROGRAMS

    @property
    def mode(self) -> int:
        """0 = single pattern, 1 = program (Ablauf), 2 = random."""
        return self._mode

    def set_mode(self, mode: int):
        mode = mode if mode in (0, 1, 2, 3) else 0
        if mode != self.mode and self.running:
            self.stop()
        self._mode = mode
        for i, b in enumerate(self.mode_btns):
            b.setChecked(i == mode)
        self.left_stack.setCurrentIndex(mode)
        self.right_stack.setCurrentIndex({0: 0, 1: 1, 2: 1, 3: 2}[mode])
        if mode == 2:
            self.pcap.setText("Beim Abspielen verschieben ↑/↓ und ←/→ (oder Controller) Tiefe und Tempo aller "
                              "Abschnitte, Bild↓ bzw. RB springt sofort zum nächsten.")
            self.prev_btn.setText("Voriger Abschnitt")
            self.next_btn.setText("Nächster Abschnitt")
        else:
            self.pcap.setText("Ein Ablauf spielt seine Schritte nacheinander, jeder mit eigenem Muster, Dauer, Tempo "
                              "und Tiefe. Beim Abspielen passen ↑/↓ und ←/→ (oder Controller) alles gemeinsam an, "
                              "Bild↑/↓ bzw. LB/RB springt zum vorigen/nächsten Schritt.")
            self.prev_btn.setText("Schritt zurück")
            self.next_btn.setText("Nächster Schritt")
        self.qs.setValue("pattern/mode", mode)
        if mode == 2:
            if self.random_prog is None:
                self.reroll()
            self._show_program(self.random_prog)
        elif mode == 1:
            self._prog_selected()

    def active_program(self) -> P.Program | None:
        return self.random_prog if self.mode == 2 else self.current_program()

    # ------------------------------------------------------------ random
    def _random_settings(self) -> dict:
        mood = str(self._setting("r_mood", "gemischt"))
        _l, _c, (b0, b1), (d0, d1) = P.MOODS.get(mood, P.MOODS["gemischt"])
        return {"mood": mood, "bpm0": int(self._setting("r_bpm0", b0)), "bpm1": int(self._setting("r_bpm1", b1)),
                "d0": int(self._setting("r_d0", d0)), "d1": int(self._setting("r_d1", d1)),
                "m0": float(self._setting("r_m0", 1.0)), "m1": float(self._setting("r_m1", 3.0)),
                "own": str(self.qs.value("pattern/r_own", "false")).lower() == "true"}

    def _mood_changed(self, *_):
        _l, _c, (b0, b1), (d0, d1) = P.MOODS[self.mood.currentData()]
        for sp, v in ((self.r_bpm0, b0), (self.r_bpm1, b1), (self.r_d0, d0), (self.r_d1, d1)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)
        self._save_random()
        self.reroll(restart=True)                  # a new mood is felt right away

    def _save_random(self):
        for k, v in (("r_mood", self.mood.currentData()), ("r_bpm0", self.r_bpm0.value()),
                     ("r_bpm1", self.r_bpm1.value()), ("r_d0", self.r_d0.value()), ("r_d1", self.r_d1.value()),
                     ("r_m0", self.r_m0.value()), ("r_m1", self.r_m1.value()),
                     ("r_own", "true" if self.r_own.isChecked() else "false")):
            self.qs.setValue(f"pattern/{k}", v)

    def _random_changed(self, *_):
        """Ranges changed: the running step moves into them now, the next step is rolled anew."""
        self._save_random()
        if not (self.mode == 2 and self.prunner.running and self.random_prog is not None):
            self.reroll()
            return
        pr, i = self.random_prog, self.prunner.index
        if 0 <= i < len(pr.steps):
            st = pr.steps[i]
            b0, b1 = sorted((self.r_bpm0.value(), self.r_bpm1.value()))
            d0, d1 = sorted((self.r_d0.value(), self.r_d1.value()))
            st.bpm = int(min(b1, max(b0, st.bpm)))
            st.depth = int(min(d1, max(d0, st.depth)))
        del pr.steps[i + 1:]
        self._roll_next()
        self._show_program(pr)

    def _roll(self, last: str | None) -> P.Step:
        return P.random_step(self._rng, self.mood.currentData(), (self.r_bpm0.value(), self.r_bpm1.value()),
                             (self.r_d0.value(), self.r_d1.value()), (self.r_m0.value(), self.r_m1.value()),
                             last, self.user if self.r_own.isChecked() else None)

    def _roll_next(self):
        pr = self.random_prog
        pr.steps.append(self._roll(pr.steps[-1].pattern if pr.steps else None))

    def reroll(self, restart: bool = False):
        """Live random: a program that only knows the current and the next step;
        each further step is rolled when the previous one starts."""
        import random

        if not hasattr(self, "_rng"):
            self._rng = random.Random()
        running = self.mode == 2 and self.prunner.running and self.random_prog is not None
        if running:
            pr, i = self.random_prog, self.prunner.index
            del pr.steps[i + 1:]
            self._roll_next()
            if restart:
                self.prunner.skip(self._now(), 1)  # straight into the new step
                self._roll_next()
        else:
            self.random_prog = P.Program("Zufall", [], loop=False, smooth=True)
            self._roll_next()
            self._roll_next()
        if self.mode == 2:
            self._show_program(self.random_prog)

    def current(self) -> P.Pattern | None:
        it = self.list.currentItem()
        return it.data(ROLE) if it is not None else None

    # ------------------------------------------------------------ library
    def _fill(self):
        cur = self.current()
        cat = self.filter.currentData()
        self.list.blockSignals(True)
        self.list.clear()
        last_cat = None
        for p in self.all_patterns():
            if cat and p.category != cat:
                continue
            if p.category != last_cat and not cat:
                head = QListWidgetItem(p.category.upper())
                head.setFlags(Qt.NoItemFlags)
                head.setForeground(theme.qcolor(theme.MUTED))
                f = head.font()
                f.setPixelSize(10)
                head.setFont(f)
                self.list.addItem(head)
                last_cat = p.category
            it = QListWidgetItem(pattern_icon(p), p.name)
            it.setData(ROLE, p)
            it.setToolTip("\n".join(b.describe() for b in p.blocks))
            self.list.addItem(it)
        if cat == "Eigene" and not self.user:
            hint = QListWidgetItem("Noch keine eigenen Muster.\n„Neu“ oder „Bearbeiten“ legt eins an.")
            hint.setFlags(Qt.NoItemFlags)
            self.list.addItem(hint)
        self.list.blockSignals(False)
        if cur is not None:
            self._select_name(cur.name)
        if self.list.currentItem() is None:
            for i in range(self.list.count()):
                if self.list.item(i).data(ROLE) is not None:
                    self.list.setCurrentRow(i)
                    break

    def _select_name(self, name: str) -> bool:
        for i in range(self.list.count()):
            p = self.list.item(i).data(ROLE)
            if p is not None and p.name == name:
                self.list.setCurrentRow(i)
                return True
        return False

    def _selected(self, *_):
        p = self.current()
        self.edit_btn.setEnabled(p is not None)
        self.del_btn.setVisible(p is not None and not p.builtin)
        self.edit_btn.setText("Als Kopie ändern" if p is not None and p.builtin else "Bearbeiten")
        if p is None:
            return
        self.qs.setValue("pattern/last", p.name)
        self.title.setText(p.name)
        self._update_info()
        self.view.set_pattern(p, self.bpm.value(), *self.stroke_range())
        self.runner.configure(self._now(), pattern=p)

    def _update_info(self):
        p = self.current()
        if p is None:
            return
        n = len(p.blocks)
        secs = P.cycle_ms(p, self.bpm.value()) / 1000
        kind = "Vorlage" if p.builtin else "Eigenes Muster"
        self.info.setText(f"{kind} · {n} Baustein{'e' if n != 1 else ''} · ein Durchlauf {secs:.1f} s"
                          .replace(".", ","))

    def stroke_range(self) -> tuple[int, int]:
        """Depth as a range: from the top (out) down by `depth` percent."""
        return 100 - self.depth.value(), 100

    def _params_changed(self, *_):
        bpm, depth = self.bpm.value(), self.depth.value()
        self.bpm_val.setText(f"{bpm} Hübe/min")
        self.depth_val.setText(f"{depth} %")
        self.qs.setValue("pattern/bpm", bpm)
        self.qs.setValue("pattern/depth", depth)
        if round(self._tempo_f) != bpm:
            self._tempo_f = float(bpm)
        if round(self._depth_f) != depth:
            self._depth_f = float(depth)
        self.view.set_pattern(self.current(), bpm, *self.stroke_range())
        self._update_info()
        if self.running:
            if not self._apply_timer.isActive():
                self._apply_timer.start()
        else:
            self._apply_live()

    def _apply_live(self):
        lo, hi = self.stroke_range()
        self.runner.configure(self._now(), bpm=self.bpm.value(), lo=lo, hi=hi)

    def nudge(self, depth: float = 0, tempo: float = 0):
        if self.mode == 3:
            if depth:
                self.m_depth.setValue(int(round(self.m_depth.value() + depth)))
            return
        if self.mode >= 1:
            self._offset_f[0] += tempo
            self._offset_f[1] += depth
            self._update_offsets()
            return
        if depth:
            self.depth.setValue(int(round(self.depth.value() + depth)))
        if tempo:
            self.bpm.setValue(int(round(self.bpm.value() + tempo)))

    def step_pattern(self, d: int):
        if self.mode == 3:
            return
        if self.mode >= 1:
            if self.prunner.running:
                if self.mode == 2:                             # random: no wrapping around
                    if d < 0 and self.prunner.index == 0:
                        return
                    while self.prunner.index + d >= len(self.random_prog.steps):
                        self._roll_next()
                self.prunner.skip(self._now(), d)
                self._tick()
            return
        rows = [i for i in range(self.list.count()) if self.list.item(i).data(ROLE) is not None]
        if not rows:
            return
        cur = self.list.currentRow()
        pos = rows.index(cur) if cur in rows else -1
        self.list.setCurrentRow(rows[(pos + d) % len(rows)])

    # ------------------------------------------------------------ music
    def _music_settings(self, *_):
        self.qs.setValue("pattern/m_source", self.m_source.currentData())
        self.qs.setValue("pattern/m_every", self.m_every.currentData())
        self.qs.setValue("pattern/m_sens", self.m_sens.value())
        self.qs.setValue("pattern/m_level", "true" if self.m_level.isChecked() else "false")
        self.qs.setValue("pattern/m_depth", self.m_depth.value())
        self.m_depth_val.setText(f"{self.m_depth.value()} %")
        self.music.every = self.m_every.currentData()
        self.music.lo, self.music.hi = 100 - self.m_depth.value(), 100
        self.music.follow_level = self.m_level.isChecked()
        if self.tracker is not None:
            self.tracker.sensitivity = self.m_sens.value() / 100

    def _on_samples(self, samples, arrival_ms: float):
        """Capture thread: find beats, remember them with their wall-clock time."""
        tr = self.tracker
        if tr is None:
            return
        self._stream_samples += len(samples)
        base = arrival_ms - self._stream_samples / M.RATE * 1000
        self._stream_base = base if self._stream_base is None else min(self._stream_base, base)
        for b in tr.feed(samples):
            self._beats.append((self._stream_base + b * 1000, tr.level))

    def _on_capture_error(self, msg: str):
        self._music_error = msg

    def _start_music(self, command=None):
        win = self.parent()
        if win is not None and getattr(win, "timer", None) is not None and win.timer.isActive():
            win.pause()
        self.runner.stop()
        self.prunner.stop()
        self._music_settings()
        self.tracker = M.BeatTracker(self.m_sens.value() / 100)
        self._beats.clear()
        self._stream_base, self._stream_samples, self._music_error = None, 0, ""
        cmd = command if command is not None else M.capture_command(self.m_source.currentData())
        self.capture = M.AudioCapture(self._on_samples, cmd, self._on_capture_error)
        try:
            self.capture.start()
        except (RuntimeError, OSError) as e:
            self.capture = None
            self.m_info.setText(str(e))
            return
        self.music.start()
        self.timer.start()
        self.play_btn.setIcon(theme.icon("pause", theme.ON_ACCENT))
        self.play_btn.setText("Stopp")
        self.m_info.setText("Höre zu…")

    def _stop_music(self):
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        self.tracker = None
        self.music.stop()
        if hasattr(self, "m_flash"):
            self.m_flash.set_on(False)

    def _music_tick(self):
        now = time.monotonic() * 1000
        while self._beats:
            t, level = self._beats.popleft()
            self.music.beat(t, level)
            self._last_flash = t
        self.music.tick(now)
        self.m_flash.set_on(now - self._last_flash < 120)
        if self._music_error and (self.capture is None or not self.capture.running):
            msg = self._music_error
            self.stop()
            self.m_info.setText(f"Aufnahme beendet: {msg[:120]}")
            return
        bpm = self.music.bpm()
        lvl = self.tracker.level if self.tracker is not None else 0.0
        bar = "▮" * int(round(lvl * 10)) + "▯" * (10 - int(round(lvl * 10)))
        self.m_info.setText(f"{bar}   " + (f"≈ {bpm:.0f} Schläge/min" if bpm else "suche den Takt…"))
        win = self.parent()
        if win is not None and hasattr(win, "rail"):
            pos = self.ctl.device_position()
            win.rail.set_device_position(pos if pos is not None else
                                         (M.position_at(self.music.acts, now) if self.music.acts else None))

    # ------------------------------------------------------------ phone remote
    def remote_state(self) -> dict:
        now = self._now()
        cur = self.prunner.current(now) if self.prunner.running else None
        if self.mode == 0:
            p = self.current()
            name = p.name if p else ""
            bpm, depth = self.bpm.value(), self.depth.value()
        else:
            pr = self.active_program()
            name = pr.name if pr else ""
            if cur is not None:
                bpm, depth = cur
            else:
                bpm, depth = self.bpm.value(), self.depth.value()
        titles = {0: "Muster", 1: "Ablauf", 2: "Zufall", 3: "Musik"}
        curve, sub = self._remote_curve(now)
        if self.mode == 3:
            return {"running": self.running, "mode": 3, "name": "", "title": "Musik",
                    "bpm": self.music.bpm() or 0, "depth": self.m_depth.value(), "now": self.m_info.text(),
                    "curve": [], "sub": "", "step": "", "time": "",
                    "patterns": [p.name for p in self.all_patterns()],
                    "programs": [p.name for p in self.all_programs()]}
        return {"running": self.running, "mode": self.mode, "name": name,
                "title": f"{titles[self.mode]}: {name}" if self.mode < 2 else "Zufall · " + self.mood.currentText(),
                "bpm": bpm, "depth": depth, "now": self.pnow.text() if self.mode else "",
                "curve": curve, "sub": sub, **self._remote_progress(now),
                "mood": self.mood.currentData(), "moods": [[k, v[0]] for k, v in P.MOODS.items()],
                "patterns": [p.name for p in self.all_patterns()],
                "programs": [p.name for p in self.all_programs()]}

    def _remote_progress(self, now: float) -> dict:
        """Step and time of a running program, short, for the phone."""
        if self.mode == 0 or not self.prunner.running or self.prunner.index < 0:
            return {"step": "", "time": ""}
        pr = self.prunner.program
        el = self.prunner.elapsed(now)
        if self.mode == 2:
            st = pr.steps[self.prunner.index]
            left = pr.starts()[self.prunner.index] + st.minutes * 60_000 - el
            return {"step": f"Abschnitt {self.prunner.index + 1}", "time": f"Wechsel in {_fmt_min(left)}"}
        return {"step": f"Schritt {self.prunner.index + 1} von {len(pr.steps)}",
                "time": f"{_fmt_min(el)} / {_fmt_min(pr.total_ms)}"}

    def _remote_curve(self, now: float) -> tuple[list, str]:
        """What plays now as points (x 0..1, position 0..1 within the depth), for the phone."""
        if self.mode == 0:
            p, bpm, depth = self.current(), self.bpm.value(), self.depth.value()
        else:
            pr = self.active_program()
            if pr is None or not pr.steps:
                return [], ""
            i = self.prunner.index if self.prunner.running and self.prunner.index >= 0 else 0
            st = pr.steps[i]
            base = self.find_pattern(st.pattern)
            p = st.part(base) if base is not None and base.blocks else None
            cur = self.prunner.current(now) if self.prunner.running else None
            bpm, depth = cur if cur is not None else (st.bpm, st.depth)
            if p is None:
                return [], ""
        if p is None:
            return [], ""
        one = P.cycle_ms(p, bpm)
        pts = P.preview(p, bpm, max(1, min(6, int(6000 / one))) if one > 0 else 1)
        if len(pts) < 2:
            return [], ""
        end = pts[-1][0] or 1.0
        lo = (100 - depth) / 100
        curve = [[round(t / end, 4), round(lo + f * (1 - lo), 4)] for t, f in pts[:400]]
        return curve, f"{p.name} · {one / 1000:.1f} s pro Durchlauf".replace(".", ",")

    def remote_command(self, c: dict) -> None:
        cmd = c.get("cmd")
        try:
            if cmd == "toggle":
                self.toggle()
            elif cmd == "start" and not self.running:
                self.start()
            elif cmd == "stop":
                self.stop()
            elif cmd == "nudge":
                self.nudge(depth=float(c.get("depth", 0)), tempo=float(c.get("tempo", 0)))
            elif cmd == "set":
                if self.mode == 0:
                    if "bpm" in c:
                        self.bpm.setValue(int(c["bpm"]))
                    if "depth" in c:
                        self.depth.setValue(int(c["depth"]))
                elif self.prunner.running:          # programs: move the plan by the difference
                    bpm, depth = self.prunner.current(self._now())
                    self.nudge(tempo=float(c.get("bpm", bpm)) - bpm, depth=float(c.get("depth", depth)) - depth)
            elif cmd == "mode":
                self.set_mode(int(c.get("mode", 0)))
            elif cmd == "select":
                name = str(c.get("name", ""))
                if self.mode == 0:
                    if not self._select_name(name):
                        self.filter.setCurrentIndex(0)
                        self._select_name(name)
                elif self.mode == 1:
                    self._select_program(name)
            elif cmd == "step":
                self.step_pattern(int(c.get("d", 1)))
            elif cmd == "mood":
                i = self.mood.findData(str(c.get("mood", "")))
                if i >= 0:
                    self.mood.setCurrentIndex(i)
        except (TypeError, ValueError):
            pass

    # ------------------------------------------------------------ controller
    def pad_state(self, name):
        if name:
            self.pad_label.setText(f"Controller: {name} · A/✕ Start/Stopp, B/○ Stopp, linker Stick/Steuerkreuz "
                                   "↕ Tiefe, rechter Stick ↕ bzw. Steuerkreuz ↔ Tempo, LB/RB Muster bzw. Schritt")
            self.pad_timer.start()
        else:
            self.pad_label.setText("Kein Controller gefunden (Xbox, PlayStation u.a. per USB oder Bluetooth).")
            self.pad_timer.stop()
            self._axes.clear()
            if self.running and self._pad_was_used:
                self.stop()                         # lost the controller while it was in charge
        self._pad_was_used = False

    _pad_was_used = False

    def pad_event(self, ev):
        if not ev.init:
            self.ctl.touch()
        if ev.kind == "axis":
            if ev.number == G.AX_DY and not ev.init and ev.value and self._axes.get(ev.number, 0) == 0:
                self.nudge(depth=self.STEP_DEPTH if ev.value < 0 else -self.STEP_DEPTH)
            if ev.number == G.AX_DX and not ev.init and ev.value and self._axes.get(ev.number, 0) == 0:
                self.nudge(tempo=self.STEP_TEMPO if ev.value > 0 else -self.STEP_TEMPO)
            self._axes[ev.number] = round(ev.value) if ev.number in (G.AX_DX, G.AX_DY) else ev.value
            return
        if ev.init or not ev.value:
            return
        self._pad_was_used = True
        if ev.number in (G.BTN_A, G.BTN_START):
            self.toggle()
        elif ev.number == G.BTN_B:
            self.stop()
        elif ev.number == G.BTN_LB:
            self.step_pattern(-1)
        elif ev.number == G.BTN_RB:
            self.step_pattern(1)

    def _stick(self, axis: int) -> float:
        v = self._axes.get(axis, 0.0)
        if abs(v) < self.DEADZONE:
            return 0.0
        return (abs(v) - self.DEADZONE) / (1 - self.DEADZONE) * (1 if v > 0 else -1)

    def _pad_tick(self):
        dt = self.pad_timer.interval() / 1000
        ly, ry = self._stick(G.AX_LY), self._stick(G.AX_RY)
        if self.mode == 3:
            if ly:
                self._pad_was_used = True
                self.m_depth.setValue(int(round(self.m_depth.value() - ly * self.STICK_DEPTH * dt)))
            return
        if self.mode >= 1 and (ly or ry):           # program: the sticks move the offsets
            self._pad_was_used = True
            self._offset_f[0] -= ry * self.STICK_TEMPO * dt
            self._offset_f[1] -= ly * self.STICK_DEPTH * dt
            self._update_offsets()
            return
        if ly:                                      # stick up = deeper / faster
            self._pad_was_used = True
            self._depth_f = max(10.0, min(100.0, self._depth_f - ly * self.STICK_DEPTH * dt))
            self.depth.setValue(int(round(self._depth_f)))
        if ry:
            self._pad_was_used = True
            self._tempo_f = max(10.0, min(240.0, self._tempo_f - ry * self.STICK_TEMPO * dt))
            self.bpm.setValue(int(round(self._tempo_f)))

    # ------------------------------------------------------------ own patterns
    def new_pattern(self):
        self._open_editor(P.Pattern("Mein Muster", [P.Block()]), None)

    def edit_pattern(self):
        p = self.current()
        if p is None:
            return
        if p.builtin:
            self._open_editor(p.copy(f"{p.name} (eigen)"), None)
        else:
            self._open_editor(p.copy(), p)

    def _open_editor(self, pattern: P.Pattern, original: P.Pattern | None):
        dlg = PatternEditor(pattern, self.bpm.value(), *self.stroke_range(), self)
        if not dlg.exec():
            return
        result = dlg.result_pattern()
        taken = [x.name for x in self.all_patterns() if x is not original]
        result.name = P.unique_name(result.name, taken)
        if original is not None:
            self.user[self.user.index(original)] = result
        else:
            self.user.append(result)
        self.save_user()
        if self.filter.currentData() not in ("", "Eigene"):
            self.filter.setCurrentIndex(self.filter.findData("Eigene"))
        self._fill()
        self._select_name(result.name)

    def delete_pattern(self):
        p = self.current()
        if p is None or p.builtin:
            return
        if QMessageBox.question(self, "Muster löschen", f"„{p.name}“ löschen?") != QMessageBox.Yes:
            return
        self.user.remove(p)
        self.save_user()
        self._fill()

    def save_user(self):
        try:
            P.save_user(self.user)
        except OSError as e:
            QMessageBox.warning(self, "Muster", f"Konnte die Muster nicht speichern: {e}")

    # ------------------------------------------------------------ programs
    def current_program(self) -> P.Program | None:
        it = self.prog_list.currentItem()
        return it.data(ROLE) if it is not None else None

    def _fill_programs(self):
        cur = self.current_program()
        self.prog_list.blockSignals(True)
        self.prog_list.clear()
        for title, items in (("EIGENE", self.programs), ("VORLAGEN", P.BUILTIN_PROGRAMS)):
            if not items:
                continue
            head = QListWidgetItem(title)
            head.setFlags(Qt.NoItemFlags)
            head.setForeground(theme.qcolor(theme.MUTED))
            fnt = head.font()
            fnt.setPixelSize(10)
            head.setFont(fnt)
            self.prog_list.addItem(head)
            for pr in items:
                it = QListWidgetItem(f"{pr.name}\n{len(pr.steps)} Schritte · {_fmt_min(pr.total_ms)}"
                                     + (" · Schleife" if pr.loop else ""))
                it.setData(ROLE, pr)
                self.prog_list.addItem(it)
        self.prog_list.blockSignals(False)
        if not (cur is not None and self._select_program(cur.name)):
            for i in range(self.prog_list.count()):
                if self.prog_list.item(i).data(ROLE) is not None:
                    self.prog_list.setCurrentRow(i)
                    break

    def _select_program(self, name: str) -> bool:
        for i in range(self.prog_list.count()):
            pr = self.prog_list.item(i).data(ROLE)
            if pr is not None and pr.name == name:
                self.prog_list.setCurrentRow(i)
                return True
        return False

    def _prog_selected(self, *_):
        pr = self.current_program()
        self.pedit_btn.setEnabled(pr is not None)
        self.pdel_btn.setVisible(pr is not None and not pr.builtin)
        self.pedit_btn.setText("Als Kopie ändern" if pr is not None and pr.builtin else "Bearbeiten")
        if pr is None:
            return
        if self.mode == 1 and self.prunner.running and pr is not self.prunner.program:
            self.stop()
        self.qs.setValue("pattern/last_program", pr.name)
        if self.mode == 1:
            self._show_program(pr)

    def _show_program(self, pr: P.Program | None):
        if pr is None:
            return
        if self.mode == 2:
            self.ptitle.setText("Zufall · " + self.mood.currentText())
            self.pinfo.setText(f"wechselt alle {self.r_m0.value():g}–{self.r_m1.value():g} min · "
                               f"Tempo {self.r_bpm0.value()}–{self.r_bpm1.value()} · "
                               f"Tiefe {self.r_d0.value()}–{self.r_d1.value()} % · würfelt jeden Abschnitt neu"
                               .replace(".", ","))
        else:
            self.ptitle.setText(pr.name)
            end = "fängt dann von vorne an" if pr.loop else "stoppt dann"
            self.pinfo.setText(f"{'Vorlage' if pr.builtin else 'Eigener Ablauf'} · {len(pr.steps)} Schritte · "
                               f"{_fmt_min(pr.total_ms)} · {end}")
        self.pview.set_program(pr)

    def _update_offsets(self):
        t, d = (int(round(v)) for v in self._offset_f)
        if t or d:
            self.offset_label.setText(f"Anpassung: Tempo {t:+d} · Tiefe {d:+d} %")
        else:
            self.offset_label.setText("Anpassung: keine")
        self.reset_btn.setEnabled(bool(t or d))
        self.prunner.offset_bpm, self.prunner.offset_depth = t, d

    def reset_offsets(self):
        self._offset_f = [0.0, 0.0]
        self._update_offsets()

    def new_program(self):
        self._open_program_editor(P.Program("Mein Ablauf", [P.Step("Ruhig", 5, 40, 60),
                                                             P.Step("Gleichmäßig", 10, 60, 90)]), None)

    def edit_program(self):
        pr = self.current_program()
        if pr is None:
            return
        if pr.builtin:
            self._open_program_editor(pr.copy(f"{pr.name} (eigen)"), None)
        else:
            self._open_program_editor(pr.copy(), pr)

    def _open_program_editor(self, program: P.Program, original: P.Program | None):
        dlg = ProgramEditor(program, self.all_patterns(), self.find_pattern, self)
        if not dlg.exec():
            return
        result = dlg.result_program()
        taken = [x.name for x in self.all_programs() if x is not original]
        result.name = P.unique_name(result.name, taken)
        if original is not None:
            if self.prunner.program is original:
                self.stop()
            self.programs[self.programs.index(original)] = result
        else:
            self.programs.append(result)
        self.save_programs()
        self._fill_programs()
        self._select_program(result.name)

    def delete_program(self):
        pr = self.current_program()
        if pr is None or pr.builtin:
            return
        if QMessageBox.question(self, "Ablauf löschen", f"„{pr.name}“ löschen?") != QMessageBox.Yes:
            return
        if self.prunner.program is pr:
            self.stop()
        self.programs.remove(pr)
        self.save_programs()
        self._fill_programs()

    def save_programs(self):
        try:
            P.save_programs(self.programs)
        except OSError as e:
            QMessageBox.warning(self, "Abläufe", f"Konnte die Abläufe nicht speichern: {e}")

    # ------------------------------------------------------------ playback
    def _device_changed(self):
        dev = self.ctl.device
        if dev is not self.sync.device:
            was, pwas = self.runner.running, self.prunner.running
            self.sync.set_device(dev)
            if was:
                self.runner.start(self._now())
            if pwas:
                self.prunner.runner.start(self._now())
        if dev is not None and dev.connected:
            self.device_label.setText(f"Gerät: {dev.name}")
        else:
            self.device_label.setText("Kein Gerät verbunden – Start zeigt nur die Vorschau.")

    @property
    def running(self) -> bool:
        return self.runner.running or self.prunner.running or self.music.running

    # safety timer source
    def safety_active(self) -> bool:
        return self.running

    def safety_pause(self) -> bool:
        if not self.running:
            return False
        now = self._now()
        self._resume = (self.mode, self.prunner.elapsed(now) if self.prunner.running else None)
        self.stop()
        return True

    def safety_resume(self) -> None:
        if self._resume is None:
            return
        mode, elapsed = self._resume
        self._resume = None
        self.set_mode(mode)
        self.start(reroll=False)
        if elapsed is not None and self.prunner.running:
            self.prunner.start_ms = self._now() - elapsed

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self, reroll: bool = True):
        if self.mode == 3:
            self._start_music()
            return
        if self.mode == 2 and reroll:
            self.reroll()
        if self.mode >= 1:
            pr = self.active_program()
            if pr is None or not pr.steps:
                return
        else:
            p = self.current()
            if p is None:
                return
        win = self.parent()
        if win is not None and hasattr(win, "pause") and getattr(win, "timer", None) is not None \
                and win.timer.isActive():
            win.pause()                    # the video and a pattern never drive the device together
        if self.mode >= 1:
            self.runner.stop()
            self.prunner.start(self._now(), pr)
        else:
            self.prunner.stop()
            lo, hi = self.stroke_range()
            self.runner.configure(self._now(), pattern=p, bpm=self.bpm.value(), lo=lo, hi=hi)
            self.runner.start(self._now())
        self.timer.start()
        self.play_btn.setIcon(theme.icon("pause", theme.ON_ACCENT))
        self.play_btn.setText("Stopp")

    def stop(self):
        self.runner.stop()
        self.prunner.stop()
        self._stop_music()
        self.timer.stop()
        self.view.set_phase(None)
        self.pview.set_elapsed(None)
        self.pnow.setText("")
        self.play_btn.setIcon(theme.icon("play", theme.ON_ACCENT))
        self.play_btn.setText("Start")

    def _tick(self):
        now = self._now()
        if self.music.running:
            self._music_tick()
            return
        if self.prunner.running or self.prunner.finished:
            if self.mode == 2 and self.prunner.running and self.random_prog is not None:
                if self.prunner.step_at(self.prunner.elapsed(now)) >= len(self.random_prog.steps) - 1:
                    self._roll_next()                          # live: roll the step after this one
            self.prunner.tick(now)
            if self.prunner.finished:
                self.prunner.finished = False
                self.stop()
                self.pnow.setText("Ablauf fertig.")
                return
            pr = self.prunner.program
            el = self.prunner.elapsed(now)
            st = pr.steps[self.prunner.index]
            if self.mode == 2:                                 # show only this step and the next
                i = self.prunner.index
                self.pview.set_program(P.Program("Zufall", pr.steps[i:i + 2]))
                self.pview.set_elapsed(el - pr.starts()[i])
            else:
                self.pview.set_elapsed(el)
            bpm, depth = self.prunner.current(now)
            if self.mode == 2:
                left = pr.starts()[self.prunner.index] + st.minutes * 60_000 - el
                self.pnow.setText(f"{st.pattern} · {round(bpm)} Hübe/min · Tiefe {round(depth)} % · "
                                  f"nächster Wechsel in {_fmt_min(left)}")
            else:
                self.pnow.setText(f"Schritt {self.prunner.index + 1}/{len(pr.steps)} · {st.pattern} · "
                                  f"{round(bpm)} Hübe/min · Tiefe {round(depth)} % · {_fmt_min(el)} von "
                                  f"{_fmt_min(pr.total_ms)}, noch {_fmt_min(pr.total_ms - el)}")
            runner = self.prunner.runner
        else:
            self.runner.tick(now)
            self.view.set_phase(self.runner.phase(now, self.view.cycles))
            runner = self.runner
        win = self.parent()
        if win is not None and hasattr(win, "rail"):
            pos = self.ctl.device_position()
            win.rail.set_device_position(pos if pos is not None else runner.position(now))

    def showEvent(self, ev):
        self.pad.reader.start()
        super().showEvent(ev)

    def _shutdown(self):
        self.stop()
        self.pad_timer.stop()
        self.pad.reader.stop()

    def closeEvent(self, ev):
        self._shutdown()
        super().closeEvent(ev)

    def reject(self):
        self._shutdown()
        super().reject()


class PatternEditor(QDialog):
    """Baukasten: put blocks together, see the result as a picture."""

    changed = Signal()

    def __init__(self, pattern: P.Pattern, bpm: float, lo: int, hi: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Muster bauen")
        self.resize(900, 640)
        self.pattern = pattern.copy()
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        self.name = QLineEdit(self.pattern.name)
        self.name.setPlaceholderText("Name")
        lay.addWidget(_hrow("Name", self.name))

        t = QLabel("Bausteine – anklicken zum Anhängen")
        t.setObjectName("section")
        lay.addWidget(t)
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (label, block) in enumerate(P.PALETTE):
            b = QToolButton()
            b.setIcon(block_icon(block))
            b.setIconSize(QSize(56, 28))
            b.setText(label)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setMinimumWidth(92)
            b.setToolTip(block.describe())
            b.clicked.connect(lambda _c=False, blk=block: self.add_block(blk))
            grid.addWidget(b, i // 7, i % 7)
        lay.addLayout(grid)

        mid = QHBoxLayout()
        mid.setSpacing(16)
        col = QVBoxLayout()
        t = QLabel("Ablauf")
        t.setObjectName("section")
        col.addWidget(t)
        self.blocks = QListWidget()
        self.blocks.setObjectName("playlist")
        self.blocks.setIconSize(QSize(56, 28))
        self.blocks.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.blocks.setWordWrap(True)
        self.blocks.setDragDropMode(QAbstractItemView.InternalMove)
        self.blocks.model().rowsMoved.connect(self._rows_moved)
        self.blocks.currentRowChanged.connect(self._block_selected)
        col.addWidget(self.blocks, 1)
        row = QHBoxLayout()
        self.up_btn = QPushButton(theme.icon("arrow-up"), "")
        self.down_btn = QPushButton(theme.icon("arrow-down"), "")
        self.dup_btn = QPushButton("Doppeln")
        self.rm_btn = QPushButton(theme.icon("trash"), "")
        self.up_btn.setToolTip("Nach vorne")
        self.down_btn.setToolTip("Nach hinten")
        self.rm_btn.setToolTip("Baustein entfernen")
        for b in (self.up_btn, self.down_btn, self.dup_btn, self.rm_btn):
            row.addWidget(b)
        self.up_btn.clicked.connect(lambda: self.move_block(-1))
        self.down_btn.clicked.connect(lambda: self.move_block(1))
        self.dup_btn.clicked.connect(self.duplicate_block)
        self.rm_btn.clicked.connect(self.remove_block)
        col.addLayout(row)
        mid.addLayout(col, 1)

        # properties of the selected block
        props = QVBoxLayout()
        t = QLabel("Baustein")
        t.setObjectName("section")
        props.addWidget(t)
        f = QFormLayout()
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        self.kind = QComboBox()
        for code, label in P.KINDS:
            self.kind.addItem(label, code)
        f.addRow("Art", self.kind)
        self.repeat = QSpinBox()
        self.repeat.setRange(1, 64)
        self.repeat.setSuffix(" ×")
        self.repeat_row = _hrow(self.repeat, stretch=True)
        f.addRow("Wiederholen", self.repeat_row)
        self.speed = _speed_spin(1.0)
        self.speed_to = _speed_spin(1.0)
        self.speed_arrow = QLabel("→")
        f.addRow("Tempo", _hrow(self.speed, self.speed_arrow, self.speed_to, stretch=True))
        self.lo = _pct_spin(0)
        self.hi = _pct_spin(100)
        self.lo_to = _pct_spin(0)
        self.hi_to = _pct_spin(100)
        self.depth_from = _hrow(self.lo, "bis", self.hi, stretch=True)
        self.depth_to = _hrow("→", self.lo_to, "bis", self.hi_to, stretch=True)
        self.pos_label = QLabel("Tiefe")
        f.addRow(self.pos_label, self.depth_from)
        f.addRow("", self.depth_to)
        self.length = QDoubleSpinBox()
        self.length.setRange(0.25, 32)
        self.length.setSingleStep(0.25)
        self.length.setSuffix(" Hub")
        self.length_row = _hrow(self.length, stretch=True)
        f.addRow("Dauer", self.length_row)
        self.ramp = QCheckBox("Verlauf: Tempo und Tiefe ändern sich über die Wiederholungen")
        f.addRow("", self.ramp)
        self.form = f
        props.addLayout(f)
        cap = QLabel("Tempo 1× = ein Hub im eingestellten Tempo. Tiefe in Prozent der Tiefe "
                     "aus der Steuerung (0 = ganz rein, 100 = ganz raus).")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        props.addWidget(cap)
        props.addStretch(1)
        pw = QWidget()
        pw.setLayout(props)
        pw.setMinimumWidth(380)
        mid.addWidget(pw)
        lay.addLayout(mid, 1)

        self.view = PatternView(cycles=2)
        self.view.set_pattern(self.pattern, bpm, lo, hi)
        lay.addWidget(self.view, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Speichern")
        bb.button(QDialogButtonBox.Save).setObjectName("primary")
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.save_btn = bb.button(QDialogButtonBox.Save)
        lay.addWidget(bb)

        for w in (self.repeat, self.speed, self.speed_to, self.lo, self.hi, self.lo_to, self.hi_to, self.length):
            w.valueChanged.connect(self._props_changed)
        self.kind.currentIndexChanged.connect(self._props_changed)
        self.ramp.toggled.connect(self._props_changed)
        self._fill(0)

    # ------------------------------------------------------------ list
    def _fill(self, select: int | None = None):
        self.blocks.blockSignals(True)
        self.blocks.clear()
        for b in self.pattern.blocks:
            self.blocks.addItem(QListWidgetItem(block_icon(b), b.describe()))
        self.blocks.blockSignals(False)
        if self.pattern.blocks:
            self.blocks.setCurrentRow(max(0, min(len(self.pattern.blocks) - 1, select or 0)))
        else:
            self._block_selected(-1)
        self._refresh()

    def _refresh(self):
        self.view.set_pattern(self.pattern)
        self.view.set_highlight(self.blocks.currentRow() if self.pattern.blocks else None)
        self.save_btn.setEnabled(bool(self.pattern.blocks))

    def add_block(self, block: P.Block):
        i = self.blocks.currentRow() + 1 if self.pattern.blocks else 0
        self.pattern.blocks.insert(i, P.Block.from_dict(block.to_dict()))
        self._fill(i)

    def move_block(self, d: int):
        i = self.blocks.currentRow()
        j = i + d
        if 0 <= i < len(self.pattern.blocks) and 0 <= j < len(self.pattern.blocks):
            bl = self.pattern.blocks
            bl[i], bl[j] = bl[j], bl[i]
            self._fill(j)

    def _rows_moved(self, _parent, start, _end, _dest, row):
        bl = self.pattern.blocks
        b = bl.pop(start)
        to = row - 1 if row > start else row
        bl.insert(to, b)
        QTimer.singleShot(0, lambda: self._fill(to))

    def duplicate_block(self):
        i = self.blocks.currentRow()
        if 0 <= i < len(self.pattern.blocks):
            self.pattern.blocks.insert(i + 1, P.Block.from_dict(self.pattern.blocks[i].to_dict()))
            self._fill(i + 1)

    def remove_block(self):
        i = self.blocks.currentRow()
        if 0 <= i < len(self.pattern.blocks):
            del self.pattern.blocks[i]
            self._fill(i - 1 if i > 0 else 0)

    # ------------------------------------------------------------ properties
    def _block_selected(self, i: int):
        has = 0 <= i < len(self.pattern.blocks)
        for w in (self.kind, self.repeat, self.speed, self.lo, self.hi, self.length, self.ramp,
                  self.up_btn, self.down_btn, self.dup_btn, self.rm_btn):
            w.setEnabled(has)
        if not has:
            self.view.set_highlight(None)
            return
        b = self.pattern.blocks[i]
        self._loading = True
        self.kind.setCurrentIndex(self.kind.findData(b.kind))
        self.repeat.setValue(b.repeat)
        self.speed.setValue(b.speed)
        self.lo.setValue(b.lo)
        self.hi.setValue(b.hi)
        self.speed_to.setValue(b.speed if b.speed_to is None else b.speed_to)
        self.lo_to.setValue(b.lo if b.lo_to is None else b.lo_to)
        self.hi_to.setValue(b.hi if b.hi_to is None else b.hi_to)
        self.length.setValue(b.length)
        self.ramp.setChecked(b.ramp)
        self._loading = False
        self._show_fields(b)
        self.view.set_highlight(i)

    def _show_fields(self, b: P.Block):
        stroke = b.kind == "stroke"
        ramp = stroke and self.ramp.isChecked()
        self.form.setRowVisible(self.repeat_row, stroke)
        self.form.setRowVisible(self.length_row, b.kind == "hold")
        self.form.setRowVisible(self.ramp, stroke)
        self.form.setRowVisible(self.depth_to, ramp)
        self.speed_arrow.setVisible(ramp)
        self.speed_to.setVisible(ramp)
        self.lo.setVisible(stroke)
        self.depth_from.findChild(QLabel).setVisible(stroke)
        self.pos_label.setText("Tiefe" if stroke else ("Ziel" if b.kind == "move" else "Position"))

    def _props_changed(self, *_):
        i = self.blocks.currentRow()
        if self._loading or not 0 <= i < len(self.pattern.blocks):
            return
        b = self.pattern.blocks[i]
        b.kind = self.kind.currentData()
        b.repeat = self.repeat.value()
        b.speed = self.speed.value()
        b.lo, b.hi = self.lo.value(), self.hi.value()
        b.length = self.length.value()
        if b.kind == "stroke" and self.ramp.isChecked():
            b.speed_to = self.speed_to.value()
            b.lo_to = self.lo_to.value()
            b.hi_to = self.hi_to.value()
        else:
            b.speed_to = b.lo_to = b.hi_to = None
        self._show_fields(b)
        it = self.blocks.item(i)
        it.setIcon(block_icon(b))
        it.setText(b.describe())
        self._refresh()

    def _accept(self):
        if not self.pattern.blocks:
            return
        self.accept()

    def result_pattern(self) -> P.Pattern:
        p = self.pattern.copy(self.name.text().strip() or "Mein Muster")
        return p


class ProgramEditor(QDialog):
    """Put steps together: pattern (or a part of it), minutes, tempo, depth."""

    def __init__(self, program: P.Program, patterns: list[P.Pattern], lookup, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ablauf bauen")
        self.resize(940, 680)
        self.program = program.copy()
        self.lookup = lookup
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        self.name = QLineEdit(self.program.name)
        self.name.setPlaceholderText("Name")
        self.loop = QCheckBox("Am Ende von vorne anfangen")
        self.loop.setChecked(self.program.loop)
        self.smooth = QCheckBox("Weiche Übergänge")
        self.smooth.setToolTip("Tempo und Tiefe gleiten in den ersten 8 Sekunden eines Schritts vom vorigen her")
        self.smooth.setChecked(self.program.smooth)
        lay.addWidget(_hrow("Name", self.name, self.loop, self.smooth))
        self.loop.toggled.connect(self._opts_changed)
        self.smooth.toggled.connect(self._opts_changed)

        mid = QHBoxLayout()
        mid.setSpacing(16)
        col = QVBoxLayout()
        t = QLabel("Schritte")
        t.setObjectName("section")
        col.addWidget(t)
        self.steps = QListWidget()
        self.steps.setObjectName("playlist")
        self.steps.setIconSize(QSize(72, 24))
        self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.steps.setWordWrap(True)
        self.steps.currentRowChanged.connect(self._step_selected)
        col.addWidget(self.steps, 1)
        row = QHBoxLayout()
        self.add_btn = QPushButton(theme.icon("plus"), "Schritt")
        self.up_btn = QPushButton(theme.icon("arrow-up"), "")
        self.down_btn = QPushButton(theme.icon("arrow-down"), "")
        self.dup_btn = QPushButton("Doppeln")
        self.rm_btn = QPushButton(theme.icon("trash"), "")
        self.up_btn.setToolTip("Nach vorne")
        self.down_btn.setToolTip("Nach hinten")
        self.rm_btn.setToolTip("Schritt entfernen")
        for b in (self.add_btn, self.up_btn, self.down_btn, self.dup_btn, self.rm_btn):
            row.addWidget(b)
        self.add_btn.clicked.connect(self.add_step)
        self.up_btn.clicked.connect(lambda: self.move_step(-1))
        self.down_btn.clicked.connect(lambda: self.move_step(1))
        self.dup_btn.clicked.connect(self.duplicate_step)
        self.rm_btn.clicked.connect(self.remove_step)
        col.addLayout(row)
        mid.addLayout(col, 1)

        props = QVBoxLayout()
        t = QLabel("Schritt")
        t.setObjectName("section")
        props.addWidget(t)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.pattern = QComboBox()
        self.pattern.setIconSize(QSize(72, 24))
        self.pattern.setMaxVisibleItems(16)
        for p in patterns:
            self.pattern.addItem(pattern_icon(p, 72, 24), p.name, p.name)
        form.addRow("Muster", self.pattern)
        self.first = QSpinBox()
        self.last = QSpinBox()
        self.part_of = QLabel()
        self.part_of.setObjectName("caption")
        form.addRow("Teil", _hrow("Baustein", self.first, "bis", self.last, self.part_of, stretch=True))
        self.minutes = QDoubleSpinBox()
        self.minutes.setRange(0.25, 600)
        self.minutes.setDecimals(2)
        self.minutes.setSingleStep(1)
        self.minutes.setSuffix(" min")
        form.addRow("Dauer", _hrow(self.minutes, stretch=True))
        self.bpm = QSpinBox()
        self.bpm.setRange(10, 240)
        self.bpm.setSingleStep(5)
        self.bpm.setSuffix(" Hübe/min")
        form.addRow("Tempo", _hrow(self.bpm, stretch=True))
        self.depth = QSpinBox()
        self.depth.setRange(10, 100)
        self.depth.setSingleStep(5)
        self.depth.setSuffix(" %")
        form.addRow("Tiefe", _hrow(self.depth, stretch=True))
        props.addLayout(form)
        cap = QLabel("Teil: nur diese Bausteine des Musters spielen, z.B. von „Kurz vor Schluss“ nur den "
                     "schnellen Anfang. Im Bild ist der gewählte Teil hervorgehoben.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        props.addWidget(cap)
        self.part_view = PatternView(cycles=1)
        self.part_view.setMinimumHeight(120)
        props.addWidget(self.part_view, 1)
        pw = QWidget()
        pw.setLayout(props)
        pw.setMinimumWidth(440)
        mid.addWidget(pw)
        lay.addLayout(mid, 1)

        self.total = QLabel()
        self.total.setObjectName("caption")
        lay.addWidget(self.total)
        self.view = ProgramView(lookup)
        self.view.setMinimumHeight(130)
        lay.addWidget(self.view)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Speichern")
        bb.button(QDialogButtonBox.Save).setObjectName("primary")
        bb.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.save_btn = bb.button(QDialogButtonBox.Save)
        lay.addWidget(bb)

        self.pattern.currentIndexChanged.connect(self._pattern_changed)
        for w in (self.first, self.last, self.minutes, self.bpm, self.depth):
            w.valueChanged.connect(self._props_changed)
        self._fill(0)

    # ------------------------------------------------------------ list
    def _describe(self, st: P.Step) -> str:
        txt = f"{st.pattern} · {st.minutes:g} min · {st.bpm}/min · {st.depth} %".replace(".", ",")
        p = self.lookup(st.pattern)
        if p is None:
            return txt + " · (Muster fehlt)"
        part = st.part(p)
        if part is not p:
            a, b = max(1, st.first), st.last or len(p.blocks)
            txt += f" · Baustein {a}" if a == b else f" · Bausteine {a}–{b}"
        return txt

    def _icon(self, st: P.Step):
        p = self.lookup(st.pattern)
        return pattern_icon(st.part(p), 72, 24) if p is not None and p.blocks else QIcon()

    def _fill(self, select: int = 0):
        self.steps.blockSignals(True)
        self.steps.clear()
        for st in self.program.steps:
            self.steps.addItem(QListWidgetItem(self._icon(st), self._describe(st)))
        self.steps.blockSignals(False)
        if self.program.steps:
            self.steps.setCurrentRow(max(0, min(len(self.program.steps) - 1, select)))
        else:
            self._step_selected(-1)
        self._refresh()

    def _refresh(self):
        self.view.set_program(self.program)
        self.view.set_selected(self.steps.currentRow() if self.program.steps else None)
        self.total.setText(f"Gesamt {_fmt_min(self.program.total_ms)} · {len(self.program.steps)} Schritte"
                           + (" · danach von vorne" if self.program.loop else " · danach Stopp"))
        self.save_btn.setEnabled(bool(self.program.steps))

    def add_step(self):
        i = self.steps.currentRow()
        base = self.program.steps[i] if 0 <= i < len(self.program.steps) else P.Step()
        st = P.Step.from_dict(base.to_dict())
        self.program.steps.insert(i + 1, st)
        self._fill(i + 1)

    def move_step(self, d: int):
        i = self.steps.currentRow()
        j = i + d
        n = len(self.program.steps)
        if 0 <= i < n and 0 <= j < n:
            st = self.program.steps
            st[i], st[j] = st[j], st[i]
            self._fill(j)

    def duplicate_step(self):
        self.add_step()

    def remove_step(self):
        i = self.steps.currentRow()
        if 0 <= i < len(self.program.steps):
            del self.program.steps[i]
            self._fill(max(0, i - 1))

    # ------------------------------------------------------------ properties
    def _step_selected(self, i: int):
        has = 0 <= i < len(self.program.steps)
        for w in (self.pattern, self.first, self.last, self.minutes, self.bpm, self.depth,
                  self.up_btn, self.down_btn, self.dup_btn, self.rm_btn):
            w.setEnabled(has)
        if not has:
            self.part_view.set_pattern(None)
            return
        st = self.program.steps[i]
        self._loading = True
        k = self.pattern.findData(st.pattern)
        if k < 0:                                      # deleted pattern: show it, keep it
            self.pattern.addItem(f"{st.pattern} (fehlt)", st.pattern)
            k = self.pattern.count() - 1
        self.pattern.setCurrentIndex(k)
        self.minutes.setValue(st.minutes)
        self.bpm.setValue(st.bpm)
        self.depth.setValue(st.depth)
        self._set_part_limits(st)
        self._loading = False
        self._show_part(st)
        self.view.set_selected(i)

    def _set_part_limits(self, st: P.Step):
        p = self.lookup(st.pattern)
        n = max(1, len(p.blocks)) if p is not None else 1
        for sp in (self.first, self.last):
            sp.setRange(1, n)
        self.first.setValue(max(1, min(n, st.first)))
        self.last.setValue(n if st.last <= 0 else max(1, min(n, st.last)))
        self.part_of.setText(f"von {n}")

    def _show_part(self, st: P.Step):
        p = self.lookup(st.pattern)
        self.part_view.set_pattern(p, st.bpm, 100 - st.depth, 100)
        if p is not None and p.blocks:
            n = len(p.blocks)
            a = max(1, min(n, st.first))
            b = n if st.last <= 0 else max(a, min(n, st.last))
            self.part_view.set_highlight(None if (a, b) == (1, n) else (a - 1, b - 1))

    def _pattern_changed(self, *_):
        i = self.steps.currentRow()
        if self._loading or not 0 <= i < len(self.program.steps):
            return
        st = self.program.steps[i]
        st.pattern = self.pattern.currentData()
        st.first, st.last = 1, 0                       # new pattern: all of it
        self._loading = True
        self._set_part_limits(st)
        self._loading = False
        self._props_changed()

    def _props_changed(self, *_):
        i = self.steps.currentRow()
        if self._loading or not 0 <= i < len(self.program.steps):
            return
        st = self.program.steps[i]
        if self.last.value() < self.first.value():
            self._loading = True
            if self.sender() is self.first:
                self.last.setValue(self.first.value())
            else:
                self.first.setValue(self.last.value())
            self._loading = False
        p = self.lookup(st.pattern)
        n = len(p.blocks) if p is not None else 1
        st.first = self.first.value()
        st.last = 0 if self.last.value() >= n else self.last.value()
        st.minutes = self.minutes.value()
        st.bpm = self.bpm.value()
        st.depth = self.depth.value()
        it = self.steps.item(i)
        it.setIcon(self._icon(st))
        it.setText(self._describe(st))
        self._show_part(st)
        self._refresh()

    def _opts_changed(self, *_):
        self.program.loop = self.loop.isChecked()
        self.program.smooth = self.smooth.isChecked()
        self._refresh()

    def _accept(self):
        if self.program.steps:
            self.accept()

    def result_program(self) -> P.Program:
        p = self.program.copy(self.name.text().strip() or "Mein Ablauf")
        p.loop, p.smooth = self.loop.isChecked(), self.smooth.isChecked()
        return p
