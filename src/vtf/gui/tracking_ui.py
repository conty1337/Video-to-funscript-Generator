"""Tracking sections in the settings sidebar (mode "Tracking").

* Drawing areas in the video starts a section at that frame (after a scene
  cut: just mark again, the previous section ends there).
* "Ende hier" ends the section at the playhead.
* "Ganz drin" / "Ganz draußen" calibrate the depth of the section at the
  playhead: from then on the script uses the real distance between the areas.
* Roles: area 1 is the toy / machine (what moves), the others are the body it
  is measured against. Choosing a role reorders the areas.
* "Umranden": areas are drawn as an outline around the thing instead of a
  rectangle. "Hub-Richtung": an arrow along the stroke, head = out.
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
from ..i18n import plural, tr
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
    drawModeChanged = Signal(str)   # "box", "outline" or "stroke": what a drag in the video draws
    autoRequested = Signal()        # "Automatisch finden": toy, reference and arrow by themselves

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
        self.end_btn = QPushButton(tr("Ende hier"))
        self.del_btn = QPushButton(tr("Löschen"))
        self.in_btn = QPushButton(tr("Ganz drin"))
        self.out_btn = QPushButton(tr("Ganz draußen"))
        self.clear_btn = QPushButton(tr("Alle löschen"))
        tips = ((self.end_btn, tr("Abschnitt an der Abspielposition beenden (E)")),
                (self.clear_btn, tr("Alle Abschnitte und Kästchen löschen und von vorne anfangen")),
                (self.del_btn, tr("Abschnitt an der Abspielposition löschen")),
                (self.in_btn, tr("Kalibrieren: an dieser Stelle ist es ganz drin = Position 0 (D)")),
                (self.out_btn, tr("Kalibrieren: an dieser Stelle ist es ganz draußen = Position 100 (A)")))
        for b, tip in tips:
            b.setToolTip(tip)
            b.setStyleSheet("padding: 0 8px;")
        grid.addWidget(self.end_btn, 0, 0)
        grid.addWidget(self.del_btn, 0, 1)
        grid.addWidget(self.in_btn, 1, 0)
        grid.addWidget(self.out_btn, 1, 1)
        grid.addWidget(self.clear_btn, 2, 0, 1, 2)
        lay.addLayout(grid)
        draw = QGridLayout()
        draw.setSpacing(6)
        self.outline_btn = QPushButton(tr("Umranden"))
        self.outline_btn.setCheckable(True)
        self.outline_btn.setToolTip(
            tr("Statt eines Rechtecks mit gedrückter Maustaste eng um das Toy bzw. den Körperteil fahren (U). Verfolgt wird nur, was innen liegt – ein paar Pixel Abstand zum Rand (im Bild rot schraffiert), damit nichts daneben (Finger, Bettdecke) mitzieht"))
        self.stroke_btn = QPushButton(tr("Hub-Richtung"))
        self.stroke_btn.setCheckable(True)
        self.stroke_btn.setToolTip(
            tr("Einen Pfeil im Bild entlang der Stoßrichtung ziehen, die Spitze zeigt nach „raus“ (Position 100). Gemessen wird dann nur entlang des Pfeils, seitliches Wackeln zählt nicht. Gilt für den Abschnitt an der Abspielposition (H)"))
        self.stroke_del = QPushButton(tr("Richtung weg"))
        self.stroke_del.setToolTip(tr("Hub-Richtung des Abschnitts an der Abspielposition entfernen"))
        for b in (self.outline_btn, self.stroke_btn, self.stroke_del):
            b.setStyleSheet("padding: 0 8px;")
        draw.addWidget(self.outline_btn, 0, 0, 1, 2)
        draw.addWidget(self.stroke_btn, 1, 0)
        draw.addWidget(self.stroke_del, 1, 1)
        lay.addLayout(draw)
        self.outline_btn.toggled.connect(self._draw_mode_clicked)
        self.stroke_btn.toggled.connect(self._draw_mode_clicked)
        self.stroke_del.clicked.connect(self._stroke_del_clicked)
        self.auto_btn = QPushButton(tr("Automatisch finden"))
        self.auto_btn.setToolTip(
            tr("Sucht ab hier ein paar Sekunden lang, was sich im Rhythmus bewegt: das wird das Toy (umrandet), eine Stelle mit Muster daneben der Bezug, dazu die Hub-Richtung (F). Danach kurz prüfen und bei Bedarf anpassen"))
        self.auto_btn.setStyleSheet("padding: 0 8px;")
        lay.addWidget(self.auto_btn)
        self.auto_btn.clicked.connect(self.autoRequested)
        self.ai_btn = QPushButton(tr("KI-Vorschlag"))
        self.ai_btn.setToolTip(tr("Körperteile im aktuellen Bild erkennen (Becken, Mund, Hände, …) und als Kästchen vorschlagen (K). Beim ersten Mal werden die KI-Modelle geladen (etwa 330 MB, einmalig)"))
        self.ai_btn.setStyleSheet("padding: 0 8px;")
        self.ai_check = QCheckBox(tr("KI-Kontrolle beim Verfolgen"))
        self.ai_check.setToolTip(
            tr("Die Körpererkennung prüft während der Analyse etwa jede Sekunde (bei Unsicherheit öfter), ob jedes Kästchen noch auf seinem Körperteil sitzt. Rutscht es weg oder liegt eine Hand darüber, wird es zurückgeholt bzw. die Stelle überbrückt. Langsamer; braucht die KI-Modelle"))
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

    def outlines_at(self, ms: float) -> list:
        """Outline per area of the section at ms (() = rectangle)."""
        i = self.index_at(ms)
        if i is None:
            return []
        s = self.sections[i]
        return [s.outline(a) for a in range(len(s.boxes))]

    def stroke_at(self, ms: float):
        i = self.index_at(ms)
        return None if i is None else self.sections[i].stroke

    def draw_mode(self) -> str:
        if self.stroke_btn.isChecked():
            return "stroke"
        return "outline" if self.outline_btn.isChecked() else "box"

    def set_draw_mode(self, mode: str) -> None:
        """Set the buttons (one of "box", "outline", "stroke"); emits drawModeChanged."""
        for b, on in ((self.stroke_btn, mode == "stroke"), (self.outline_btn, mode == "outline")):
            b.blockSignals(True)
            b.setChecked(on)
            b.blockSignals(False)
        self.drawModeChanged.emit(self.draw_mode())

    def _draw_mode_clicked(self, on: bool):
        if on:              # the two exclude each other
            other = self.outline_btn if self.sender() is self.stroke_btn else self.stroke_btn
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
        self.drawModeChanged.emit(self.draw_mode())

    def _stroke_del_clicked(self):
        self._say(self.set_stroke(self.current_ms(), None))

    def set_stroke(self, ms: float, line) -> str:
        """Stroke arrow (x0, y0, x1, y1 normalized, head = out) for the section at ms; None removes it."""
        i = self.index_at(ms)
        if i is None:
            return tr("Hub-Richtung geht nur innerhalb eines Abschnitts – erst Bereiche markieren")
        if line is None and self.sections[i].stroke is None:
            return tr("Abschnitt {0} hat keine Hub-Richtung", self._number(i))
        line = None if line is None else tuple(round(float(v), 4) for v in line[:4])
        self.sections[i] = replace(self.sections[i], stroke=line)
        self.refresh()
        self.generatorChanged.emit()
        if line is None:
            return tr("Abschnitt {0}: Hub-Richtung entfernt", self._number(i))
        return tr("Abschnitt {0}: Hub wird entlang des Pfeils gemessen, Spitze = raus", self._number(i))

    def _reorder(self, i: int, order: list[int]) -> None:
        """Areas of section i in a new order (new index -> old index); corrections follow."""
        s = self.sections[i]
        lines = [s.outline(a) for a in range(len(s.boxes))]
        new_of = {old: new for new, old in enumerate(order)}
        keys = tuple(sorted((ms, new_of[a], b) for ms, a, b in s.keys if a in new_of))
        self.sections[i] = replace(s, boxes=tuple(s.boxes[o] for o in order), keys=keys,
                                   outlines=_trim(tuple(lines[o] for o in order)))

    def set_role(self, ms: float, area: int, role: str) -> str:
        """role "toy" (what moves, becomes area 1) or "body" (what it is measured against)."""
        i = self.index_at(ms)
        if i is None or area >= len(self.sections[i].boxes):
            return tr("Hier ist kein Bereich")
        n = len(self.sections[i].boxes)
        if role == "toy":
            if area == 0:
                return tr("Bereich 1 ist schon das Toy / der bewegte Teil")
            order = [area] + [a for a in range(n) if a != area]
        else:
            if area != 0:
                return tr("Bereich {0} ist schon Bezug (Körper)", area + 1)
            if n < 2:
                return tr("Für einen Bezug braucht es noch einen Bereich als Toy – erst einen zweiten aufziehen")
            order = [1, 0] + list(range(2, n))
        self._reorder(i, order)
        self.refresh()
        self.analysisChanged.emit()
        return (tr("Rollen geändert: Bereich 1 = Toy / bewegt, ")
                + (tr("Bereich 2 = Bezug") if n == 2 else tr("Bereiche 2–3 = Bezug")) + tr(" – die Nummern haben sich geändert"))

    def remove_area(self, ms: float, area: int) -> str:
        """Remove one area from the section at ms (with its corrections)."""
        i = self.index_at(ms)
        if i is None or area >= len(self.sections[i].boxes):
            return tr("Hier ist kein Bereich")
        if len(self.sections[i].boxes) == 1:
            return self.delete_at(ms)
        n = len(self.sections[i].boxes)
        self._reorder(i, [a for a in range(n) if a != area])
        self.refresh()
        self.analysisChanged.emit()
        return tr("Bereich {0} entfernt", area + 1)

    def clear_outline(self, ms: float, area: int) -> str:
        i = self.index_at(ms)
        if i is None or not self.sections[i].outline(area):
            return tr("Dieser Bereich hat keine Umrandung")
        s = self.sections[i]
        lines = [s.outline(a) for a in range(len(s.boxes))]
        lines[area] = ()
        self.sections[i] = replace(s, outlines=_trim(tuple(lines)))
        self.refresh()
        self.analysisChanged.emit()
        return tr("Bereich {0} ist wieder ein Rechteck", area + 1)

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
            self, tr("Alle löschen"),
            plural(n, "Den Abschnitt mit seinen Kästchen, Korrekturen und der Kalibrierung löschen?", "Alle {0} Abschnitte mit ihren Kästchen, Korrekturen und der Kalibrierung löschen?"))
        if ans == QMessageBox.Yes:
            self._say(self.clear_all())

    def _say(self, msg: str | None) -> None:
        if msg:
            self.message.emit(msg)

    def add_box(self, box, ms: float, shown=(), keep_first: bool = False, outline=()) -> str:
        """An area was drawn on the frame at ms. `shown` = the areas visible there
        (tracked positions after an analysis): drawing over one of them inside a
        section corrects that area instead of starting a new section.
        keep_first (boxes from the KI): area 1 is never replaced or corrected, a full
        section swaps its oldest area among 2 and 3.
        outline: polygon in the box's own coordinates (see TrackSection), () = rectangle."""
        box = tuple(round(v, 4) for v in box)
        outline = tuple((round(float(u), 4), round(float(v), 4)) for u, v in outline)
        cur = self.index_at(ms)
        if cur is not None and abs(self.sections[cur].start_ms - ms) > self.frame_ms * 0.6:
            if keep_first and shown:
                shown = ((-9.0, -9.0, 0.0, 0.0),) + tuple(shown[1:])    # area 1 can't be hit
            hit = _overlapping(box, shown)
            if hit is not None:
                return self._add_key(cur, ms, hit, box, shown[hit])
        for i, s in enumerate(self.sections):
            if abs(s.start_ms - ms) <= self.frame_ms * 0.6:          # same frame: one more area
                lines = tuple(s.outline(a) for a in range(len(s.boxes))) + (outline,)
                if keep_first and s.boxes:
                    boxes = s.boxes[:1] + (s.boxes[1:] + (box,))[-(MAX_AREAS - 1):]
                    lines = lines[:1] + lines[1:][-(MAX_AREAS - 1):]
                else:
                    boxes = (s.boxes + (box,))[-MAX_AREAS:]
                    lines = lines[-MAX_AREAS:]
                # a full section drops its oldest area: its corrections go, the others move up
                dropped = len(s.boxes) + 1 - len(boxes)
                keys = s.keys
                if dropped:
                    first = 1 if keep_first else 0
                    keys = tuple((kms, a - (a > first), kb) for kms, a, kb in s.keys if a != first)
                self.sections[i] = replace(s, boxes=boxes, outlines=_trim(lines), keys=keys)
                self.refresh()
                self.analysisChanged.emit()
                return plural(len(boxes), "Abschnitt {1}: {0} Bereich", "Abschnitt {1}: {0} Bereiche", self._number(i))
        cut = self.index_at(ms)
        end = None
        if cut is not None and self.sections[cut].end_ms is not None and self.sections[cut].end_ms > ms:
            # splitting a section with an end: the new part keeps that end
            end = self.sections[cut].end_ms
            self.sections[cut] = replace(self.sections[cut], end_ms=int(ms))
        first = ()
        first_line = ()
        if keep_first and cut is not None and shown and shown[0][0] > -1:
            # a KI box inside a section: area 1 goes on in the new part from where it is now
            first = (tuple(round(float(v), 4) for v in shown[0][:5]),)
            first_line = (self.sections[cut].outline(0),)
        self.sections.append(TrackSection(int(ms), first + (box,), end_ms=end,
                                          outlines=_trim(first_line + (outline,)),
                                          stroke=self.sections[cut].stroke if cut is not None else None))
        self.sections.sort(key=lambda s: s.start_ms)
        self.refresh()
        self.analysisChanged.emit()
        i = next(k for k, s in enumerate(self.sections) if s.start_ms == int(ms))
        return (tr("Neuer Abschnitt {0} ab {1}", self._number(i), fmt_ms(ms))
                + (tr(" – der vorige endet hier") if cut is not None else ""))

    def _add_key(self, i: int, ms: float, area: int, box, shown_box) -> str:
        s = self.sections[i]
        angle = shown_box[4] if len(shown_box) > 4 else (s.boxes[area][4] if len(s.boxes[area]) > 4 else 0.0)
        key_box = tuple(box[:4]) + (round(float(angle), 1),)
        keys = tuple(k for k in s.keys if not (abs(k[0] - ms) <= self.frame_ms * 0.6 and k[1] == area))
        keys = tuple(sorted(keys + ((int(ms), area, key_box),)))
        self.sections[i] = replace(s, keys=keys)
        self.refresh()
        self.analysisChanged.emit()
        return (tr("Bereich {0} bei {1} nachjustiert – nach neuer Analyse folgt er von hier aus, die Abweichung davor wird ausgeglichen", area + 1, fmt_ms(ms)))

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
            return tr("Hier ist kein Abschnitt")
        s = self.sections[cur]
        if abs(s.start_ms - ms) <= self.frame_ms * 0.6:
            return self.update_box(ms, area, box)
        if area >= len(s.boxes):
            return tr("Diesen Bereich gibt es in dem Abschnitt nicht")
        box = tuple(round(float(v), 4) for v in box[:4]) + ((float(box[4]) if len(box) > 4 else 0.0),)
        return self._add_key(cur, ms, area, box, box)

    def rotate_box(self, ms: float, area: int, angle: float) -> str:
        """Turn a marked area (at a section start or at a correction)."""
        if self._edit_marked(ms, area, lambda b: tuple(b[:4]) + (round(angle, 1),)):
            return tr("Bereich {0} gedreht: {1:.0f}°", area + 1, angle)
        return tr("Drehen geht an der Startstelle eines Abschnitts (Klick in der Liste)")

    def update_box(self, ms: float, area: int, box) -> str:
        """Move/resize a marked area (at a section start or at a correction)."""
        x, y, w, h = (round(float(v), 4) for v in box[:4])

        def fn(old):
            angle = box[4] if len(box) > 4 else (old[4] if len(old) > 4 else 0.0)
            return (x, y, w, h, round(float(angle), 1))

        if self._edit_marked(ms, area, fn):
            return tr("Bereich {0} angepasst", area + 1)
        return tr("Ändern geht an der Startstelle eines Abschnitts (Klick in der Liste)")

    def clear_all(self) -> str:
        if not self.sections:
            return tr("Keine Abschnitte")
        n = len(self.sections)
        self.sections = []
        self.refresh()
        self.analysisChanged.emit()
        return plural(n, "{0} Abschnitt gelöscht – Bereiche neu aufziehen", "{0} Abschnitte gelöscht – Bereiche neu aufziehen")

    def set_end(self, ms: float) -> str:
        cand = [i for i, s in enumerate(self.sections) if s.start_ms < ms]
        if not cand:
            return tr("Kein Abschnitt vor dieser Stelle – erst Bereiche markieren")
        i = max(cand, key=lambda k: self.sections[k].start_ms)
        self.sections[i] = replace(self.sections[i], end_ms=int(ms))
        self.refresh()
        self.analysisChanged.emit()
        return tr("Abschnitt {0} endet bei {1}", self._number(i), fmt_ms(ms))

    def delete_at(self, ms: float) -> str:
        i = self.index_at(ms)
        if i is None:
            return tr("Hier ist kein Abschnitt")
        n = self._number(i)
        del self.sections[i]
        self.refresh()
        self.analysisChanged.emit()
        return tr("Abschnitt {0} gelöscht", n)

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
            msg = tr("Abschnitt {0} endet bei {1}", n, fmt_ms(ms))
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
            msg = (tr("Abschnitt {0} beginnt jetzt bei {1} – die Kästchen gelten ab hier, bitte dort prüfen", n, fmt_ms(ms)))
        self.refresh()
        self.analysisChanged.emit()
        return msg

    def calibrate(self, kind: str, ms: float) -> str:
        i = self.index_at(ms)
        if i is None:
            return tr("Kalibrieren geht nur innerhalb eines Abschnitts")
        key = "calib_in_ms" if kind == "in" else "calib_out_ms"
        self.sections[i] = replace(self.sections[i], **{key: int(ms)})
        self.refresh()
        self.generatorChanged.emit()
        s = self.sections[i]
        what = tr("ganz drin (0)") if kind == "in" else tr("ganz draußen (100)")
        rest = (tr("kalibriert – das Script nutzt jetzt die echte Tiefe") if s.calibrated else
                tr("jetzt noch „Ganz draußen“ setzen") if kind == "in" else tr("jetzt noch „Ganz drin“ setzen"))
        return tr("Abschnitt {0}: {1} = {2}, {3}", self._number(i), fmt_ms(ms), what, rest)

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
            cal = tr("kalibriert") if s.calibrated else (tr("halb kalibriert") if half else tr("relativ"))
            fix = tr("  ·  {0}× korr.", len(s.keys)) if s.keys else ""
            item = QListWidgetItem(tr("{0}   {1} – {2}   ·  {3}  ·  {4}{5}", i + 1, fmt_ms(a), fmt_ms(b),
                                      plural(n, "1 Ber.", "{0} Ber."), cal, fix))
            item.setData(Qt.UserRole, s.start_ms)
            tip = [tr("Bereiche: {0} (1 = Toy / bewegt", n) + (tr(", 2–3 = Bezug)") if n > 1 else ")")]
            lined = [str(a + 1) for a in range(n) if s.outline(a)]
            if lined:
                tip.append(tr("Umrandet: Bereich ") + ", ".join(lined))
            if s.stroke:
                tip.append(tr("Hub-Richtung: Pfeil gesetzt"))
            if s.keys:
                tip.append(tr("Nachjustiert: {0}× (", len(s.keys)) + ", ".join(fmt_ms(k[0]) for k in s.keys) + ")")
            if s.calib_in_ms is not None:
                tip.append(tr("ganz drin bei {0}", fmt_ms(s.calib_in_ms)))
            if s.calib_out_ms is not None:
                tip.append(tr("ganz draußen bei {0}", fmt_ms(s.calib_out_ms)))
            item.setToolTip("\n".join(tip))
            self.list.addItem(item)
        has = bool(self.sections)
        self.list.setVisible(has)
        if has:          # as tall as the entries, at most four rows
            row = max(self.list.sizeHintForRow(0), 24)
            self.list.setFixedHeight(min(len(self.sections), 4) * (row + 2) + 6)
        for b in (self.end_btn, self.del_btn, self.in_btn, self.out_btn, self.clear_btn, self.stroke_del):
            b.setEnabled(has)
        if not has:
            self.help.setText(tr("Im Video bis zu 3 Bereiche aufziehen (oder mit „Umranden“ eng drum herum fahren). Der erste ist das Toy / die Maschine, weitere sind der Bezug (Körper, bewegt sich wenig) – gemessen wird das Toy relativ dazu, die Kamera spielt dann keine Rolle. Rechtsklick auf ein Kästchen: Rolle ändern. Kästchen innen ziehen = verschieben, an einer Ecke = Größe, am runden Griff oder mit dem Mausrad drehen. Nach einem Szenenwechsel einfach neu markieren."))
        else:
            self.help.setText(tr("Klick auf einen Abschnitt springt zu seinem Start, dort lassen sich die Kästchen verschieben, vergrößern und drehen. Wandert ein Kästchen ab: an der Stelle einfach anpacken und richtig hinschieben (nachjustieren). Für echte Tiefe: ganz drin → „Ganz drin“, ganz draußen → „Ganz draußen“."))

    def _clicked(self, item: QListWidgetItem) -> None:
        self.seekRequested.emit(int(item.data(Qt.UserRole)))


def _trim(lines: tuple) -> tuple:
    """Outlines per area without trailing empty ones (no outline at all = ())."""
    lines = tuple(tuple(o) for o in lines)
    while lines and not lines[-1]:
        lines = lines[:-1]
    return lines


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
