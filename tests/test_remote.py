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
        assert code == 200 and b"NOT-AUS" in body
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
