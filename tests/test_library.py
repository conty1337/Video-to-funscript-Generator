"""Funscript library (library.py) and its dialog."""
import os

import pytest

from vtf.funscript import Action, Funscript, funscript_path_for
from vtf.library import filtered, load_ratings, save_rating, scan


def _video(folder, name, period):
    v = folder / f"{name}.mp4"
    v.write_bytes(b"x")
    Funscript([Action(k * period, 90 if k % 2 else 10) for k in range(40)]).save(funscript_path_for(v))
    return v


def test_scan_numbers_search_sort_and_ratings(tmp_path):
    a = _video(tmp_path, "langsam", 1000)
    sub = tmp_path / "sub"
    sub.mkdir()
    _video(sub, "schnell", 250)
    (tmp_path / "ohne_script.mp4").write_bytes(b"x")
    es = scan(tmp_path)
    assert [e.name for e in es] == ["langsam", "schnell"]
    slow, fast = es
    assert slow.duration_ms == 39000 and slow.strokes == 19 and abs(slow.per_min - 30) < 1
    assert fast.avg_speed > slow.avg_speed and len(fast.heat) == 120
    assert [e.name for e in filtered(es, "schn")] == ["schnell"]
    assert [e.name for e in filtered(es, sort="tempo")] == ["schnell", "langsam"]
    save_rating(a, 4)
    assert load_ratings()[str(a.resolve())] == 4
    es = scan(tmp_path)
    assert es[0].rating == 4 and [e.name for e in filtered(es, min_rating=3)] == ["langsam"]
    save_rating(a, 0)
    assert str(a.resolve()) not in load_ratings()
    assert scan(tmp_path / "gibt_es_nicht") == []


def test_library_dialog(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from vtf.gui.library_dialog import LibraryDialog

    app = QApplication.instance() or QApplication([])
    _video(tmp_path, "eins", 500)
    _video(tmp_path, "zwei", 300)
    qs = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    dlg = LibraryDialog(qs)
    dlg.set_folder(str(tmp_path))
    assert dlg.tree.topLevelItemCount() == 2 and "2 von 2" in dlg.count.text()
    dlg.search.setText("zwei")
    assert dlg.tree.topLevelItemCount() == 1
    it = dlg.tree.topLevelItem(0)
    dlg.tree.setCurrentItem(it)
    QTest.keyClick(dlg, Qt.Key_3)
    assert dlg._entry(it).rating == 3
    played = []
    dlg.playRequested.connect(played.append)
    dlg._play(it)
    assert played and played[0].endswith("zwei.mp4")
    dlg.grab()                                          # paints heatmap and stars
    assert app is not None


def test_played_and_check_columns(tmp_path):
    """Last/most played come from the statistics, the check count from the editor's check."""
    from vtf import stats
    a = _video(tmp_path, "alt", 1000)
    _video(tmp_path, "neu", 1000)
    gap = tmp_path / "luecke.mp4"; gap.write_bytes(b"x")
    Funscript([Action(0, 10), Action(500, 90), Action(20000, 10), Action(20500, 90)]).save(funscript_path_for(gap))
    rec = stats.Recorder(today=lambda: "2026-11-01")
    rec.add("video", "alt", 600)
    rec.flush()
    rec = stats.Recorder(today=lambda: "2026-11-14")
    rec.add("video", "neu", 60)
    rec.flush()
    es = {e.name: e for e in scan(tmp_path)}
    assert es["alt"].played_s == 600 and es["neu"].last_played == "2026-11-14" and es["luecke"].played_s == 0
    assert [e.name for e in filtered(es.values(), sort="recent")][:2] == ["neu", "alt"]
    assert [e.name for e in filtered(es.values(), sort="played")][0] == "alt"
    assert es["luecke"].issues >= 1 and es["luecke"].issue_text and es["alt"].issues == 0
    assert [e.name for e in filtered(es.values(), sort="check")][0] == "luecke"
    # the rating saved in the script (else none) and sorting by it
    Funscript([Action(0, 10), Action(500, 90)], metadata={"vtf_rating": 86}).save(funscript_path_for(gap))
    es = {e.name: e for e in scan(tmp_path)}
    assert es["luecke"].score == 86 and es["alt"].score == -1
    assert [e.name for e in filtered(es.values(), sort="score")][0] == "luecke"
    stats.reset()
    assert a.exists()
