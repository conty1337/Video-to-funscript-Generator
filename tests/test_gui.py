"""Smoke test of the GUI (offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


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

    w.export()
    assert len(Funscript.load(funscript_path_for(sine_video)).actions) == n
    w.dirty = False
    w.close()
