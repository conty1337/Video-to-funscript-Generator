"""Pattern control: play stroke patterns on the device, build and save own patterns."""

from __future__ import annotations

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
        for i, text in enumerate(("Muster", "Abläufe")):
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
        cap = QLabel("Ein Ablauf spielt seine Schritte nacheinander, jeder mit eigenem Muster, Dauer, Tempo und "
                     "Tiefe. Beim Abspielen passen ↑/↓ und ←/→ (oder Controller) alles gemeinsam an, "
                     "Bild↑/↓ bzw. LB/RB springt zum vorigen/nächsten Schritt.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        pr.addWidget(cap)
        self.right_stack.addWidget(page)
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
        """0 = single pattern, 1 = program (Ablauf)."""
        return self.right_stack.currentIndex()

    def set_mode(self, mode: int):
        mode = 1 if mode == 1 else 0
        if mode != self.mode and self.running:
            self.stop()
        for i, b in enumerate(self.mode_btns):
            b.setChecked(i == mode)
        self.left_stack.setCurrentIndex(mode)
        self.right_stack.setCurrentIndex(mode)
        self.qs.setValue("pattern/mode", mode)

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
        if self.mode == 1:
            self._offset_f[0] += tempo
            self._offset_f[1] += depth
            self._update_offsets()
            return
        if depth:
            self.depth.setValue(int(round(self.depth.value() + depth)))
        if tempo:
            self.bpm.setValue(int(round(self.bpm.value() + tempo)))

    def step_pattern(self, d: int):
        if self.mode == 1:
            if self.prunner.running:
                self.prunner.skip(self._now(), d)
                self._tick()
            return
        rows = [i for i in range(self.list.count()) if self.list.item(i).data(ROLE) is not None]
        if not rows:
            return
        cur = self.list.currentRow()
        pos = rows.index(cur) if cur in rows else -1
        self.list.setCurrentRow(rows[(pos + d) % len(rows)])

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
        if self.mode == 1 and (ly or ry):           # program: the sticks move the offsets
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
        if self.prunner.running and pr is not self.prunner.program:
            self.stop()
        self.qs.setValue("pattern/last_program", pr.name)
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
        return self.runner.running or self.prunner.running

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.mode == 1:
            pr = self.current_program()
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
        if self.mode == 1:
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
        self.timer.stop()
        self.view.set_phase(None)
        self.pview.set_elapsed(None)
        self.pnow.setText("")
        self.play_btn.setIcon(theme.icon("play", theme.ON_ACCENT))
        self.play_btn.setText("Start")

    def _tick(self):
        now = self._now()
        if self.prunner.running or self.prunner.finished:
            self.prunner.tick(now)
            if self.prunner.finished:
                self.prunner.finished = False
                self.stop()
                self.pnow.setText("Ablauf fertig.")
                return
            pr = self.prunner.program
            el = self.prunner.elapsed(now)
            self.pview.set_elapsed(el)
            st = pr.steps[self.prunner.index]
            bpm, depth = self.prunner.current(now)
            self.pnow.setText(f"Schritt {self.prunner.index + 1}/{len(pr.steps)} · {st.pattern} · "
                              f"{round(bpm)} Hübe/min · Tiefe {round(depth)} % · "
                              f"{_fmt_min(el)} von {_fmt_min(pr.total_ms)}, noch {_fmt_min(pr.total_ms - el)}")
            runner = self.prunner.runner
        else:
            self.runner.tick(now)
            self.view.set_phase(self.runner.phase(now))
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
