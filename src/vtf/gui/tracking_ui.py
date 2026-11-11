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
    QCheckBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
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
    suggestRequested = Signal()     # "KI-Vorschlag": find body parts in the current frame

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
        self.clear_btn = QPushButton("Alle löschen")
        tips = ((self.end_btn, "Abschnitt an der Abspielposition beenden (E)"),
                (self.clear_btn, "Alle Abschnitte und Kästchen löschen und von vorne anfangen"),
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
        grid.addWidget(self.clear_btn, 2, 0, 1, 2)
        lay.addLayout(grid)
        self.ai_btn = QPushButton("KI-Vorschlag")
        self.ai_btn.setToolTip("Körperteile im aktuellen Bild erkennen (Becken, Mund, Hände, …) und "
                               "als Kästchen vorschlagen (K). Beim ersten Mal werden die KI-Modelle "
                               "geladen (etwa 330 MB, einmalig)")
        self.ai_btn.setStyleSheet("padding: 0 8px;")
        self.ai_check = QCheckBox("KI-Kontrolle beim Verfolgen")
        self.ai_check.setToolTip(
            "Die Körpererkennung prüft während der Analyse etwa jede Sekunde (bei Unsicherheit "
            "öfter), ob jedes Kästchen noch auf seinem Körperteil sitzt. Rutscht es weg oder liegt "
            "eine Hand darüber, wird es zurückgeholt bzw. die Stelle überbrückt. Langsamer; "
            "braucht die KI-Modelle")
        lay.addWidget(self.ai_btn)
        lay.addWidget(self.ai_check)
        self.ai_btn.clicked.connect(self.suggestRequested)
        self.ai_check.toggled.connect(self.analysisChanged)

        # bound methods, no lambdas: a lambda holding self makes a reference cycle that
        # the garbage collector may break at any time - also after the QApplication is gone
        self.end_btn.clicked.connect(self._end_clicked)
        self.del_btn.clicked.connect(self._del_clicked)
        self.in_btn.clicked.connect(self._in_clicked)
        self.out_btn.clicked.connect(self._out_clicked)
        self.clear_btn.clicked.connect(self._clear_clicked)
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
        """Marked areas of the section at ms; at a correction, the corrected ones."""
        i = self.index_at(ms)
        if i is None:
            return []
        s = self.sections[i]
        boxes = list(s.boxes)
        for kms, area, kbox in s.keys:
            if abs(kms - ms) <= self.frame_ms * 0.6 and area < len(boxes):
                boxes[area] = kbox
        return boxes

    def _end_clicked(self):
        self._say(self.set_end(self.current_ms()))

    def _del_clicked(self):
        self._say(self.delete_at(self.current_ms()))

    def _in_clicked(self):
        self._say(self.calibrate("in", self.current_ms()))

    def _out_clicked(self):
        self._say(self.calibrate("out", self.current_ms()))

    def _clear_clicked(self):
        n = len(self.sections)
        if not n:
            return
        ans = QMessageBox.question(
            self, "Alle löschen",
            f"Alle {n} Abschnitt{'e' if n > 1 else ''} mit ihren Kästchen, Korrekturen und der "
            "Kalibrierung löschen?")
        if ans == QMessageBox.Yes:
            self._say(self.clear_all())

    def _say(self, msg: str | None) -> None:
        if msg:
            self.message.emit(msg)

    def add_box(self, box, ms: float, shown=(), keep_first: bool = False) -> str:
        """An area was drawn on the frame at ms. `shown` = the areas visible there
        (tracked positions after an analysis): drawing over one of them inside a
        section corrects that area instead of starting a new section.
        keep_first (boxes from the KI): area 1 is never replaced or corrected, a full
        section swaps its oldest area among 2 and 3."""
        box = tuple(round(v, 4) for v in box)
        cur = self.index_at(ms)
        if cur is not None and abs(self.sections[cur].start_ms - ms) > self.frame_ms * 0.6:
            if keep_first and shown:
                shown = ((-9.0, -9.0, 0.0, 0.0),) + tuple(shown[1:])    # area 1 can't be hit
            hit = _overlapping(box, shown)
            if hit is not None:
                return self._add_key(cur, ms, hit, box, shown[hit])
        for i, s in enumerate(self.sections):
            if abs(s.start_ms - ms) <= self.frame_ms * 0.6:          # same frame: one more area
                if keep_first and s.boxes:
                    boxes = s.boxes[:1] + (s.boxes[1:] + (box,))[-(MAX_AREAS - 1):]
                else:
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
        first = ()
        if keep_first and cut is not None and shown and shown[0][0] > -1:
            # a KI box inside a section: area 1 goes on in the new part from where it is now
            first = (tuple(round(float(v), 4) for v in shown[0][:5]),)
        self.sections.append(TrackSection(int(ms), first + (box,), end_ms=end))
        self.sections.sort(key=lambda s: s.start_ms)
        self.refresh()
        self.analysisChanged.emit()
        i = next(k for k, s in enumerate(self.sections) if s.start_ms == int(ms))
        return (f"Neuer Abschnitt {self._number(i)} ab {fmt_ms(ms)}"
                + (" – der vorige endet hier" if cut is not None else ""))

    def _add_key(self, i: int, ms: float, area: int, box, shown_box) -> str:
        s = self.sections[i]
        angle = shown_box[4] if len(shown_box) > 4 else (s.boxes[area][4] if len(s.boxes[area]) > 4 else 0.0)
        key_box = tuple(box[:4]) + (round(float(angle), 1),)
        keys = tuple(k for k in s.keys if not (abs(k[0] - ms) <= self.frame_ms * 0.6 and k[1] == area))
        keys = tuple(sorted(keys + ((int(ms), area, key_box),)))
        self.sections[i] = replace(s, keys=keys)
        self.refresh()
        self.analysisChanged.emit()
        return (f"Bereich {area + 1} bei {fmt_ms(ms)} nachjustiert – nach neuer Analyse folgt er von hier "
                "aus, die Abweichung davor wird ausgeglichen")

    def _edit_marked(self, ms: float, area: int, fn) -> bool:
        """Apply fn(box) -> box to marked area `area` at a section start or a correction at ms."""
        for i, s in enumerate(self.sections):
            if abs(s.start_ms - ms) <= self.frame_ms * 0.6 and area < len(s.boxes):
                boxes = list(s.boxes)
                boxes[area] = fn(boxes[area])
                self.sections[i] = replace(s, boxes=tuple(boxes))
                break
            k = next((k for k, (kms, karea, _b) in enumerate(s.keys)
                      if abs(kms - ms) <= self.frame_ms * 0.6 and karea == area), None)
            if k is not None:
                keys = list(s.keys)
                kms, karea, kbox = keys[k]
                keys[k] = (kms, karea, fn(kbox))
                self.sections[i] = replace(s, keys=tuple(keys))
                break
        else:
            return False
        self.refresh()
        self.analysisChanged.emit()
        return True

    def correct_area(self, ms: float, area: int, box) -> str:
        """An area was grabbed at ms (inside a section, not at its start) and put
        where it really is: a correction there."""
        cur = self.index_at(ms)
        if cur is None:
            return "Hier ist kein Abschnitt"
        s = self.sections[cur]
        if abs(s.start_ms - ms) <= self.frame_ms * 0.6:
            return self.update_box(ms, area, box)
        if area >= len(s.boxes):
            return "Diesen Bereich gibt es in dem Abschnitt nicht"
        box = tuple(round(float(v), 4) for v in box[:4]) + ((float(box[4]) if len(box) > 4 else 0.0),)
        return self._add_key(cur, ms, area, box, box)

    def rotate_box(self, ms: float, area: int, angle: float) -> str:
        """Turn a marked area (at a section start or at a correction)."""
        if self._edit_marked(ms, area, lambda b: tuple(b[:4]) + (round(angle, 1),)):
            return f"Bereich {area + 1} gedreht: {angle:.0f}°"
        return "Drehen geht an der Startstelle eines Abschnitts (Klick in der Liste)"

    def update_box(self, ms: float, area: int, box) -> str:
        """Move/resize a marked area (at a section start or at a correction)."""
        x, y, w, h = (round(float(v), 4) for v in box[:4])

        def fn(old):
            angle = box[4] if len(box) > 4 else (old[4] if len(old) > 4 else 0.0)
            return (x, y, w, h, round(float(angle), 1))

        if self._edit_marked(ms, area, fn):
            return f"Bereich {area + 1} angepasst"
        return "Ändern geht an der Startstelle eines Abschnitts (Klick in der Liste)"

    def clear_all(self) -> str:
        if not self.sections:
            return "Keine Abschnitte"
        n = len(self.sections)
        self.sections = []
        self.refresh()
        self.analysisChanged.emit()
        return f"{n} Abschnitt{'e' if n > 1 else ''} gelöscht – Bereiche neu aufziehen"

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

    def move_edge(self, i: int, which: str, ms: float) -> str:
        """Start or end of section i dragged in the curve to ms."""
        if not 0 <= i < len(self.sections):
            return ""
        s = self.sections[i]
        ms = int(round(ms))
        gap = int(self.frame_ms * 2)
        ranges = {k: (a, b) for k, a, b in self._ranges()}
        n = self._number(i)
        if which == "end":
            ms = max(s.start_ms + gap, min(ms, int(self.duration_ms or ms)))
            self.sections[i] = replace(s, end_ms=ms)
            msg = f"Abschnitt {n} endet bei {fmt_ms(ms)}"
        else:
            prev = max((o.start_ms for o in self.sections if o.start_ms < s.start_ms), default=None)
            lo = 0 if prev is None else prev + gap
            hi = int(ranges.get(i, (0, s.end_ms or self.duration_ms))[1]) - gap
            ms = max(lo, min(ms, hi))
            keys = tuple(k for k in s.keys if k[0] > ms + gap)
            end = s.end_ms if s.end_ms is None or s.end_ms > ms + gap else None

            def inside(c):
                return c if c is not None and ms <= c < (end or hi + gap) else None

            self.sections[i] = replace(s, start_ms=ms, keys=keys, end_ms=end,
                                       calib_in_ms=inside(s.calib_in_ms), calib_out_ms=inside(s.calib_out_ms))
            self.sections.sort(key=lambda x: x.start_ms)
            msg = (f"Abschnitt {n} beginnt jetzt bei {fmt_ms(ms)} – die Kästchen gelten ab hier, "
                   "bitte dort prüfen")
        self.refresh()
        self.analysisChanged.emit()
        return msg

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
            fix = f"  ·  {len(s.keys)}× korr." if s.keys else ""
            item = QListWidgetItem(f"{i + 1}   {fmt_ms(a)} – {fmt_ms(b)}   ·  {n} Ber.  ·  {cal}{fix}")
            item.setData(Qt.UserRole, s.start_ms)
            tip = [f"Bereiche: {n}"]
            if s.keys:
                tip.append(f"Nachjustiert: {len(s.keys)}× (" + ", ".join(fmt_ms(k[0]) for k in s.keys) + ")")
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
        for b in (self.end_btn, self.del_btn, self.in_btn, self.out_btn, self.clear_btn):
            b.setEnabled(has)
        if not has:
            self.help.setText("Im Video bis zu 3 Bereiche aufziehen, z.B. um die beiden Körperteile. "
                              "Das startet einen Abschnitt an dieser Stelle. Kästchen innen ziehen = "
                              "verschieben, an einer Ecke = Größe, am runden Griff oder mit dem Mausrad "
                              "drehen. Nach einem Szenenwechsel einfach neu "
                              "markieren. 2–3 Bereiche: Bereich 1 relativ zu den anderen, die Kamera "
                              "spielt dann keine Rolle.")
        else:
            self.help.setText("Klick auf einen Abschnitt springt zu seinem Start, dort lassen sich die "
                              "Kästchen verschieben, vergrößern und drehen. Wandert ein Kästchen ab: "
                              "an der Stelle einfach anpacken und richtig hinschieben (nachjustieren). Für echte "
                              "Tiefe: ganz drin → „Ganz drin“, ganz draußen → „Ganz draußen“.")

    def _clicked(self, item: QListWidgetItem) -> None:
        self.seekRequested.emit(int(item.data(Qt.UserRole)))


def _overlapping(box, shown) -> int | None:
    """Index of the shown area the new box lies over (its centre inside, or a clear overlap)."""
    bx, by, bw, bh = box[:4]
    cx, cy = bx + bw / 2, by + bh / 2
    best, best_iou = None, 0.15
    for j, sb in enumerate(shown or ()):
        x, y, w, h = sb[:4]
        if x <= cx <= x + w and y <= cy <= y + h:
            return j
        ix = max(0.0, min(bx + bw, x + w) - max(bx, x))
        iy = max(0.0, min(by + bh, y + h) - max(by, y))
        inter = ix * iy
        union = bw * bh + w * h - inter
        iou = inter / union if union > 0 else 0.0
        if iou > best_iou:
            best, best_iou = j, iou
    return best
