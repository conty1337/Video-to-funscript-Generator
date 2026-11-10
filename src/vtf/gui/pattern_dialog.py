"""Pattern control: play stroke patterns on the device, build and save own patterns."""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QSize, Qt, QTimer, Signal
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import patterns as P
from ..device.sync import ScriptSync
from . import theme
from .pattern_view import PatternView, block_icon, pattern_icon

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


class PatternDialog(QDialog):
    """Library of patterns (presets and own ones) with pictures, live tempo and
    stroke range (kept), start/stop on the device."""

    TICK_MS = 20

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.qs = ctl.qs
        self.setWindowTitle("Muster-Steuerung")
        self.resize(940, 600)
        self.user: list[P.Pattern] = P.load_user()
        self.sync = ScriptSync(ctl.device, ctl.sync.settings)
        self.runner = P.PatternRunner(self.sync)
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
        t = QLabel("Muster")
        t.setObjectName("section")
        ll.addWidget(t)
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
        root.addWidget(left)

        # ---- preview and control
        right = QVBoxLayout()
        right.setContentsMargins(24, 16, 24, 20)
        right.setSpacing(10)
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
        self.lo = _pct_spin(int(self._setting("lo", 0)))
        self.hi = _pct_spin(int(self._setting("hi", 100)))
        form.addRow(self._label("Hub"), _hrow(self.lo, "bis", self.hi, stretch=True))
        right.addLayout(form)
        cap = QLabel("Tempo und Hub bleiben gespeichert und gelten für alle Muster. Sie lassen sich "
                     "auch während des Abspielens ändern. Grenzen und Tempo-Limit aus „Gerät“ gelten zusätzlich.")
        cap.setObjectName("caption")
        cap.setWordWrap(True)
        right.addWidget(cap)

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
        root.addLayout(right, 1)

        self.bpm.valueChanged.connect(self._params_changed)
        self.lo.valueChanged.connect(self._params_changed)
        self.hi.valueChanged.connect(self._params_changed)
        self._fill()
        self._select_name(str(self._setting("last", P.BUILTIN[0].name)))
        self._params_changed()
        self._device_changed()

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
        self.view.set_pattern(p, self.bpm.value(), self.lo.value(), self.hi.value())
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

    def _params_changed(self, *_):
        bpm, lo, hi = self.bpm.value(), self.lo.value(), self.hi.value()
        self.bpm_val.setText(f"{bpm} Hübe/min")
        for k, v in (("bpm", bpm), ("lo", lo), ("hi", hi)):
            self.qs.setValue(f"pattern/{k}", v)
        self.view.set_pattern(self.current(), bpm, lo, hi)
        self._update_info()
        self.runner.configure(self._now(), bpm=bpm, lo=lo, hi=hi)

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
        dlg = PatternEditor(pattern, self.bpm.value(), self.lo.value(), self.hi.value(), self)
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

    # ------------------------------------------------------------ playback
    def _device_changed(self):
        dev = self.ctl.device
        if dev is not self.sync.device:
            was = self.runner.running
            self.sync.set_device(dev)
            if was:
                self.runner.start(self._now())
        if dev is not None and dev.connected:
            self.device_label.setText(f"Gerät: {dev.name}")
        else:
            self.device_label.setText("Kein Gerät verbunden – Start zeigt nur die Vorschau.")

    @property
    def running(self) -> bool:
        return self.runner.running

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        p = self.current()
        if p is None:
            return
        win = self.parent()
        if win is not None and hasattr(win, "pause") and getattr(win, "timer", None) is not None \
                and win.timer.isActive():
            win.pause()                    # the video and a pattern never drive the device together
        self.runner.configure(self._now(), pattern=p, bpm=self.bpm.value(), lo=self.lo.value(),
                              hi=self.hi.value())
        self.runner.start(self._now())
        self.timer.start()
        self.play_btn.setIcon(theme.icon("pause", theme.ON_ACCENT))
        self.play_btn.setText("Stopp")

    def stop(self):
        self.runner.stop()
        self.timer.stop()
        self.view.set_phase(None)
        self.play_btn.setIcon(theme.icon("play", theme.ON_ACCENT))
        self.play_btn.setText("Start")

    def _tick(self):
        now = self._now()
        self.runner.tick(now)
        self.view.set_phase(self.runner.phase(now))
        win = self.parent()
        if win is not None and hasattr(win, "rail"):
            pos = self.ctl.device_position()
            win.rail.set_device_position(pos if pos is not None else self.runner.position(now))

    def closeEvent(self, ev):
        self.stop()
        super().closeEvent(ev)

    def reject(self):
        self.stop()
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
        cap = QLabel("Tempo 1× = ein Hub im eingestellten Tempo. Tiefe in Prozent vom Hub, "
                     "den du in der Steuerung einstellst (0 = unten, 100 = oben).")
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
