"""Script folder: where scripts are saved and found."""
import os

import pytest

from vtf import scriptdir
from vtf.funscript import Action, Funscript


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    scriptdir._index_cache.clear()
    return tmp_path


def _script(path, n=4):
    path.parent.mkdir(parents=True, exist_ok=True)
    Funscript([Action(i * 500, 100 * (i % 2)) for i in range(n)]).save(path)
    return path


def test_default_is_next_to_the_video(cfg):
    sd = scriptdir.settings()
    assert sd.folder is None and sd.next_to_video and not sd.active
    v = cfg / "videos" / "Clip.mp4"
    assert scriptdir.save_paths(v) == [cfg / "videos" / "Clip.funscript"]
    assert scriptdir.save_paths(v, "R1") == [cfg / "videos" / "Clip.roll.funscript"]


def test_settings_are_stored(cfg):
    scriptdir.save_settings(cfg / "scripts", next_to_video=False)
    sd = scriptdir.settings()
    assert sd.folder == cfg / "scripts" and not sd.next_to_video
    scriptdir.save_settings(None)
    assert scriptdir.settings().folder is None
    (cfg / "config" / "video-to-funscript" / "scripts.json").write_text("{kaputt")
    assert scriptdir.settings() == scriptdir.ScriptDir()


def test_save_paths_with_a_folder(cfg):
    v = cfg / "videos" / "Clip.mp4"
    both = scriptdir.ScriptDir(cfg / "scripts", True)
    only = scriptdir.ScriptDir(cfg / "scripts", False)
    assert scriptdir.save_paths(v, sd=both) == [cfg / "videos" / "Clip.funscript", cfg / "scripts" / "Clip.funscript"]
    assert scriptdir.save_paths(v, sd=only) == [cfg / "scripts" / "Clip.funscript"]
    assert scriptdir.save_paths(v, "L1", sd=only) == [cfg / "scripts" / "Clip.surge.funscript"]


def test_find_next_to_the_video_first_then_folder_then_below(cfg):
    v = cfg / "videos" / "Clip.mp4"
    sd = scriptdir.ScriptDir(cfg / "scripts", True)
    assert scriptdir.find_script(v, sd=sd) is None
    deep = _script(cfg / "scripts" / "2026" / "clip.FUNSCRIPT")          # any case, in a subfolder
    assert scriptdir.find_script(v, sd=sd) == deep
    scriptdir._index_cache.clear()
    flat = _script(cfg / "scripts" / "Clip.funscript")
    assert scriptdir.find_script(v, sd=sd) == flat
    near = _script(cfg / "videos" / "Clip.funscript")
    assert scriptdir.find_script(v, sd=sd) == near
    roll = _script(cfg / "scripts" / "Clip.roll.funscript")
    assert scriptdir.find_axes(v, sd=sd) == {"L0": near, "R1": roll}
    assert scriptdir.find_script(v, sd=scriptdir.ScriptDir()) == near


def test_library_player_and_batch_use_the_folder(cfg):
    from vtf.batch import is_analyzed
    from vtf.funscript import load_axes
    from vtf.library import scan
    from vtf.playlist import has_script

    videos = cfg / "videos"; videos.mkdir()
    (videos / "Clip.mp4").write_bytes(b"\0")
    (videos / "Other.mp4").write_bytes(b"\0")
    assert scan(videos) == [] and not has_script(videos / "Clip.mp4") and not is_analyzed(videos / "Clip.mp4")
    scriptdir.save_settings(cfg / "scripts", next_to_video=False)
    _script(cfg / "scripts" / "Clip.funscript", n=6)
    entries = scan(videos)
    assert [e.name for e in entries] == ["Clip"] and entries[0].script == cfg / "scripts" / "Clip.funscript"
    assert has_script(videos / "Clip.mp4") and is_analyzed(videos / "Clip.mp4")
    assert not has_script(videos / "Other.mp4")
    assert len(load_axes(videos / "Clip.mp4")["L0"]) == 6


def test_editor_saves_into_the_folder_and_next_to_the_video(cfg, sine_video):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from vtf.gui.main_window import MainWindow

    folder = cfg / "scripts"
    scriptdir.save_settings(folder, next_to_video=True)
    w = MainWindow()
    try:
        w.set_player_mode(False)
        w.open_video(str(sine_video))
        w.curve.set_actions([Action(0, 0), Action(500, 100), Action(1000, 0)])
        w.export()
        near = sine_video.with_suffix(".funscript")
        assert near.is_file() and (folder / near.name).is_file()
        assert "(" in w.status_label.text()                          # says where it went
        near.unlink()
        scriptdir.save_settings(folder, next_to_video=False)
        (folder / near.name).unlink()
        w.export()
        assert not near.exists() and (folder / near.name).is_file()
        w.open_video(str(sine_video))                                 # found again in the folder
        assert len(w.curve.actions) == 3
    finally:
        w.dirty = False
        w.close()


def test_folder_dialog_stores_the_choice(cfg):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from vtf.gui.scripts_ui import ScriptFolderDialog, describe

    d = ScriptFolderDialog()
    assert not d.next_to.isEnabled() and describe() == "Scripts: neben dem Video"
    d._set_folder(cfg / "scripts")
    assert d.next_to.isEnabled() and scriptdir.settings().folder == cfg / "scripts"
    d.next_to.setChecked(False)
    assert not scriptdir.settings().next_to_video
    assert describe() == f"Scripts: in {cfg / 'scripts'}"
    d._set_folder(None)
    assert scriptdir.settings().folder is None
    d.deleteLater()


def test_video_data_folder(cfg):
    from vtf.analysis import TrackSection
    from vtf.prep import load_bookmarks, load_cuts, load_prep, prep_path, save_bookmarks, save_cuts, save_prep

    v = cfg / "videos" / "Clip.mp4"; v.parent.mkdir()
    secs = [TrackSection(0, ((0.1, 0.1, 0.2, 0.2),))]
    near = cfg / "videos" / "Clip.vtf.json"
    save_prep(v, secs)                                              # no folder: next to the video
    assert prep_path(v) == near and near.is_file()
    data = cfg / "data"
    scriptdir.save_settings(None, True, data)
    assert scriptdir.settings().data_folder == data
    assert prep_path(v) == near and load_prep(v)                    # the old file is still found
    save_cuts(v, [(1000, 2000)])                                    # saving moves it into the folder
    assert not near.exists() and prep_path(v) == data / "Clip.vtf.json"
    assert load_prep(v) and load_cuts(v) == [(1000, 2000)]
    save_bookmarks(v, [(500, "a")])
    assert load_bookmarks(v) == [(500, "a")] and not near.exists()
    save_prep(v, []); save_cuts(v, []); save_bookmarks(v, [])      # nothing left: no file anywhere
    assert not (data / "Clip.vtf.json").exists() and not near.exists()


def test_dialog_keeps_both_folders(cfg):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from vtf.gui.scripts_ui import ScriptFolderDialog

    d = ScriptFolderDialog()
    d.data.set_folder(cfg / "data")
    d.scripts.set_folder(cfg / "scripts")                           # changing one keeps the other
    sd = scriptdir.settings()
    assert sd.folder == cfg / "scripts" and sd.data_folder == cfg / "data"
    d.data.set_folder(None)
    assert scriptdir.settings().data_folder is None and scriptdir.settings().folder == cfg / "scripts"
    d.deleteLater()
