"""Smoke test of the GUI (offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


@pytest.fixture(scope="module")
def app():
    from vtf.gui import theme
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(app)
    return app


def test_open_analyze_export(app, sine_video, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from PySide6.QtCore import QEventLoop

    from vtf.funscript import Funscript, funscript_path_for
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    w.set_player_mode(False)                  # editing; the mode is remembered between runs
    w.open_video(str(sine_video))
    assert w.frame_count >= 179
    w.panel.hwaccel.setCurrentIndex(w.panel.hwaccel.findData("none"))
    w.start_analysis()
    loop = QEventLoop()
    w.worker.finished.connect(loop.quit)
    if w.worker.isRunning():
        loop.exec()
    app.processEvents()
    assert w.motion is not None and len(w.curve.actions) >= 8

    n = len(w.curve.actions)
    w.curve.set_selection({0})
    w.curve.delete_selection()
    assert len(w.curve.actions) == n - 1
    w.undo()
    assert len(w.curve.actions) == n

    assert w.rail._pos is not None
    assert "Punkte" in w.curve_meta.text()

    w.export()
    assert len(Funscript.load(funscript_path_for(sine_video)).actions) == n
    w.dirty = False
    w.close()


def test_batch_dialog(app, sine_video):
    from vtf.gui.batch_dialog import BatchDialog
    from vtf.gui.settings_panel import SettingsPanel
    d = BatchDialog(SettingsPanel(), str(sine_video.parent))
    assert d.info.text().startswith("1 Videos")


def test_update_flow_in_gui(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop

    from vtf import updater
    from vtf.gui.main_window import MainWindow

    target = tmp_path / "Video.AppImage"
    target.write_bytes(b"old")
    rel = updater.Release("9.9.9", "v9.9.9", "Neu", "Video-to-Funscript-x86_64.AppImage",
                          "http://x", 3, "http://x/sums", "http://x/page")

    def fake_install(release, target_=None, progress=None, cancel=None):
        progress(1.0)
        target.write_bytes(b"new")
        return target

    monkeypatch.setattr(updater, "can_self_update", lambda: True)
    monkeypatch.setattr(updater, "download_and_install", fake_install)
    w = MainWindow()
    w.updates._checked(rel)
    loop = QEventLoop()
    w.updates.worker.finished.connect(loop.quit)
    if w.updates.worker.isRunning():
        loop.exec()
    app.processEvents()
    assert target.read_bytes() == b"new"
    assert "9.9.9 installiert" in w.updates.button.text()
    assert w.updates.button.isVisible() or not w.isVisible()
    w.close()


def test_device_playback_with_simulation(app, sine_video, tmp_path, monkeypatch):
    """Play with the simulated device: the device follows the script."""
    import time

    from PySide6.QtCore import QEventLoop, QTimer

    from vtf.funscript import Action
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    w.open_video(str(sine_video))
    w.curve.set_actions([Action(i * 250, 0 if i % 2 else 100) for i in range(24)])
    w.devices.connect_device("sim", "")
    end = time.time() + 3
    while (w.devices.device is None or not w.devices.device.connected) and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    dev = w.devices.device
    assert dev is not None and dev.connected
    w.toggle_play()
    loop = QEventLoop()
    QTimer.singleShot(1500, loop.quit)
    loop.exec()
    assert w.timer.isActive()
    assert w._cur_idx > 20                     # video advanced by the clock (~1.5 s at 30 fps)
    assert len(dev.commands) >= 4              # approach + several strokes
    positions = {round(p) for p, _ in dev.commands[1:]}
    assert positions & {0} and positions & {100}
    n = len(dev.commands)
    w.emergency_stop()
    assert not w.timer.isActive()
    assert len(dev.commands) == n              # nothing sent after stop
    w.devices.shutdown()
    w.close()


def test_device_dialog_layout(app):
    """The dialog cannot be squeezed, and a status message does not resize it."""
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    w.devices.open_dialog()
    d = w.devices.dialog
    app.processEvents()
    d.resize(300, 200)                         # smaller than the content
    app.processEvents()
    assert d.width() >= 460
    for field in (d.kind, d.url, d.device_box, d.pmin, d.pmax, d.connect_btn):
        assert field.height() >= 30
        assert field.parentWidget().height() >= field.height()   # not clipped
    size = d.size()
    d.log("Fehler: Intiface Central nicht erreichbar unter ws://127.0.0.1:12345. "
          "Läuft Intiface und ist der Server gestartet?")
    app.processEvents()
    assert d.size() == size
    assert d.pmin.height() >= 30
    d.close()
    w.close()


@pytest.fixture
def av_video(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg fehlt")
    out = tmp_path / "av.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def _wait(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_sound_follows_playback(app, av_video, tmp_path, monkeypatch):
    """The audio track plays with the video clock, follows seeks and pauses."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    if not w.audio.available:
        pytest.skip("Qt Multimedia nicht verfügbar")
    w.open_video(str(av_video))
    _wait(400)
    assert w.audio.has_audio
    w.toggle_play()
    _wait(1200)
    assert w.audio.playing
    assert abs(w.audio.position_ms() - w._media_ms()) < 400
    w.seek_frame(75)                               # 2.5 s
    _wait(300)
    assert w.audio.position_ms() >= 2400
    w.pause()
    assert not w.audio.playing
    # mute and volume are kept
    w.volume.setValue(50)                          # stored state of an earlier run
    w._set_muted(False)
    w.toggle_mute()
    assert w.audio.muted
    w.volume.setValue(30)
    assert abs(w.audio.volume - 0.3) < 1e-6
    assert not w.audio.muted                       # turning it up unmutes
    w.close()


def test_audio_resyncs_on_drift(app, av_video):
    from vtf.gui.audio import MAX_DRIFT_MS, AudioPlayer

    a = AudioPlayer()
    if not a.available:
        pytest.skip("Qt Multimedia nicht verfügbar")
    a.load(str(av_video))
    _wait(300)
    a.play(0)
    _wait(300)
    a.sync(3000)                                   # clock far ahead of the audio
    assert a.resyncs == 1
    _wait(100)
    assert a.position_ms() >= 3000 - MAX_DRIFT_MS
    a.sync(3050)                                   # within the check interval: no-op
    assert a.resyncs == 1
    a.shutdown()


