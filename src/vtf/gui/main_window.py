"""Main application window."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QSplitter, QVBoxLayout, QWidget)

from .. import __version__, gpu
from ..analysis import MotionData
from ..batch import VIDEO_EXTS, make_metadata
from ..funscript import Action, Funscript, funscript_path_for, position_at
from ..generator import generate
from ..video import probe
from . import theme
from .batch_dialog import BatchDialog
from .curve_widget import CurveWidget
from .settings_panel import SettingsPanel
from .stroke_rail import StrokeRail
from .video_view import VideoView
from .update_ui import UpdateController
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
        self.rail = StrokeRail()
        self.curve = CurveWidget()
        self.panel = SettingsPanel()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("timeline")
        self.slider.setEnabled(False)
        self.time_label = QLabel("0:00.00 / 0:00.00")
        self.time_label.setFont(theme.mono_font(12))
        self.time_label.setObjectName("value")
        self.play_btn = self._transport("play", "Abspielen (Leertaste)")
        self.back_btn = self._transport("chevron-left", "Frame zurück (←)")
        self.fwd_btn = self._transport("chevron-right", "Frame vor (→)")
        self.analyze_btn = QPushButton(theme.icon("activity", theme.ON_ACCENT), "Analysieren")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.setEnabled(False)
        self.cancel_btn = QPushButton(theme.icon("x"), "Abbrechen")
        self.cancel_btn.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.progress_label = QLabel()
        self.progress_label.setObjectName("meta")
        self.progress_label.setFont(theme.mono_font(12))
        self.progress_label.setVisible(False)
        self.status_label = QLabel()
        self.curve_title = QLabel("Script")
        self.curve_title.setObjectName("section")
        self.curve_meta = QLabel("Noch nicht analysiert")
        self.curve_meta.setObjectName("meta")
        self.curve_meta.setFont(theme.mono_font(12))
        curve_hint = QLabel("Ziehen verschiebt · Doppelklick fügt hinzu · Rechtsklick löscht · Mausrad zoomt")
        curve_hint.setObjectName("caption")

        controls = QHBoxLayout()
        controls.setContentsMargins(12, 8, 16, 8)
        controls.setSpacing(4)
        controls.addWidget(self.back_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.fwd_btn)
        controls.addSpacing(8)
        controls.addWidget(self.slider, 1)
        controls.addSpacing(12)
        controls.addWidget(self.time_label)
        stage = QHBoxLayout()
        stage.setContentsMargins(0, 0, 0, 0)
        stage.setSpacing(1)
        stage.addWidget(self.view, 1)
        stage.addWidget(self.rail)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        ll.addLayout(stage, 1)
        ll.addLayout(controls)

        right = QWidget()
        right.setObjectName("sidebar")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("sidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.panel)
        rl.addWidget(scroll, 1)
        footer = QWidget()
        footer.setObjectName("sidebarFooter")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(16, 12, 16, 16)
        fl.setSpacing(8)
        fl.addWidget(self.progress_label)
        fl.addWidget(self.progress)
        fl.addWidget(self.analyze_btn)
        fl.addWidget(self.cancel_btn)
        rl.addWidget(footer)
        right.setFixedWidth(320)

        curve_box = QWidget()
        cl = QVBoxLayout(curve_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 16, 8)
        head.setSpacing(12)
        head.addWidget(self.curve_title)
        head.addWidget(self.curve_meta)
        head.addStretch(1)
        head.addWidget(curve_hint)
        cl.addLayout(head)
        cl.addWidget(self.curve, 1)

        main = QSplitter(Qt.Vertical)
        main.addWidget(left)
        main.addWidget(curve_box)
        main.setStretchFactor(0, 3)
        main.setStretchFactor(1, 2)
        main.setChildrenCollapsible(False)
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(main, 1)
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {theme.HAIRLINE};")
        bl.addWidget(sep)
        bl.addWidget(right)
        self.setCentralWidget(body)
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().setSizeGripEnabled(False)
        ocl = gpu.opencl_device_name()
        ocl_label = QLabel(f"OpenCL · {ocl}" if ocl else "OpenCL · nicht verfügbar")
        ocl_label.setFont(theme.mono_font(11))
        self.statusBar().addPermanentWidget(ocl_label)
        if not ocl:
            self.panel.opencl.setEnabled(False)
            hint = gpu.opencl_hint()
            self.panel.opencl.setToolTip(hint)
            ocl_label.setToolTip(hint)

        self.updates = UpdateController(self, self.qsettings)
        self._build_menu()
        self._build_toolbar()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.regen_timer = QTimer(self)
        self.regen_timer.setSingleShot(True)
        self.regen_timer.setInterval(120)
        self.regen_timer.timeout.connect(self.regenerate)

        # signals
        self.play_btn.clicked.connect(self.toggle_play)
        self.back_btn.clicked.connect(lambda: self.step_frames(-1))
        self.fwd_btn.clicked.connect(lambda: self.step_frames(1))
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
        self.updates.add_menu_entries(m)
        self._setup_desktop_integration(m)

    def _build_toolbar(self):
        tb = self.addToolBar("Werkzeuge")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(16, 16))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.toggleViewAction().setVisible(False)
        tb.addAction(theme.icon("folder-open"), "Öffnen", self.open_dialog)
        self.save_action = tb.addAction(theme.icon("save"), "Speichern", self.export)
        tb.addAction(theme.icon("folders"), "Stapel", self.open_batch)
        tb.addSeparator()
        tb.addAction(theme.icon("undo"), "", self.undo).setToolTip("Rückgängig (Strg+Z)")
        tb.addAction(theme.icon("redo"), "", self.redo).setToolTip("Wiederholen (Strg+Umschalt+Z)")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.file_label = QLabel()
        self.file_label.setObjectName("meta")
        tb.addWidget(self.file_label)

    def _transport(self, name: str, tip: str) -> QPushButton:
        b = QPushButton(theme.icon(name, theme.TEXT_2, 16), "")
        b.setObjectName("transport")
        b.setToolTip(tip)
        b.setEnabled(False)
        return b

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
            QMessageBox.critical(self, "Fehler", "OpenCV kann dieses Video nicht öffnen.")
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
        for b in (self.play_btn, self.back_btn, self.fwd_btn, self.analyze_btn):
            b.setEnabled(True)
        self.view.set_roi(None)
        self.panel.set_roi(None)
        self.curve.set_data(self.curve.t_ms[:0], self.curve.signal[:0], [], info.duration * 1000)
        self.setWindowTitle(f"{Path(path).name} – Video-to-Funscript Generator")
        self.file_label.setText(f"{Path(path).name}  ·  {info.width}×{info.height}  ·  {info.fps:.2f} fps")
        existing = funscript_path_for(path)
        if existing.is_file():
            try:
                self.curve.set_actions(Funscript.load(existing).actions)
                self._status(f"Vorhandenes Funscript geladen: {existing.name}")
            except (OSError, ValueError, KeyError) as e:
                self._status(f"Funscript konnte nicht geladen werden: {e}")
        else:
            self._status("Bereit zur Analyse")
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
        self.rail.set_position(position_at(self.curve.actions, ms))
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
            self.play_btn.setIcon(theme.icon("pause", theme.TEXT_2, 16))

    def pause(self):
        self.timer.stop()
        self.play_btn.setIcon(theme.icon("play", theme.TEXT_2, 16))

    # ------------------------------------------------------------ analysis
    def start_analysis(self):
        if not self.video_path or (self.worker and self.worker.isRunning()):
            return
        if self.curve.actions and self.dirty and QMessageBox.question(
                self, "Analyse", "Manuelle Änderungen werden durch die neue Analyse ersetzt. Fortfahren?"
        ) != QMessageBox.Yes:
            return
        self.worker = AnalysisWorker(self.video_path, self.panel.analysis_settings())
        self.worker.progress.connect(self._progress)
        self.worker.finishedOk.connect(self._analysis_done)
        self.worker.failed.connect(self._analysis_failed)
        self.worker.cancelled.connect(lambda: self._analysis_end("Analyse abgebrochen"))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.progress_label.setText("Analyse  0 %")
        self.progress_label.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.analyze_btn.setVisible(False)
        self._status("Analysiere Bewegung")
        self.worker.start()

    def _progress(self, p: float):
        self.progress.setValue(int(p * 100))
        self.progress_label.setText(f"Analyse {p * 100:3.0f} %")

    def cancel_analysis(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(10000)

    def _analysis_end(self, msg: str):
        self.progress.setVisible(False)
        self.progress_label.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.analyze_btn.setVisible(True)
        self.analyze_btn.setEnabled(self.video_path is not None)
        self._status(msg)

    def _analysis_done(self, motion: MotionData):
        if motion.video != self.video_path:
            return
        self.motion = motion
        self._push_undo()
        self.regenerate()
        ocl = " (OpenCL)" if motion.meta.get("opencl") else ""
        self._analysis_end(f"Analyse fertig{ocl}, {len(self.curve.actions)} Punkte erzeugt")

    def _analysis_failed(self, msg: str):
        self._analysis_end("Analyse fehlgeschlagen")
        QMessageBox.critical(self, "Analyse fehlgeschlagen", msg)

    def _analysis_outdated(self):
        if self.motion is not None:
            self._status("Analyse-Einstellungen geändert, neu analysieren, um sie anzuwenden")

    def regenerate(self):
        if self.motion is None:
            return
        script = generate(self.motion, self.panel.generator_settings())
        self.curve.set_data(script.t_ms, script.signal, script.actions,
                            max(self.motion.duration_ms, self.frame_count / self.fps * 1000))
        self.dirty = True
        self._refresh_position()
        self._status(f"{len(script.actions)} Punkte erzeugt")

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
        self._refresh_position()
        self._status("Punkte bearbeitet")

    def _refresh_position(self):
        self.rail.set_position(position_at(self.curve.actions, self.curve.playhead_ms))
        self._update_meta()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(list(self.curve.actions))
            self.curve.set_actions(self.undo_stack.pop())
            self.dirty = True
            self._refresh_position()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(list(self.curve.actions))
            self.curve.set_actions(self.redo_stack.pop())
            self.dirty = True
            self._refresh_position()

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
            QMessageBox.information(self, "Export", "Noch keine Punkte vorhanden. Analysiere zuerst das Video.")
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

    def _setup_desktop_integration(self, menu):
        """Start menu entry for the AppImage: added on start, switchable in the help menu."""
        from .. import desktop_integration as di
        from .. import updater

        self._appimage = updater.running_appimage()
        act = QAction("Im Startmenü anzeigen", self)
        act.setCheckable(True)
        v = self.qsettings.value("desktop_integration", True)
        enabled = v if isinstance(v, bool) else str(v).lower() == "true"
        act.setChecked(enabled and self._appimage is not None)
        act.setEnabled(self._appimage is not None)
        if self._appimage is None:
            act.setToolTip("Nur verfügbar, wenn die App als AppImage läuft")
        menu.insertAction(menu.actions()[1] if len(menu.actions()) > 1 else None, act)

        def toggled(on: bool):
            self.qsettings.setValue("desktop_integration", on)
            if on:
                di.install(self._appimage)
                self._status("Ins Startmenü aufgenommen")
            else:
                di.uninstall()
                self._status("Aus dem Startmenü entfernt")

        act.toggled.connect(toggled)
        if enabled and self._appimage is not None:
            msg = di.auto_integrate(self._appimage)
            if msg:
                self._status(msg)

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
        self._update_meta()

    def _update_meta(self):
        n = len(self.curve.actions)
        if not n:
            self.curve_meta.setText("Noch nicht analysiert" if self.motion is None else "Keine Punkte")
            return
        dur = self.curve.actions[-1].at - self.curve.actions[0].at
        rate = (n - 1) / 2 / (dur / 60000) if dur > 0 else 0
        edited = " · nicht gespeichert" if self.dirty else ""
        self.curve_meta.setText(f"{n} Punkte · {rate:.0f} Hübe/min{edited}")

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

    def prepare_restart(self) -> bool:
        """Save state before the process is replaced by an updated version."""
        if not self._confirm_discard():
            return False
        self.cancel_analysis()
        self.qsettings.setValue("settings", json.dumps(self.panel.to_dict()))
        self.qsettings.sync()
        if self.cap:
            self.cap.release()
        return True

    def closeEvent(self, ev):
        if not self._confirm_discard():
            ev.ignore()
            return
        self.cancel_analysis()
        self.updates.shutdown()
        self.qsettings.setValue("settings", json.dumps(self.panel.to_dict()))
        if self.cap:
            self.cap.release()
        super().closeEvent(ev)


def _fmt(ms: float) -> str:
    s = ms / 1000
    return f"{int(s // 60)}:{s % 60:05.2f}"
