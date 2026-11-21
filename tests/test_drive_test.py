"""Drive test (stopwatch): the step asked for and the copied report."""
import pytest


def test_sent_duration_picks_the_step_and_report():
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from vtf.gui.drive_test import FULL_STROKE_MS, report, sent_ms
    times = sorted(FULL_STROKE_MS.values(), reverse=True)
    for step, t in FULL_STROKE_MS.items():
        # the driver takes the slowest step whose table time is below the duration
        assert next(tt for tt in times if tt < sent_ms(step)) == t
    text = report({6: [2900, 3100], 9: [1000.4]})
    assert text.startswith("Reaktionszeit: 122 ms")
    assert "    6      1930      3000  2878    1.49  2900, 3100" in text
    assert "Übernommen: nein" in text and " 2000 ms -> Stufe 7, 1800 ms, 1801 ms" in text
    assert "  500 ms -> wie Intiface, 500 ms" in report({})   # nothing measured: as Intiface


def test_dialog_measures_taps():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from vtf.device import SimulatedDevice
    from vtf.device.sync import ScriptSync
    from vtf.gui.drive_test import ROUNDS, DriveTestDialog, sent_ms
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class Ctl:
        device = SimulatedDevice()
        sync = ScriptSync()
        saved: dict = {}

        def fred_factors(self):
            return self.saved

        def set_fred_factors(self, f):
            Ctl.saved = dict(f)
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
    Ctl.saved = {4: 1.3, 6: 9.9}               # from an earlier session
    d.take()                                  # no reference step yet: still a factor from 0 reaction
    assert Ctl.saved[4] == 1.3 and Ctl.saved[6] != 9.9 and "Übernommen: 4:1.30, 6:" in d.summary.text()
    d.clear()
    assert Ctl.saved == {} and "Übernommen: nein" in d.summary.text()
    d.done(0)
    app.processEvents()
