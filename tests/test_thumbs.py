"""Preview pictures of the videos."""
import os
import time
import urllib.request

import pytest

from vtf import scriptdir, thumbs


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def test_thumb_is_made_and_kept_in_the_app_folder(cfg, sine_video):
    p = thumbs.make_thumb(sine_video)
    assert p == cfg / "data" / "video-to-funscript" / "thumbs" / f"{sine_video.stem}.jpg"
    assert p.is_file() and p.stat().st_size > 1000 and p.read_bytes()[:2] == b"\xff\xd8"
    assert not (sine_video.parent / f"{sine_video.stem}.jpg").exists()          # never next to the video
    assert thumbs.get_thumb(sine_video.stem) == p == thumbs.get_thumb(sine_video)
    first = p.stat().st_mtime_ns
    assert thumbs.make_thumb(sine_video) == p and p.stat().st_mtime_ns == first   # kept while current


def test_thumbs_follow_the_data_folder_and_can_be_off(cfg, sine_video):
    scriptdir.save_settings(None, True, cfg / "vdata")
    p = thumbs.make_thumb(sine_video)
    assert p == cfg / "vdata" / "thumbs" / f"{sine_video.stem}.jpg"
    scriptdir.save_settings(None, True, cfg / "vdata", thumbnails=False)
    assert thumbs.get_thumb(sine_video) is None and thumbs.make_thumb(sine_video, force=True) is None
    assert thumbs.delete_all() == 1 and not p.exists()


def test_unreadable_video_gives_no_thumb(cfg):
    bad = cfg / "bad.mp4"; bad.write_bytes(b"no video")
    assert thumbs.make_thumb(bad) is None


def test_phone_gets_the_pictures_with_the_pin(cfg):
    from vtf.remote import RemoteServer
    srv = RemoteServer(lambda: {}, lambda c: None, "123456", 0, "127.0.0.1",
                       get_thumb=lambda i: b"\xff\xd8jpeg" if i == 0 else None)
    srv.start()
    try:
        base = f"http://127.0.0.1:{srv.port}/api/thumb"
        with urllib.request.urlopen(f"{base}?pin=123456&i=0") as r:
            assert r.headers["Content-Type"] == "image/jpeg" and r.read() == b"\xff\xd8jpeg"
        for url, code in ((f"{base}?pin=000000&i=0", 401), (f"{base}?pin=123456&i=3", 404)):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(url)
            assert e.value.code == code
    finally:
        srv.stop()
    from vtf.remote import page
    assert "/api/thumb?pin=" in page()


def test_library_and_playlist_show_the_pictures(cfg, sine_video):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6.QtCore import QSettings
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from vtf.funscript import Action, Funscript, funscript_path_for
    from vtf.gui.library_dialog import COL_THUMB, ENTRY_ROLE, LibraryDialog
    from vtf.gui.player_panel import PlayerPanel

    Funscript([Action(0, 0), Action(500, 100)]).save(funscript_path_for(sine_video))
    qs = QSettings(str(cfg / "lib.ini"), QSettings.IniFormat)
    d = LibraryDialog(qs)
    d.set_folder(str(sine_video.parent))
    deadline = time.monotonic() + 10
    while thumbs.get_thumb(sine_video) is None and time.monotonic() < deadline:   # made in the background
        app.processEvents()
        time.sleep(0.05)
    assert thumbs.get_thumb(sine_video) is not None
    assert d.tree.topLevelItem(0).data(COL_THUMB, ENTRY_ROLE).name == sine_video.stem
    d.deleteLater()
    panel = PlayerPanel(qs)
    panel.playlist.items = [str(sine_video), str(sine_video.with_name("other.avi"))]
    panel.playlist.current = 1
    panel.refresh()
    assert not panel.list.item(0).icon().isNull()                    # its picture
    panel.deleteLater()


def test_switching_pictures_off_deletes_them(cfg, sine_video):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from vtf.gui.scripts_ui import ScriptFolderDialog

    p = thumbs.make_thumb(sine_video)
    d = ScriptFolderDialog()
    d.thumbs.setChecked(False)
    assert not p.exists() and not scriptdir.settings().thumbnails and "1" in d.result.text()
    d.thumbs.setChecked(True)
    assert scriptdir.settings().thumbnails
    d.data.set_folder(cfg / "vdata")                                 # pictures follow the data folder
    p2 = thumbs.make_thumb(sine_video)
    d.data.set_folder(cfg / "vdata2")
    assert not p2.exists() and (cfg / "vdata2" / "thumbs" / p2.name).is_file()
    d.deleteLater()


def test_file_managers_get_the_picture_for_the_script(cfg, sine_video, monkeypatch):
    """freedesktop thumbnail cache: <md5 of the URI>.png with Thumb::URI and Thumb::MTime."""
    import hashlib

    import cv2
    monkeypatch.setenv("XDG_CACHE_HOME", str(cfg / "cache"))
    script = cfg / "Mein Clip.funscript"
    script.write_text("{}")
    assert not thumbs.publish(script, sine_video)                  # no picture yet
    thumbs.make_thumb(sine_video)
    assert thumbs.publish(script, sine_video)
    uri = "file://" + str(script).replace(" ", "%20")
    for size, px in (("normal", 128), ("large", 256)):
        out = cfg / "cache" / "thumbnails" / size / (hashlib.md5(uri.encode()).hexdigest() + ".png")
        data = out.read_bytes()
        assert b"Thumb::URI\0" + uri.encode() in data
        assert b"Thumb::MTime\0" + str(int(script.stat().st_mtime)).encode() in data
        img = cv2.imread(str(out))
        assert img is not None and max(img.shape[:2]) == px
    first = out.stat().st_mtime_ns
    assert thumbs.publish(script, sine_video) and out.stat().st_mtime_ns == first   # still current
    os.utime(script, (time.time() + 5, time.time() + 5))           # saved again: new picture
    assert thumbs.publish(script, sine_video) and out.stat().st_mtime_ns != first
