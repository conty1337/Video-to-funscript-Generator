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


def test_video_stem():
    assert scriptdir.video_stem("a/Clip.funscript") == "Clip"
    assert scriptdir.video_stem("Clip.roll.funscript") == "Clip"
    assert scriptdir.video_stem("Clip.vtf.json") == "Clip"
    assert scriptdir.video_stem("My.Clip.v2.funscript") == "My.Clip.v2"


def test_find_video_for_a_script(cfg):
    videos = cfg / "videos"; videos.mkdir()
    s = _script(cfg / "scripts" / "Clip.funscript")
    assert scriptdir.find_video(s) is None
    v = videos / "Clip.mp4"; v.write_bytes(b"\0")
    assert scriptdir.find_video(s, search=(cfg,)) == v                # by name in a search folder
    moved = videos / "sub" / "Clip.mp4"; moved.parent.mkdir(); v.rename(moved)
    assert scriptdir.find_video(s) == moved                           # below where it was last seen
    other = cfg / "elsewhere" / "Renamed.mkv"; other.parent.mkdir(); other.write_bytes(b"\0")
    scriptdir.remember_video(other, "Clip")                           # the user picked a renamed video
    assert scriptdir.find_video(s) == other
    near = _script(videos / "sub" / "Clip.funscript")
    assert scriptdir.find_video(near) == moved                        # next to the script wins


def test_move_files_on_a_folder_change(cfg):
    old, new = cfg / "old", cfg / "new"
    a = _script(old / "Clip.funscript")
    b = _script(old / "2026" / "Other.funscript")
    _script(new / "Taken.funscript"); _script(old / "Taken.funscript")      # never overwritten
    moved, left = scriptdir.move_files(old, new, "scripts")
    assert (moved, left) == (2, 1)
    assert (new / "Clip.funscript").is_file() and (new / "2026" / "Other.funscript").is_file()
    assert not a.exists() and not b.exists() and (old / "Taken.funscript").exists()
    v = cfg / "videos" / "Clip.mp4"; v.parent.mkdir(); v.write_bytes(b"\0")
    scriptdir.remember_video(v)
    moved, left = scriptdir.move_files(new, None, "scripts")          # back next to the known videos
    assert (cfg / "videos" / "Clip.funscript").is_file() and moved == 1 and left == 2


def test_library_lists_scripts_whose_video_is_gone(cfg):
    from vtf.library import scan
    videos = cfg / "videos"; videos.mkdir()
    (videos / "Here.mp4").write_bytes(b"\0")
    scriptdir.save_settings(cfg / "scripts", True)
    _script(cfg / "scripts" / "Here.funscript")
    _script(cfg / "scripts" / "Gone.funscript")
    _script(cfg / "scripts" / "Gone.roll.funscript")                 # an extra axis is no entry of its own
    entries = scan(videos)
    assert [(e.name, e.missing) for e in entries] == [("Gone", True), ("Here", False)]
    assert entries[0].script == cfg / "scripts" / "Gone.funscript"


def test_opening_a_script_opens_its_video(cfg, sine_video, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from vtf.gui.main_window import MainWindow

    script = _script(cfg / "anywhere" / f"{sine_video.stem}.funscript", n=5)
    _script(cfg / "anywhere" / f"{sine_video.stem}.roll.funscript", n=3)
    w = MainWindow()
    try:
        w.set_player_mode(False)
        w.open_video(str(sine_video))                                 # seen once: remembered
        w.close_video()
        w.open_video(str(script))                                     # a script: its video comes along
        assert w.video_path and w.video_path.endswith(sine_video.name)
        assert len(w.curve.actions) == 5 and len(w.axis_actions.get("R1", [])) == 3
        lost = _script(cfg / "anywhere" / "Lost.funscript")           # unknown video: the user looks for it
        monkeypatch.setattr(w, "_run_dialog", lambda box: box.buttons()[0].click())
        monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(sine_video), ""))
        w.dirty = False
        w.open_video(str(lost))
        assert len(w.curve.actions) == 4 and scriptdir.find_video(lost) == sine_video.resolve()
    finally:
        w.dirty = False
        w.close()


