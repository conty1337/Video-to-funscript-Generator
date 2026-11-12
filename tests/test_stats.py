"""Session statistics (stats.py) and its dialog."""
import os

import pytest

from vtf import stats as S


def test_recorder_summary_and_reset():
    S.reset()
    day = ["2026-11-10"]
    clock = [0.0]
    r = S.Recorder(today=lambda: day[0], clock=lambda: clock[0])
    r.add("video", "Clip A", 600, 300)
    r.add("pattern", "Welle", 120, 90)
    assert S.load()["days"] == {}                      # kept in memory until flushed
    clock[0] = 31.0
    r.add("video", "Clip B", 60, 20)                   # 30 s passed: written
    day[0] = "2026-11-12"
    r.add("video", "Clip A", 300, 100)
    r.flush()
    data = S.load()
    assert set(data["days"]) == {"2026-11-10", "2026-11-12"}
    today = S.summary(data, 1, today="2026-11-12")
    assert today["seconds"] == 300 and today["strokes"] == 100 and today["days"] == 1
    week = S.summary(data, 7, today="2026-11-12")
    assert week["seconds"] == 1080 and week["strokes"] == 510 and week["days"] == 2
    assert week["videos"][0] == ("Clip A", 900.0) and week["patterns"] == [("Welle", 120.0)]
    assert S.fmt_duration(3720) == "1 h 02 min" and S.fmt_duration(90) == "2 min"
    S.reset()
    assert S.load() == {"days": {}}


def test_stats_dialog_shows_numbers():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6.QtWidgets import QApplication, QLabel

    from vtf.gui.stats_dialog import StatsDialog

    QApplication.instance() or QApplication([])
    S.reset()
    r = S.Recorder()
    r.add("video", "Mein Clip", 3720, 1234)
    r.flush()
    d = StatsDialog()
    texts = [w.text() for w in d.findChildren(QLabel)]
    assert "1 h 02 min" in texts and "1.234" in texts and "Mein Clip" in texts
    S.reset()
