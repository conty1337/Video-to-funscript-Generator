import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from videogen import make_video  # noqa: E402

# Settings and models of the tests never go to the real user folders (Qt keeps the
# settings path it saw first, so a monkeypatched XDG_CONFIG_HOME in a later test is not
# enough): otherwise e.g. "KI-Kontrolle on" from one run makes the next run wait for a
# download question.
_home = tempfile.mkdtemp(prefix="vtf-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_home, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_home, "data")


@pytest.fixture
def sine_video(tmp_path):
    return make_video(tmp_path / "sine.avi")


def pytest_unconfigure(config):
    import shutil
    shutil.rmtree(_home, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    """Tear Qt down in order before Python exits.

    The GUI tests leave many windows and dialogs behind (closed is only hidden) and analysis
    threads may still be waiting. At exit PySide then walks all its objects and now and
    then reaches a widget whose C++ side is already gone: a crash after all tests passed.
    Here every top-level window is deleted and every Qt thread stopped while Qt still runs.
    """
    if "PySide6.QtWidgets" not in sys.modules:
        return
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent, QThread
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for obj in gc.get_objects():
        try:
            if isinstance(obj, QThread) and obj.isRunning():
                obj.requestInterruption()
                obj.quit()
                obj.wait(3000)
        except RuntimeError:                  # C++ object already deleted
            pass
    for w in app.topLevelWidgets():
        try:
            w.close()
            w.deleteLater()
        except RuntimeError:
            pass
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    gc.collect()