def test_timeline_click_jumps_and_curve_pans(app, sine_video):
    """Video timeline: click anywhere jumps there. Curve: drag on empty space scrolls,
    a plain click seeks, the scroll bar scrolls."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from vtf.funscript import Action
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.open_video(str(sine_video))
    app.processEvents()
    sl = w.slider
    QTest.mouseClick(sl, Qt.LeftButton, pos=QPoint(int(sl.width() * 0.75), sl.height() // 2))
    app.processEvents()
    assert abs(sl.value() / sl.maximum() - 0.75) < 0.08
    assert w._cur_idx == sl.value()

    c = w.curve
    c.set_actions([Action(i * 300, 0 if i % 2 else 100) for i in range(10)])
    c.view_ms = 1000
    c.start_ms = c.duration_ms / 2
    c._clamp_view()
    start = c.start_ms
    y = int(c._y(0.5))
    QTest.mousePress(c, Qt.LeftButton, pos=QPoint(800, y))
    QTest.mouseMove(c, QPoint(600, y))
    QTest.mouseRelease(c, Qt.LeftButton, pos=QPoint(600, y))
    assert c.start_ms > start + 100                # dragged left -> later part visible
    assert c.scrollbar.value() == int(c.start_ms)
    idx = w._cur_idx
    QTest.mouseClick(c, Qt.LeftButton, pos=QPoint(300, y))
    app.processEvents()
    assert w._cur_idx != idx                       # a click without moving still seeks
    c.scrollbar.setValue(0)
    assert c.start_ms == 0
    w.close()



def test_player_mode_plays_playlist(app, tmp_path, monkeypatch):
    """Player mode: plays the videos with a script one after another, skips the rest,
    and the curve is read-only."""
    from pathlib import Path

    from videogen import make_video

    from vtf.funscript import Action, Funscript
    from vtf.gui.main_window import MainWindow

    vids = [make_video(tmp_path / f"{n}.avi", seconds=1.0) for n in "abc"]
    for v in (vids[0], vids[2]):
        Funscript([Action(0, 0), Action(500, 100), Action(1000, 0)]).save(v.with_suffix(".funscript"))

    w = MainWindow()
    w.player.playlist.clear()
    w.set_player_mode(True)
    assert w.player_mode and not w.curve.editable
    assert w.player.add_paths([tmp_path]) == 3
    assert "2 mit Script" in w.player.meta.text()
    w.player._play_clicked()
    assert Path(w.video_path).stem == "a" and w.timer.isActive()
    assert len(w.curve.actions) == 3
    _wait(1600)                                    # a ends -> b skipped -> c
    assert Path(w.video_path).stem == "c"
    assert w.player.playlist.current == 2
    w.pause()
    w.play_prev()
    assert Path(w.video_path).stem == "a"
    w.pause()
    w.set_player_mode(False)
    assert w.curve.editable
    w.player.playlist.clear()
    w.player.save()
    w.close()


def test_curve_multi_select_drag_copy_paste(app, sine_video):
    """Shift+drag selects, dragging moves all selected points, copy/paste, scale, undo."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from vtf.funscript import Action
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    w.set_player_mode(False)
    w.resize(1400, 900)
    w.show()
    w.open_video(str(sine_video))
    c = w.curve
    c.set_actions([Action(i * 500, 20 if i % 2 else 80) for i in range(8)])
    c.view_ms, c.start_ms = 4000, 0
    c._clamp_view()
    app.processEvents()
    # rectangle around points 1..3 (500..1500 ms)
    x0, x1 = int(c._x(400)), int(c._x(1600))
    y0, y1 = int(c._y(1.0)) + 1, int(c._y(0.0)) - 1
    QTest.mousePress(c, Qt.LeftButton, Qt.ShiftModifier, QPoint(x0, y0))
    QTest.mouseMove(c, QPoint(x1, y1))
    QTest.mouseRelease(c, Qt.LeftButton, Qt.ShiftModifier, QPoint(x1, y1))
    assert c.selection == {1, 2, 3}
    # drag point 2 up by ~10 positions: all three move
    before = [a.pos for a in c.actions]
    p2 = QPoint(int(c._x(1000)), int(c._y(0.8)))
    QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, p2)
    QTest.mouseMove(c, QPoint(p2.x(), int(c._y(0.9))))
    QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(p2.x(), int(c._y(0.9))))
    after = [a.pos for a in c.actions]
    assert all(after[i] > before[i] for i in (1, 2, 3))
    assert after[0] == before[0] and after[4] == before[4]
    # undo restores
    w.undo()
    assert [a.pos for a in c.actions] == before
    # copy, paste at 3 s
    c.set_selection({1, 2, 3})
    w.copy_points()
    c.set_playhead(3000)
    w.paste_points()
    assert {a.at for a in c.actions} >= {3000, 3500, 4000}
    assert c.selection and min(c.actions[i].at for i in c.selection) == 3000
    # make the pasted strokes bigger
    sel = c.selection
    span = lambda: max(c.actions[i].pos for i in sel) - min(c.actions[i].pos for i in sel)
    s0 = span()
    w.scale_points(1.2)
    assert span() > s0
    n = len(c.actions)
    w.delete_points()
    assert len(c.actions) == n - 3 and not c.selection
    w.dirty = False
    w.close()


def test_axes_edit_save_load_and_device_uses_stroke(app, sine_video, tmp_path):
    from pathlib import Path

    from vtf.funscript import Action, load_axes
    from vtf.gui.main_window import MainWindow

    video = tmp_path / "axes.avi"
    video.write_bytes(Path(sine_video).read_bytes())
    w = MainWindow()
    w.set_player_mode(False)
    w.open_video(str(video))
    stroke = [Action(0, 0), Action(500, 100), Action(1000, 0)]
    w.curve.set_actions(list(stroke))
    w.switch_axis("R1")
    assert w.axis == "R1" and w.curve.actions == []
    assert w.stroke_actions() == stroke                 # device/rail keep the stroke
    w._push_undo()
    w.curve.set_actions([Action(0, 50), Action(300, 90)])
    w._edited()
    assert "· 2" in w.axis_box.currentText()
    w.switch_axis("L0")
    w.undo()                                            # jumps back to the roll axis
    assert w.axis == "R1" and w.curve.actions == []
    w.redo()
    assert len(w.curve.actions) == 2
    w.export()
    assert (tmp_path / "axes.funscript").is_file() and (tmp_path / "axes.roll.funscript").is_file()
    assert set(load_axes(video)) == {"L0", "R1"}
    w.open_video(str(video))                            # reload: both axes are back
    assert w.axis == "L0" and len(w.curve.actions) == 3
    assert len(w.actions_for("R1")) == 2
    w.dirty = False
    w.close()


def test_tracking_sections_box(app):
    """Marking starts sections, a later mark splits, end/calibrate/delete act at the playhead."""
    from vtf.gui.tracking_ui import TrackingBox

    tb = TrackingBox()
    tb.reset(10_000, 30)
    seen = []
    tb.analysisChanged.connect(lambda: seen.append("a"))
    tb.generatorChanged.connect(lambda: seen.append("g"))
    box = (0.1, 0.1, 0.2, 0.2)
    assert "Neuer Abschnitt 1" in tb.add_box(box, 0)
    assert "2 Bereiche" in tb.add_box(box, 10)               # same frame (within ~1 frame)
    assert "endet bei" in tb.set_end(6000)
    assert tb.sections[0].end_ms == 6000
    msg = tb.add_box(box, 3000)                              # inside section 1: splits it
    assert "der vorige endet hier" in msg
    assert [s.start_ms for s in tb.sections] == [0, 3000] and tb.sections[0].end_ms == 3000
    assert tb.sections[1].end_ms == 6000                     # the new part keeps the old end
    assert tb.index_at(1000) == 0 and tb.index_at(4000) == 1 and tb.index_at(9000) is None
    seen.clear()
    assert "jetzt noch" in tb.calibrate("in", 3500)
    assert "kalibriert" in tb.calibrate("out", 4000)
    assert tb.sections[1].calibrated and seen == ["g", "g"]   # no new analysis needed
    assert "nur innerhalb" in tb.calibrate("in", 9000)
    assert "gelöscht" in tb.delete_at(1000)
    assert [s.start_ms for s in tb.sections] == [3000]
    tb.deleteLater()                                         # gone before the QApplication
    app.processEvents()


