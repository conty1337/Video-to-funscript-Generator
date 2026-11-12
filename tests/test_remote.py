"""Phone remote: web server with PIN."""
import json
import urllib.error
import urllib.request

from vtf.remote import MAX_WRONG, RemoteServer, local_addresses, new_pin


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_server_pin_state_commands():
    got = []
    srv = RemoteServer(lambda: {"running": False, "bpm": 60}, got.append, "123456", port=0, host="127.0.0.1")
    srv.start()
    try:
        base = f"http://127.0.0.1:{srv.port}"
        code, body = _get(base + "/")
        assert code == 200 and b"Not-Aus" in body
        assert _get(base + "/api/state?pin=000000")[0] == 401
        code, body = _get(base + "/api/state?pin=123456")
        assert code == 200 and json.loads(body)["bpm"] == 60
        assert _post(base + "/api/cmd", {"pin": "123456", "cmd": "toggle"}) == 200
        assert _post(base + "/api/cmd", {"pin": "1", "cmd": "toggle"}) == 401
        assert got == [{"cmd": "toggle"}]                 # the PIN is not passed on
        for _ in range(MAX_WRONG):
            _get(base + "/api/state?pin=999999")
        assert _get(base + "/api/state?pin=123456")[0] == 429   # locked for a while
        assert all(u.startswith("http://") for u in srv.urls())
    finally:
        srv.stop()
    assert not srv.running
    assert len(new_pin()) == 6 and local_addresses()


def test_qr_code_holds_address_and_pin():
    import os

    import pytest

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    segno = pytest.importorskip("segno")
    from PySide6.QtWidgets import QApplication

    from vtf.gui.remote_ui import qr_pixmap

    QApplication.instance() or QApplication([])
    text = "http://192.168.1.20:8765/#pin=123456"
    pix = qr_pixmap(text, module_px=4)
    q = segno.make(text, error="m", micro=False)
    n = q.symbol_size(scale=1, border=4)[0]
    assert pix is not None and pix.width() == pix.height() == n * 4
    img = pix.toImage()
    rows = list(q.matrix_iter(scale=1, border=4))
    for y in (4, n // 2, n - 5):                       # the dark modules are where segno put them
        for x in range(n):
            dark = img.pixelColor(x * 4 + 1, y * 4 + 1).value() < 128
            assert dark == bool(rows[y][x])


def test_page_takes_the_pin_from_the_qr_link():
    from vtf import remote

    page = remote.PAGE if hasattr(remote, "PAGE") else open(remote.__file__, encoding="utf-8").read()
    assert "location.hash.match(/pin=" in page and "history.replaceState" in page
