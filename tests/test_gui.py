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