def test_tracking_sections_in_main_window(app, tmp_path):
    from PySide6.QtCore import QEventLoop
    from test_tracking import _video

    from vtf.gui.main_window import MainWindow

    path, a, b = _video(tmp_path / "t.avi", seconds=3.0, pan=False)
    w = MainWindow()
    w.set_player_mode(False)
    w.open_video(str(path))
    w.panel.method.setCurrentIndex(w.panel.method.findData("track"))
    w._box_drawn(a)
    w._box_drawn(b)
    w.seek_ms(2000)
    w._track_cmd(w.panel.tracking.set_end)
    w.seek_ms(250)
    w._track_cmd(w.panel.tracking.calibrate, "in")
    w.seek_ms(750)
    w._track_cmd(w.panel.tracking.calibrate, "out")
    assert w.panel.sections[0].calibrated and w.panel.sections[0].end_ms == 2000
    assert w.curve.sections and abs(w.curve.sections[0][2] - 250) < 40     # nearest frame
    w.start_analysis()
    loop = QEventLoop()
    w.worker.finished.connect(loop.quit)
    if w.worker.isRunning():
        loop.exec()
    app.processEvents()
    assert w.motion is not None and w.motion.track_section is not None
    acts = w.curve.actions
    assert acts and all(x.at < 2000 for x in acts)
    assert max(x.pos for x in acts) >= 90 and min(x.pos for x in acts) <= 10
    w.seek_ms(1000)
    assert w.view._overlay                                   # tracked areas shown
    w.panel.method.setCurrentIndex(w.panel.method.findData("flow"))
    w.dirty = False
    w.close()


def test_tracking_rotate_and_correct(app):
    """Turning a marked area, and drawing over a drifted area = correction (not a new section)."""
    from vtf.gui.tracking_ui import TrackingBox

    tb = TrackingBox()
    tb.reset(10_000, 30)
    a = (0.1, 0.1, 0.2, 0.2)
    b = (0.6, 0.6, 0.2, 0.2)
    tb.add_box(a, 0)
    tb.add_box(b, 0)
    assert "gedreht" in tb.rotate_box(0, 1, 25.0)
    assert tb.sections[0].boxes[1][4] == 25.0
    assert "Startstelle" in tb.rotate_box(4000, 0, 10.0)        # nothing to turn there
    # at 4 s area 2 has drifted to (0.5, 0.5); the user draws over it at its real place
    shown = [(0.12, 0.1, 0.2, 0.2, 0.0), (0.5, 0.5, 0.2, 0.2, 30.0)]
    msg = tb.add_box((0.55, 0.52, 0.2, 0.2), 4000, shown)
    assert "nachjustiert" in msg and len(tb.sections) == 1
    ms, area, box = tb.sections[0].keys[0]
    assert (ms, area) == (4000, 1) and box[4] == 30.0           # keeps the tracked angle
    assert tb.boxes_at(4000)[1][:2] == (0.55, 0.52)
    assert "gedreht" in tb.rotate_box(4000, 1, 40.0)            # a correction can be turned too
    # drawing somewhere free starts a new section as before
    assert "Neuer Abschnitt" in tb.add_box((0.8, 0.05, 0.1, 0.1), 6000, shown)
    assert len(tb.sections) == 2
    tb.deleteLater()
    app.processEvents()


def test_tracking_preparation_saved_and_restored(app, tmp_path):
    from test_tracking import _video

    from vtf.gui.main_window import MainWindow
    from vtf.prep import prep_path

    path, a, b = _video(tmp_path / "p.avi", seconds=2.0, pan=False)
    other, _a, _b = _video(tmp_path / "q.avi", seconds=1.0, pan=False)
    w = MainWindow()
    w.set_player_mode(False)
    w.open_video(str(path))
    w.panel.method.setCurrentIndex(w.panel.method.findData("track"))
    w._box_drawn(a)
    w._box_drawn(b)
    assert prep_path(path).is_file()                          # saved right away
    w.open_sibling(1)                                         # next video in the folder
    assert w.video_path.endswith("q.avi") and not w.panel.tracking.sections
    w.open_sibling(-1)
    assert w.video_path.endswith("p.avi")
    assert len(w.panel.tracking.sections) == 1 and len(w.panel.tracking.sections[0].boxes) == 2
    assert w.panel.mode == "track"
    w.panel.method.setCurrentIndex(w.panel.method.findData("flow"))
    w.dirty = False
    w.close()


def test_tracking_edit_and_clear(app):
    """Moving/resizing a marked area, and clearing all sections."""
    from vtf.gui.tracking_ui import TrackingBox

    tb = TrackingBox()
    tb.reset(10_000, 30)
    tb.add_box((0.1, 0.1, 0.2, 0.2), 0)
    tb.rotate_box(0, 0, 30.0)
    assert "angepasst" in tb.update_box(0, 0, (0.3, 0.4, 0.1, 0.15))
    assert tb.sections[0].boxes[0] == (0.3, 0.4, 0.1, 0.15, 30.0)          # angle kept
    assert "Startstelle" in tb.update_box(4000, 0, (0.5, 0.5, 0.1, 0.1))
    tb.add_box((0.5, 0.5, 0.2, 0.2), 6000)
    assert tb.clear_btn.isEnabled() and len(tb.sections) == 2
    seen = []
    tb.analysisChanged.connect(lambda: seen.append(1))
    assert "2 Abschnitte gelöscht" in tb.clear_all()
    assert not tb.sections and seen and not tb.clear_btn.isEnabled()
    tb.deleteLater()


def test_video_view_move_resize(app):
    """Dragging inside a marked area moves it, dragging a corner resizes it."""
    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from vtf.gui.video_view import VideoView

    v = VideoView()
    v.resize(400, 300)
    v.set_frame(np.zeros((300, 400, 3), np.uint8))     # image fills the widget: 1 norm = 400/300 px
    v.set_mode("track")
    v.set_tracks([(0.25, 0.25, 0.25, 0.25, 0.0)])
    edits = []
    v.boxEdited.connect(lambda i, b: edits.append((i, b)))
    v.show()
    # move: grab the middle (125, 112) and drag 40 px right, 30 px down
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(125, 112))
    QTest.mouseMove(v, QPoint(165, 142))
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(165, 142))
    i, b = edits[-1]
    assert i == 0 and abs(b[0] - 0.35) < 0.01 and abs(b[1] - 0.35) < 0.01 and abs(b[2] - 0.25) < 1e-6
    # resize: bottom right corner (now at 240, 180) out to (280, 240); top left stays
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(240, 180))
    QTest.mouseMove(v, QPoint(280, 240))
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(280, 240))
    i, b = edits[-1]
    assert abs(b[0] - 0.35) < 0.01 and abs(b[2] - 0.35) < 0.01 and abs(b[3] - 0.45) < 0.01
    # not editable (away from a section start): dragging an area is a correction,
    # dragging on free space draws a new area
    v.set_editable(False)
    drawn, fixed = [], []
    v.boxDrawn.connect(drawn.append)
    v.boxCorrected.connect(lambda i, b: fixed.append(i))
    n = len(edits)
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(180, 150))
    QTest.mouseMove(v, QPoint(200, 160))
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(200, 160))
    assert len(edits) == n and fixed == [0] and not drawn
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
    QTest.mouseMove(v, QPoint(70, 70))
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(70, 70))
    assert drawn
    v.close()
    v.deleteLater()


