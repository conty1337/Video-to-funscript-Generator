"""Drive test: the planned strokes and the copied report."""
import pytest


def test_plan_and_report():
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from vtf.gui.drive_test import LEAD_MS, STROKES, plan, report
    p = plan(1000, 2000)
    assert p[0][1] == 0 and len(p) == STROKES + 1
    assert [x[1] for x in p[1:]] == [100.0, 0.0] * (STROKES // 2)
    assert all(x[2] == 2000 for x in p[1:])
    assert [x[0] for x in p[1:3]] == [1000 + LEAD_MS, 1000 + LEAD_MS + 2000]
    assert report({2000: ("früh", True), 500: ("ok", False)}) == \
        "500 ms: ok, NICHT ganz durch\n2000 ms: früh, ganz durch"


def test_dialog_drives_the_device():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from vtf.device import SimulatedDevice
    from vtf.device.sync import ScriptSync
    from vtf.gui.drive_test import DriveTestDialog
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class Ctl:
        device = SimulatedDevice()
        sync = ScriptSync()
    Ctl.device.connect()
    d = DriveTestDialog(Ctl())
    d.duration.setCurrentIndex(0)                 # 0.5 s
    d.start()
    d._todo = [(t - 10**6, p, ms) for t, p, ms in d._todo]   # all due now
    d._tick()
    assert [round(c[0]) for c in Ctl.device.commands] == [0, 100, 0, 100, 0, 100, 0]
    d.verdict.setCurrentIndex(1)
    d.full.setChecked(False)
    d.take()
    assert d.results == {500: ("früh", False)} and d.duration.currentData() == 1000
    d.done(0)
    app.processEvents()
