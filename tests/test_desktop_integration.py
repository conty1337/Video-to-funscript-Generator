from pathlib import Path

from vtf import desktop_integration as di


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.delenv("VTF_NO_DESKTOP_INTEGRATION", raising=False)
    monkeypatch.setattr(di, "_refresh_menus", lambda: None)
    app = tmp_path / "Apps dir" / "Video-to-Funscript-x86_64.AppImage"
    app.parent.mkdir()
    app.write_bytes(b"x")
    return app


def test_auto_integrate_creates_entry_and_icon(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    assert di.auto_integrate(app) == "Ins Startmenü aufgenommen"
    text = di.desktop_file().read_text()
    assert f'Exec="{app}" %f' in text  # path with space is quoted
    assert f"Icon={di.icon_png_file()}" in text  # absolute path, no theme lookup
    assert di.icon_png_file().read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert di.icon_file().read_text().startswith("<svg")
    # second start: nothing to do
    assert di.auto_integrate(app) is None


def test_moved_appimage_updates_entry(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    di.auto_integrate(app)
    moved = tmp_path / "Video.AppImage"
    moved.write_bytes(b"x")
    assert di.auto_integrate(moved) == "Startmenü-Eintrag aktualisiert"
    assert str(moved) in di.desktop_file().read_text()


def test_no_duplicate_when_gear_lever_integrated(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    apps = di.data_home() / "applications"
    apps.mkdir(parents=True)
    (apps / "gearlever_video_to_funscript.desktop").write_text(f"[Desktop Entry]\nExec={app}\n")
    assert di.auto_integrate(app) is None
    assert not di.desktop_file().exists()


def test_uninstall_and_not_appimage(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    di.install(app)
    assert di.uninstall()
    assert not di.desktop_file().exists() and not di.icon_file().exists()
    assert not di.icon_png_file().exists()
    assert di.auto_integrate(None) is None


def test_exec_quoting_special_chars():
    assert di._quote('/a b/$x"y') == '"/a b/\\$x\\"y"'


def test_old_entry_with_theme_icon_gets_rewritten(tmp_path, monkeypatch):
    app = setup(tmp_path, monkeypatch)
    di.install(app)
    old = di.desktop_file().read_text().replace(f"Icon={di.icon_png_file()}", "Icon=video-to-funscript")
    di.desktop_file().write_text(old)
    assert di.auto_integrate(app) == "Startmenü-Eintrag aktualisiert"
    assert f"Icon={di.icon_png_file()}" in di.desktop_file().read_text()
