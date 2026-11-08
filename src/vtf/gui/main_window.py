"""Main application window."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSlider, QSplitter,
                               QVBoxLayout, QWidget)

from .. import __version__, gpu
from ..analysis import MotionData
from ..batch import VIDEO_EXTS, make_metadata
from ..funscript import Action, Funscript, funscript_path_for
from ..generator import generate
from ..video import probe
from .batch_dialog import BatchDialog
from .curve_widget import CurveWidget
from .settings_panel import SettingsPanel
from .video_view import VideoView
from .workers import AnalysisWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Video-to-Funscript Generator {__version__}")
        self.resize(1400, 900)
        self.qsettings = QSettings("video-to-funscript", "vtf")

        self.video_path: str | None = None
        self.cap: cv2.VideoCapture | None = None
        self.fps = 30.0
        self.frame_count = 0
        self.motion: MotionData | None = None
        self.worker: AnalysisWorker | None = None
        self.undo_stack: list[list[Action]] = []
        self.redo_stack: list[list[Action]] = []
        self.dirty = False

        # widgets
        self.view = VideoView()
        self.curve = CurveWidget()
        self.panel = SettingsPanel()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setEnabled(False)
        self.time_label = QLabel("0:00.0 / 0:00.0")
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(40)
        self.play_btn.setEnabled(False)
        self.analyze_btn = QPushButton("Analysieren")
        self.analyze_btn.setEnabled(False)
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status_label = QLabel()

        controls = QHBoxLayout()
        controls.addWidget(self.play_btn)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.time_label)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.view, 1)
        ll.addLayout(controls)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.panel)
        rl.addWidget(scroll, 1)
        rl.addWidget(self.analyze_btn)
        rl.addWidget(self.cancel_btn)
        rl.addWidget(self.progress)
        right.setMinimumWidth(340)

        top = QSplitter(Qt.Horizontal)
        top.addWidget(left)
        top.addWidget(right)
        top.setStretchFactor(0, 1)
        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(top)
        vsplit.addWidget(self.curve)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 1)
        self.setCentralWidget(vsplit)
        self.statusBar().addWidget(self.status_label, 1)
        ocl = gpu.opencl_device_name()
        self.statusBar().addPermanentWidget(QLabel(f"OpenCL: {ocl}" if ocl else "OpenCL: nicht verfügbar"))
        if not ocl:
            self.panel.opencl.setEnabled(False)
            self.panel.opencl.setToolTip("Kein OpenCL-Gerät gefunden (siehe README: ROCm/rusticl)")

        self._build_menu()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.regen_timer = QTimer(self)
        self.regen_timer.setSingleShot(True)
        self.regen_timer.setInterval(120)
        self.regen_timer.timeout.connect(self.regenerate)

        # signals
        self.play_btn.clicked.connect(self.toggle_play)
        self.slider.sliderMoved.connect(self.seek_frame)
        self.slider.sliderPressed.connect(lambda: self.seek_frame(self.slider.value()))
        self.analyze_btn.clicked.connect(self.start_analysis)
        self.cancel_btn.clicked.connect(self.cancel_analysis)
        self.view.roiChanged.connect(self.panel.set_roi)
        self.panel.generatorChanged.connect(self.regen_timer.start)
        self.panel.analysisChanged.connect(self._analysis_outdated)
        self.curve.seekRequested.connect(self.seek_ms)
        self.curve.editStarted.connect(self._push_undo)
        self.curve.actionsEdited.connect(self._edited)

        try:
            self.panel.load_dict(json.loads(self.qsettings.value("settings", "{}")))
        except (TypeError, ValueError):
            pass
        self.setAcceptDrops(True)

    # ------------------------------------------------------------ menu
    def _build_menu(self):
        m = self.menuBar().addMenu("&Datei")
        self._act(m, "Video öffnen…", self.open_dialog, QKeySequence.Open)
        self._act(m, "Funscript exportieren", self.export, QKeySequence.Save)
        self._act(m, "Funscript exportieren unter…", self.export_as, QKeySequence.SaveAs)
        self._act(m, "Funscript laden…", self.load_funscript_dialog)
        m.addSeparator()
        self._act(m, "Stapelverarbeitung…", self.open_batch, "Ctrl+B")
        m.addSeparator()
        self._act(m, "Beenden", self.close, QKeySequence.Quit)
        m = self.menuBar().addMenu("&Bearbeiten")
        self._act(m, "Rückgängig", self.undo, QKeySequence.Undo)
        self._act(m, "Wiederholen", self.redo, QKeySequence.Redo)
        m.addSeparator()
        self._act(m, "Script aus Analyse neu erzeugen", self.regenerate_manual, "Ctrl+R")
        m = self.menuBar().addMenu("&Wiedergabe")
        self._act(m, "Abspielen/Pause", self.toggle_play, "Space")
        self._act(m, "Frame vor", lambda: self.step_frames(1), "Right")
        self._act(m, "Frame zurück", lambda: self.step_frames(-1), "Left")
        self._act(m, "1 s vor", lambda: self.step_frames(int(self.fps)), "Shift+Right")
        self._act(m, "1 s zurück", lambda: self.step_frames(-int(self.fps)), "Shift+Left")
        m = self.menuBar().addMenu("&Hilfe")
        self._act(m, "Bedienung", self.show_help, QKeySequence.HelpContents)

    def _act(self, menu, text, slot, shortcut=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    # ------------------------------------------------------------ video
    def open_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "Video öffnen", self.qsettings.value("last_dir", ""),
                                              f"Videos ({exts});;Alle Dateien (*)")
        if path:
            self.open_video(path)

    def open_video(self, path: str):
        if not self._confirm_discard():
            return
        self.cancel_analysis()
        self.pause()
        try:
            info = probe(path)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Video kann nicht geöffnet werden:\n{e}")
            return
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            QMessageBox.critical(self, "Fehler", "OpenCV kann das Video nicht öffnen.")
            return
        if self.cap:
            self.cap.release()
        self.cap = cap
        self.video_path = path
        self.qsettings.setValue("last_dir", str(Path(path).parent))
        self.fps = info.fps
        self.frame_count = max(1, info.frame_count)
        self.motion = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self.slider.setRange(0, self.frame_count - 1)
        self.slider.setEnabled(True)
        self.play_btn.setEnabled(True)
        self.analyze_btn.setEnabled(True)
        self.view.set_roi(None)
        self.panel.set_roi(None)
        self.curve.set_data(self.curve.t_ms[:0], self.curve.signal[:0], [], info.duration * 1000)
        self.setWindowTitle(f"{Path(path).name} – Video-to-Funscript Generator")
        existing = funscript_path_for(path)
        if existing.is_file():
            try:
                self.curve.set_actions(Funscript.load(existing).actions)
                self._status(f"Vorhandenes Funscript geladen: {existing.name}")
            except (OSError, ValueError, KeyError) as e:
                self._status(f"Funscript konnte nicht geladen werden: {e}")
        else:
            self._status(f"{info.width}x{info.height}, {info.fps:.2f} fps, {info.duration:.1f} s")
        self.seek_frame(0)

    def _read_frame(self):
        if not self.cap:
            return
        ok, frame = self.cap.read()
        if not ok:
            self.pause()
            return
        idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
        self.view.set_frame(frame)
        self.slider.blockSignals(True)
        self.slider.setValue(idx)
        self.slider.blockSignals(False)
        ms = idx / self.fps * 1000
        self.curve.set_playhead(ms)
        self.time_label.setText(f"{_fmt(ms)} / {_fmt(self.frame_count / self.fps * 1000)}")

    def seek_frame(self, idx: int):
        if not self.cap:
            return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(int(idx), self.frame_count - 1)))
        self._read_frame()

    def seek_ms(self, ms: int):
        self.curve.follow = True
        self.seek_frame(int(round(ms / 1000 * self.fps)))

    def step_frames(self, n: int):
        if self.cap:
            self.pause()
            self.seek_frame(self.slider.value() + n)

    def _next_frame(self):
        self._read_frame()

    def toggle_play(self):
        if not self.cap:
            return
        if self.timer.isActive():
            self.pause()
        else:
            self.curve.follow = True
            self.timer.start(max(1, int(1000 / self.fps)))
            self.play_btn.setText("⏸")

    def pause(self):
        self.timer.stop()
        self.play_btn.setText("▶")

    # ------------------------------------------------------------ analysis
    def start_analysis(self):
        if not self.video_path or (self.worker and self.worker.isRunning()):
            return
        if self.curve.actions and self.dirty and QMessageBox.question(
                self, "Analyse", "Manuelle Änderungen werden durch die neue Analyse ersetzt. Fortfahren?"
        ) != QMessageBox.Yes:
            return
        self.worker = AnalysisWorker(self.video_path, self.panel.analysis_settings())
        self.worker.progress.connect(lambda p: self.progress.setValue(int(p * 100)))
        self.worker.finishedOk.connect(self._analysis_done)
        self.worker.failed.connect(self._analysis_failed)
        self.worker.cancelled.connect(lambda: self._analysis_end("Analyse abgebrochen."))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.analyze_btn.setEnabled(False)
        self._status("Analysiere…")
        self.worker.start()

    def cancel_analysis(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(10000)

    def _analysis_end(self, msg: str):
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.analyze_btn.setEnabled(self.video_path is not None)
        self._status(msg)

    def _analysis_done(self, motion: MotionData):
        if motion.video != self.video_path:
            return
        self.motion = motion
        self._push_undo()
        self.regenerate()
        ocl = " (OpenCL)" if motion.meta.get("opencl") else ""
        self._analysis_end(f"Analyse fertig{ocl}: {len(self.curve.actions)} Punkte erzeugt.")

    def _analysis_failed(self, msg: str):
        self._analysis_end("Analyse fehlgeschlagen.")
        QMessageBox.critical(self, "Analyse fehlgeschlagen", msg)

    def _analysis_outdated(self):
        if self.motion is not None:
            self._status("Analyse-Einstellungen geändert – bitte neu analysieren.")

    def regenerate(self):
        if self.motion is None:
            return
        script = generate(self.motion, self.panel.generator_settings())
        self.curve.set_data(script.t_ms, script.signal, script.actions,
                            max(self.motion.duration_ms, self.frame_count / self.fps * 1000))
        self.dirty = True
        self._status(f"{len(script.actions)} Punkte")

    def regenerate_manual(self):
        if self.motion is not None:
            self._push_undo()
            self.regenerate()

    # ------------------------------------------------------------ editing
    def _push_undo(self):
        self.undo_stack.append([Action(a.at, a.pos) for a in self.curve.actions])
        del self.undo_stack[:-100]
        self.redo_stack.clear()

    def _edited(self):
        self.dirty = True
        self._status(f"{len(self.curve.actions)} Punkte (bearbeitet)")

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(list(self.curve.actions))
            self.curve.set_actions(self.undo_stack.pop())
            self.dirty = True

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(list(self.curve.actions))
            self.curve.set_actions(self.redo_stack.pop())
            self.dirty = True

    # ------------------------------------------------------------ files
    def export(self):
        if self.video_path:
            self._save(funscript_path_for(self.video_path))

    def export_as(self):
        if not self.video_path:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Funscript speichern", str(funscript_path_for(self.video_path)),
                                              "Funscript (*.funscript)")
        if path:
            self._save(Path(path))

    def _save(self, path: Path):
        if not self.curve.actions:
            QMessageBox.information(self, "Export", "Keine Punkte vorhanden – bitte zuerst analysieren.")
            return
        try:
            Funscript(list(self.curve.actions), metadata=make_metadata(self.video_path)).save(path)
        except OSError as e:
            QMessageBox.critical(self, "Fehler", f"Speichern fehlgeschlagen:\n{e}")
            return
        self.dirty = False
        self._status(f"Gespeichert: {path}")

    def load_funscript_dialog(self):
        start = str(funscript_path_for(self.video_path)) if self.video_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Funscript laden", start, "Funscript (*.funscript)")
        if path:
            try:
                fs = Funscript.load(path)
            except (OSError, ValueError, KeyError) as e:
                QMessageBox.critical(self, "Fehler", str(e))
                return
            self._push_undo()
            self.curve.set_actions(fs.actions)
            self._status(f"Geladen: {path}")

    def open_batch(self):
        folder = str(Path(self.video_path).parent) if self.video_path else self.qsettings.value("last_dir", "")
        BatchDialog(self.panel, folder, self).exec()

    def show_help(self):
        QMessageBox.information(self, "Bedienung", (
            "1. Video öffnen (Strg+O oder Datei hierher ziehen).\n"
            "2. Optional: Im Video ein Rechteck um den relevanten Bereich ziehen (ROI).\n"
            "3. 'Analysieren' klicken.\n"
            "4. Empfindlichkeit, Glättung usw. anpassen – die Kurve aktualisiert sich sofort.\n"
            "5. Punkte bearbeiten: ziehen = verschieben, Doppelklick = hinzufügen,\n"
            "   Rechtsklick/Entf = löschen, Mausrad = Zoom, Umschalt+Mausrad = scrollen.\n"
            "6. Strg+S speichert <Video>.funscript neben dem Video.\n\n"
            "Leertaste = Abspielen, Pfeiltasten = Frame vor/zurück."))

    # ------------------------------------------------------------ misc
    def _status(self, msg: str):
        self.status_label.setText(msg)

    def _confirm_discard(self) -> bool:
        if not self.dirty or not self.curve.actions:
            return True
        r = QMessageBox.question(self, "Ungespeicherte Änderungen",
                                 "Das aktuelle Funscript wurde nicht gespeichert. Verwerfen?")
        return r == QMessageBox.Yes

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        urls = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if urls:
            p = Path(urls[0])
            if p.is_dir():
                BatchDialog(self.panel, str(p), self).exec()
            else:
                self.open_video(str(p))

    def closeEvent(self, ev):
        if not self._confirm_discard():
            ev.ignore()
            return
        self.cancel_analysis()
        self.qsettings.setValue("settings", json.dumps(self.panel.to_dict()))
        if self.cap:
            self.cap.release()
        super().closeEvent(ev)


def _fmt(ms: float) -> str:
    s = ms / 1000
    return f"{int(s // 60)}:{s % 60:04.1f}"
