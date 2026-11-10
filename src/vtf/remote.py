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
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#141415">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Fernbedienung</title>
<style>
/* same tokens as the desktop app (gui/theme.py): graphite ramp, amber tally light */
:root{--deep:#0f0f10;--bg:#141415;--card:#19191b;--raised:#232325;--line:rgba(237,237,237,.08);
--text:#ededed;--t2:#a1a1a6;--muted:#85858b;--accent:#e5a043;--on-accent:#141415;--neg:#e5484d;
--ease:cubic-bezier(.22,1,.36,1);--mono:ui-monospace,"JetBrains Mono","Noto Sans Mono",monospace}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{background:var(--bg)}
body{margin:0 auto;max-width:480px;min-height:100dvh;color:var(--text);
font:15px/20px system-ui,"Noto Sans",sans-serif;padding:16px 16px calc(96px + env(safe-area-inset-bottom))}
button,select,input{font:inherit;color:inherit}
svg.i{width:18px;height:18px;flex:none;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;
stroke-linejoin:round}
.hidden{display:none!important}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.muted{color:var(--muted);font-size:13px}

/* header */
header{display:flex;align-items:center;gap:8px;min-height:32px}
.tally{width:8px;height:8px;border-radius:4px;background:var(--raised);transition:background-color .18s var(--ease)}
.tally.on{background:var(--accent)}
header h1{flex:1;margin:0;font-size:16px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#dev{margin:0 0 16px 16px;font-size:12px;line-height:16px}

/* the moment: what plays now */
.stage{background:var(--card);border-radius:16px;padding:12px 12px 12px}
.stage svg{display:block;width:100%;height:136px}
#sub{margin-top:8px;font-size:13px;line-height:20px;color:var(--t2)}
.meta{display:flex;justify-content:space-between;gap:12px;font-size:12px;line-height:16px;color:var(--muted);
min-height:16px}
.unit{color:var(--muted);font-size:13px;font-family:system-ui,"Noto Sans",sans-serif}

/* controls */
section{margin-top:24px}
.seg{display:flex;gap:4px;background:var(--card);border-radius:12px;padding:4px}
.seg button{flex:1;min-height:36px;border:0;border-radius:8px;background:transparent;color:var(--t2);
transition:background-color .15s var(--ease),color .15s var(--ease)}
.seg button.on{background:var(--raised);color:var(--text);box-shadow:inset 0 1px 0 rgba(255,255,255,.06)}
.pickrow{display:flex;gap:8px;margin-top:8px}
select{flex:1;min-width:0;min-height:44px;border:1px solid var(--line);border-radius:10px;background:var(--raised);
padding:0 12px;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2385858b' stroke-width='1.5' stroke-linecap='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
background-repeat:no-repeat;background-position:right 12px center;background-size:16px}
.icon{width:44px;min-height:44px;display:grid;place-items:center;border:1px solid var(--line);border-radius:10px;
background:var(--raised);color:var(--t2);transition:background-color .15s var(--ease),transform .12s var(--ease)}
.icon:active,.bar button:active{transform:scale(.97)}
.ctl{background:var(--card);border-radius:16px;padding:16px}
.ctl+.ctl{margin-top:8px}
.ctl .head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.ctl .head span:first-child{color:var(--t2)}
.ctl .val{font-size:15px}
.ctl .row{display:flex;align-items:center;gap:12px}
input[type=range]{flex:1;min-width:0;height:44px;margin:0;background:transparent;appearance:none}
input[type=range]::-webkit-slider-runnable-track{height:4px;border-radius:2px;background:var(--raised)}
input[type=range]::-moz-range-track{height:4px;border-radius:2px;background:var(--raised)}
input[type=range]::-moz-range-progress{height:4px;border-radius:2px;background:var(--text)}
input[type=range]::-webkit-slider-thumb{appearance:none;width:24px;height:24px;margin-top:-10px;border-radius:12px;
background:var(--text);border:0}
input[type=range]::-moz-range-thumb{width:24px;height:24px;border-radius:12px;background:var(--text);border:0}
#hint{margin-top:8px}
#safety{margin:24px 0 0;text-align:center}

/* thumb bar: emergency stop and start always within reach */
.bar{position:fixed;left:0;right:0;bottom:0;display:flex;gap:8px;justify-content:center;
padding:12px 16px calc(12px + env(safe-area-inset-bottom));background:var(--deep);border-top:1px solid var(--line)}
.bar>div{display:flex;gap:8px;width:100%;max-width:448px}
.bar button{min-height:52px;border:0;border-radius:12px;display:flex;align-items:center;justify-content:center;
gap:8px;font-weight:500;transition:background-color .15s var(--ease),transform .12s var(--ease)}
#estop{flex:0 0 40%;background:var(--neg);color:#fff}
#play{flex:1;background:var(--accent);color:var(--on-accent)}
#play.running{background:var(--raised);color:var(--text);box-shadow:inset 0 0 0 1px var(--line)}

/* login */
.login{margin-top:48px}
.login h1{font-size:20px;font-weight:500;margin:0 0 8px}
.login input::placeholder{color:var(--muted)}
.login input{width:100%;min-height:52px;margin-top:24px;border:1px solid var(--line);border-radius:12px;
background:var(--raised);padding:0 16px;font-family:var(--mono);font-size:24px;letter-spacing:.3em;text-align:center}
.login input:focus,select:focus,button:focus-visible{outline:2px solid var(--t2);outline-offset:2px}
.login button{width:100%;min-height:52px;margin-top:12px;border:0;border-radius:12px;background:var(--accent);
color:var(--on-accent);font-weight:500}
#err{margin-top:12px;color:var(--neg);font-size:13px;min-height:20px}
</style></head><body>
<svg style="display:none"><defs>
<symbol id="i-play" viewBox="0 0 24 24"><polygon points="6 3 20 12 6 21 6 3"/></symbol>
<symbol id="i-pause" viewBox="0 0 24 24"><rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/></symbol>
<symbol id="i-back" viewBox="0 0 24 24"><path d="M19 20 9 12l10-8z"/><path d="M5 19V5"/></symbol>
<symbol id="i-fwd" viewBox="0 0 24 24"><path d="M5 4l10 8-10 8z"/><path d="M19 5v14"/></symbol>
<symbol id="i-minus" viewBox="0 0 24 24"><path d="M5 12h14"/></symbol>
<symbol id="i-plus" viewBox="0 0 24 24"><path d="M5 12h14"/><path d="M12 5v14"/></symbol>
<symbol id="i-stop" viewBox="0 0 24 24"><path d="M2.586 16.726A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2h6.624a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586z"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></symbol>
</defs></svg>

<div id="login" class="login hidden">
<h1>Fernbedienung</h1>
<p class="muted">PIN aus der App eingeben: Gerät → Handy-Fernbedienung</p>
<input id="pin" type="tel" inputmode="numeric" maxlength="6" autocomplete="one-time-code" aria-label="PIN"
placeholder="······">
<button onclick="login()">Verbinden</button>
<p id="err"></p>
</div>

<main id="app" class="hidden">
<header><span id="tally" class="tally" aria-hidden="true"></span><h1 id="title">Fernbedienung</h1></header>
<p id="dev" class="muted"></p>

<div class="stage">
<svg id="curve" viewBox="0 0 300 136" preserveAspectRatio="none" aria-label="Verlauf des Musters"></svg>
<div id="sub"></div>
<div class="meta"><span id="step"></span><span id="time" class="mono"></span></div>
</div>

<section>
<div class="seg" id="modes"><button data-m="0">Muster</button><button data-m="1">Ablauf</button>
<button data-m="2">Zufall</button></div>
<div class="pickrow">
<button class="icon" aria-label="Zurück" onclick="cmd({cmd:'step',d:-1})"><svg class="i"><use href="#i-back"/></svg></button>
<select id="pick" aria-label="Auswahl"></select>
<button class="icon" aria-label="Weiter" onclick="cmd({cmd:'step',d:1})"><svg class="i"><use href="#i-fwd"/></svg></button>
</div>
</section>

<section>
<div class="ctl">
<div class="head"><span>Tiefe</span><span class="val mono" id="dv"></span></div>
<div class="row">
<button class="icon" aria-label="Weniger tief" onclick="cmd({cmd:'nudge',depth:-5})"><svg class="i"><use href="#i-minus"/></svg></button>
<input id="depth" type="range" min="10" max="100" step="1" aria-label="Tiefe">
<button class="icon" aria-label="Tiefer" onclick="cmd({cmd:'nudge',depth:5})"><svg class="i"><use href="#i-plus"/></svg></button>
</div></div>
<div class="ctl">
<div class="head"><span>Tempo</span><span class="val mono" id="bv"></span></div>
<div class="row">
<button class="icon" aria-label="Langsamer" onclick="cmd({cmd:'nudge',tempo:-5})"><svg class="i"><use href="#i-minus"/></svg></button>
<input id="bpm" type="range" min="10" max="240" step="1" aria-label="Tempo">
<button class="icon" aria-label="Schneller" onclick="cmd({cmd:'nudge',tempo:5})"><svg class="i"><use href="#i-plus"/></svg></button>
</div></div>
<p class="muted" id="hint"></p>
</section>
<p class="muted" id="safety"></p>
</main>

<nav class="bar hidden" id="bar"><div>
<button id="estop" onclick="cmd({cmd:'estop'})"><svg class="i"><use href="#i-stop"/></svg>Not-Aus</button>
<button id="play" onclick="cmd({cmd:'toggle'})"><svg class="i"><use href="#i-play"/></svg><span>Start</span></button>
</div></nav>

<script>
let pin=localStorage.getItem('vtf-pin')||'',st=null,drag=0,listKey='',curveKey='';
const $=id=>document.getElementById(id);
const esc=t=>String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function show(ok){$('login').classList.toggle('hidden',ok);$('app').classList.toggle('hidden',!ok);
$('bar').classList.toggle('hidden',!ok)}
function login(){pin=$('pin').value.trim();localStorage.setItem('vtf-pin',pin);poll()}
$('pin').addEventListener('keydown',e=>{if(e.key==='Enter')login()});
async function cmd(c){c.pin=pin;try{await fetch('/api/cmd',{method:'POST',body:JSON.stringify(c)})}catch(e){}
setTimeout(poll,120)}
async function poll(){if(!pin){show(false);return}
try{const r=await fetch('/api/state?pin='+encodeURIComponent(pin));
if(r.status==401||r.status==429){const j=await r.json();$('err').textContent=j.error;show(false);return}
st=await r.json();show(true);render()}catch(e){$('dev').textContent='Keine Verbindung zur App'}}
function drawCurve(pts){const W=300,H=136,p=6;const y=v=>H-p-(H-2*p)*v;
if(!pts||pts.length<2){$('curve').innerHTML='<text x="150" y="72" text-anchor="middle" fill="#85858b" '+
'font-size="12" font-family="system-ui">'+(st.mode==3?'Musik: Gerät folgt dem Takt':'Nichts ausgewählt')+'</text>';return}
const d=pts.map((q,i)=>(i?'L':'M')+(q[0]*W).toFixed(1)+' '+y(q[1]).toFixed(1)).join(' ');
const lo=Math.min(...pts.map(q=>q[1]));
let g='';for(const v of [0,.5,1])g+='<line x1="0" x2="300" y1="'+y(v)+'" y2="'+y(v)+'" stroke="rgba(237,237,237,.06)"/>';
$('curve').innerHTML=g+'<rect x="0" y="'+y(1)+'" width="300" height="'+(y(lo)-y(1))+'" fill="rgba(237,237,237,.025)"/>'+
'<path d="'+d+' L300 '+y(0)+' L0 '+y(0)+'Z" fill="rgba(229,160,67,.12)"/>'+
'<path d="'+d+'" stroke="#e5a043" stroke-width="2" fill="none" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'}
function render(){$('title').textContent=st.title;$('dev').textContent=st.device;
$('tally').classList.toggle('on',st.running);
const play=$('play');play.classList.toggle('running',st.running);
play.querySelector('use').setAttribute('href',st.running?'#i-pause':'#i-play');
play.querySelector('span').textContent=st.running?'Stopp':'Start';
$('sub').textContent=st.sub||'';$('step').textContent=st.step||'';$('time').textContent=st.time||'';$('safety').textContent=st.safety||'';
$('hint').textContent=st.mode==1||st.mode==2?'Im Ablauf und bei Zufall verschiebt das den ganzen Plan':'';
const ck=JSON.stringify(st.curve||[]);if(ck!==curveKey){curveKey=ck;drawCurve(st.curve)}
document.querySelectorAll('#modes button').forEach(b=>b.classList.toggle('on',+b.dataset.m==st.mode));
const items=st.mode==0?st.patterns:st.mode==1?st.programs:[];const key=st.mode+'|'+items.join('|');
$('pick').parentElement.classList.toggle('hidden',st.mode>=2);
if(key!=listKey){listKey=key;$('pick').innerHTML=items.map(n=>'<option>'+esc(n)+'</option>').join('')}
if(document.activeElement!==$('pick'))$('pick').value=st.name;
if(Date.now()-drag>1500){$('depth').value=st.depth;$('bpm').value=st.bpm}
val('dv',Math.round(st.depth),'%');val('bv',Math.round(st.bpm),'Hübe/min')}
function val(id,v,u){$(id).innerHTML=v+' <span class="unit">'+u+'</span>'}
document.querySelectorAll('#modes button').forEach(b=>b.onclick=()=>cmd({cmd:'mode',mode:+b.dataset.m}));
$('pick').onchange=e=>cmd({cmd:'select',name:e.target.value});
let tm=null;for(const id of ['depth','bpm'])$(id).oninput=e=>{drag=Date.now();
val(id=='depth'?'dv':'bv',e.target.value,id=='depth'?'%':'Hübe/min');clearTimeout(tm);
tm=setTimeout(()=>cmd({cmd:'set',[id]:+e.target.value}),150)};
poll();setInterval(poll,700);
</script></body></html>
"""
