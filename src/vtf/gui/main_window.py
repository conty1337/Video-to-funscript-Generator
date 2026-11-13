"""Main application window."""

from __future__ import annotations

import bisect
import json
import math
import threading
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QElapsedTimer, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, editing, gpu
from ..analysis import MotionData
from ..batch import VIDEO_EXTS, make_metadata
from ..editing import rhythm
from ..funscript import (
    AXES,
    Action,
    Funscript,
    funscript_path_for,
    load_axes,
    position_at,
)
from ..generator import generate, generate_axes
from ..i18n import plural, tr
from ..playlist import intro_skip_ms, quiet_stretches, resume_ms, skip_target
from ..prep import (
    load_bookmarks,
    load_cuts,
    load_prep,
    prep_path,
    save_bookmarks,
    save_cuts,
    save_prep,
)
from ..quality import check as check_script
from ..quality import describe as describe_issue
from ..quality import summary as issue_summary
from ..scriptdir import (
    find_script,
    find_video,
    remember_video,
    remembered_video,
    save_paths,
    video_stem,
)
from ..scriptdir import settings as script_settings
from ..thumbs import get_thumb, make_thumb
from ..video import DECODER_LABELS, probe, test_decoders
from . import pose_ui, theme
from .audio import AudioPlayer
from .batch_dialog import BatchDialog
from .curve_widget import CurveWidget
from .device_ui import DeviceController
from .heatbar import HeatBar
from .look_ui import LookMenus
from .player_panel import PlayerPanel
from .settings_panel import SettingsPanel
from .stroke_rail import StrokeRail
from .timeline import TimelineSlider
from .update_ui import UpdateController
from .video_view import VideoView
from .workers import AnalysisWorker

TRACK_UNDO = "tracking"      # undo entry holding the tracking sections, not script points