def test_video_view_grab_tracked_area(app):
    """Away from a section start (tracked overlay shown) grabbing an area and moving
    it is a correction; a plain click changes nothing."""
    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from vtf.gui.tracking_ui import TrackingBox
    from vtf.gui.video_view import VideoView

    v = VideoView()
    v.resize(400, 300)
    v.set_frame(np.zeros((300, 400, 3), np.uint8))
    v.set_mode("track")
    v.set_editable(False)
    v.set_track_overlay([(0.25, 0.25, 0.25, 0.25, 10.0, False)])
    got = []
    v.boxCorrected.connect(lambda i, b: got.append((i, b)))
    v.show()
    QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, QPoint(125, 112))
    assert not got and v._overlay                                # just a click
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(125, 112))
    QTest.mouseMove(v, QPoint(165, 112))
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(165, 112))
    i, b = got[-1]
    assert i == 0 and abs(b[0] - 0.35) < 0.01 and b[4] == 10.0

    tb = TrackingBox()
    tb.reset(10_000, 30)
    tb.add_box((0.25, 0.25, 0.25, 0.25), 0)
    assert "nachjustiert" in tb.correct_area(4000, 0, b)
    assert tb.sections[0].keys[0][:2] == (4000, 0) and tb.sections[0].keys[0][2][0] == b[0]
    assert "angepasst" in tb.correct_area(0, 0, b)                # at the start: plain edit
    v.close()
    v.deleteLater()
    tb.deleteLater()


def test_player_countdown(app):
    """Player: the countdown shows during the 10 s before the script starts."""
    from vtf.funscript import Action
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    w.set_player_mode(True)
    w.player.countdown_cb.setChecked(True)
    w.stroke_actions = lambda: [Action(0, 50), Action(30_000, 50), Action(30_400, 90)]
    w._update_countdown(15_000)
    assert w.view._countdown is None
    w._update_countdown(22_500)
    assert w.view._countdown == 8
    w._update_countdown(31_000)
    assert w.view._countdown is None
    w.player.countdown_cb.setChecked(False)
    w._update_countdown(25_000)
    assert w.view._countdown is None
    w.set_player_mode(False)
    w.dirty = False
    w.close()
    w.deleteLater()


def test_player_hides_tracking_areas(app):
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    w.set_player_mode(False)
    w.panel.method.setCurrentIndex(w.panel.method.findData("track"))
    assert w.view.mode == "track"
    w.set_player_mode(True)
    assert w.view.mode == "play"
    w.set_player_mode(False)
    assert w.view.mode == "track"
    w.panel.method.setCurrentIndex(w.panel.method.findData("flow"))
    w.dirty = False
    w.close()
    w.deleteLater()


def test_close_video(app, tmp_path):
    from pathlib import Path

    from videogen import make_video

    from vtf.gui.main_window import MainWindow

    vid = tmp_path / "c.avi"
    make_video(Path(vid))
    w = MainWindow()
    w.set_player_mode(False)
    w.open_video(str(vid))
    assert w.cap is not None and w.close_act.isVisible() is not None
    w.close_video()
    assert w.cap is None and w.video_path is None and not w.analyze_btn.isEnabled()
    assert w.view._img is None and not w.curve.actions
    w.open_video(str(vid))                                    # a new one can be opened again
    assert w.cap is not None
    w.dirty = False
    w.close()
    w.deleteLater()


def test_bookmarks_rhythm_preview(app, tmp_path):
    from pathlib import Path

    from videogen import make_video

    from vtf.gui.main_window import MainWindow
    from vtf.prep import load_bookmarks

    vid = tmp_path / "bm.avi"
    make_video(Path(vid))
    w = MainWindow()
    w.set_player_mode(False)
    w.open_video(str(vid))
    w.add_bookmark_at(1000, "Szene A")
    w.add_bookmark_at(3000, "Szene B")
    assert load_bookmarks(vid) == [(1000, "Szene A"), (3000, "Szene B")]
    assert w.curve.bookmarks == w.bookmarks and len(w.slider.marks) == 2
    w.seek_ms(0)
    w.jump_bookmark(1)
    assert abs(w._cur_ms() - 1000) < 50
    w.jump_bookmark(1)
    assert abs(w._cur_ms() - 3000) < 50
    w.jump_bookmark(-1)
    assert abs(w._cur_ms() - 1000) < 50
    w.delete_bookmark()
    assert w.bookmarks == [(3000, "Szene B")]
    # rhythm template with undo
    w.apply_rhythm(0, 4000, 60, 10, 90, "even")
    assert len(w.curve.actions) >= 8 and {a.pos for a in w.curve.actions} == {10, 90}
    w.undo()
    assert not w.curve.actions
    w.apply_rhythm(0, 4000, 60, 10, 90, "even")
    # the stroke rail shows position and speed
    w._show_time(250)
    assert 10 <= w.rail._pos <= 90 and w.rail._speed > 100
    # reopening loads the bookmarks again
    w.dirty = False
    w.close_video()
    assert w.bookmarks == []
    w.open_video(str(vid))
    assert w.bookmarks == [(3000, "Szene B")]
    w.dirty = False
    w.close()
    w.deleteLater()


def test_section_edges_move(app):
    from vtf.gui.tracking_ui import TrackingBox

    tb = TrackingBox()
    tb.reset(20_000, 30)
    tb.add_box((0.1, 0.1, 0.2, 0.2), 0)
    tb.add_box((0.5, 0.5, 0.2, 0.2), 8000)
    tb.calibrate("in", 9000)
    assert "endet" in tb.move_edge(0, "end", 6000)
    assert tb.sections[0].end_ms == 6000
    assert "beginnt" in tb.move_edge(1, "start", 9500)
    s = tb.sections[1]
    assert s.start_ms == 9500 and s.calib_in_ms is None          # calibration before the start dropped
    tb.move_edge(1, "start", 100)                                 # cannot pass the previous start
    assert tb.sections[1].start_ms > tb.sections[0].start_ms
    tb.deleteLater()


def test_curve_drags_section_edge(app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from vtf.gui.curve_widget import CurveWidget

    c = CurveWidget()
    c.resize(640, 240)
    c.set_data(c.t_ms[:0], c.signal[:0], [], 20_000)
    c.view_ms, c.start_ms = 20_000, 0
    c.set_sections([(2000, 10_000, None, None, 0, True)])
    moved = []
    c.sectionEdgeMoved.connect(lambda i, w, ms: moved.append((i, w, ms)))
    c.show()
    x_end = int(c._x(10_000))
    x_to = int(c._x(12_000))
    QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, QPoint(x_end, 100))
    QTest.mouseMove(c, QPoint(x_to, 100))
    QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(x_to, 100))
    assert moved and moved[-1][:2] == (0, "end") and abs(moved[-1][2] - 12_000) < 100
    c.close()
    c.deleteLater()


