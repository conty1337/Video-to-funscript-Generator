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
