import hashlib
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vtf import updater


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def publish(root, base, tag, payload: bytes, good_sum=True, with_sums=True):
    name = "Video-to-Funscript-x86_64.AppImage"
    (root / name).write_bytes(payload)
    digest = hashlib.sha256(payload if good_sum else b"other").hexdigest()
    assets = [
        {"name": f"Video-to-Funscript-{tag[1:]}-x86_64.AppImage", "size": 1,
         "browser_download_url": f"{base}/missing"},
        {"name": name, "size": len(payload), "browser_download_url": f"{base}/{name}"},
    ]
    if with_sums:
        (root / "SHA256SUMS").write_text(f"{digest}  {name}\n")
        assets.append({"name": "SHA256SUMS", "size": 1, "browser_download_url": f"{base}/SHA256SUMS"})
    (root / "latest.json").write_text(json.dumps({
        "tag_name": tag, "body": "Neu: Dinge", "html_url": f"{base}/page", "assets": assets}))


@pytest.fixture
def api(server, monkeypatch):
    root, base = server
    monkeypatch.setattr(updater, "API_LATEST", f"{base}/latest.json")
    return root, base


def test_versions():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("0.10") == (0, 10)
    assert updater.parse_version("nope") == ()
    assert updater.is_newer("v0.2.0", "0.1.0")
    assert updater.is_newer("0.1.1", "0.1")
    assert not updater.is_newer("v0.1.0", "0.1.0")
    assert not updater.is_newer("0.9.9", "0.10.0")
    assert not updater.is_newer("garbage", "0.1.0")


def test_check_newer_and_current(api, monkeypatch):
    root, base = api
    publish(root, base, "v9.0.0", b"new")
    rel = updater.check_for_update()
    assert rel.version == "9.0.0"
    assert rel.asset_name == "Video-to-Funscript-x86_64.AppImage"  # version-less preferred
    assert rel.notes == "Neu: Dinge"
    monkeypatch.setattr(updater, "__version__", "9.0.0")
    monkeypatch.setattr(updater, "is_newer", lambda c, cur="9.0.0": updater.parse_version(c) > (9, 0, 0))
    assert updater.check_for_update() is None


def test_private_repo_or_offline_is_quiet_error(api, monkeypatch):
    root, base = api
    monkeypatch.setattr(updater, "API_LATEST", f"{base}/does-not-exist")
    with pytest.raises(updater.UpdateError):
        updater.check_for_update()


def test_install_replaces_file(api, tmp_path):
    root, base = api
    publish(root, base, "v9.0.0", b"NEW-APPIMAGE" * 1000)
    target = tmp_path / "app" / "Video.AppImage"
    target.parent.mkdir()
    target.write_bytes(b"old")
    seen = []
    updater.download_and_install(updater.check_for_update(), target, progress=seen.append)
    assert target.read_bytes() == b"NEW-APPIMAGE" * 1000
    assert target.stat().st_mode & 0o111
    assert seen and seen[-1] == 1.0
    assert list(target.parent.iterdir()) == [target]  # no temp file left


def test_bad_checksum_keeps_old_file(api, tmp_path):
    root, base = api
    publish(root, base, "v9.0.0", b"evil", good_sum=False)
    target = tmp_path / "app" / "Video.AppImage"
    target.parent.mkdir()
    target.write_bytes(b"old")
    with pytest.raises(updater.UpdateError, match="Prüfsumme"):
        updater.download_and_install(updater.check_for_update(), target)
    assert target.read_bytes() == b"old"
    assert list(target.parent.iterdir()) == [target]


def test_missing_checksums_refused(api, tmp_path):
    root, base = api
    publish(root, base, "v9.0.0", b"x", with_sums=False)
    target = tmp_path / "Video.AppImage"
    target.write_bytes(b"old")
    with pytest.raises(updater.UpdateError, match="SHA256SUMS"):
        updater.download_and_install(updater.check_for_update(), target)
    assert target.read_bytes() == b"old"


def test_not_appimage_means_no_self_update(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert updater.running_appimage() is None
    assert not updater.can_self_update()


def test_restart_resets_pyinstaller_env(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updater.os, "execve", lambda *a: calls.append(a))
    monkeypatch.setenv("APPIMAGE", "/x")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/orig")
    updater.restart(tmp_path / "a.AppImage", ["video.mp4"])
    path, argv, env = calls[0]
    assert argv[1:] == ["video.mp4"]
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "APPIMAGE" not in env and env["LD_LIBRARY_PATH"] == "/orig"


def test_ssl_context_prefers_system_bundle_then_certifi(monkeypatch, tmp_path):
    import certifi

    loaded = []

    class Ctx:
        def load_verify_locations(self, cafile):
            loaded.append(cafile)

    monkeypatch.setattr(updater.ssl, "create_default_context", lambda: Ctx())
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text("x")
    # Fedora-like: system bundle exists -> used
    monkeypatch.setattr(updater, "SYSTEM_CA_FILES", (str(tmp_path / "missing.pem"), str(bundle)))
    monkeypatch.setattr(updater, "_ssl_ctx", None)
    updater.ssl_context()
    assert loaded == [str(bundle)]
    # nothing on the system -> certifi
    loaded.clear()
    monkeypatch.setattr(updater, "SYSTEM_CA_FILES", (str(tmp_path / "missing.pem"),))
    monkeypatch.setattr(updater, "_ssl_ctx", None)
    updater.ssl_context()
    assert loaded == [certifi.where()]
    monkeypatch.setattr(updater, "_ssl_ctx", None)