def test_layout_sides(app):
    """Analysis on the left, script on the right; the player hides the script side,
    both sides can be folded away."""
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    w.show()
    w.left_action.setChecked(True)
    w.right_action.setChecked(True)
    w.set_player_mode(False)
    assert w.panel.analysis_page.window() is w and w.panel.script_page.window() is w
    assert w.side.isVisible() and w.script_side.isVisible()
    w.set_player_mode(True)
    assert w.side.isVisible() and not w.script_side.isVisible()
    w.set_player_mode(False)
    w.right_action.setChecked(False)
    w._sides_toggled()
    assert not w.script_side.isVisible()
    w.right_action.setChecked(True)
    w._sides_toggled()
    w.panel.options_btn.setChecked(True)
    assert w.panel.options.isVisibleTo(w.panel.analysis_page)
    w.dirty = False
    w.close()
    w.deleteLater()


def test_curve_follows_again_after_scrolling(app):
    """Scrolling by hand stops following the playhead only for a while; zooming
    never stops it."""
    from vtf.gui import curve_widget
    from vtf.gui.curve_widget import CurveWidget

    c = CurveWidget()
    c.resize(640, 240)
    c.set_data(c.t_ms[:0], c.signal[:0], [], 120_000)
    c.view_ms = 20_000
    c.set_playhead(1000)
    c.scroll_by(30_000)                                 # looked somewhere else
    assert not c.follow
    c.set_playhead(2000)                                # right after: stays where the user looks
    assert not c.follow and c.start_ms > 20_000
    c._scrolled_at -= curve_widget.FOLLOW_AGAIN_S + 1   # a while later
    c.set_playhead(3000)
    assert c.follow and c.start_ms <= 3000 <= c.start_ms + c.view_ms
    c.set_data(c.t_ms[:0], c.signal[:0], [], 120_000)   # a new video always follows
    assert c.follow
    c.deleteLater()


def test_video_zoom_for_small_areas(app):
    """Strg+wheel zooms around the cursor; a small rectangle drawn while zoomed
    becomes a small area of the frame."""
    import numpy as np
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest

    from vtf.gui.video_view import VideoView

    v = VideoView()
    v.resize(400, 300)
    v.set_frame(np.zeros((300, 400, 3), np.uint8))
    v.set_mode("track")
    v.show()
    p = QPointF(300, 75)
    before = v._to_norm(p)
    v._zoom_at(p, 4.0)
    after = v._to_norm(p)
    assert v._zoom == 4.0 and abs(before.x() - after.x()) < 1e-3 and abs(before.y() - after.y()) < 1e-3
    drawn = []
    v.boxDrawn.connect(drawn.append)
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(280, 60))
    QTest.mouseMove(v, QPoint(320, 90))
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(320, 90))
    x, y, w, h = drawn[-1]
    # 40 px at 4x zoom = 10 px of the 400 px frame, starting 20 px (5 px) left of the anchor
    assert w < 0.03 and h < 0.03 and abs(x - (0.75 - 5 / 400)) < 0.005
    v.reset_zoom()
    assert v._zoom == 1.0 and v._image_rect().width() == 400
    v.close()
    v.deleteLater()


def test_default_resolution_is_640(app):
    from vtf.gui.settings_panel import SettingsPanel

    p = SettingsPanel()
    assert p.width.currentData() == 640 and p.analysis_settings().width == 640
    p.deleteLater()


