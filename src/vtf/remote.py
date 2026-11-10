"""Phone remote: a small web page in the home network (no app, just the browser).

Standard library only. The page asks for a PIN once (kept in the phone's browser).
Commands go to the GUI through a callback; the state is a snapshot dict the GUI
refreshes. Wrong PINs are counted per address and lock that address for a while.
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8765
MAX_WRONG = 8
LOCK_S = 60


def new_pin() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def local_addresses() -> list[str]:
    """IPv4 addresses of this computer in the local network (best guess first)."""
    out: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))           # no packet is sent
            out.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out or ["127.0.0.1"]


class RemoteServer:
    def __init__(self, get_state: Callable[[], dict], on_command: Callable[[dict], None], pin: str,
                 port: int = DEFAULT_PORT, host: str = "0.0.0.0"):
        self.get_state = get_state
        self.on_command = on_command
        self.pin = pin
        self.port = port
        self.host = host
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._wrong: dict[str, tuple[int, float]] = {}

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        if self._httpd is not None:
            return
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):              # keep the terminal quiet
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj):
                self._send(code, json.dumps(obj, ensure_ascii=False).encode())

            def _authorized(self, pin: str) -> bool:
                ip = self.client_address[0]
                n, until = server._wrong.get(ip, (0, 0.0))
                if until > time.monotonic():
                    self._json(429, {"error": "Zu viele falsche PINs, kurz warten"})
                    return False
                if secrets.compare_digest(str(pin), server.pin):
                    server._wrong.pop(ip, None)
                    return True
                n += 1
                server._wrong[ip] = (0, time.monotonic() + LOCK_S) if n >= MAX_WRONG else (n, 0.0)
                self._json(401, {"error": "Falsche PIN"})
                return False

            def do_GET(self):
                path, _, query = self.path.partition("?")
                if path in ("/", "/index.html"):
                    self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                elif path == "/api/state":
                    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                    if self._authorized(params.get("pin", "")):
                        self._json(200, server.get_state())
                else:
                    self._json(404, {"error": "nicht gefunden"})

            def do_POST(self):
                if self.path != "/api/cmd":
                    self._json(404, {"error": "nicht gefunden"})
                    return
                try:
                    n = min(int(self.headers.get("Content-Length", 0)), 4096)
                    data = json.loads(self.rfile.read(n) or b"{}")
                except (ValueError, OSError):
                    self._json(400, {"error": "ungültig"})
                    return
                if not isinstance(data, dict) or not self._authorized(data.pop("pin", "")):
                    return
                server.on_command(data)
                self._json(200, {"ok": True})

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="remote", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self._thread = None

    def urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}" for ip in local_addresses()]


PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="theme-color" content="#141415">
<title>Fernbedienung</title>
<style>
:root{--bg:#141415;--card:#19191b;--raised:#232325;--text:#ededed;--t2:#a1a1a6;--muted:#85858b;
--accent:#e5a043;--neg:#e5484d}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.4 system-ui,sans-serif;padding:16px;
max-width:520px;margin:auto}
h1{font-size:17px;font-weight:600;margin:4px 0 2px}
.muted{color:var(--muted);font-size:13px}
.card{background:var(--card);border-radius:12px;padding:14px;margin:12px 0}
button{font:inherit;color:var(--text);background:var(--raised);border:0;border-radius:10px;padding:14px;
min-height:52px;width:100%}
button:active{filter:brightness(1.3)}
.primary{background:var(--accent);color:#141415;font-weight:600;font-size:19px;min-height:64px}
.stop{background:var(--neg);color:#fff;font-weight:700;font-size:19px;min-height:64px}
.row{display:flex;gap:8px;margin-top:8px}.row>*{flex:1}
.seg button{min-height:42px;padding:8px}.seg button.on{background:var(--text);color:var(--bg)}
label{display:flex;justify-content:space-between;color:var(--t2);font-size:14px;margin-top:14px}
input[type=range]{width:100%;accent-color:var(--accent);height:36px}
select,input[type=tel]{width:100%;font:inherit;color:var(--text);background:var(--raised);border:0;
border-radius:10px;padding:12px;min-height:48px}
.val{font-variant-numeric:tabular-nums;color:var(--text)}
#now{font-variant-numeric:tabular-nums;font-size:14px;color:var(--t2);min-height:20px}
.hidden{display:none}
</style></head><body>
<div id="login" class="card hidden">
<h1>PIN eingeben</h1><p class="muted">Die PIN steht in der App unter Gerät → Handy-Fernbedienung.</p>
<input id="pin" type="tel" inputmode="numeric" maxlength="6" autocomplete="off">
<div class="row"><button class="primary" onclick="login()">Verbinden</button></div>
<p id="err" class="muted"></p></div>
<div id="app" class="hidden">
<h1 id="title">Fernbedienung</h1><div id="dev" class="muted"></div>
<div class="row"><button id="play" class="primary" onclick="cmd({cmd:'toggle'})">Start</button></div>
<div class="row"><button class="stop" onclick="cmd({cmd:'estop'})">NOT-AUS</button></div>
<div class="card"><div class="row seg" id="modes">
<button data-m="0">Muster</button><button data-m="1">Ablauf</button><button data-m="2">Zufall</button></div>
<div class="row"><select id="pick"></select></div>
<div class="row"><button onclick="cmd({cmd:'step',d:-1})">‹ Zurück</button>
<button onclick="cmd({cmd:'step',d:1})">Weiter ›</button></div>
<p id="now"></p></div>
<div class="card">
<label>Tiefe <span class="val" id="dv"></span></label>
<input id="depth" type="range" min="10" max="100" step="1">
<div class="row"><button onclick="cmd({cmd:'nudge',depth:-5})">− Tiefe</button>
<button onclick="cmd({cmd:'nudge',depth:5})">+ Tiefe</button></div>
<label>Tempo <span class="val" id="bv"></span></label>
<input id="bpm" type="range" min="10" max="240" step="1">
<div class="row"><button onclick="cmd({cmd:'nudge',tempo:-5})">− Tempo</button>
<button onclick="cmd({cmd:'nudge',tempo:5})">+ Tempo</button></div>
<p class="muted" id="hint"></p></div>
<p class="muted" id="safety"></p></div>
<script>
let pin=localStorage.getItem('vtf-pin')||'',st=null,drag=0,listKey='';
const $=id=>document.getElementById(id);
function show(ok){$('login').classList.toggle('hidden',ok);$('app').classList.toggle('hidden',!ok)}
function login(){pin=$('pin').value.trim();localStorage.setItem('vtf-pin',pin);poll()}
async function cmd(c){c.pin=pin;try{await fetch('/api/cmd',{method:'POST',body:JSON.stringify(c)})}catch(e){}
setTimeout(poll,120)}
async function poll(){if(!pin){show(false);return}
try{const r=await fetch('/api/state?pin='+encodeURIComponent(pin));
if(r.status==401||r.status==429){const j=await r.json();$('err').textContent=j.error;show(false);return}
st=await r.json();show(true);render()}catch(e){$('dev').textContent='Keine Verbindung zur App'}}
function render(){$('title').textContent=st.title;$('dev').textContent=st.device;
$('play').textContent=st.running?'Stopp':'Start';$('now').textContent=st.now||'';
$('safety').textContent=st.safety||'';$('hint').textContent=st.mode>0?
'Im Ablauf und bei Zufall verschiebt das alles gemeinsam.':'';
document.querySelectorAll('#modes button').forEach(b=>b.classList.toggle('on',+b.dataset.m==st.mode));
const items=st.mode==0?st.patterns:st.mode==1?st.programs:[];const key=st.mode+'|'+items.join('|');
$('pick').classList.toggle('hidden',st.mode==2);
if(key!=listKey){listKey=key;$('pick').innerHTML=items.map(n=>'<option>'+n.replace(/</g,'&lt;')+'</option>').join('')}
if(document.activeElement!==$('pick'))$('pick').value=st.name;
if(Date.now()-drag>1500){$('depth').value=st.depth;$('bpm').value=st.bpm}
$('dv').textContent=Math.round(st.depth)+' %';$('bv').textContent=Math.round(st.bpm)+' Hübe/min'}
document.querySelectorAll('#modes button').forEach(b=>b.onclick=()=>cmd({cmd:'mode',mode:+b.dataset.m}));
$('pick').onchange=e=>cmd({cmd:'select',name:e.target.value});
let tm=null;for(const id of ['depth','bpm'])$(id).oninput=e=>{drag=Date.now();
$(id=='depth'?'dv':'bv').textContent=e.target.value+(id=='depth'?' %':' Hübe/min');clearTimeout(tm);
tm=setTimeout(()=>cmd({cmd:'set',[id]:+e.target.value}),150)};
poll();setInterval(poll,700);
</script></body></html>
"""
