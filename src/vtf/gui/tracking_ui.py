"""Tracking sections in the settings sidebar (mode "Tracking").

* Drawing areas in the video starts a section at that frame (after a scene
  cut: just mark again, the previous section ends there).
* "Ende hier" ends the section at the playhead.
* "Ganz drin" / "Ganz draußen" calibrate the depth of the section at the
  playhead: from then on the script uses the real distance between the areas.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..analysis import TrackSection, section_ranges
from ..tracking import MAX_AREAS


def fmt_ms(ms: float) -> str:
    s = max(0.0, ms) / 1000
    return f"{int(s // 60)}:{s % 60:04.1f}"


class TrackingBox(QWidget):
    analysisChanged = Signal()      # areas, start or end changed: needs a new analysis
    generatorChanged = Signal()     # calibration changed: the script is rebuilt at once
    seekRequested = Signal(int)     # ms
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sections: list[TrackSection] = []
        self.duration_ms = 0.0
        self.frame_ms = 1000 / 30
        self.current_ms: Callable[[], float] = lambda: 0.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(6)
        self.help = QLabel()
        self.help.setObjectName("caption")
        self.help.setWordWrap(True)
        lay.addWidget(self.help)
        self.list = QListWidget()
        self.list.setObjectName("playlist")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.list)
        grid = QGridLayout()
        grid.setSpacing(6)
        self.end_btn = QPushButton("Ende hier")
        self.del_btn = QPushButton("Löschen")
        self.in_btn = QPushButton("Ganz drin")
        self.out_btn = QPushButton("Ganz draußen")
        tips = ((self.end_btn, "Abschnitt an der Abspielposition beenden (E)"),
                (self.del_btn, "Abschnitt an der Abspielposition löschen"),
                (self.in_btn, "Kalibrieren: an dieser Stelle ist es ganz drin = Position 0 (D)"),
                (self.out_btn, "Kalibrieren: an dieser Stelle ist es ganz draußen = Position 100 (A)"))
        for b, tip in tips:
            b.setToolTip(tip)
            b.setStyleSheet("padding: 0 8px;")
        grid.addWidget(self.end_btn, 0, 0)
        grid.addWidget(self.del_btn, 0, 1)
        grid.addWidget(self.in_btn, 1, 0)
        grid.addWidget(self.out_btn, 1, 1)
        lay.addLayout(grid)

        self.end_btn.clicked.connect(lambda: self._say(self.set_end(self.current_ms())))
        self.del_btn.clicked.connect(lambda: self._say(self.delete_at(self.current_ms())))
        self.in_btn.clicked.connect(lambda: self._say(self.calibrate("in", self.current_ms())))
        self.out_btn.clicked.connect(lambda: self._say(self.calibrate("out", self.current_ms())))
        self.list.itemClicked.connect(self._clicked)
        self.refresh()

    # ------------------------------------------------------------ model
    def reset(self, duration_ms: float, fps: float) -> None:
        self.sections = []
        self.duration_ms = float(duration_ms)
        self.frame_ms = 1000 / max(fps, 1e-6)
        self.refresh()

    def _ranges(self):
        return section_ranges(self.sections, self.duration_ms or 1e12)

    def index_at(self, ms: float) -> int | None:
        """Section whose stretch contains ms."""
        return next((i for i, a, b in self._ranges() if a <= ms < b), None)

    def boxes_at(self, ms: float) -> list:
        i = self.index_at(ms)
        return list(self.sections[i].boxes) if i is not None else []

    def _say(self, msg: str | None) -> None:
        if msg:
            self.message.emit(msg)

    def add_box(self, box, ms: float) -> str:
        """An area was drawn on the frame at ms."""
        box = tuple(round(v, 4) for v in box)
        for i, s in enumerate(self.sections):
            if abs(s.start_ms - ms) <= self.frame_ms * 0.6:          # same frame: one more area
                boxes = (s.boxes + (box,))[-MAX_AREAS:]
                self.sections[i] = replace(s, boxes=boxes)
                self.refresh()
                self.analysisChanged.emit()
                return f"Abschnitt {self._number(i)}: {len(boxes)} Bereich{'e' if len(boxes) > 1 else ''}"
        cut = self.index_at(ms)
        end = None
        if cut is not None and self.sections[cut].end_ms is not None and self.sections[cut].end_ms > ms:
            # splitting a section with an end: the new part keeps that end
            end = self.sections[cut].end_ms
            self.sections[cut] = replace(self.sections[cut], end_ms=int(ms))
        self.sections.append(TrackSection(int(ms), (box,), end_ms=end))
        self.sections.sort(key=lambda s: s.start_ms)
        self.refresh()
        self.analysisChanged.emit()
        i = next(k for k, s in enumerate(self.sections) if s.start_ms == int(ms))
        return (f"Neuer Abschnitt {self._number(i)} ab {fmt_ms(ms)}"
                + (" – der vorige endet hier" if cut is not None else ""))

    def set_end(self, ms: float) -> str:
        cand = [i for i, s in enumerate(self.sections) if s.start_ms < ms]
        if not cand:
            return "Kein Abschnitt vor dieser Stelle – erst Bereiche markieren"
        i = max(cand, key=lambda k: self.sections[k].start_ms)
        self.sections[i] = replace(self.sections[i], end_ms=int(ms))
        self.refresh()
        self.analysisChanged.emit()
        return f"Abschnitt {self._number(i)} endet bei {fmt_ms(ms)}"

    def delete_at(self, ms: float) -> str:
        i = self.index_at(ms)
        if i is None:
            return "Hier ist kein Abschnitt"
        n = self._number(i)
        del self.sections[i]
        self.refresh()
        self.analysisChanged.emit()
        return f"Abschnitt {n} gelöscht"

    def calibrate(self, kind: str, ms: float) -> str:
        i = self.index_at(ms)
        if i is None:
            return "Kalibrieren geht nur innerhalb eines Abschnitts"
        key = "calib_in_ms" if kind == "in" else "calib_out_ms"
        self.sections[i] = replace(self.sections[i], **{key: int(ms)})
        self.refresh()
        self.generatorChanged.emit()
        s = self.sections[i]
        what = "ganz drin (0)" if kind == "in" else "ganz draußen (100)"
        rest = ("kalibriert – das Script nutzt jetzt die echte Tiefe" if s.calibrated else
                "jetzt noch „Ganz draußen“ setzen" if kind == "in" else "jetzt noch „Ganz drin“ setzen")
        return f"Abschnitt {self._number(i)}: {fmt_ms(ms)} = {what}, {rest}"

    def _number(self, i: int) -> int:
        return i + 1

    # ------------------------------------------------------------ view
    def refresh(self) -> None:
        self.list.clear()
        ranges = {i: (a, b) for i, a, b in self._ranges()}
        for i, s in enumerate(self.sections):
            a, b = ranges.get(i, (s.start_ms, s.end_ms or self.duration_ms))
            n = len(s.boxes)
            half = s.calib_in_ms is not None or s.calib_out_ms is not None
            cal = "kalibriert" if s.calibrated else ("halb kalibriert" if half else "relativ")
            item = QListWidgetItem(f"{i + 1}   {fmt_ms(a)} – {fmt_ms(b)}   ·  {n} Ber.  ·  {cal}")
            item.setData(Qt.UserRole, s.start_ms)
            tip = [f"Bereiche: {n}"]
            if s.calib_in_ms is not None:
                tip.append(f"ganz drin bei {fmt_ms(s.calib_in_ms)}")
            if s.calib_out_ms is not None:
                tip.append(f"ganz draußen bei {fmt_ms(s.calib_out_ms)}")
            item.setToolTip("\n".join(tip))
            self.list.addItem(item)
        has = bool(self.sections)
        self.list.setVisible(has)
        if has:          # as tall as the entries, at most four rows
            row = max(self.list.sizeHintForRow(0), 24)
            self.list.setFixedHeight(min(len(self.sections), 4) * (row + 2) + 6)
        for b in (self.end_btn, self.del_btn, self.in_btn, self.out_btn):
            b.setEnabled(has)
        if not has:
            self.help.setText("Im Video bis zu 3 Bereiche aufziehen, z.B. um die beiden Körperteile. "
                              "Das startet einen Abschnitt an dieser Stelle. Nach einem Szenenwechsel "
                              "einfach neu markieren. 2–3 Bereiche: Bereich 1 relativ zu den anderen, "
                              "die Kamera spielt dann keine Rolle.")
        else:
            self.help.setText("Klick auf einen Abschnitt springt zu seinem Start. Für echte Tiefe: zu einer "
                              "Stelle spulen, wo es ganz drin ist → „Ganz drin“, dann ganz draußen → "
                              "„Ganz draußen“.")

    def _clicked(self, item: QListWidgetItem) -> None:
        self.seekRequested.emit(int(item.data(Qt.UserRole)))