def test_pattern_control(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from vtf import patterns as P
    from vtf.gui.main_window import MainWindow
    from vtf.gui.pattern_dialog import PatternEditor

    w = MainWindow()
    w.open_patterns()
    d = w.pattern_dialog
    d.set_mode(0)
    assert d.list.count() > len(P.BUILTIN)              # with category headers
    assert d._select_name("Welle") and d.current().name == "Welle"
    d.bpm.setValue(90)
    d.depth.setValue(80)
    assert w.qsettings.value("pattern/bpm") in (90, "90") and w.qsettings.value("pattern/last") == "Welle"
    assert w.qsettings.value("pattern/depth") in (80, "80") and d.stroke_range() == (20, 100)
    d.start()                                            # without a device: only the preview runs
    d._tick()
    assert d.running and d.view.phase is not None
    assert (d.runner.lo, d.runner.hi, d.runner.bpm) == (20, 100, 90)

    # on the fly: keys and controller, applied to the running pattern (bundled)
    d.nudge(depth=-5, tempo=10)
    assert (d.depth.value(), d.bpm.value()) == (75, 100)
    assert d._apply_timer.isActive()
    d._apply_live()
    assert (d.runner.lo, d.runner.bpm) == (25, 100)
    from vtf.gamepad import Event
    d.pad_state("Testpad")
    assert "Testpad" in d.pad_label.text() and d.pad_timer.isActive()
    d.pad_event(Event("axis", 7, -1.0))                  # D-pad up: deeper
    d.pad_event(Event("axis", 7, 0.0))
    d.pad_event(Event("axis", 6, -1.0))                  # D-pad left: slower
    assert (d.depth.value(), d.bpm.value()) == (80, 95)
    d.pad_event(Event("axis", 1, -1.0))                  # left stick up, held
    for _ in range(5):
        d._pad_tick()
    assert d.depth.value() == 95
    d.pad_event(Event("axis", 1, 0.1))                   # inside the dead zone
    d._pad_tick()
    assert d.depth.value() == 95
    name = d.current().name
    d.pad_event(Event("button", 5, 1.0))                 # RB: next pattern, keeps playing
    assert d.current().name != name and d.running
    d.pad_event(Event("button", 1, 1.0))                 # B: stop
    assert not d.running
    d.pad_event(Event("button", 0, 1.0))                 # A: start
    assert d.running
    d.pad_state(None)                                    # controller gone while in charge: stop
    assert not d.running and not d.pad_timer.isActive()
    d.start()
    w.emergency_stop()                                   # Esc stops the pattern too
    assert not d.running and d.view.phase is None

    # build an own pattern from blocks
    e = PatternEditor(P.Pattern("Neu", []), 60, 0, 100, d)
    assert not e.save_btn.isEnabled()
    e.add_block(P.PALETTE[0][1])
    e.add_block(P.PALETTE[-2][1])                        # pause at the top
    e.blocks.setCurrentRow(0)
    e.repeat.setValue(3)
    e.ramp.setChecked(True)
    e.speed_to.setValue(2.5)
    e.move_block(1)
    res = e.result_pattern()
    assert [b.kind for b in res.blocks] == ["hold", "stroke"]
    assert res.blocks[1].repeat == 3 and res.blocks[1].speed_to == 2.5
    d.user.append(res)
    d.save_user()
    assert [p.name for p in P.load_user()] == ["Neu"]
    d.filter.setCurrentIndex(d.filter.findData("Eigene"))
    assert d._select_name("Neu") and not d.del_btn.isHidden()

    # own and preset patterns are offered when inserting a rhythm
    from vtf.gui.rhythm_dialog import RhythmDialog
    r = RhythmDialog(0, 5000, 60_000, w)
    names = [r.pattern.itemData(i).name for i in range(r.pattern.count())
             if isinstance(r.pattern.itemData(i), P.Pattern)]
    assert names[0] == "Neu" and "Welle" in names

    # programs (Abläufe): steps with a part of a pattern, offsets, step skipping
    from vtf.gui.pattern_dialog import ProgramEditor
    d.set_mode(1)
    assert d.left_stack.currentIndex() == 1 and d._select_program("Eine Stunde")
    pe = ProgramEditor(P.Program("Abend", [P.Step("Gleichmäßig", 2, 50, 70)]), d.all_patterns(),
                       d.find_pattern, d)
    pe.add_step()
    pe.pattern.setCurrentIndex(pe.pattern.findData("Kurz vor Schluss"))
    assert pe.last.maximum() == 3 and pe.last.value() == 3
    pe.last.setValue(1)                                  # only the fast start of it
    pe.minutes.setValue(3)
    pe.depth.setValue(90)
    assert pe.part_view.highlight == (0, 0)
    res = pe.result_program()
    assert res.steps[1].pattern == "Kurz vor Schluss" and (res.steps[1].first, res.steps[1].last) == (1, 1)
    assert res.total_ms == 5 * 60_000
    d.programs.append(res)
    d.save_programs()
    assert [p.name for p in P.load_programs()] == ["Abend"]
    d._fill_programs()
    assert d._select_program("Abend")
    d.start()
    assert d.prunner.running and not d.runner.running
    d._tick()
    assert "Schritt 1/2" in d.pnow.text()
    d.nudge(depth=-10, tempo=5)                          # keys move the whole program
    assert (d.prunner.offset_bpm, d.prunner.offset_depth) == (5, -10)
    d.step_pattern(1)                                    # next step
    assert d.prunner.index == 1 and "Schritt 2/2" in d.pnow.text()
    assert len(d.prunner.runner.pattern.blocks) == 1
    d.reset_offsets()
    assert d.prunner.offset_depth == 0
    d.set_mode(0)                                        # switching mode stops
    assert not d.running

    # random: rolls a new plan on every start, offsets and skipping work as in programs
    d.set_mode(2)
    assert d.right_stack.currentIndex() == 1 and d.ptitle.text().startswith("Zufall")
    d.mood.setCurrentIndex(d.mood.findData("intensiv"))
    assert (d.r_bpm0.value(), d.r_bpm1.value()) == (70, 150)
    d.start()
    first = d.random_prog
    d._tick()
    assert d.prunner.running and "nächster Wechsel" in d.pnow.text()
    d.step_pattern(1)
    assert d.prunner.index == 1
    d.stop()
    d.start()
    assert d.random_prog is not first
    # live: only the current and the next step exist, the next is rolled when a step starts
    pr = d.random_prog
    assert len(pr.steps) == 2
    d.prunner.start_ms -= pr.steps[0].minutes * 60_000 + 10
    d._tick()
    assert d.prunner.index == 1 and len(pr.steps) == 3
    assert len(d.pview.program.steps) == 2               # the view shows this step and the next
    # ranges changed while playing: the running step moves into them, the next is rolled anew
    d.r_bpm0.setValue(200)
    d.r_bpm1.setValue(210)
    assert 200 <= pr.steps[d.prunner.index].bpm <= 210 and 200 <= pr.steps[-1].bpm <= 210
    # a new mood switches to a new step right away
    i = d.prunner.index
    d.mood.setCurrentIndex(d.mood.findData("alles"))
    assert d.prunner.index == i + 1 and d.running
    for _ in range(4):
        d.step_pattern(1)                                # skipping ahead rolls as needed
    assert d.prunner.index == i + 5 and len(pr.steps) == i + 7
    d.stop()
    d.set_mode(0)
    d.close()
    w.close()


def test_setup_wizard(app):
    import time

    from vtf.gui.main_window import MainWindow
    from vtf.gui.setup_wizard import (
        SPEED_LEVELS,
        SetupWizard,
        metronome_actions,
        speed_for,
    )

    acts = metronome_actions(0, 3)
    assert [(a.at, a.pos) for a in acts[:3]] == [(0, 0), (500, 100), (1000, 0)]
    assert speed_for(200) == 500
    w = MainWindow()
    s = w.devices.sync.settings
    old = (s.invert, s.pos_min, s.pos_max, s.max_speed, s.offset_ms)
    wz = SetupWizard(w.devices, w)
    assert not wz.next_btn.isEnabled()                   # first connect
    w.devices.connect_device("sim", "")
    for _ in range(50):
        app.processEvents()
        if w.devices.device is not None:
            break
        time.sleep(0.02)
    wz._refresh()
    assert wz.next_btn.isEnabled()
    wz.go(1)
    wz.invert.setChecked(not old[0])
    assert s.invert != old[0]
    wz.go(2)
    wz.r_lo.setValue(10)
    wz.r_hi.setValue(90)
    assert (s.pos_min, s.pos_max) == (10, 90)
    wz.go(3)
    wz.level = SPEED_LEVELS.index(250)
    wz.try_speed()
    assert len(wz._strokes) == 9 and wz.timer.isActive()
    wz.take_speed()
    assert s.max_speed == 400
    wz.go(4)
    assert wz._strokes == []                             # leaving a page stops the motion
    wz.toggle_metro()
    assert wz._metro is not None
    wz.offset.setValue(120)
    assert s.offset_ms == 120
    wz._metro_start -= 2000
    wz._tick()
    wz.toggle_metro()
    assert wz._metro is None
    wz.go(5)
    assert "400 /s" in wz.summary.text()
    wz.reject()
    s.invert, s.pos_min, s.pos_max, s.max_speed, s.offset_ms = old
    w.devices.save_sync()
    w.devices.disconnect_device()
    w.close()


def test_remote_commands(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    rc = w.remote
    rc.set_enabled(False)
    rc._handle({"cmd": "mode", "mode": 0})
    d = w.pattern_dialog
    rc._handle({"cmd": "select", "name": "Welle"})
    rc._handle({"cmd": "set", "bpm": 77, "depth": 66})
    assert d.current().name == "Welle" and (d.bpm.value(), d.depth.value()) == (77, 66)
    rc._handle({"cmd": "toggle"})
    assert d.running and rc._state["running"] and rc._state["name"] == "Welle"
    rc._handle({"cmd": "estop"})
    assert not d.running
    rc._handle({"cmd": "mode", "mode": 1})
    rc._handle({"cmd": "select", "name": "Eine Stunde"})
    rc._handle({"cmd": "start"})
    rc._handle({"cmd": "set", "bpm": rc._state["bpm"] + 10})
    assert d.prunner.offset_bpm == 10
    rc._handle({"cmd": "step", "d": 1})
    assert d.prunner.index == 1
    rc._handle({"cmd": "stop"})
    assert not d.running and "Eine Stunde" in rc._state["programs"]
    d.reset_offsets()
    d.set_mode(0)
    w.close()


def test_music_mode_and_script(app, sine_video, tmp_path, monkeypatch):
    import sys
    import time

    import numpy as np

    from vtf import music
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    d = w.ensure_patterns()
    d.set_mode(3)
    assert d.right_stack.currentIndex() == 2
    # fake recorder: a click track at 120 per minute
    x = np.zeros(int(4 * music.RATE), np.float32)
    n = int(0.03 * music.RATE)
    hit = np.sin(2 * np.pi * 80 * np.arange(n) / music.RATE) * np.exp(-np.arange(n) / (0.008 * music.RATE))
    for k in range(8):
        i = int((0.2 + 0.5 * k) * music.RATE)
        x[i:i + n] += 0.8 * hit
    raw = tmp_path / "s.raw"
    raw.write_bytes((x * 32767).astype(np.int16).tobytes())
    script = (f"import sys,time; d=open({str(raw)!r},'rb').read()\n"
              "for i in range(0,len(d),4410): sys.stdout.buffer.write(d[i:i+4410]); sys.stdout.flush(); time.sleep(0.05)")
    d._start_music([sys.executable, "-c", script])
    assert d.running and d.music.running
    t0 = time.monotonic()
    while time.monotonic() - t0 < 6 and not d._music_error:
        app.processEvents()
        d._tick()
        time.sleep(0.02)
    assert d.music.count >= 5
    d._tick()                                            # recorder ended: stops with a message
    assert not d.running and "Aufnahme beendet" in d.m_info.text()
    assert w.remote is not None and d.remote_state()["mode"] == 3
    d.set_mode(0)

    # script from music (beats faked, the test video has no sound track)
    w.open_video(str(sine_video))
    monkeypatch.setattr(music, "beats_from_file", lambda *a, **k: [1.0, 1.5, 2.0, 2.5, 3.0])
    w.curve.actions = []
    w.apply_music_script(0, 4000, every=1, lo=10, hi=90)
    assert [a.at for a in w.curve.actions if a.pos == 10] == [1000, 1500, 2000, 2500, 3000]
    w.undo()
    assert w.curve.actions == []
    w.close()


def test_external_player_sync(app, tmp_path):
    import time

    from test_players import _fake_mpv

    from vtf.funscript import Action, Funscript
    from vtf.gui.main_window import MainWindow

    video = tmp_path / "film.mp4"
    video.write_bytes(b"")
    Funscript([Action(0, 0), Action(500, 100), Action(1000, 0), Action(60_000, 100)]).save(
        tmp_path / "film.funscript")
    sock = str(tmp_path / "mpv.sock")
    state = {"time-pos": 0.2, "pause": False, "path": str(video), "duration": 60.0}
    srv = _fake_mpv(sock, state)
    w = MainWindow()
    w.devices.connect_device("sim", "")
    for _ in range(50):
        app.processEvents()
        if w.devices.device is not None:
            break
        time.sleep(0.02)
    ext = w.external
    ext.connect_player("mpv", mpv_socket=sock)
    assert ext.running
    t0 = time.monotonic()
    while time.monotonic() - t0 < 2 and not ext.script:
        app.processEvents()
        ext._tick()
        time.sleep(0.02)
    assert ext.points == 4 and "Script gefunden" in ext.status
    assert ext.safety_active()
    for _ in range(20):
        ext._tick()
        time.sleep(0.02)
    assert w.devices.device.commands                   # the device got moves
    w.emergency_stop()                                 # Esc disconnects from the player
    assert not ext.running and "Not-Aus" in ext.status
    srv.close()
    w.devices.disconnect_device()
    w.close()


def test_multi_axis_tcode(app):
    import os
    import time

    from vtf.funscript import Action
    from vtf.gui.main_window import MainWindow

    master, slave = os.openpty()
    w = MainWindow()
    ctl = w.devices
    ctl.connect_device("tcode", os.ttyname(slave))
    for _ in range(50):
        app.processEvents()
        if ctl.device is not None:
            break
        time.sleep(0.02)
    assert set(ctl.axis_syncs) == {"L1", "L2", "R0", "R1", "R2"}
    ctl.set_actions([Action(0, 0), Action(1000, 100), Action(2000, 0)])
    ctl.set_axes({"L0": [], "R1": [Action(0, 50), Action(1000, 90), Action(2000, 10)]})
    for t in range(0, 1600, 20):
        ctl.tick(t, True)
    ctl.tick(0, False)
    from test_devices_more import _read_until
    text = _read_until(master, "DSTOP\n")
    assert "L0" in text and "R1" in text and "DSTOP" in text
    ctl.disconnect_device()
    assert ctl.axis_syncs == {}
    os.close(master)
    os.close(slave)
    w.close()


def test_remote_random_mood(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    rc = w.remote
    rc._handle({"cmd": "mode", "mode": 2})
    d = w.pattern_dialog
    rc._handle({"cmd": "start"})
    i = d.prunner.index
    rc._handle({"cmd": "mood", "mood": "intensiv"})
    assert d.mood.currentData() == "intensiv" and d.prunner.index == i + 1
    assert rc._state["mood"] == "intensiv" and ["alles", "Alles"] in rc._state["moods"]
    rc._handle({"cmd": "stop"})
    d.set_mode(0)
    w.close()


def test_live_strip_all_modes(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from vtf.gui.main_window import MainWindow
    from vtf.gui.pattern_view import _window

    assert _window([(0, 0), (10, 100), (20, 0)], 5, 15) == [(5, 50.0), (10, 100), (15, 50.0)]
    w = MainWindow()
    d = w.ensure_patterns()
    d.resize(1200, 800)
    for mode in (0, 1, 2):
        d.set_mode(mode)
        assert d.live.future                          # a preview even before starting
        d.start()
        for _ in range(5):
            d._tick()
        assert d.live.past and d.live.future and d.live.now >= d.live.past[-1][0]
        before, after = d.live.window_ms()
        assert after > before > 0
        d.stop()
        assert d.live.past == []
    d.set_mode(1)
    d.start()
    d.prunner.start_ms -= d.prunner.program.steps[0].minutes * 60_000 - 3000   # 3 s before the next step
    d._tick()
    assert d.live.marks and d.live.marks[0][1] == d.prunner.program.steps[1].pattern
    d.stop()
    d.set_mode(3)
    d._update_live()
    assert d.live.hint
    d.set_mode(0)
    w.close()


def test_dialogs_never_squeezed(app, tmp_path, monkeypatch):
    """Wrapped text counts towards the minimum height, so a window manager cannot squeeze the rows."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from vtf.gui.device_ui import DeviceDialog
    from vtf.gui.main_window import MainWindow
    from vtf.gui.players_ui import PlayersDialog
    from vtf.gui.remote_ui import RemoteDialog

    w = MainWindow()
    w.remote.set_enabled(True)
    for d in (RemoteDialog(w.remote, w), PlayersDialog(w.external, w)):
        d.show()
        app.processEvents()
        d.resize(d.width(), 120)              # e.g. a window manager restoring a too small size
        for _ in range(5):
            app.processEvents()
        lay = d.layout()
        assert d.height() >= lay.totalHeightForWidth(d.width()) - 1
        d.close()
    dd = DeviceDialog(w.devices, w)
    dd.show()
    for _ in range(5):
        app.processEvents()
    avail = dd.screen().availableGeometry().height()
    assert dd.height() <= avail                  # taller content scrolls instead
    assert dd.scroll.widget() is dd.body
    dd.close()
    w.remote.set_enabled(False)
    w.dirty = False
    w.close()


def test_random_single_depth(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    d = w.ensure_patterns()
    d.set_mode(2)
    d.r_vary.setChecked(False)
    d.r_depth.setValue(80)
    assert all(st.depth == 80 for st in d.random_prog.steps)
    assert "Tiefe 80 %" in d.pinfo.text()
    d.r_vary.setChecked(True)
    d.start()
    for _ in range(40):
        d.reroll(restart=True)
    assert all(56 <= st.depth <= 80 for st in d.random_prog.steps)
    d.r_depth.setValue(50)                     # the running step follows at once
    assert d.random_prog.steps[d.prunner.index].depth <= 50
    d.stop()
    assert d.qs.value("pattern/r_depth") in (50, "50")
    d.set_mode(0)
    w.close()


def test_remote_video_player(app, sine_video, tmp_path, monkeypatch):
    """Phone as remote for the video player: playlist, play/pause, skip, seek, remaining time."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from vtf.gui.main_window import MainWindow

    w = MainWindow()
    rc = w.remote
    w.player.add_paths([str(sine_video)])
    rc._snapshot()
    v = rc._state["video"]
    assert v["items"] == [sine_video.stem] and v["title"] == ""
    rc._handle({"cmd": "vplay", "index": 0})
    assert w.player_mode and w.timer.isActive()
    v = rc._state["video"]
    assert v["title"] == sine_video.stem and v["playing"] and v["current"] == 0 and v["dur"] > 5000
    rc._handle({"cmd": "vtoggle"})                     # pause
    assert not w.timer.isActive() and not rc._state["video"]["playing"]
    rc._handle({"cmd": "vseek", "ms": 3000})
    assert abs(rc._state["video"]["pos"] - 3000) <= 50
    rc._handle({"cmd": "vskip", "ms": -10000})         # not before the start
    assert rc._state["video"]["pos"] == 0
    rc._handle({"cmd": "vskip", "ms": 10**9})          # not past the end
    assert rc._state["video"]["pos"] <= rc._state["video"]["dur"]
    rc._handle({"cmd": "vnext"})                       # only one video: end of the playlist
    rc._handle({"cmd": "vplay", "index": "x"})         # junk is ignored
    rc._handle({"cmd": "estop"})
    assert not w.timer.isActive()
    w.set_player_mode(False)                  # do not leave the shared settings in player mode
    w.dirty = False
    w.close()


def test_reset_to_defaults(app, tmp_path, monkeypatch):
    """Every adjustable area has a "Standard" button; a double-click resets a single slider."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from vtf.device.sync import SyncSettings
    from vtf.generator import GeneratorSettings
    from vtf.gui.device_ui import DeviceDialog
    from vtf.gui.main_window import MainWindow
    from vtf.gui.pattern_dialog import DEFAULT_BPM, DEFAULT_DEPTH
    from vtf.safety import SafetySettings

    w = MainWindow()
    p = w.panel
    p.sensitivity.setValue(10)
    p.invert.setChecked(True)
    p.width.setCurrentIndex(0)
    p.reset_script()
    assert p.generator_settings() == GeneratorSettings()
    p.reset_options()
    assert p.width.currentData() == 640
    p.smoothing.setValue(400)
    ev = QMouseEvent(QEvent.MouseButtonDblClick, QPointF(5, 5), QPointF(5, 5), Qt.LeftButton, Qt.LeftButton,
                     Qt.NoModifier)
    app.sendEvent(p.smoothing, ev)
    assert p.smoothing.value() == GeneratorSettings().smoothing_ms

    dd = DeviceDialog(w.devices, w)
    dd.offset[1].setValue(250)
    dd.pmax.setValue(70)
    dd.invert.setChecked(True)
    dd.reset_playback()
    s = w.devices.sync.settings
    assert (s.offset_ms, s.pos_max, s.invert) == (SyncSettings().offset_ms, 100, False)
    dd.safe_on.setChecked(True)
    dd.safe_max.setValue(90)
    dd.reset_safety()
    assert w.devices.safety.settings == SafetySettings()
    dd.close()

    d = w.ensure_patterns()
    d.bpm.setValue(150)
    d.depth.setValue(30)
    d.reset_sliders()
    assert (d.bpm.value(), d.depth.value()) == (DEFAULT_BPM, DEFAULT_DEPTH)
    d.set_mode(2)
    d.r_depth.setValue(40)
    d.r_vary.setChecked(False)
    d.r_m1.setValue(10)
    d.reset_random()
    assert d.r_depth.value() == 100 and d.r_vary.isChecked() and d.r_m1.value() == 3.0
    assert all(st.depth >= 70 for st in d.random_prog.steps)
    d.m_sens.setValue(5)
    d.m_level.setChecked(False)
    d.reset_music()
    assert d.m_sens.value() == 50 and d.m_level.isChecked()
    d.set_mode(0)
    w.dirty = False
    w.close()


def test_ai_suggestion_and_check(app, sine_video, tmp_path, monkeypatch):
    """KI-Vorschlag: found body parts become a menu, choosing one adds the box; the
    KI-Kontrolle setting reaches the analysis and is saved with the settings."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    import numpy as np

    from vtf import pose
    from vtf.gui import pose_ui
    from vtf.gui.main_window import MainWindow
    from vtf.gui.settings_panel import SettingsPanel

    w = MainWindow()
    w.set_player_mode(False)
    w.open_video(str(sine_video))
    w.panel.method.setCurrentIndex(w.panel.method.findData("track"))
    app.processEvents()
    monkeypatch.setattr(pose_ui, "ensure_models", lambda parent: True)
    pts = np.zeros((pose.N_POINTS, 2))
    conf = np.zeros(pose.N_POINTS)
    h, wd = w._frame.shape[:2]
    pts[pose.L_HIP], pts[pose.R_HIP] = (wd * 0.55, h * 0.5), (wd * 0.45, h * 0.5)
    conf[[pose.L_HIP, pose.R_HIP]] = 5.0
    person = pose.Person(np.array([0, 0, wd, h]), 0.9, pts, conf)

    menu = w._suggestion_menu([person], w._frame.shape)
    acts = [a for a in menu.actions() if not a.isSeparator() and a.isEnabled()]
    assert [a.text() for a in acts] == ["Becken / Schritt", "Hüfte (breit)"]
    acts[0].trigger()
    assert w._suggestion_menu([], w._frame.shape) is None
    assert len(w.panel.tracking.sections) == 1
    box = w.panel.tracking.sections[0].boxes[0]
    assert abs(box[0] + box[2] / 2 - 0.5) < 0.02

    w.panel.tracking.ai_check.setChecked(True)
    assert w.panel.analysis_settings().ai_check
    p = SettingsPanel()
    p.load_dict(w.panel.to_dict())
    assert p.tracking.ai_check.isChecked()
    p.reset_options()
    assert not p.tracking.ai_check.isChecked()
    p.deleteLater()
    w.dirty = False
    w.close()
