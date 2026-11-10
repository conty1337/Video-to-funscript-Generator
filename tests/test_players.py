"""Other players: mpv, VLC, DeoVR/HereSphere (fake servers), clock, script lookup."""
import json
import os
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vtf import players as PL


def test_clock_and_names(tmp_path):
    c = PL.SmoothClock(400)
    c.report(10_000, True, 0)
    assert c.now(500) == 10_500
    c.report(10_520, True, 500)                     # small drift: corrected gently
    assert 10_500 < c.now(500) < 10_520
    c.report(60_000, True, 600)                     # seek: jumps
    assert c.now(600) == 60_000
    c.report(60_100, False, 700)
    assert c.now(5000) == 60_100                    # paused: stands still
    assert PL.media_name("http://x/videos/My%20Film.mp4?x=1") == "My Film.mp4"
    assert PL.local_path("file:///home/a/b%20c.mp4") == "/home/a/b c.mp4"
    assert PL.local_path("https://x/y.mp4") is None
    v = tmp_path / "Film.mp4"
    v.write_bytes(b"")
    (tmp_path / "Film.funscript").write_text("{}")
    assert PL.find_script(str(v)) == str(tmp_path / "Film.funscript")
    lib = tmp_path / "lib" / "sub"
    lib.mkdir(parents=True)
    (lib / "remote.FUNSCRIPT").write_text("{}")
    assert PL.find_script("http://quest/remote.mp4", [str(tmp_path / "lib")]) == str(lib / "remote.FUNSCRIPT")
    assert PL.find_script("http://quest/none.mp4", [str(tmp_path)]) is None


def _fake_mpv(path, state):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                req = json.loads(line)
                prop = req["command"][1]
                conn.sendall(json.dumps({"event": "noise"}).encode() + b"\n" +
                             json.dumps({"request_id": req["request_id"], "data": state[prop],
                                         "error": "success"}).encode() + b"\n")

    threading.Thread(target=run, daemon=True).start()
    return srv


def test_mpv(tmp_path):
    path = str(tmp_path / "mpv.sock")
    state = {"time-pos": 12.5, "pause": False, "path": "/v/a.mp4", "duration": 60.0}
    srv = _fake_mpv(path, state)
    p = PL.MpvPlayer(path)
    p.connect()
    st = p.poll()
    assert (st.path, st.time_ms, st.playing, st.duration_ms) == ("/v/a.mp4", 12500, True, 60000)
    state["pause"] = True
    assert not p.poll().playing
    p.close()
    srv.close()
    with pytest.raises(PL.PlayerError):
        PL.MpvPlayer(str(tmp_path / "none.sock")).connect()


def test_vlc():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.headers.get("Authorization") != "Basic OmdlaGVpbQ==":     # ":geheim"
                self.send_response(401)
                self.end_headers()
                return
            if self.path.endswith("status.json"):
                body = {"state": "playing", "length": 100, "position": 0.25, "time": 25,
                        "information": {"category": {"meta": {"filename": "b.mp4"}}}}
            else:
                body = {"children": [{"children": [{"uri": "file:///v/b.mp4", "current": "current"}]}]}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        addr = f"127.0.0.1:{srv.server_address[1]}"
        with pytest.raises(PL.PlayerError):
            PL.VlcPlayer(addr, "falsch").connect()
        p = PL.VlcPlayer(addr, "geheim")
        p.connect()
        st = p.poll()
        assert st.path == "file:///v/b.mp4" and st.time_ms == 25_000 and st.playing
    finally:
        srv.shutdown()


def test_deovr():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got_ping = threading.Event()

    def run():
        conn, _ = srv.accept()
        msg = json.dumps({"path": "http://q/c.mp4", "currentTime": 33.0, "playerState": 0,
                          "duration": 90.0}).encode()
        conn.sendall(struct.pack("<I", len(msg))[:2])            # split on purpose
        time.sleep(0.05)
        conn.sendall(struct.pack("<I", len(msg))[2:] + msg)
        if conn.recv(4) == struct.pack("<I", 0):
            got_ping.set()
        time.sleep(0.3)
        conn.close()

    threading.Thread(target=run, daemon=True).start()
    p = PL.DeoVrPlayer("127.0.0.1", port, "HereSphere")
    p.connect()
    st = PL.PlayerState()
    for _ in range(50):
        st = p.poll()
        if st.path:
            break
        time.sleep(0.02)
    assert (st.path, st.time_ms, st.playing) == ("http://q/c.mp4", 33_000, True)
    assert got_ping.wait(1)
    time.sleep(0.5)
    with pytest.raises(PL.PlayerError):
        p.poll()                                                   # player closed the connection
    p.close()
    srv.close()
    assert os.path.basename(PL.make_player("vlc").address) == PL.DEFAULT_VLC