class ElidedLabel(QLabel):
    """A one-line label that ends in … when it does not fit (instead of being cut off)."""

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        text = p.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        p.drawText(self.rect(), int(self.alignment()), text)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("Video-to-Funscript Generator {0}", __version__))
        self.resize(1400, 900)
        self.qsettings = QSettings("video-to-funscript", "vtf")

        self.video_path: str | None = None
        self.cap: cv2.VideoCapture | None = None
        self._frame = None                    # last frame shown (BGR)
        self._pose_worker = None
        self.fps = 30.0
        self.frame_count = 0
        self.motion: MotionData | None = None
        self.worker: AnalysisWorker | None = None
        self.undo_stack: list[tuple[str, list[Action]]] = []   # (axis, actions)
        self.redo_stack: list[tuple[str, list[Action]]] = []
        # tracking changes are undone with the same Strg+Z: entries (TRACK_UNDO, sections)
        self._track_shadow: list = []          # the sections after the last change
        self._track_restoring = False
        self.dirty = False

        # widgets
        self.view = VideoView()
        self.rail = StrokeRail()
        self.curve = CurveWidget()
        self.panel = SettingsPanel()
        self.slider = TimelineSlider()
        self.slider.setObjectName("timeline")
        self.slider.setEnabled(False)
        self.time_label = QLabel("0:00.00 / 0:00.00")
        self.time_label.setFont(theme.mono_font(12))
        self.time_label.setObjectName("value")
        self.play_btn = self._transport("play", tr("Abspielen (Leertaste)"))
        self.back_btn = self._transport("chevron-left", tr("Frame zurück (←)"))
        self.fwd_btn = self._transport("chevron-right", tr("Frame vor (→)"))
        self.prev_btn = self._transport("skip-back", tr("Vorheriges Video (Bild ↑)"))
        self.next_btn = self._transport("skip-forward", tr("Nächstes Video (Bild ↓)"))
        self.full_btn = self._transport("maximize", tr("Vollbild (F11)"))
        self.full_btn.setEnabled(True)
        self.audio = AudioPlayer(self)
        self.mute_btn = self._transport("volume", tr("Ton an/aus (M)"))
        self.mute_btn.setEnabled(self.audio.available)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setObjectName("volume")
        self.volume.setRange(0, 100)
        self.volume.setToolTip(tr("Lautstärke"))
        self.volume.setEnabled(self.audio.available)
        self.analyze_btn = theme.accent_icon(QPushButton(), "activity", tr("Analysieren"))
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.setEnabled(False)
        self.cancel_btn = QPushButton(theme.icon("x"), tr("Abbrechen"))
        self.cancel_btn.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.progress_label = QLabel()
        self.progress_label.setObjectName("meta")
        self.progress_label.setFont(theme.mono_font(12))
        self.progress_label.setVisible(False)
        self.status_label = QLabel()
        self.curve_title = QLabel(tr("Zeitleiste"))
        self.curve_title.setObjectName("section")
        self.axis = "L0"                       # axis shown in the curve
        self.bookmarks: list[tuple[int, str]] = []   # (ms, name), saved next to the video
        self.cuts: list[tuple[int, int]] = []         # (from, to) the player skips, saved there too
        self._cut_start: float | None = None          # X pressed once: the cut starts here
        self._quiet_cache: tuple = ((), [])
        self._skip_resume: int | None = None          # after a skip: the stroke the countdown runs to
        self.issues: list = []                        # script check: places worth a look
        from ..stats import Recorder
        self.stats = Recorder()                       # playing time, strokes (only on this computer)
        self._stat_ms: float | None = None            # media time of the last counted tick
        self._stat_half = 0                           # script points passed (two = one stroke)
        self.axis_actions: dict[str, list[Action]] = {}   # the other axes
        self._l0_signal = None                 # analysis curve of L0 (t_ms, signal)
        self.axis_box = QComboBox()
        self.axis_box.setToolTip(tr("Script-Achse. Der Fredorch F21S nutzt nur „Hub“, die anderen Achsen werden als <Video>.roll.funscript usw. für Mehrachsen-Geräte gespeichert"))
        for code, (label, _suffix) in AXES.items():
            self.axis_box.addItem(f"{label} ({code})", code)
        self.axis_box.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.axis_box.setStyleSheet("QComboBox { padding-right: 28px; }")
        self.curve_meta = ElidedLabel(tr("Noch nicht analysiert"))
        self.curve_meta.setObjectName("meta")
        self.curve_meta.setFont(theme.mono_font(12))
        self.curve_meta.setMinimumWidth(120)          # longer languages: shortened, the window stays narrow
        curve_hint = self.curve_hint = ElidedLabel(tr("Punkt ziehen verschiebt · Fläche ziehen scrollt · Doppelklick fügt hinzu · Rechtsklick löscht · Mausrad zoomt"))
        curve_hint.setObjectName("caption")
        # long hint must not widen the window: shortened with … when there is no room
        curve_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        curve_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # ---- centre: video, stroke rail, transport
        controls = QHBoxLayout()
        controls.setContentsMargins(12, 8, 16, 8)
        controls.setSpacing(4)
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.back_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.fwd_btn)
        controls.addWidget(self.next_btn)
        controls.addStretch(1)
        controls.addWidget(self.time_label)
        controls.addStretch(1)
        controls.addWidget(self.mute_btn)
        controls.addWidget(self.volume)
        controls.addSpacing(4)
        controls.addWidget(self.full_btn)
        stage = QHBoxLayout()
        stage.setContentsMargins(0, 0, 0, 0)
        stage.setSpacing(1)
        stage.addWidget(self.view, 1)
        stage.addWidget(self.rail)
        center = QWidget()
        ll = QVBoxLayout(center)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        ll.addLayout(stage, 1)
        ll.addLayout(controls)

        # ---- left: analysis (method, tracking, options) or, in the player, the playlist
        analysis = QWidget()
        analysis.setObjectName("sidebar")
        al = QVBoxLayout(analysis)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(0)
        scroll = self._side_scroll = QScrollArea()
        scroll.setObjectName("sidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.panel.analysis_page)
        al.addWidget(scroll, 1)
        footer = QWidget()
        footer.setObjectName("sidebarFooter")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(16, 12, 16, 16)
        fl.setSpacing(8)
        fl.addWidget(self.progress_label)
        fl.addWidget(self.progress)
        fl.addWidget(self.analyze_btn)
        fl.addWidget(self.cancel_btn)
        al.addWidget(footer)
        self.player = PlayerPanel(self.qsettings)
        self.player.setObjectName("sidebar")
        self.side = QStackedWidget()
        self.side.addWidget(analysis)
        self.side.addWidget(self.player)
        self.side.setFixedWidth(300)

        # ---- right: script settings
        script_scroll = QScrollArea()
        script_scroll.setObjectName("sidebarScroll")
        script_scroll.setWidgetResizable(True)
        script_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        script_scroll.setWidget(self.panel.script_page)
        self.script_side = script_scroll
        self.script_side.setFixedWidth(300)

        # ---- bottom: the timeline across the whole width, one lane per row
        self.heat = HeatBar()
        self.heat.inset = 0
        curve_box = self.curve_box = QWidget()
        curve_box.setObjectName("timelinePanel")
        cl = QVBoxLayout(curve_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 16, 8)
        head.setSpacing(12)
        head.addWidget(self.curve_title)
        head.addWidget(self.axis_box)
        head.addWidget(self.curve_meta)
        head.addSpacing(8)
        self.edit_tools = QWidget()                 # buttons for the menu commands, editing only
        tools = QHBoxLayout(self.edit_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(8)
        self.cut_btn = self._tool("scissors", tr("Schnitt"), self.toggle_cut,
                                  tr("Schnitt: Anfang hier, nochmal am Ende drücken (X). Der Player springt darüber"))
        self.cut_btn.setCheckable(True)
        self._tool("skip-forward", tr("Bis zum nächsten Hub"), self.cut_to_next_move,
                   tr("Schnitt von hier bis zum Countdown vor dem nächsten Hub (Umschalt+X)"), tools)
        self._tool("x", tr("Schnitt weg"), self.remove_cut_here, tr("Schnitt an der Abspielposition entfernen"), tools)
        tools.insertWidget(0, self.cut_btn)
        tools.addSpacing(12)                        # cuts, then the other edits
        self._tool("waves", tr("Glätten"), self.smooth_points, tr("Ausgewählte Punkte glätten (Strg+G)"), tools)
        self._tool("bookmark", tr("Lesezeichen"), self.add_bookmark, tr("Lesezeichen an der Abspielposition (B)"), tools)
        self._tool("plus", tr("Rhythmus"), self.insert_rhythm, tr("Rhythmus oder Muster einfügen (Strg+T)"), tools)
        tools.addSpacing(12)
        self.check_btn = self._tool("alert", tr("Prüfung"), self._issue_menu,
                                    tr("Stellen, die einen zweiten Blick wert sind (F3 springt zur nächsten)"), tools)
        head.addWidget(self.edit_tools)
        head.addWidget(curve_hint, 1)
        cl.addLayout(head)
        for label, widget in ((tr("Video"), self.slider), (tr("Tempo"), self.heat)):
            lane = QHBoxLayout()
            # lanes line up with the plot of the curve below (its left/right margins)
            lane.setContentsMargins(0, 0, 16, 0)
            lane.setSpacing(0)
            tag = QLabel(label)
            tag.setObjectName("laneLabel")
            tag.setFixedWidth(40 - 4)
            tag.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lane.addWidget(tag)
            lane.addSpacing(4)
            lane.addWidget(widget, 1)
            cl.addLayout(lane)
        cl.addSpacing(4)
        cl.addWidget(self.curve, 1)

        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        tl.addWidget(self.side)
        sep = self._side_sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {theme.HAIRLINE};")
        tl.addWidget(sep)
        tl.addWidget(center, 1)
        sep2 = self._script_sep = QWidget()
        sep2.setFixedWidth(1)
        sep2.setStyleSheet(f"background: {theme.HAIRLINE};")
        tl.addWidget(sep2)
        tl.addWidget(self.script_side)

        main = QSplitter(Qt.Vertical)
        main.addWidget(top)
        main.addWidget(curve_box)
        main.setStretchFactor(0, 3)
        main.setStretchFactor(1, 2)
        main.setChildrenCollapsible(False)
        body = main
        self.setCentralWidget(body)
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().setSizeGripEnabled(False)
        ocl = gpu.opencl_device_name()
        if ocl:
            ocl_label = QLabel(tr("OpenCL · {0}", ocl))
            ocl_label.setFont(theme.mono_font(11))
            self.statusBar().addPermanentWidget(ocl_label)
        else:                    # nothing to choose: keep the option out of sight
            self.panel.opencl.setEnabled(False)
            self.panel.opencl.setVisible(False)
            self.panel.opencl.setToolTip(gpu.opencl_hint())

        self.updates = UpdateController(self, self.qsettings)
        self.devices = DeviceController(self, self.qsettings)
        self.devices.register_source(self)
        self.pattern_dialog = None
        from .remote_ui import RemoteController
        self.remote = RemoteController(self, self.qsettings)
        from .players_ui import ExternalSync
        self.external = ExternalSync(self, self.devices, self.qsettings)
        self._build_menu()
        self._build_toolbar()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._next_frame)
        self._clock = QElapsedTimer()
        self._play_origin_ms = 0.0
        self._cur_idx = -1
        self.regen_timer = QTimer(self)
        self.regen_timer.setSingleShot(True)
        self.regen_timer.setInterval(120)
        self.regen_timer.timeout.connect(self.regenerate)

        # signals
        self.play_btn.clicked.connect(self.toggle_play)
        self.back_btn.clicked.connect(lambda: self.step_frames(-1))
        self.fwd_btn.clicked.connect(lambda: self.step_frames(1))
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.prev_btn.clicked.connect(self.play_prev)
        self.next_btn.clicked.connect(self.play_next)
        self.full_btn.clicked.connect(self.toggle_fullscreen)
        self.player.playRequested.connect(self.play_index)
        self.volume.valueChanged.connect(self._volume_changed)
        self._load_audio_settings()
        self.set_player_mode(str(self.qsettings.value("mode", "edit")) == "player")
        self.slider.sliderMoved.connect(self.seek_frame)
        self.slider.sliderPressed.connect(lambda: self.seek_frame(self.slider.value()))
        self.analyze_btn.clicked.connect(self.start_analysis)
        self.cancel_btn.clicked.connect(self.cancel_analysis)
        self.view.roiChanged.connect(self.panel.set_roi)
        self.view.boxDrawn.connect(self._box_drawn)
        self.view.boxRotated.connect(self._box_rotated)
        self.view.boxEdited.connect(self._box_edited)
        self.view.boxCorrected.connect(self._box_corrected)
        self.view.skeletonPicked.connect(self._skeleton_picked)
        self.view.outlineDrawn.connect(self._outline_drawn)
        self.view.strokeDrawn.connect(self._stroke_drawn)
        self.view.areaMenuRequested.connect(self._area_menu)
        self.panel.tracking.drawModeChanged.connect(self.view.set_draw_mode)
        self.panel.tracking.autoRequested.connect(self.auto_find)
        self.view.sectionMenuRequested.connect(self._section_menu)
        self.panel.tracking.current_ms = self._cur_ms
        self.panel.tracking.message.connect(self._status)
        self.panel.tracking.suggestRequested.connect(self.suggest_boxes)
        self.panel.tracking.seekRequested.connect(self.seek_ms)
        self.panel.tracking.analysisChanged.connect(self._sections_changed)
        self.panel.tracking.generatorChanged.connect(self._sections_changed)
        self.panel.methodChanged.connect(self._method_changed)
        self.panel.generatorChanged.connect(self.regen_timer.start)
        self.panel.analysisChanged.connect(self._analysis_outdated)
        self.curve.seekRequested.connect(self.seek_ms)
        self.curve.sectionEdgeMoved.connect(self._section_edge_moved)
        self.curve.editStarted.connect(self._push_undo)
        self.curve.actionsEdited.connect(self._edited)
        self.axis_box.activated.connect(lambda i: self.switch_axis(self.axis_box.itemData(i)))

        try:
            self.panel.load_dict(json.loads(self.qsettings.value("settings", "{}")))
        except (TypeError, ValueError):
            pass
        if str(self.qsettings.value("width640_default", "")) != "1":
            # 0.8.3: 640 px is the new default analysis resolution, once for everyone
            self.panel.width.setCurrentIndex(self.panel.width.findData(640))
            self.qsettings.setValue("width640_default", "1")
        self.setAcceptDrops(True)

    # ------------------------------------------------------------ menu
    def _build_menu(self):
        m = self.menuBar().addMenu(tr("&Datei"))
        self._act(m, tr("Video öffnen…"), self.open_dialog, QKeySequence.Open)
        self._act(m, tr("Video schließen"), self.close_video, "Ctrl+W")
        self._act(m, tr("Funscript exportieren"), self.export, QKeySequence.Save)
        self._act(m, tr("Funscript exportieren unter…"), self.export_as, QKeySequence.SaveAs)
        self._act(m, tr("Funscript laden…"), self.load_funscript_dialog)
        self._act(m, tr("Speicherorte…"), self.open_script_folder)
        m.addSeparator()
        self._act(m, tr("Stapelverarbeitung…"), self.open_batch, "Ctrl+B")
        self._act(m, tr("Bibliothek…"), self.open_library, "Ctrl+L")
        m.addSeparator()
        self._act(m, tr("Beenden"), self.close, QKeySequence.Quit)
        m = self.menuBar().addMenu(tr("&Bearbeiten"))
        self._act(m, tr("Rückgängig"), self.undo, QKeySequence.Undo)
        self._act(m, tr("Wiederholen"), self.redo, QKeySequence.Redo)
        m.addSeparator()
        self._act(m, tr("Alle Punkte auswählen"), self.select_all_points, "Ctrl+A")
        self._act(m, tr("Ausschneiden"), self.cut_points, "Ctrl+X")
        self._act(m, tr("Kopieren"), self.copy_points, "Ctrl+C")
        self._act(m, tr("Einfügen an Abspielposition"), self.paste_points, "Ctrl+V")
        self._act(m, tr("Auswahl löschen  (Entf)"), self.delete_points)
        m.addSeparator()
        self._act(m, tr("Auswahl glätten"), self.smooth_points, "Ctrl+G")
        self._act(m, tr("Hub verstärken"), lambda: self.scale_points(1.2), "Ctrl+Up")
        self._act(m, tr("Hub abschwächen"), lambda: self.scale_points(1 / 1.2), "Ctrl+Down")
        m.addSeparator()
        self._act(m, tr("Script aus Analyse neu erzeugen"), self.regenerate_manual, "Ctrl+R")
        self._act(m, tr("Weitere Achsen aus Analyse erzeugen (Sway, Roll)"), self.generate_more_axes)
        self._act(m, tr("Rhythmus einfügen…"), self.insert_rhythm, "Ctrl+T")
        self._act(m, tr("Script aus Musik…"), self.music_script)
        m.addSeparator()
        self._act(m, tr("Schnitt: Anfang / Ende hier"), self.toggle_cut, "X")
        self._act(m, tr("Schnitt bis zur nächsten Bewegung"), self.cut_to_next_move, "Shift+X")
        self._act(m, tr("Schnitt hier entfernen"), self.remove_cut_here)
        m.addSeparator()
        self._act(m, tr("Nächste Stelle zum Prüfen"), lambda: self.jump_issue(1), "F3")
        self._act(m, tr("Vorige Stelle zum Prüfen"), lambda: self.jump_issue(-1), "Shift+F3")
        m = self.menuBar().addMenu(tr("&Wiedergabe"))
        self._act(m, tr("Abspielen/Pause"), self.toggle_play, "Space")
        self._act(m, tr("Frame vor"), lambda: self.step_frames(1), "Right")
        self._act(m, tr("Frame zurück"), lambda: self.step_frames(-1), "Left")
        self._act(m, tr("1 s vor"), lambda: self.step_frames(int(self.fps)), "Shift+Right")
        self._act(m, tr("1 s zurück"), lambda: self.step_frames(-int(self.fps)), "Shift+Left")
        m.addSeparator()
        self._act(m, tr("Ton an/aus"), self.toggle_mute, "M")
        m.addSeparator()
        self._act(m, tr("Nächstes Video"), self.play_next, "PgDown")
        self._act(m, tr("Nächstes Video im Ordner"), lambda: self.open_sibling(1), "Ctrl+PgDown")
        self._act(m, tr("Vorheriges Video im Ordner"), lambda: self.open_sibling(-1), "Ctrl+PgUp")
        self._act(m, tr("Vorheriges Video"), self.play_prev, "PgUp")
        m = self.menuBar().addMenu(tr("&Ansicht"))
        self.player_action = self._act(m, tr("Player-Modus"), lambda on: self.set_player_mode(on), "Ctrl+P")
        self.player_action.setCheckable(True)
        self._act(m, tr("Vollbild"), self.toggle_fullscreen, "F11")
        self._act(m, tr("Video-Zoom zurücksetzen (Strg+Mausrad zoomt)"), self.view.reset_zoom, "Ctrl+0")
        m.addSeparator()
        self.left_action = self._act(m, tr("Linke Leiste (Analyse, Playlist)"), self._sides_toggled, "Ctrl+1")
        self.right_action = self._act(m, tr("Rechte Leiste (Script)"), self._sides_toggled, "Ctrl+2")
        for a, key in ((self.left_action, "show_left"), (self.right_action, "show_right")):
            a.setCheckable(True)
            a.setChecked(str(self.qsettings.value(key, "true")).lower() == "true")
        m.addSeparator()
        self.look = LookMenus(self, m)
        m = self.menuBar().addMenu(tr("&Lesezeichen"))
        self._act(m, tr("Lesezeichen setzen…"), self.add_bookmark, "B")
        self._act(m, tr("Nächstes Lesezeichen"), lambda: self.jump_bookmark(1), "Alt+Right")
        self._act(m, tr("Vorheriges Lesezeichen"), lambda: self.jump_bookmark(-1), "Alt+Left")
        self._act(m, tr("Lesezeichen hier löschen"), self.delete_bookmark)
        m.addSeparator()
        self._bookmark_menu = m
        self._bookmark_items: list = []
        m.aboutToShow.connect(self._fill_bookmark_menu)
        m = self.menuBar().addMenu(tr("&Tracking"))
        self._act(m, tr("Abschnitt-Ende hier"), lambda: self._track_cmd(self.panel.tracking.set_end), "E")
        self._act(m, tr("Ganz drin hier (Position 0)"),
                  lambda: self._track_cmd(self.panel.tracking.calibrate, "in"), "D")
        self._act(m, tr("Ganz draußen hier (Position 100)"),
                  lambda: self._track_cmd(self.panel.tracking.calibrate, "out"), "A")
        m.addSeparator()
        self._act(m, tr("Abschnitt hier löschen"), lambda: self._track_cmd(self.panel.tracking.delete_at))
        self._act(m, tr("KI-Vorschlag für Kästchen"), self.suggest_boxes, "K")
        self._act(m, tr("Bereiche automatisch finden"), self.auto_find, "F")
        self._act(m, tr("Umranden statt Rechteck an/aus"), lambda: self._toggle_draw("outline"), "U")
        self._act(m, tr("Hub-Richtung ziehen"), lambda: self._toggle_draw("stroke"), "H")
        self._act(m, tr("Alle Abschnitte löschen…"), self._clear_tracking)
        m = self.menuBar().addMenu(tr("&Gerät"))
        self._act(m, tr("Verbinden…"), lambda: self.devices.open_dialog(), "Ctrl+D")
        self._act(m, tr("Einrichtungs-Assistent…"), lambda: self.devices.open_wizard())
        self._act(m, tr("Trennen"), lambda: self.devices.disconnect_device())
        m.addSeparator()
        self._act(m, tr("Muster-Steuerung…"), self.open_patterns, "Ctrl+M")
        self._act(m, tr("Handy-Fernbedienung…"), self.open_remote)
        self._act(m, tr("Statistik…"), self.open_stats)
        self._act(m, tr("Andere Player (mpv, VLC, VR)…"), self.open_players)
        m.addSeparator()
        self._act(m, tr("Not-Aus (Gerät stoppen)"), self.emergency_stop, "Esc")
        m = self.menuBar().addMenu(tr("&Hilfe"))
        self._act(m, tr("Bedienung"), self.show_help, QKeySequence.HelpContents)
        self._act(m, tr("Dekodierung testen…"), self.test_decoding)
        self.updates.add_menu_entries(m)
        self._setup_desktop_integration(m)

    def _build_toolbar(self):
        tb = self.addToolBar(tr("Werkzeuge"))
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(16, 16))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.toggleViewAction().setVisible(False)
        tb.addAction(theme.icon("folder-open"), tr("Öffnen"), self.open_dialog)
        self.save_action = tb.addAction(theme.icon("save"), tr("Speichern"), self.export)
        tb.addAction(theme.icon("folders"), tr("Stapel"), self.open_batch)
        tb.addAction(theme.icon("library"), tr("Bibliothek"), self.open_library).setToolTip(
            tr("Alle Scripts eines Ordners mit Heatmap, Suche und Bewertung (Strg+L)"))
        tb.addAction(theme.icon("bluetooth"), tr("Gerät"), self.devices.open_dialog)
        tb.addAction(theme.icon("activity"), tr("Muster"), self.open_patterns).setToolTip(
            tr("Muster-Steuerung: fertige und eigene Muster auf dem Gerät abspielen (Strg+M)"))
        tb.addSeparator()
        self.mode_edit = tb.addAction(theme.icon("pencil"), tr("Bearbeiten"), lambda: self.set_player_mode(False))
        self.mode_player = tb.addAction(theme.icon("list-video"), tr("Player"), lambda: self.set_player_mode(True))
        for a, tip in ((self.mode_edit, tr("Scripts erstellen und bearbeiten")),
                       (self.mode_player, tr("Videos mit Script abspielen, mit Playlist (Strg+P)"))):
            a.setCheckable(True)
            a.setToolTip(tip)
        self.mode_edit.setChecked(True)
        tb.addSeparator()
        tb.addAction(theme.icon("undo"), "", self.undo).setToolTip(tr("Rückgängig (Strg+Z)"))
        tb.addAction(theme.icon("redo"), "", self.redo).setToolTip(tr("Wiederholen (Strg+Umschalt+Z)"))
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.file_label = QLabel()
        self.file_label.setObjectName("meta")
        tb.addWidget(self.file_label)
        self.close_act = tb.addAction(theme.icon("x"), "", self.close_video)
        self.close_act.setToolTip(tr("Video schließen (Strg+W)"))
        self.close_act.setVisible(False)
        self._toolbar = tb

    def _tool(self, icon: str, text: str, slot, tip: str, layout=None) -> QPushButton:
        """A small button in the timeline header for a menu command."""
        b = QPushButton(theme.icon(icon, theme.TEXT_2, 16), text)
        b.setObjectName("tool")
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(lambda _c=False: slot())
        if layout is not None:
            layout.addWidget(b)
        return b

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
        path, _ = QFileDialog.getOpenFileName(self, tr("Video oder Script öffnen"), self.qsettings.value("last_dir", ""),
                                              tr("Videos und Scripts ({0} *.funscript);;Alle Dateien (*)", exts))
        if path:
            self.open_video(path)

    # ------------------------------------------------------------ script -> video
    def _video_search_places(self) -> tuple:
        return tuple(Path(str(p)) for p in (self.qsettings.value("library/folder", ""),
                                            self.qsettings.value("last_dir", "")) if p)

    def locate_video(self, script: str | Path, ask: bool = True) -> Path | None:
        """The video of a script: found by itself (next to it, where it was last, by name in
        the library folder), else the user is asked to look for it (and that is remembered)."""
        video = find_video(script, self._video_search_places())
        if video is not None or not ask:
            return video
        box = QMessageBox(QMessageBox.Question, tr("Video nicht gefunden"),
                          tr("Zum Script „{0}“ wurde kein Video gefunden. Verschoben oder umbenannt?",
                             Path(script).name), parent=self)
        pic = get_thumb(video_stem(script))      # the picture from before helps to recognise it
        if pic is not None:
            box.setIconPixmap(QPixmap(str(pic)).scaledToWidth(160, Qt.SmoothTransformation))
        look = box.addButton(tr("Video suchen…"), QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        self._run_dialog(box)
        if box.clickedButton() is not look:
            return None
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        last = remembered_video(video_stem(script))
        start = str(last.parent) if last is not None else str(self.qsettings.value("last_dir", ""))
        path, _ = QFileDialog.getOpenFileName(self, tr("Video zu „{0}“ suchen", video_stem(script)), start,
                                              tr("Videos ({0});;Alle Dateien (*)", exts))
        if not path:
            return None
        remember_video(path, video_stem(script))
        return Path(path)

    def _run_dialog(self, box) -> None:
        box.exec()

    def open_script(self, script: str | Path) -> None:
        """A funscript was opened: its video opens with it (and plays when in player mode)."""
        script = Path(script)
        video = self.locate_video(script)
        if video is None:
            return
        if self.player_mode:
            self._library_play(str(video))
        else:
            self.open_video(str(video))
        if self.video_path and Path(self.video_path) == video and find_script(video) != script:
            self._load_script_file(script)

    def _load_script_file(self, script: Path) -> None:
        """Load a script (with its other axes) that lies where the video's own lookup does not
        look, e.g. a script opened from anywhere."""
        base = video_stem(script)
        main = script.with_name(base + ".funscript")
        try:
            axes = {"L0": Funscript.load(main if main.is_file() else script).actions}
            for axis, (_label, suffix) in AXES.items():
                f = script.with_name(f"{base}.{suffix}.funscript")
                if suffix and f.is_file():
                    axes[axis] = Funscript.load(f).actions
        except (OSError, ValueError, KeyError, TypeError) as e:
            QMessageBox.warning(self, tr("Fehler"), str(e))
            return
        self.axis_actions = {k: v for k, v in axes.items() if k != "L0"}
        self.axis = "L0"
        self.axis_box.setCurrentIndex(0)
        self.curve.set_actions(axes["L0"])
        self._update_axis_labels()
        self._update_heat()
        self._update_issues()
        self.dirty = False
        self._status(tr("Script geladen: {0}", script.name))

    def open_video(self, path: str):
        if str(path).lower().endswith(".funscript"):
            self.open_script(path)
            return
        if not self._confirm_discard():
            return
        self.cancel_analysis()
        self.pause()
        try:
            info = probe(path)
        except Exception as e:
            QMessageBox.critical(self, tr("Fehler"), tr("Video kann nicht geöffnet werden:\n{0}", e))
            return
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            QMessageBox.critical(self, tr("Fehler"), tr("OpenCV kann dieses Video nicht öffnen."))
            return
        if self.cap:
            self.cap.release()
        self.cap = cap
        self.video_path = path
        self.audio.load(path)
        self.qsettings.setValue("last_dir", str(Path(path).parent))
        self.fps = info.fps
        self.frame_count = max(1, info.frame_count)
        self.motion = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self.slider.setRange(0, self.frame_count - 1)
        self.slider.wheel_step = max(1, int(round(self.fps)))     # 1 s per wheel notch
        self.slider.setEnabled(True)
        for b in (self.play_btn, self.back_btn, self.fwd_btn, self.analyze_btn):
            b.setEnabled(True)
        self.view.set_roi(None)
        self.panel.set_roi(None)
        self.view.set_tracks([])
        self.view.set_track_overlay(None)
        self.view.reset_zoom()
        self.panel.tracking.reset(info.duration * 1000, info.fps)
        prepared = load_prep(path)
        if prepared:                  # prepared earlier: tracking with its areas
            self.panel.tracking.sections = prepared
            self.panel.tracking.refresh()
            self.panel.method.setCurrentIndex(self.panel.method.findData("track"))
            self._update_section_marks()
        self._track_shadow = list(self.panel.tracking.sections)
        self.bookmarks = load_bookmarks(path)
        self._show_bookmarks()
        self.cuts = load_cuts(path)
        self._cut_start = None
        self._skip_resume = None
        self._show_cuts()
        self.curve.set_data(self.curve.t_ms[:0], self.curve.signal[:0], [], info.duration * 1000)
        self.setWindowTitle(tr("{0} – Video-to-Funscript Generator", Path(path).name))
        self.file_label.setText(tr("{0}  ·  {1}×{2}  ·  {3:.2f} fps", Path(path).name, info.width, info.height, info.fps))
        self.close_act.setVisible(True)
        self._l0_signal = None
        loaded = load_axes(path)
        remember_video(path)                     # a script opened later finds its video again
        threading.Thread(target=make_thumb, args=(path,), daemon=True, name="thumb").start()
        self.axis_actions = {k: v for k, v in loaded.items()}
        self.axis = "L0"
        self.axis_box.setCurrentIndex(0)
        self.curve.set_actions(self.axis_actions.pop("L0", []))
        self._update_axis_labels()
        if loaded:
            extra = [AXES[a][0] for a in loaded if a != "L0"]
            self._status(tr("Vorhandenes Funscript geladen") + (f" (+ {', '.join(extra)})" if extra else ""))
        else:
            self._status(tr("Bereit zur Analyse"))
        self.seek_frame(0)
        self._update_heat()
        self._update_issues()

    def close_video(self):
        """Take the video out (asks first if the script is not saved)."""
        if not self.cap or not self._confirm_discard():
            return
        self.cancel_analysis()
        self.pause()
        self.cap.release()
        self.cap = None
        self.video_path = None
        self.audio.load(None)
        self.motion = None
        self._l0_signal = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self._cur_idx = 0
        self.slider.setRange(0, 0)
        self.slider.setEnabled(False)
        for b in (self.play_btn, self.back_btn, self.fwd_btn, self.analyze_btn):
            b.setEnabled(False)
        self.view.set_countdown(None)
        self.view.reset_zoom()
        self.view.set_frame(None)
        self.view.set_roi(None)
        self.panel.set_roi(None)
        self.view.set_tracks([])
        self.view.set_track_overlay(None)
        self.panel.tracking.reset(0, 30)
        self._track_shadow = []
        self.bookmarks = []
        self._show_bookmarks()
        self.cuts = []
        self._cut_start = None
        self._show_cuts()
        self.rail.set_speed(0.0)
        self.axis_actions = {}
        self.axis = "L0"
        self.axis_box.setCurrentIndex(0)
        self.curve.set_data(self.curve.t_ms[:0], self.curve.signal[:0], [], 0)
        self._update_axis_labels()
        self._update_section_marks()
        self.setWindowTitle(tr("Video-to-Funscript Generator {0}", __version__))
        self.file_label.setText("")
        self.close_act.setVisible(False)
        self.time_label.setText("0:00.00 / 0:00.00")
        self._refresh_position()
        self._status(tr("Video geschlossen – neues Video öffnen (Strg+O) oder hierher ziehen"))

    def _read_frame(self):
        if not self.cap:
            return
        ok, frame = self.cap.read()
        if not ok:
            was_playing = self.timer.isActive()
            self.pause()
            if was_playing:
                self._video_ended()
            return
        idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
        if idx != self._cur_idx and self.view.skeleton_shown():
            self.view.set_skeleton(None)        # the points belong to the frame they were found in
        self._cur_idx = idx
        self._frame = frame
        self.view.set_frame(frame)
        self.slider.blockSignals(True)
        self.slider.setValue(idx)
        self.slider.blockSignals(False)
        ms = idx / self.fps * 1000
        self._show_time(ms)

    def _show_time(self, ms: float):
        self._update_countdown(ms)
        self._update_rail_speed(ms)
        self._update_track_overlay(ms)
        self.curve.set_playhead(ms)
        self.rail.set_position(position_at(self.stroke_actions(), ms))
        self.time_label.setText(f"{_fmt(ms)} / {_fmt(self.frame_count / self.fps * 1000)}")

    def _update_rail_speed(self, ms: float):
        """The stroke rail shows how fast the script moves here (heatmap colours)."""
        acts = self.stroke_actions()
        if len(acts) < 2:
            self.rail.set_speed(0.0)
            return
        i = bisect.bisect_right([a.at for a in acts], ms)
        speed = 0.0
        if 0 < i < len(acts):
            a, b = acts[i - 1], acts[i]
            speed = abs(b.pos - a.pos) * 1000.0 / max(1, b.at - a.at)
        self.rail.set_speed(speed)

    # ------------------------------------------------------------ script check
    def _update_issues(self):
        """Check the stroke script again (cheap): marks in the curve, count on the button."""
        acts = self.stroke_actions()
        lost = [(a, b) for a, b, _ar in self._lost_stretches()]
        self.issues = check_script(acts, self.panel.generator_settings().max_speed, self.cuts, lost) if acts else []
        self.curve.set_issues([(it.start_ms, it.end_ms) for it in self.issues])
        n = len(self.issues)
        self.check_btn.setText(tr("{0} prüfen", n) if n else (tr("Prüfung ok") if acts else tr("Prüfung")))
        self.check_btn.setToolTip(issue_summary(self.issues) + tr(" · F3 springt zur nächsten Stelle")
                                  if acts else tr("Noch kein Script"))

    def jump_issue(self, direction: int):
        if not self.issues:
            self._status(tr("Prüfung: nichts aufgefallen") if self.stroke_actions() else tr("Noch kein Script"))
            return
        cur = self._cur_ms()
        if direction > 0:
            it = next((i for i in self.issues if i.start_ms - 1000 > cur + 50), self.issues[0])
        else:
            it = next((i for i in reversed(self.issues) if i.start_ms - 1000 < cur - 50), self.issues[-1])
        self._show_issue(it)

    def _show_issue(self, it):
        self.seek_ms(max(0, it.start_ms - 1000))            # a second before, to see it coming
        k = self.issues.index(it) + 1 if it in self.issues else 0
        self._status(tr("Prüfung {0}/{1}: {2}", k, len(self.issues), describe_issue(it)))

    def _issue_menu(self):
        menu = QMenu(self)
        head = menu.addAction(issue_summary(self.issues) if self.stroke_actions() else tr("Noch kein Script"))
        head.setEnabled(False)
        if self.issues:
            menu.addSeparator()
            for it in self.issues[:40]:
                menu.addAction(describe_issue(it)).triggered.connect(
                    lambda _c=False, x=it: self._show_issue(x))
            if len(self.issues) > 40:
                more = menu.addAction(tr("… und {0} weitere (F3)", len(self.issues) - 40))
                more.setEnabled(False)
        self._run_menu(menu, self.check_btn.mapToGlobal(self.check_btn.rect().bottomLeft()))

    # ------------------------------------------------------------ cuts
    def _show_cuts(self):
        self.curve.set_cuts(self.cuts, self._cut_start)
        self.cut_btn.setChecked(self._cut_start is not None)          # a cut waits for its end
        self.cut_btn.setText(tr("Schnitt-Ende") if self._cut_start is not None else tr("Schnitt"))

    def _cuts_changed(self):
        self._show_cuts()
        self._update_issues()
        if self.video_path:
            try:
                save_cuts(self.video_path, self.cuts)
            except OSError as e:
                self._status(tr("Schnitte nicht gespeichert: {0}", e))

    def add_cut(self, a: float, b: float) -> str:
        """Cut from a to b (ms); overlapping cuts become one."""
        a, b = sorted((int(a), int(b)))
        if b - a < 200:
            return tr("Schnitt zu kurz – erst zum Ende spulen")
        merged = []
        for c in sorted([*self.cuts, (a, b)]):
            if merged and c[0] <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], c[1]))
            else:
                merged.append(c)
        self.cuts = merged
        self._cuts_changed()
        return tr("Schnitt {0} – {1}: der Player springt darüber", _fmt(a), _fmt(b))

    def toggle_cut(self):
        """X: first press marks where the cut starts, the second where it ends."""
        if not self.cap:
            self._show_cuts()
            return
        ms = self._cur_ms()
        if self._cut_start is None:
            self._cut_start = ms
            self._show_cuts()
            self._status(tr("Schnitt beginnt bei {0} – zum Ende spulen und nochmal X", _fmt(ms)))
            return
        start, self._cut_start = self._cut_start, None
        self._status(self.add_cut(start, ms))
        self._show_cuts()

    def cut_to_next_move(self):
        """Shift+X: cut from here to shortly before the next stroke (or the end)."""
        if not self.cap:
            return
        ms = self._cur_ms()
        acts = self.stroke_actions()
        lead = self.player.lead_ms()             # the countdown then runs up to the stroke
        nxt = next((a.at for a, b in zip(acts, acts[1:]) if a.at > ms + lead and abs(b.pos - a.pos) >= 10),
                   None)
        end = nxt - lead if nxt is not None else self.frame_count / self.fps * 1000
        self._cut_start = None
        self._status(self.add_cut(ms, end))

    def remove_cut_here(self):
        ms = self._cur_ms()
        keep = [c for c in self.cuts if not c[0] <= ms <= c[1]]
        if len(keep) == len(self.cuts):
            self._status(tr("Hier ist kein Schnitt"))
            return
        self.cuts = keep
        self._cuts_changed()
        self._status(tr("Schnitt entfernt"))

    def _player_skip(self, media_ms: float) -> bool:
        """Player: jump over cuts and (if wanted) long stretches without strokes."""
        if not self.player_mode:
            return False
        cuts = self.cuts if self.player.cuts_cb.isChecked() else ()
        quiet = ()
        if self.player.quiet_cb.isChecked():
            acts = self.stroke_actions()
            lead = self.player.lead_ms()
            key = (id(acts), len(acts), acts[0].at if acts else 0, acts[-1].at if acts else 0, lead)
            if self._quiet_cache[0] != key:
                self._quiet_cache = (key, quiet_stretches(acts, self.frame_count / self.fps * 1000,
                                                          lead_ms=lead))
            quiet = self._quiet_cache[1]
        to = skip_target(media_ms, cuts, quiet)
        if to is None or to <= media_ms:
            return False
        if to >= self.frame_count / self.fps * 1000 - 100:
            self.pause()
            self._video_ended()
        else:
            self.seek_ms(to)
            self._skip_resume = resume_ms(to, self.stroke_actions(), pause_ms=0)
            self._status(tr("Übersprungen bis {0}", _fmt(to)))
        return True

    # ------------------------------------------------------------ bookmarks
    def _show_bookmarks(self):
        self.curve.set_bookmarks(self.bookmarks)
        self.slider.set_marks(int(ms / 1000 * self.fps) for ms, _n in self.bookmarks)

    def _bookmarks_changed(self):
        self._show_bookmarks()
        if self.video_path:
            try:
                save_bookmarks(self.video_path, self.bookmarks)
            except OSError as e:
                self._status(tr("Lesezeichen nicht gespeichert: {0}", e))

    def add_bookmark_at(self, ms: float, name: str) -> None:
        ms = int(ms)
        keep = [b for b in self.bookmarks if abs(b[0] - ms) > 500]
        self.bookmarks = sorted([*keep, (ms, name.strip() or tr("Lesezeichen"))])
        self._bookmarks_changed()
        self._status(tr("Lesezeichen „{0}“ bei {1}", name, _fmt(ms)))

    def add_bookmark(self):
        if not self.cap:
            return
        from PySide6.QtWidgets import QInputDialog

        ms = self._cur_ms()
        name, ok = QInputDialog.getText(self, tr("Lesezeichen setzen"), tr("Name für {0}:", _fmt(ms)),
                                        text=tr("Kapitel {0}", len(self.bookmarks) + 1))
        if ok:
            self.add_bookmark_at(ms, name)

    def jump_bookmark(self, direction: int):
        if not self.bookmarks:
            self._status(tr("Noch keine Lesezeichen – mit B setzen"))
            return
        cur = self._cur_ms()
        if direction > 0:
            nxt = next((b for b in self.bookmarks if b[0] > cur + 50), None)
        else:   # a bit of slack so pressing again while playing goes further back
            nxt = next((b for b in reversed(self.bookmarks) if b[0] < cur - 1000), None)
        if nxt is None:
            self._status(tr("Kein weiteres Lesezeichen"))
            return
        self.seek_ms(nxt[0])
        self._status(tr("Lesezeichen: {0} ({1})", nxt[1], _fmt(nxt[0])))

    def delete_bookmark(self):
        if not self.bookmarks:
            return
        cur = self._cur_ms()
        b = min(self.bookmarks, key=lambda x: abs(x[0] - cur))
        if abs(b[0] - cur) > 2000:
            self._status(tr("Hier ist kein Lesezeichen (bis 2 s Abstand)"))
            return
        self.bookmarks.remove(b)
        self._bookmarks_changed()
        self._status(tr("Lesezeichen „{0}“ gelöscht", b[1]))

    def _fill_bookmark_menu(self):
        for a in self._bookmark_items:
            self._bookmark_menu.removeAction(a)
        self._bookmark_items = []
        if not self.bookmarks:
            a = self._bookmark_menu.addAction(tr("(keine Lesezeichen)"))
            a.setEnabled(False)
            self._bookmark_items.append(a)
        for ms, name in self.bookmarks:
            a = self._bookmark_menu.addAction(f"{_fmt(ms)}   {name}")
            a.triggered.connect(lambda _c=False, t=ms: self.seek_ms(t))
            self._bookmark_items.append(a)

    # ------------------------------------------------------------ patterns
    def ensure_patterns(self):
        """The pattern control (created on first use, also for the phone remote)."""
        from .pattern_dialog import PatternDialog

        if self.pattern_dialog is None:
            self.pattern_dialog = PatternDialog(self.devices, self)
        return self.pattern_dialog

    def open_players(self):
        from .players_ui import PlayersDialog

        if getattr(self, "_players_dialog", None) is None:
            self._players_dialog = PlayersDialog(self.external, self)
        self._players_dialog.show()
        self._players_dialog.raise_()

    def open_remote(self):
        from .remote_ui import RemoteDialog

        RemoteDialog(self.remote, self).exec()

    def open_patterns(self):
        self.ensure_patterns()
        self.pattern_dialog.show()
        self.pattern_dialog.raise_()
        self.pattern_dialog.activateWindow()

    # safety timer source: the video drives the device while it plays
    def safety_active(self) -> bool:
        dev = self.devices.device
        return self.timer.isActive() and dev is not None and dev.connected

    def safety_pause(self) -> bool:
        if self.timer.isActive():
            self.pause()
            return True
        return False

    def safety_resume(self) -> None:
        if not self.timer.isActive():
            self.toggle_play()

    def _stop_patterns(self):
        if self.pattern_dialog is not None and self.pattern_dialog.running:
            self.pattern_dialog.stop()

    # ------------------------------------------------------------ rhythm
    def insert_rhythm(self):
        if not self.cap or not self.curve.editable:
            self._status(tr("Rhythmus einfügen geht im Modus „Bearbeiten“ mit geladenem Video"))
            return
        from .rhythm_dialog import RhythmDialog

        sel = sorted(self.curve.selection)
        acts = self.curve.actions
        if len(sel) >= 2:
            a, b = acts[sel[0]].at, acts[sel[-1]].at
        else:
            a = self._cur_ms()
            b = min(a + 10_000, self.curve.duration_ms)
        dlg = RhythmDialog(a, b, self.curve.duration_ms, self)
        if dlg.exec():
            self.apply_rhythm(**dlg.values())

    def music_script(self):
        if not self.cap or not self.curve.editable or not self.video_path:
            self._status(tr("Script aus Musik geht im Modus „Bearbeiten“ mit geladenem Video"))
            return
        from .music_dialog import MusicScriptDialog

        sel = sorted(self.curve.selection)
        acts = self.curve.actions
        a, b = (acts[sel[0]].at, acts[sel[-1]].at) if len(sel) >= 2 else (0, self.curve.duration_ms)
        dlg = MusicScriptDialog(a, b, self.curve.duration_ms, self)
        if dlg.exec():
            self.apply_music_script(**dlg.values())

    def apply_music_script(self, start_ms, end_ms, every=1, sensitivity=0.5, lo=0, hi=100, offset_ms=0):
        from .. import music

        start_ms, end_ms = sorted((start_ms, end_ms))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            beats = music.beats_from_file(self.video_path, sensitivity, start_ms / 1000,
                                          (end_ms - start_ms) / 1000)
        except (RuntimeError, OSError) as e:
            self._status(tr("Tonspur nicht lesbar: {0}", e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        new = [x for x in music.script_from_beats(beats, every, lo, hi, offset_ms) if start_ms <= x.at <= end_ms]
        if not new:
            self._status(tr("Keine Schläge gefunden (keine Tonspur oder zu leise). Empfindlicher stellen?"))
            return
        self.curve.editStarted.emit()
        keep = [x for x in self.curve.actions if not start_ms <= x.at <= end_ms]
        self.curve.actions = sorted(keep + new, key=lambda x: x.at)
        ids = {id(x) for x in new}
        self.curve.actionsEdited.emit()
        self.curve.set_selection({i for i, x in enumerate(self.curve.actions) if id(x) in ids})
        bpm = music.tempo(beats)
        self._status(tr("Script aus Musik: {0} Schläge", len(beats)) + (tr(", ≈ {0:.0f} pro Minute", bpm) if bpm else ""))

    def apply_rhythm(self, start_ms, end_ms, bpm, lo, hi, pattern):
        self.curve.editStarted.emit()
        self.curve.actions, sel = rhythm(self.curve.actions, start_ms, end_ms, bpm, lo, hi, pattern)
        self.curve.actionsEdited.emit()
        self.curve.set_selection(sel)
        self._status(tr("Rhythmus eingefügt: {0} Punkte, {1} Hübe/min", len(sel), bpm))

    def _section_edge_moved(self, idx: int, which: str, ms: int):
        tb = self.panel.tracking
        self._status(tb.move_edge(idx, which, ms))
        if which == "start" and tb.sections:   # show the new start: the areas apply from there
            self.seek_ms(min(tb.sections, key=lambda s: abs(s.start_ms - ms)).start_ms)

    def _update_countdown(self, ms: float):
        """Player: count the last seconds down before the script starts, and before it
        goes on after a pause (a skipped cut, or just a long still stretch)."""
        sec = None
        if self.player_mode and self.player.countdown_cb.isChecked():
            start = resume_ms(ms, self.stroke_actions())
            if start is None and self._skip_resume is not None and ms < self._skip_resume:
                start = self._skip_resume            # just jumped over a cut: count down to it too
            if start is not None and 0 < start - ms <= self.player.lead_ms():
                sec = math.ceil((start - ms) / 1000)
        self.view.set_countdown(sec)

    def seek_frame(self, idx: int):
        if not self.cap:
            return
        idx = max(0, min(int(idx), self.frame_count - 1))
        self._stat_ms = None                     # a jump is not played time
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        self._read_frame()
        if self.timer.isActive():   # keep playing from the new position
            self._play_origin_ms = idx / self.fps * 1000
            self._clock.restart()
            self.audio.seek(self._play_origin_ms)

    def seek_ms(self, ms: int):
        self.curve.follow = True
        self.seek_frame(int(round(ms / 1000 * self.fps)))

    def step_frames(self, n: int):
        if self.cap:
            self.pause()
            self.seek_frame(self.slider.value() + n)

    def _media_ms(self) -> float:
        return self._play_origin_ms + self._clock.elapsed()

    def _next_frame(self):
        """Playback tick (every 10 ms): wall clock drives video and device."""
        if not self.cap:
            return
        media_ms = self._media_ms()
        self._record_play(media_ms)
        if self._player_skip(media_ms):
            return
        target = int(media_ms * self.fps / 1000)
        if target >= self.frame_count:
            self.pause()
            self._video_ended()
            return
        behind = target - self._cur_idx
        if behind > 30:                       # far behind (slow decoder): jump
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            self._read_frame()
        elif behind >= 1:
            for _ in range(behind - 1):       # drop frames to catch up
                if not self.cap.grab():
                    break
            self._read_frame()
        if self.timer.isActive():
            self.audio.sync(media_ms)
            self.devices.tick(media_ms, True)
            self.rail.set_device_position(self.devices.device_position())

    def toggle_play(self):
        if not self.cap:
            return
        if self.timer.isActive():
            self.pause()
        else:
            self.curve.follow = True
            self._stop_patterns()
            if self.external.running:
                self.external.disconnect_player()
            if self._cur_idx >= self.frame_count - 1:
                self.seek_frame(0)
            self._play_origin_ms = max(0, self._cur_idx) / self.fps * 1000
            self._clock.start()
            self.devices.set_actions(self.stroke_actions())
            self.devices.set_axes(self.all_axes())
            self.timer.start(10)
            self.audio.play(self._play_origin_ms)
            self.play_btn.setIcon(theme.icon("pause", theme.TEXT_2, 16))

    def _record_play(self, media_ms: float):
        """Statistics: time played with a script and the strokes passed meanwhile."""
        last, self._stat_ms = self._stat_ms, media_ms
        acts = self.stroke_actions()
        if last is None or not acts or not self.video_path or not 0 < media_ms - last < 500:
            return
        times = [a.at for a in acts]
        self._stat_half += bisect.bisect_right(times, media_ms) - bisect.bisect_right(times, last)
        whole, self._stat_half = divmod(self._stat_half, 2)
        self.stats.add("video", Path(self.video_path).stem, (media_ms - last) / 1000, whole)

    def open_stats(self):
        from .stats_dialog import StatsDialog

        self.stats.flush()
        StatsDialog(self).exec()

    def pause(self):
        self._stat_ms = None
        self.stats.flush()
        self.timer.stop()
        self.audio.pause()
        self.devices.tick(0, False)
        self.rail.set_device_position(self.devices.device_position())
        self.play_btn.setIcon(theme.icon("play", theme.TEXT_2, 16))

    # ------------------------------------------------------------ tracking
    def _method_changed(self, mode: str):
        if self.player_mode:
            return
        self.view.set_mode("track" if mode == "track" else "roi")
        if mode == "track":       # bring the section buttons into view
            QTimer.singleShot(0, self._side_scroll,       # context: dropped if the window is gone
                              lambda: self._side_scroll.ensureWidgetVisible(self.panel.tracking, 0, 16))
        self._update_track_overlay(self._cur_ms())
        self._update_section_marks()

    def _cur_ms(self) -> float:
        return max(0, self._cur_idx) / self.fps * 1000 if self.cap else 0.0

    def auto_find(self):
        """Find toy, reference and stroke direction from the playhead on, as a new section."""
        if self.player_mode or not self.video_path or self.panel.mode != "track":
            self._status(tr("Automatisch finden: erst ein Video öffnen und „Tracking“ als Methode wählen"))
            return
        from ..autofind import find_in_video

        self.pause()
        ms = self._cur_ms()
        self._status(tr("Suche, was sich im Rhythmus bewegt …"))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found = find_in_video(self.video_path, ms)
        finally:
            QApplication.restoreOverrideCursor()
        if found is None:
            self._status(tr("Automatisch finden: hier bewegt sich nichts deutlich im Rhythmus – an eine Stelle mit gleichmäßigen Hüben spulen oder selbst markieren"))
            return
        tb = self.panel.tracking
        i = tb.index_at(ms)
        replaced = i is not None and abs(tb.sections[i].start_ms - ms) <= 1000 / self.fps * 0.6
        if replaced:                            # marked here already: the found areas replace them
            tb.delete_at(ms)
        tb.add_box(found.toy, ms, (), outline=found.outline)
        if found.body is not None:
            tb.add_box(found.body, ms, ())
        if found.stroke is not None:
            tb.set_stroke(ms, found.stroke)
        self._update_track_overlay(ms)
        sure = tr("sicher") if found.sure >= 0.7 else tr("unsicher – bitte genau prüfen")
        self._status(tr("Gefunden ({0}): Toy umrandet{1}, Hub-Richtung, etwa {2:.0f} Hübe/min – prüfen, dann analysieren", sure, tr(", Bezug") if found.body else tr(", kein Bezug"), found.rate * 60)
                     + (tr(" · die Kästchen von vorher ersetzt, Strg+Z holt sie zurück") if replaced else ""))

    def suggest_boxes(self):
        """KI-Vorschlag: find body parts in the shown frame and offer them as boxes."""
        if self.player_mode or self._frame is None or self.panel.mode != "track":
            self._status(tr("KI-Vorschlag: erst ein Video öffnen und „Tracking“ als Methode wählen"))
            return
        if self._pose_worker is not None and self._pose_worker.isRunning():
            self._status(tr("KI-Vorschlag: die KI sucht noch, einen Moment …"))
            return
        if not pose_ui.ensure_models(self):
            return
        self.pause()
        frame, ms = self._frame.copy(), self._cur_ms()
        first = not pose_ui.PoseWorker.loaded()
        self._status(tr("KI-Vorschlag: KI wird gestartet (beim ersten Mal ein paar Sekunden) und sucht Körperteile …") if first else tr("KI-Vorschlag: suche Körperteile …"))
        btn = self.panel.tracking.ai_btn
        btn.setEnabled(False)
        btn.setText(tr("KI startet …") if first else tr("KI sucht …"))
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self._pose_busy = True
        w = pose_ui.PoseWorker(frame)
        w.done.connect(lambda people: self._show_suggestions(people, frame.shape, ms))
        w.failed.connect(lambda e: (self._pose_search_ended(),
                                    self._status(tr("KI-Vorschlag fehlgeschlagen: {0}", e))))
        w.finished.connect(self._pose_search_ended)      # also when neither signal came
        self._pose_worker = w
        w.start()

    def _pose_search_ended(self):
        if not getattr(self, "_pose_busy", False):
            return
        self._pose_busy = False
        btn = self.panel.tracking.ai_btn
        btn.setEnabled(True)
        btn.setText(tr("KI-Vorschlag"))
        QApplication.restoreOverrideCursor()

    def _show_suggestions(self, people, shape, ms: float):
        self._pose_search_ended()            # before the menu opens: normal cursor, button back
        if abs(self._cur_ms() - ms) > 1:
            self._status(tr("KI-Vorschlag verworfen: das Video ist inzwischen weitergelaufen"))
            return
        menu = self._suggestion_menu(people, shape)
        if menu is None:
            self._status(tr("KI-Vorschlag: keine Person erkannt – Kästchen bitte selbst aufziehen"))
            return
        self._show_skeleton(people, shape)
        btn = self.panel.tracking.ai_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _show_skeleton(self, people, shape) -> None:
        """Draw the recognised points and lines into the video: click one to put a box there."""
        from .. import pose
        h, w = shape[:2]
        self._skel_people = people
        self._skel_shape = shape
        self.view.set_skeleton([(p.pts / np.array([w, h]), p.conf >= pose.SURE) for p in people])
        self._status(tr("KI-Vorschlag: aus der Liste wählen oder im Bild einen Punkt (Gelenk, Hand, Finger) bzw. eine Linie (z.B. Oberschenkel) anklicken"))

    def _skeleton_picked(self, k: int, target) -> None:
        from .. import pose
        people = getattr(self, "_skel_people", None) or []
        if not 0 <= k < len(people):
            return
        b = pose.pick_box(people[k], target)
        if b is None:
            return
        h, w = self._skel_shape[:2]
        x, y, bw, bh, ang = b
        box = (float(x / w), float(y / h), float(bw / w), float(bh / h)) + ((float(ang),) if ang else ())
        self._box_drawn(box, keep_first=True)
        name = pose.point_name(target) if isinstance(target, int) else pose.line_name(*target)
        self._status(tr("Kästchen auf „{0}“ · {1}", name, self.status_label.text()))

    def _suggestion_menu(self, people, shape) -> QMenu | None:
        """Menu with one entry per visible body part; choosing one adds its box."""
        from .. import pose
        found = pose.suggestions(people)
        if not found:
            return None
        h, w = shape[:2]
        menu = QMenu(self)
        head = menu.addAction(plural(len(people), "{0} Person erkannt – Kästchen setzen:", "{0} Personen erkannt – Kästchen setzen:"))
        head.setEnabled(False)          # a heading (addSection shows no text in every style)
        menu.addSeparator()
        for i, region, (x, y, bw, bh) in found:
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(float(w), x + bw), min(float(h), y + bh)
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            box = (float(x0 / w), float(y0 / h), float((x1 - x0) / w), float((y1 - y0) / h))
            who = tr("Person {0}: ", i + 1) if len(people) > 1 else ""
            menu.addAction(who + region.label).triggered.connect(
                lambda _c=False, b=box: self._pick_from_menu(b))
        menu.addSeparator()
        menu.addAction(tr("Selbst im Bild wählen (Punkt oder Linie anklicken)"))
        return menu

    def _pick_from_menu(self, box) -> None:
        self.view.set_skeleton(None)
        self._box_drawn(box, keep_first=True)

    def _box_drawn(self, box, keep_first: bool = False, outline=()):
        """keep_first: a box from the KI, it never replaces or corrects area 1."""
        shown = self.view._overlay if self.view._overlay is not None else self.view.tracks()
        msg = self.panel.tracking.add_box(box, self._cur_ms(), shown, keep_first=keep_first, outline=outline)
        img = self.view._img
        if img is not None:     # how big the area is in the analysis picture
            aw = self.panel.width.currentData()
            px = min(box[2] * aw, box[3] * aw * img.height() / max(1, img.width()))
            if px < 24:
                msg += (tr(" · kleines Kästchen ({0:.0f} px bei {1} px Auflösung): unter Optionen eine höhere Auflösung wählen", px, aw))
        self._status(msg)

    def _outline_drawn(self, box, outline):
        self._box_drawn(box, outline=outline)

    def _stroke_drawn(self, line):
        tb = self.panel.tracking
        self._status(tb.set_stroke(self._cur_ms(), line))
        tb.set_draw_mode("outline" if tb.outline_btn.isChecked() else "box")

    def _toggle_draw(self, mode: str):
        if self.panel.mode != "track":
            self._status(tr("Nur bei Analyse-Methode „Tracking“"))
            return
        tb = self.panel.tracking
        tb.set_draw_mode("box" if tb.draw_mode() == mode else mode)

    def _area_menu(self, area: int, pos):
        """Right click on an area: its role, outline, remove."""
        tb = self.panel.tracking
        ms = self._cur_ms()
        i = tb.index_at(ms)
        if i is None or area >= len(tb.sections[i].boxes):
            return
        sec = tb.sections[i]
        menu = QMenu(self)
        head = menu.addAction(tr("Bereich {0}", area + 1))
        head.setEnabled(False)
        menu.addSeparator()
        toy = menu.addAction(tr("Das ist das Toy / die Maschine (bewegt sich)"))
        toy.setCheckable(True)
        toy.setChecked(area == 0)
        body = menu.addAction(tr("Das ist der Körper / Bezug (bewegt sich wenig)"))
        body.setCheckable(True)
        body.setChecked(area > 0)
        body.setEnabled(len(sec.boxes) > 1)
        menu.addSeparator()
        flat = menu.addAction(tr("Umrandung entfernen (wieder Rechteck)"))
        flat.setEnabled(bool(sec.outline(area)))
        rm = menu.addAction(tr("Bereich entfernen"))
        chosen = self._run_menu(menu, pos)
        if chosen is toy:
            self._status(tb.set_role(ms, area, "toy"))
        elif chosen is body:
            self._status(tb.set_role(ms, area, "body"))
        elif chosen is flat:
            self._status(tb.clear_outline(ms, area))
        elif chosen is rm:
            self._status(tb.remove_area(ms, area) + tr(" – Strg+Z holt ihn zurück"))

    def _section_menu(self, pos):
        """Right click in the video beside the areas: delete only from a menu."""
        if self.panel.mode != "track":
            return
        tb = self.panel.tracking
        ms = self._cur_ms()
        i = tb.index_at(ms)
        menu = QMenu(self)
        head = menu.addAction(tr("Abschnitt {0}", i + 1) if i is not None else tr("Kein Abschnitt an dieser Stelle"))
        head.setEnabled(False)
        menu.addSeparator()
        stroke = menu.addAction(tr("Hub-Richtung entfernen"))
        stroke.setEnabled(i is not None and tb.sections[i].stroke is not None)
        delete = menu.addAction(tr("Abschnitt hier löschen"))
        delete.setEnabled(i is not None)
        chosen = self._run_menu(menu, pos)
        if chosen is stroke:
            self._status(tb.set_stroke(ms, None))
        elif chosen is delete:
            self._status(tb.delete_at(ms) + tr(" – Strg+Z holt ihn zurück"))

    def _run_menu(self, menu: QMenu, pos):
        return menu.exec(pos)

    def _box_rotated(self, area: int, angle: float):
        self._status(self.panel.tracking.rotate_box(self._cur_ms(), area, angle))

    def _box_edited(self, area: int, box):
        self._status(self.panel.tracking.update_box(self._cur_ms(), area, box))

    def _box_corrected(self, area: int, box):
        self._status(self.panel.tracking.correct_area(self._cur_ms(), area, box))
        self._update_track_overlay(self._cur_ms())

    def _track_cmd(self, fn, *args):
        """Tracking commands from the menu: only in mode Tracking."""
        if self.panel.mode != "track":
            self._status(tr("Nur bei Analyse-Methode „Tracking“"))
            return
        self._status(fn(*args, self._cur_ms()))

    def _clear_tracking(self):
        if self.panel.mode != "track":
            self._status(tr("Nur bei Analyse-Methode „Tracking“"))
            return
        self.panel.tracking._clear_clicked()

    def _edit_points(self) -> list[int]:
        out = []
        for s in self.panel.tracking.sections:
            out.append(s.start_ms)
            out += [k[0] for k in s.keys]
        return out

    def _lost_stretches(self) -> list[tuple[float, float, list[int]]]:
        """(start ms, end ms, areas) where an area could not be followed."""
        m = self.motion
        if m is None or m.track_lost is None:
            return []
        anyl = m.track_lost.any(axis=1) & (m.track_section >= 0)
        out, i, n = [], 0, len(anyl)
        while i < n:
            if anyl[i]:
                j = i
                while j < n and anyl[j]:
                    j += 1
                areas = sorted({int(a) + 1 for a in np.nonzero(m.track_lost[i:j].any(axis=0))[0]})
                out.append((i / m.fps * 1000, j / m.fps * 1000, areas))
                i = j
            else:
                i += 1
        return out

    def _sections_changed(self):
        secs = list(self.panel.tracking.sections)
        if not self._track_restoring and secs != self._track_shadow:
            self.undo_stack.append((TRACK_UNDO, self._track_shadow))     # Strg+Z brings it back
            del self.undo_stack[:-100]
            self.redo_stack.clear()
        self._track_shadow = secs
        self._update_track_overlay(self._cur_ms())
        self._update_section_marks()
        if self.video_path and self.panel.mode == "track":
            # keep the preparation next to the video, for the batch and for next time
            try:
                save_prep(self.video_path, self.panel.tracking.sections)
            except OSError as e:
                self._status(tr("Tracking-Vorbereitung nicht gespeichert: {0}", e))

    def _update_section_marks(self):
        """Sections and calibration moments as marks in the curve."""
        tb = self.panel.tracking
        if self.panel.mode != "track":
            self.curve.set_sections([])
            return
        marks = []
        for i, a, b in tb._ranges():
            sec = tb.sections[i]
            marks.append((a, b, sec.calib_in_ms, sec.calib_out_ms, i, sec.end_ms is not None))
        self.curve.set_sections(marks)
        self.curve.set_lost([(a, b) for a, b, _ar in self._lost_stretches()])

    def _update_track_overlay(self, ms: float):
        if self.view.mode != "track":
            self.view.set_track_overlay(None)
            return
        # at a section start or a correction the marked areas can be edited
        editable = any(abs(k - ms) <= 1000 / self.fps * 0.6 for k in self._edit_points())
        self.view.set_editable(editable)
        tb = self.panel.tracking
        self.view.analysis_width = int(self.panel.width.currentData() or 320)
        self.view.set_area_info(tb.outlines_at(ms), tb.stroke_at(ms))
        m = self.motion
        if (m is not None and m.tracks is not None and m.track_section is not None and len(m.tracks)
                and not editable and self.panel.tracking.index_at(ms) is not None):
            i = min(len(m.tracks) - 1, max(0, int(ms / 1000 * m.fps)))
            sec = int(m.track_section[i])
            if sec >= 0 and m.track_sizes is not None and sec < len(m.track_sizes):
                geom = m.track_geom[i] if m.track_geom is not None else [(1.0, 0.0)] * len(m.tracks[i])
                lost = m.track_lost[i] if m.track_lost is not None else [False] * len(m.tracks[i])
                boxes = []
                for (cx, cy), (w, h), (sc, ang), lo in zip(m.tracks[i], m.track_sizes[sec], geom, lost):
                    if np.isnan(cx) or np.isnan(w):
                        continue
                    sc = 1.0 if np.isnan(sc) else float(sc)
                    ang = 0.0 if np.isnan(ang) else float(ang)
                    boxes.append((cx - w * sc / 2, cy - h * sc / 2, w * sc, h * sc, ang, bool(lo)))
                self.view.set_track_overlay(boxes)
                return
        # editable here, or not analysed (yet): show the areas as marked
        self.view.set_track_overlay(None)
        self.view.set_tracks(self.panel.tracking.boxes_at(ms))

    # ------------------------------------------------------------ player mode
    @property
    def player_mode(self) -> bool:
        return self.side.currentWidget() is self.player

    def set_player_mode(self, on: bool):
        on = bool(on)
        self.side.setCurrentWidget(self.player if on else self.side.widget(0))
        self._apply_sides()
        self.mode_player.setChecked(on)
        self.mode_edit.setChecked(not on)
        self.player_action.setChecked(on)
        self.curve.editable = not on
        # player: only the video, no tracking areas or analysis rectangle
        if on:
            self.view.reset_zoom()
            self.view.set_mode("play")
        else:
            self.view.set_mode("track" if self.panel.mode == "track" else "roi")
            self._update_track_overlay(self._cur_ms())
        self.save_action.setEnabled(not on)
        self.prev_btn.setVisible(on)
        self.next_btn.setVisible(on)
        has_list = bool(self.player.playlist.items)
        self.prev_btn.setEnabled(on and has_list)
        self.next_btn.setEnabled(on and has_list)
        self.edit_tools.setVisible(not on)
        self.curve_hint.setText(tr("Nur ansehen") if on else tr("Umschalt+Ziehen wählt aus · Fläche ziehen scrollt"))
        self.curve_hint.setToolTip(
            tr("Zum Bearbeiten in den Modus „Bearbeiten“ wechseln") if on else
            tr("Punkt ziehen = verschieben (alle ausgewählten zusammen)\nUmschalt/Strg+Ziehen = Auswahlrechteck, Strg+Klick = Punkt an/aus, Strg+A = alle\nFläche ziehen = scrollen, Klick = zur Stelle springen, Mausrad = Zoom\nDoppelklick = Punkt hinzufügen, Rechtsklick/Entf = löschen\nStrg+C/V = kopieren/einfügen, Strg+G = glätten, Strg+↑/↓ = Hub verstärken/abschwächen"))
        self.qsettings.setValue("mode", "player" if on else "edit")

    def play_index(self, index: int):
        """Open playlist entry `index` and start playback (player mode)."""
        pl = self.player.playlist
        if not 0 <= index < len(pl.items):
            return
        if not self.player_mode:
            self.set_player_mode(True)
        self.player.set_current(index)
        self.open_video(pl.items[index])
        if self.video_path and Path(self.video_path) == Path(pl.items[index]):
            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
            skip = (intro_skip_ms(self.stroke_actions(), self.player.lead_ms())
                    if self.player.intro_cb.isChecked() else 0)
            if skip:
                self.seek_ms(skip)
            if not self.timer.isActive():
                self.toggle_play()
        self._status(tr("Playlist {0}/{1}: {2}", index + 1, len(pl.items), Path(pl.items[index]).name)
                     + ("" if self.stroke_actions() else tr("  (kein Script)")))

    def open_sibling(self, direction: int):
        """Next/previous video in the same folder (quick preparing of many videos)."""
        if not self.video_path:
            return
        cur = Path(self.video_path)
        vids = sorted(p for p in cur.parent.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
        if cur not in vids:
            return
        i = vids.index(cur) + direction
        if 0 <= i < len(vids):
            self.open_video(str(vids[i]))
            prep = tr(" · Tracking vorbereitet") if prep_path(vids[i]).is_file() else ""
            self._status(tr("Video {0}/{1} im Ordner: {2}{3}", i + 1, len(vids), vids[i].name, prep))
        else:
            self._status(tr("Kein weiteres Video in diesem Ordner"))

    def play_next(self):
        i = self.player.playlist.next_index()
        if i is None:
            self._status(tr("Ende der Playlist"))
            return
        self.play_index(i)

    def play_prev(self):
        # like music players: more than 3 s in -> restart this video
        if self.timer.isActive() and self._media_ms() > 3000:
            self.seek_frame(0)
            return
        i = self.player.playlist.prev_index()
        if i is not None:
            self.play_index(i)

    def _video_ended(self):
        if self.player_mode and self.player.playlist.items:
            QTimer.singleShot(0, self.play_next)

    def _apply_sides(self, full: bool | None = None):
        """Which side panels show: folded away by the user, no script panel in the
        player, nothing but the video in full screen."""
        full = self.isFullScreen() if full is None else full
        left = self.left_action.isChecked() and not full
        right = self.right_action.isChecked() and not full and not self.player_mode
        self.side.setVisible(left)
        self._side_sep.setVisible(left)
        self.script_side.setVisible(right)
        self._script_sep.setVisible(right)

    def _sides_toggled(self, _on: bool = False):
        self.qsettings.setValue("show_left", "true" if self.left_action.isChecked() else "false")
        self.qsettings.setValue("show_right", "true" if self.right_action.isChecked() else "false")
        self._apply_sides()

    def toggle_fullscreen(self):
        full = not self.isFullScreen()
        for w in (self.menuBar(), self._toolbar, self.statusBar(), self.curve_box):
            w.setVisible(not full)
        self._apply_sides(full)
        if full:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        elif getattr(self, "_was_maximized", False):
            self.showMaximized()
        else:
            self.showNormal()

    # ------------------------------------------------------------ sound
    def _load_audio_settings(self):
        try:
            vol = int(self.qsettings.value("audio/volume", 80))
        except (TypeError, ValueError):
            vol = 80
        muted = str(self.qsettings.value("audio/muted", "false")).lower() == "true"
        self.volume.setValue(max(0, min(100, vol)))
        self.audio.set_volume(self.volume.value() / 100)
        self._set_muted(muted)

    def _volume_changed(self, v: int):
        self.audio.set_volume(v / 100)
        self.qsettings.setValue("audio/volume", v)
        if v > 0 and self.audio.muted:
            self._set_muted(False)

    def toggle_mute(self):
        self._set_muted(not self.audio.muted)

    def _set_muted(self, muted: bool):
        self.audio.set_muted(muted)
        self.qsettings.setValue("audio/muted", "true" if muted else "false")
        self.mute_btn.setIcon(theme.icon("volume-x" if muted else "volume", theme.TEXT_2, 16))

    def emergency_stop(self):
        """Esc: stop the device immediately and pause playback."""
        self.view.set_skeleton(None)
        self._stop_patterns()
        self.external.emergency_stop()
        self.devices.emergency_stop()
        if self.timer.isActive():
            self.pause()
        if self.isFullScreen():
            self.toggle_fullscreen()
        self._status(tr("Not-Aus: Gerät gestoppt"))

    # ------------------------------------------------------------ analysis
    def start_analysis(self):
        if not self.video_path or (self.worker and self.worker.isRunning()):
            return
        if self.panel.mode == "track" and not self.panel.sections:
            self._status(tr("Tracking: erst im Video die Bereiche aufziehen, die verfolgt werden sollen"))
            return
        if self.curve.actions and self.dirty and QMessageBox.question(
                self, tr("Analyse"), tr("Manuelle Änderungen werden durch die neue Analyse ersetzt. Fortfahren?")
        ) != QMessageBox.Yes:
            return
        settings = self.panel.analysis_settings()
        if settings.mode == "track" and settings.ai_check and not pose_ui.ensure_models(self):
            settings = replace(settings, ai_check=False)
            self._status(tr("KI-Kontrolle aus: ohne KI-Modelle wird normal verfolgt"))
        self.worker = AnalysisWorker(self.video_path, settings)
        self.worker.progress.connect(self._progress)
        self.worker.finishedOk.connect(self._analysis_done)
        self.worker.failed.connect(self._analysis_failed)
        self.worker.cancelled.connect(lambda: self._analysis_end(tr("Analyse abgebrochen")))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.progress_label.setText(tr("Analyse  0 %"))
        self.progress_label.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.analyze_btn.setVisible(False)
        self._status(tr("Analysiere Bewegung"))
        self.worker.start()

    def _progress(self, p: float):
        self.progress.setValue(int(p * 100))
        self.progress_label.setText(tr("Analyse {0:3.0f} %", p * 100))

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
        self._update_track_overlay(max(0, self._cur_idx) / self.fps * 1000)
        self._update_section_marks()
        ocl = tr(" (OpenCL)") if motion.meta.get("opencl") else ""
        dec = motion.meta.get("decoder")
        dec_txt = tr(" · Dekodierung: {0}", DECODER_LABELS.get(dec, dec)) if dec else tr(" · aus dem Zwischenspeicher")
        lost = [x for x in self._lost_stretches() if x[1] - x[0] >= 300]
        lost_txt = ""
        if lost:
            a, _b, areas = lost[0]
            lost_txt = (tr(" · Bereich {0} ab {1} verloren", ', '.join(map(str, areas)), _fmt(a))
                        + (plural(len(lost) - 1, " (+{0} weitere Stelle, rot in der Kurve)", " (+{0} weitere Stellen, rot in der Kurve)") if len(lost) > 1 else
                           tr(" (rot in der Kurve) – dort nachjustieren")))
        notes = motion.meta.get("notes") or []
        note_txt = "".join(f" · {n}" for n in notes)        # only the AI check writes notes
        self._analysis_end(tr("Analyse fertig{0}, {1} Punkte erzeugt{2}{3}{4}", ocl, len(self.curve.actions), lost_txt, dec_txt, note_txt))
        errs = motion.meta.get("decoder_errors") or []
        self.status_label.setToolTip("\n".join(f"{DECODER_LABELS.get(m, m)}: {e}" for m, e in errs)
                                     if errs else "")

    def _analysis_failed(self, msg: str):
        self._analysis_end(tr("Analyse fehlgeschlagen"))
        QMessageBox.critical(self, tr("Analyse fehlgeschlagen"), msg)

    def _analysis_outdated(self):
        if self.motion is not None:
            self._status(tr("Analyse-Einstellungen geändert, neu analysieren, um sie anzuwenden"))

    def regenerate(self):
        if self.motion is None:
            return
        script = generate(self.motion, self.panel.generator_settings(), self.panel.sections)
        self._l0_signal = (script.t_ms, script.signal)
        dur = max(self.motion.duration_ms, self.frame_count / self.fps * 1000)
        if self.axis == "L0":
            self.curve.set_data(script.t_ms, script.signal, script.actions, dur)
        else:                                   # stroke changes in the background
            self.axis_actions["L0"] = script.actions
            self.curve.duration_ms = max(self.curve.duration_ms, dur)
        self._update_axis_labels()
        self.dirty = True
        self._refresh_position()
        self._status(tr("{0} Punkte erzeugt", len(script.actions)))

    def regenerate_manual(self):
        if self.motion is not None:
            if self.axis != "L0":
                self.switch_axis("L0")
            self._push_undo()
            self.regenerate()

    def generate_more_axes(self):
        """Sway (and roll with tracking) from the analysis."""
        if self.motion is None:
            self._status(tr("Erst analysieren"))
            return
        axes = generate_axes(self.motion, self.panel.generator_settings(), self.panel.sections)
        self._sync_axis()
        for code, acts in axes.items():
            self.undo_stack.append((code, list(self.actions_for(code))))
            self._set_axis_actions(code, acts)
        self.redo_stack.clear()
        self.dirty = True
        self._update_axis_labels()
        names = ", ".join(tr("{0} ({1} Punkte)", AXES[c][0], len(a)) for c, a in axes.items())
        hint = "" if "R1" in axes else tr(" · Roll braucht Tracking mit 2 Bereichen")
        self._status(tr("Erzeugt: {0}{1}", names, hint))

    # ------------------------------------------------------------ axes
    def _sync_axis(self):
        """The curve edits its own list; keep the dict view of the shown axis current."""
        self.axis_actions.pop(self.axis, None)

    def actions_for(self, axis: str) -> list[Action]:
        return self.curve.actions if axis == self.axis else self.axis_actions.get(axis, [])

    def stroke_actions(self) -> list[Action]:
        return self.actions_for("L0")

    def _set_axis_actions(self, axis: str, acts: list[Action]):
        if axis == self.axis:
            self.curve.set_actions(list(acts))
        else:
            self.axis_actions[axis] = list(acts)

    def all_axes(self) -> dict[str, list[Action]]:
        out = {a: list(v) for a, v in self.axis_actions.items() if a != self.axis}
        out[self.axis] = list(self.curve.actions)
        return out

    def switch_axis(self, axis: str):
        if axis not in AXES or axis == self.axis:
            self.axis_box.setCurrentIndex(self.axis_box.findData(self.axis))
            return
        self.axis_actions[self.axis] = self.curve.actions
        self.axis = axis
        acts = self.axis_actions.pop(axis, [])
        if axis == "L0" and self._l0_signal is not None:
            t, sig = self._l0_signal
            self.curve.set_data(t, sig, acts, self.curve.duration_ms)
        else:
            self.curve.set_data(self.curve.t_ms[:0], self.curve.signal[:0], acts, self.curve.duration_ms)
        self.axis_box.setCurrentIndex(self.axis_box.findData(axis))
        self._update_axis_labels()
        self._refresh_position()
        self._status(tr("Achse: {0} ({1})", AXES[axis][0], axis)
                     + ("" if axis == "L0" else tr(" · das Gerät folgt weiter der Achse „Hub“")))

    def _update_axis_labels(self):
        for i in range(self.axis_box.count()):
            code = self.axis_box.itemData(i)
            n = len(self.actions_for(code))
            label = AXES[code][0]
            self.axis_box.setItemText(i, f"{label} ({code})" + (f" · {n}" if n else ""))
        # the box does not grow by itself when a text gets longer (point count added)
        self.axis_box.ensurePolished()               # the style sheet sets the real font size
        fm = self.axis_box.fontMetrics()
        widest = max(fm.horizontalAdvance(self.axis_box.itemText(i)) for i in range(self.axis_box.count()))
        self.axis_box.setMinimumWidth(widest + 64)

    # ------------------------------------------------------------ editing
    # ------------------------------------------------------------ point editing
    def _need_selection(self) -> bool:
        if not self.curve.editable:
            self._status(tr("Im Player-Modus nicht möglich – zu „Bearbeiten“ wechseln"))
            return False
        if not self.curve.selection:
            self._status(tr("Erst Punkte auswählen: Umschalt+Ziehen, Strg+Klick oder Strg+A"))
            return False
        return True

    def select_all_points(self):
        self.curve.select_all()
        self._status(plural(len(self.curve.selection), "{0} Punkt ausgewählt", "{0} Punkte ausgewählt"))

    def copy_points(self):
        if self._need_selection():
            self._status(plural(self.curve.copy_selection(), "{0} Punkt kopiert", "{0} Punkte kopiert"))

    def cut_points(self):
        if self._need_selection():
            n = self.curve.copy_selection()
            self.curve.delete_selection()
            self._status(plural(n, "{0} Punkt ausgeschnitten", "{0} Punkte ausgeschnitten"))

    def paste_points(self):
        if not self.curve.editable:
            return
        n = self.curve.paste()
        self._status(plural(n, "{0} Punkt eingefügt bei {1}", "{0} Punkte eingefügt bei {1}", _fmt(self.curve.playhead_ms)) if n else
                     tr("Nichts zum Einfügen – erst Punkte kopieren (Strg+C)"))

    def delete_points(self):
        if self._need_selection():
            n = len(self.curve.selection)
            self.curve.delete_selection()
            self._status(plural(n, "{0} Punkt gelöscht", "{0} Punkte gelöscht"))

    def smooth_points(self):
        if self._need_selection():
            self.curve.apply(editing.smooth)
            self._status(tr("Auswahl geglättet (mehrmals anwenden für mehr Wirkung)"))

    def scale_points(self, factor: float):
        if self._need_selection():
            self.curve.apply(editing.scale, factor)
            self._status(tr("Hub verstärkt") if factor > 1 else tr("Hub abgeschwächt"))

    def _push_undo(self):
        self.undo_stack.append((self.axis, [Action(a.at, a.pos) for a in self.curve.actions]))
        del self.undo_stack[:-100]
        self.redo_stack.clear()

    def _edited(self):
        self.dirty = True
        self._update_axis_labels()
        self._refresh_position()
        self._status(tr("Punkte bearbeitet"))

    def _update_heat(self):
        dur = self.frame_count / self.fps * 1000 if self.cap else 0.0
        self.heat.set_actions(self.stroke_actions(), dur)

    def _refresh_position(self):
        self._update_issues()
        self.devices.set_actions(self.stroke_actions())
        self.devices.set_axes(self.all_axes())
        self.rail.set_position(position_at(self.stroke_actions(), self.curve.playhead_ms))
        self._update_heat()
        self._update_meta()

    def _restore(self, src: list, dst: list):
        if src[-1][0] == TRACK_UNDO:
            self._restore_tracking(src, dst)
            return
        axis, acts = src.pop()
        dst.append((axis, list(self.actions_for(axis))))
        if axis != self.axis:
            self.switch_axis(axis)
        self.curve.set_actions(list(acts))
        self.dirty = True
        self._update_axis_labels()
        self._refresh_position()

    def _restore_tracking(self, src: list, dst: list):
        _key, secs = src.pop()
        tb = self.panel.tracking
        before = list(tb.sections)
        dst.append((TRACK_UNDO, before))
        tb.sections = list(secs)
        tb.refresh()
        self._track_restoring = True
        try:
            # only the arrow or the calibration differs: no new analysis needed
            same = [s.analysis_key() for s in before] == [s.analysis_key() for s in secs]
            (tb.generatorChanged if same else tb.analysisChanged).emit()
        finally:
            self._track_restoring = False
        self._status(tr("Tracking: ") + (tr("rückgängig gemacht") if dst is self.redo_stack else tr("wiederhergestellt")))

    def undo(self):
        if self.undo_stack:
            self._restore(self.undo_stack, self.redo_stack)

    def redo(self):
        if self.redo_stack:
            self._restore(self.redo_stack, self.undo_stack)

    # ------------------------------------------------------------ files
    def export(self):
        """Ctrl+S: next to the video and/or into the script folder (Datei → Speicherorte)."""
        if self.video_path:
            self._save(save_paths(self.video_path), prune=True)

    def export_as(self):
        if not self.video_path:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Funscript speichern"), str(save_paths(self.video_path)[-1]),
                                              tr("Funscript (*.funscript)"))
        if path:
            self._save([Path(path)])

    def _save(self, bases: list[Path], prune: bool = False):
        """Saves the stroke to each base path and every other axis with points next to it
        (<name>.roll.funscript etc.). prune: remove the files of axes that were emptied, so
        they do not come back on the next open. A script made by another program keeps its
        metadata (title, creator)."""
        axes = {a: v for a, v in self.all_axes().items() if v}
        if not axes:
            QMessageBox.information(self, tr("Export"), tr("Noch keine Punkte vorhanden. Analysiere zuerst das Video."))
            return
        meta = make_metadata(self.video_path)
        saved: list[str] = []
        try:
            for base in bases:
                base.parent.mkdir(parents=True, exist_ok=True)
                for axis, (_label, suffix) in AXES.items():
                    target = base if axis == "L0" else base.with_suffix(f".{suffix}.funscript")
                    acts = axes.get(axis)
                    if not acts:
                        if prune and axis != "L0":
                            target.unlink(missing_ok=True)
                        continue
                    Funscript(acts, metadata=self._metadata_for(target, meta)).save(target)
                    if target.name not in saved:
                        saved.append(target.name)
        except OSError as e:
            QMessageBox.critical(self, tr("Fehler"), tr("Speichern fehlgeschlagen:\n{0}", e))
            return
        self.dirty = False
        near = funscript_path_for(self.video_path)
        folder = script_settings().folder
        in_folder = folder is not None and any(b.parent == folder for b in bases)
        where = (tr(" (neben dem Video und im Script-Ordner)") if in_folder and near in bases
                 else tr(" (im Script-Ordner)") if in_folder else "")
        self._status(tr("Gespeichert: ") + ", ".join(saved) + where)

    @staticmethod
    def _metadata_for(target: Path, ours: dict) -> dict:
        try:
            old = Funscript.load(target).metadata if target.is_file() else {}
        except (OSError, ValueError):
            old = {}
        return old if old.get("creator") not in (None, "", ours["creator"]) else ours

    def load_funscript_dialog(self):
        start = str(find_script(self.video_path) or save_paths(self.video_path)[-1]) if self.video_path else ""
        path, _ = QFileDialog.getOpenFileName(self, tr("Funscript laden"), start, tr("Funscript (*.funscript)"))
        if path:
            try:
                fs = Funscript.load(path)
            except (OSError, ValueError, KeyError) as e:
                QMessageBox.critical(self, tr("Fehler"), str(e))
                return
            self._push_undo()
            self.curve.set_actions(fs.actions)
            self._update_axis_labels()
            self._status(tr("Geladen in Achse {0}: {1}", AXES[self.axis][0], path))

    def open_script_folder(self):
        from .scripts_ui import ScriptFolderDialog, describe

        ScriptFolderDialog(self).exec()
        self.player.refresh()                    # "with script" may change for the playlist
        self._status(describe())

    def open_library(self):
        from .library_dialog import LibraryDialog

        dlg = LibraryDialog(self.qsettings, self)
        dlg.playRequested.connect(self._library_play)
        dlg.queueRequested.connect(self._library_queue)
        dlg.editRequested.connect(self._library_edit)
        dlg.exec()

    def _library_play(self, path: str):
        if path.lower().endswith(".funscript"):           # its video is missing: find it first
            video = self.locate_video(path)
            if video is None:
                return
            path = str(video)
        self.player.add_paths([path])
        items = self.player.playlist.items
        target = str(Path(path).resolve())
        if target in items:
            self.play_index(items.index(target))

    def _library_queue(self, paths: list):
        n = self.player.add_paths(paths)
        self._status(plural(n, "{0} Video zur Playlist hinzugefügt", "{0} Videos zur Playlist hinzugefügt"))

    def _library_edit(self, path: str):
        self.set_player_mode(False)
        self.open_video(path)                    # a script (video missing) is handled there

    def open_batch(self):
        folder = str(Path(self.video_path).parent) if self.video_path else self.qsettings.value("last_dir", "")
        BatchDialog(self.panel, folder, self).exec()

    def _setup_desktop_integration(self, menu):
        """Start menu entry for the AppImage: added on start, switchable in the help menu."""
        from .. import desktop_integration as di
        from .. import updater

        self._appimage = updater.running_appimage()
        act = QAction(tr("Im Startmenü anzeigen"), self)
        act.setCheckable(True)
        v = self.qsettings.value("desktop_integration", True)
        enabled = v if isinstance(v, bool) else str(v).lower() == "true"
        act.setChecked(enabled and self._appimage is not None)
        act.setEnabled(self._appimage is not None)
        if self._appimage is None:
            act.setToolTip(tr("Nur verfügbar, wenn die App als AppImage läuft"))
        menu.insertAction(menu.actions()[1] if len(menu.actions()) > 1 else None, act)

        def toggled(on: bool):
            self.qsettings.setValue("desktop_integration", on)
            if on:
                di.install(self._appimage)
                self._status(tr("Ins Startmenü aufgenommen"))
            else:
                di.uninstall()
                self._status(tr("Aus dem Startmenü entfernt"))

        act.toggled.connect(toggled)
        if enabled and self._appimage is not None:
            msg = di.auto_integrate(self._appimage)
            if msg:
                self._status(msg)

    def test_decoding(self):
        """Try every decoder on the open video and show which one works and how fast."""
        if not self.video_path:
            QMessageBox.information(self, tr("Dekodierung testen"), tr("Erst ein Video öffnen."))
            return
        self._status(tr("Teste Dekodierung…"))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            results = test_decoders(probe(self.video_path), self.panel.analysis_settings().width)
        finally:
            QApplication.restoreOverrideCursor()
        lines = []
        for r in results:
            if r["ok"]:
                lines.append(tr("✓ {0}: {1:.0f} Bilder/s", r['label'], r['fps']))
            else:
                err = (r["error"] or "keine Bilder").replace("FFmpeg lieferte keine Frames: ", "")
                lines.append(f"✗ {r['label']}:\n    {err[:300]}")
        gpu_ok = any(r["ok"] and r["mode"] == "vaapi-gpu" for r in results)
        lines.append("")
        lines.append(tr("Die Grafikkarte dekodiert und verkleinert – mit „Dekodierung: Automatisch“ oder „VAAPI“ wird das genutzt.") if gpu_ok else
                     tr("Die Grafikkarte kann dieses Video nicht dekodieren (Treiber, Codec oder ffmpeg ohne VAAPI). Unter Nobara hilft meist: sudo dnf install ffmpeg libva-utils mesa-va-drivers-freeworld"))
        lines.append("")
        lines.append(tr("Hinweis: Die Anzeige „GPU-Auslastung“ zeigt meist nur die 3D-Einheit. Das Dekodieren läuft im eigenen Video-Block der Karte (VCN) und taucht dort kaum auf – sichtbar z.B. mit amdgpu_top. Das Tracking selbst rechnet die CPU."))
        QMessageBox.information(self, tr("Dekodierung testen"), "\n".join(lines))
        self._status(tr("Dekodierungstest fertig"))

    def show_help(self):
        QMessageBox.information(self, tr("Bedienung"), (
            tr("Bearbeiten\n1. Video öffnen (Strg+O oder Datei hierher ziehen).\n2. Methode wählen: „Bewegung im Bild“ (optional Rechteck = Analysebereich)\n   oder „Tracking“: bis zu 3 Bereiche im Video aufziehen (z.B. die beiden Körperteile).\n   Tracking: E = Abschnitt-Ende hier, nach Szenenwechsel neu markieren;\n   D = ganz drin, A = ganz draußen (echte Tiefe statt gestreckter Hübe).\n   Kästchen (am Abschnitt-Start): innen ziehen = verschieben, Ecke = Größe,\n   runder Griff oder Mausrad = drehen. Abgewandert: an der Stelle anpacken und\n   richtig hinschieben (nachjustieren). Neu anfangen: Tracking → Alle Abschnitte löschen.\n   Kleine Stellen: Strg+Mausrad zoomt ins Video, mittlere Maustaste verschiebt,\n   Strg+0 setzt zurück. Kleine Kästchen brauchen eine höhere Auflösung (Optionen).\n   Strg+Bild↓/↑ = nächstes/voriges Video im Ordner.\n3. „Analysieren“ klicken, dann Empfindlichkeit, Glättung usw. anpassen.\n4. Punkte: ziehen = verschieben, Doppelklick = hinzufügen, Rechtsklick/Entf = löschen.\n   Umschalt+Ziehen = Auswahlrechteck, Strg+Klick = Punkt an/aus, Strg+A = alle.\n   Strg+C/V = kopieren/einfügen, Strg+G = glätten, Strg+↑/↓ = Hub stärker/schwächer.\n   Fläche ziehen = scrollen, Mausrad = Zoom.\n   Strg+T = Rhythmus einfügen (gleichmäßiger Takt für einen Bereich).\n   Tracking-Abschnitte: Anfang/Ende in der Kurve mit der Maus ziehen.\n5. Achse über der Kurve wählen (Hub, Roll, Twist …).\n   Lesezeichen: B = setzen, Alt+→/← = nächstes/voriges, Liste im Menü „Lesezeichen“.\n6. Strg+S speichert <Video>.funscript (und <Video>.roll.funscript usw.).\n\nPlayer (Strg+P)\nVideos in die Playlist ziehen und „Playlist abspielen“. Bild ↑/↓ = vorheriges/nächstes\nVideo, F11 = Vollbild. Ein ruhiger Anfang wird übersprungen (Start 10 s vor dem Script),\ndie letzten 10 s vor dem Start zählt ein Countdown herunter (beides abschaltbar).\n\nLeertaste = Abspielen, Pfeiltasten = Frame vor/zurück, M = Ton an/aus,\nMuster-Steuerung (Strg+M)\nFertige Muster oder eigene aus Bausteinen direkt auf dem Gerät abspielen.\nTiefe und Tempo bleiben gespeichert, ändern mit ↑/↓ und ←/→ oder per Controller\n(linker Stick Tiefe, rechter Stick Tempo, A Start/Stopp, B Stopp).\nAbläufe: mehrere Muster hintereinander mit eigener Dauer, Tempo und Tiefe.\nZufall wechselt selbst, Musik folgt dem Takt der Musik am PC.\n\nGerät-Menü: Einrichtungs-Assistent, Handy-Fernbedienung, Andere Player (mpv, VLC, VR).\nIm Fenster „Gerät“: Sicherheits-Timer (abschaltbar), Vibration folgt Tempo/Tiefe.\nEigene Muster gibt es auch bei Strg+T.\n\nEsc = Not-Aus (Gerät stoppen).")))

    # ------------------------------------------------------------ misc
    def _status(self, msg: str):
        self.status_label.setText(msg)
        self._update_meta()

    def _update_meta(self):
        n = len(self.curve.actions)
        if not n:
            self.curve_meta.setText(tr("Noch nicht analysiert") if self.motion is None else tr("Keine Punkte"))
            return
        dur = self.curve.actions[-1].at - self.curve.actions[0].at
        rate = (n - 1) / 2 / (dur / 60000) if dur > 0 else 0
        edited = tr(" · nicht gespeichert") if self.dirty else ""
        self.curve_meta.setText(tr("{0} Punkte · {1:.0f} Hübe/min{2}", n, rate, edited))

    def _confirm_discard(self) -> bool:
        if not self.dirty or not any(self.all_axes().values()):
            return True
        r = QMessageBox.question(self, tr("Ungespeicherte Änderungen"),
                                 tr("Das aktuelle Funscript wurde nicht gespeichert. Verwerfen?"))
        return r == QMessageBox.Yes

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        urls = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if urls and self.player_mode:          # player: dropped videos go to the playlist
            vids = []
            for u in urls:                     # a dropped script brings its video
                v = self.locate_video(u) if u.lower().endswith(".funscript") else u
                if v is not None:
                    vids.append(str(v))
            n = self.player.add_paths(vids)
            self._status(plural(n, "{0} Video zur Playlist hinzugefügt", "{0} Videos zur Playlist hinzugefügt"))
            return
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
        self.devices.shutdown()
        self.audio.shutdown()
        if self.cap:
            self.cap.release()
        return True

    def closeEvent(self, ev):
        if not self._confirm_discard():
            ev.ignore()
            return
        self.cancel_analysis()
        self.updates.shutdown()
        self._stop_patterns()
        self.stats.flush()
        self.remote.shutdown()
        self.external.shutdown()
        self.devices.shutdown()
        self.audio.shutdown()
        self.player.save()
        self.qsettings.setValue("settings", json.dumps(self.panel.to_dict()))
        if self.cap:
            self.cap.release()
        super().closeEvent(ev)


def _fmt(ms: float) -> str:
    s = ms / 1000
    return f"{int(s // 60)}:{s % 60:05.2f}"
