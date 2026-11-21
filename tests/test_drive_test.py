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
    assert "Übernommen: nein" in text and "(Vorschau)" in text
    assert " 2000 ms -> Stufe 7, 1800 ms, 1801 ms" in text      # preview from the measurement
    assert "  500 ms -> wie Intiface, 500 ms" in report({})   # nothing measured: as Intiface


def test_dialog_measures_taps(tmp_path):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6 import QtCore
    from vtf.device import SimulatedDevice
    from vtf.device.sync import ScriptSync
    from vtf.gui.drive_test import ROUNDS, DriveTestDialog, sent_ms
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class Ctl:
        device = SimulatedDevice()
        sync = ScriptSync()
        saved: dict = {}
        qs = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.IniFormat)

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
    assert Ctl.saved[4] == 1.3 and Ctl.saved[6] != 9.9 and "Übernommen: 4:1.30, 6:" in d.summary.toPlainText()
    d.clear()
    assert Ctl.saved == {} and "Übernommen: nein" in d.summary.toPlainText()
    d.done(0)
    again = DriveTestDialog(Ctl())               # measurements come back next time
    assert set(again.results) == {6} and "Stufe" in again.summary.toPlainText()
    again.done(0)
    app.processEvents()


def test_correction_is_off_while_measuring(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6 import QtCore, QtWidgets
    from vtf.device.buttplug import ButtplugDevice, RemoteDevice
    from vtf.device.sync import ScriptSync
    from vtf.gui.drive_test import DriveTestDialog, sent_ms
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dev = ButtplugDevice()
    sent = []
    dev._device = lambda: RemoteDevice(3, "Fredorch Device", 1, 15)
    dev._send = sent.append
    monkeypatch.setattr(ButtplugDevice, "connected", property(lambda self: True))
    factors = {s: 1.5 for s in range(1, 9)}
    dev.fred_factors = dict(factors)

    class Ctl:
        device = dev
        sync = ScriptSync()
        qs = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.IniFormat)

        def fred_factors(self):
            return dict(factors)
    d = DriveTestDialog(Ctl())
    d.step.setCurrentIndex(d.step.findData(6))
    d.start()
    d.lead.stop()
    d._stroke()
    assert sent[-1]["LinearCmd"]["Vectors"][0]["Duration"] == sent_ms(6)   # plain step 6
    d.stop()
    assert dev.fred_factors == factors                                      # back on
    d.done(0)
    app.processEvents()


def test_tempo_test_saves_last_clean_tempo(tmp_path):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from vtf.device import SimulatedDevice
    from vtf.device.sync import ScriptSync
    from vtf.gui.drive_test import PER_LEVEL, TEMPO_START, TEMPO_STEP, TempoTestDialog, limits_report
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class Ctl:
        device = SimulatedDevice()
        sync = ScriptSync()
        saved: dict = {8: 200.0}

        def fred_limits(self):
            return dict(self.saved)

        def set_fred_limits(self, f):
            Ctl.saved = dict(f)
    Ctl.device.connect()
    d = TempoTestDialog(Ctl())
    d.depth.setCurrentIndex(d.depth.findData(15))
    d.start()
    for _ in range(2 * PER_LEVEL + 1):          # two levels, then the third starts
        d._stroke()
    assert d.bpm == TEMPO_START + 2 * TEMPO_STEP
    assert [round(c[0]) for c in Ctl.device.commands[:3]] == [0, 100, 0]
    d.tap()                                     # juddering now: the level before was clean
    assert Ctl.saved == {8: 200.0, 15: 30_000 / (TEMPO_START + TEMPO_STEP)} and d.depth.currentData() == 11
    assert " 70" in d.summary.toPlainText() and "150" in limits_report({})
    d.done(0)
    app.processEvents()