def test_dialog_offers_to_move_files(cfg):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from vtf.gui.scripts_ui import ScriptFolderDialog

    scriptdir.save_settings(cfg / "old", True)
    _script(cfg / "old" / "Clip.funscript")
    d = ScriptFolderDialog()
    d._ask = lambda *_a: False                                        # no: the files stay
    d.scripts.set_folder(cfg / "other")
    assert (cfg / "old" / "Clip.funscript").is_file() and "1" in d.result.text()
    d.scripts.set_folder(cfg / "old", notify=False); scriptdir.save_settings(cfg / "old", True)
    d._ask = lambda *_a: True                                         # yes: they move along
    d.scripts.set_folder(cfg / "new")
    assert (cfg / "new" / "Clip.funscript").is_file() and not (cfg / "old" / "Clip.funscript").exists()
    assert scriptdir.settings().folder == cfg / "new"
    d.deleteLater()


def test_same_name_in_two_folders_does_not_overwrite(cfg):
    from vtf import prep, thumbs
    from vtf.analysis import TrackSection

    a, b = cfg / "A" / "video.mp4", cfg / "B" / "video.mp4"
    for v in (a, b):
        v.parent.mkdir(parents=True)
        v.write_bytes(b"x")
    scriptdir.save_settings(cfg / "scripts", False, cfg / "data")
    scriptdir.remember_video(a)
    scriptdir.remember_video(b)                                       # the name stays with A
    assert scriptdir.remembered_video("video") == a.resolve() and scriptdir.foreign(b) == a.resolve()
    assert scriptdir.save_paths(a) == [cfg / "scripts" / "video.funscript"]
    assert scriptdir.save_paths(b) == [b.with_suffix(".funscript")]  # B keeps its script next to it
    _script(cfg / "scripts" / "video.funscript")
    assert scriptdir.find_script(a) is not None and scriptdir.find_script(b) is None
    assert prep.prep_path(a).parent == cfg / "data" and prep.prep_path(b) == b.with_suffix(".vtf.json")
    prep.save_prep(b, [TrackSection(start_ms=0, boxes=((0.1, 0.1, 0.2, 0.2),))])
    assert prep.load_prep(a) == [] and len(prep.load_prep(b)) == 1
    assert thumbs.thumb_path(a) != thumbs.thumb_path(b)
    scriptdir.remember_video(b, "video")                              # the user picked B for the name
    assert scriptdir.foreign(a) == b.resolve()


def test_broken_scripts_give_one_error(cfg):
    tmp_path = cfg
    f = tmp_path / "x.funscript"
    for text in ("[]", '{"actions":[{"at":null,"pos":1}]}', '{"actions":{"a":1}}', '{"actions":[{"at":1}]}'):
        f.write_text(text)
        with pytest.raises(ValueError):
            Funscript.load(f)
    f.write_text('{"inverted":true,"actions":[{"at":5,"pos":10}],"metadata":[]}')
    fs = Funscript.load(f)
    assert [(a.at, a.pos) for a in fs.actions] == [(5, 90)] and fs.metadata == {}
    from vtf.library import scan                                      # a broken file does not stop the library
    f.write_text("[]")
    (tmp_path / "x.mp4").write_bytes(b"x")
    assert [e.name for e in scan(tmp_path)] == ["x"]


def test_saving_drops_emptied_axes_and_keeps_foreign_metadata(cfg, sine_video):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from vtf.gui.main_window import MainWindow

    near = sine_video.with_suffix(".funscript")
    roll = sine_video.with_suffix(".roll.funscript")
    Funscript([Action(0, 0), Action(500, 100)], metadata={"creator": "Someone", "title": "T"}).save(near)
    Funscript([Action(0, 50), Action(500, 60)]).save(roll)
    w = MainWindow()
    try:
        w.set_player_mode(False)
        w.open_video(str(sine_video))
        assert w.axis_actions.get("R1")
        w.axis_actions["R1"] = []                                     # the user cleared Roll
        w.export()
        assert not roll.exists()
        assert Funscript.load(near).metadata == {"creator": "Someone", "title": "T"}
    finally:
        w.dirty = False
        w.close()
