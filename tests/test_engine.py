"""Built-in Intiface Engine: started only when needed, stopped again."""
import socket
import sys

from vtf.device import engine


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_engine(tmp_path):
    """Stands in for intiface-engine: listens on --websocket-port until stopped."""
    p = tmp_path / "intiface-engine"
    p.write_text(f"#!{sys.executable}\n"
                 "import socket, sys, time\n"
                 "args = sys.argv[1:]\n"
                 "assert '--use-bluetooth-le' in args\n"
                 "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                 "s.bind(('127.0.0.1', int(args[args.index('--websocket-port') + 1]))); s.listen()\n"
                 "time.sleep(60)\n")
    p.chmod(0o755)
    return p


def test_engine_starts_only_when_needed(tmp_path, monkeypatch):
    monkeypatch.setenv("VTF_INTIFACE_ENGINE", str(_fake_engine(tmp_path)))
    port = _free_port()
    url = f"ws://127.0.0.1:{port}"
    assert engine.ensure(f"ws://192.168.0.5:{port}") is None          # phone or other PC: not ours
    proc = engine.ensure(url)
    try:
        assert proc is not None and engine.listening("127.0.0.1", port)
        assert engine.ensure(url) is None                             # already served (or Central)
    finally:
        engine.stop(proc)
    assert proc.poll() is not None


def test_no_engine_no_start(tmp_path, monkeypatch):
    monkeypatch.setenv("VTF_INTIFACE_ENGINE", str(tmp_path / "fehlt"))
    assert engine.engine_path() is None
    assert engine.ensure(f"ws://127.0.0.1:{_free_port()}") is None
