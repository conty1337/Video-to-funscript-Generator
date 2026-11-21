"""Drive test (stopwatch): the step asked for and the copied report."""
import pytest


def test_sent_duration_picks_the_step_and_report():
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from vtf.gui.drive_test import FULL_STROKE_MS, report, sent_ms
    times = sorted(FULL_STROKE_MS.values(), reverse=True)
    for step, t in FULL_STROKE_MS.items():
        # the driver takes the slowest step whose table time is below the duration
        assert next(tt for tt in times if tt < sent_ms(step)) == t
    assert report({6: [2900, 3100], 9: [1000.4]}) == (
        "Stufe 9: Intiface 878 ms, gemessen 1000 ms (1000)\n"
        "Stufe 6: Intiface 1930 ms, gemessen 3000 ms (2900, 3100)")


def test_dialog_measures_taps():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from vtf.device import SimulatedDevice
    from vtf.device.sync import ScriptSync
    from vtf.gui.drive_test import ROUNDS, DriveTestDialog, sent_ms
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class Ctl:
        device = SimulatedDevice()
        sync = ScriptSync()
    Ctl.device.connect()
    d = DriveTestDialog(Ctl())
    d.step.setCurrentIndex(d.step.findData(6))
    d.start()
    d.tap()                                   # not running yet: ignored
    assert d._times == []
    d.lead.stop()
    d._stroke()
    for _ in range(ROUNDS):
        d.tap()
    assert len(d.results[6]) == ROUNDS and d.step.currentData() == 5
    cmds = Ctl.device.commands
    assert [round(c[0]) for c in cmds] == [0, 100, 0, 100, 0]
    assert all(c[1] >= sent_ms(6) for c in cmds[1:])
    d.done(0)
    app.processEvents()
