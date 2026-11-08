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
    w.curve._delete(0)
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
