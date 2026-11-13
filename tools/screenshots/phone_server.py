"""Serve the phone page with a realistic state (random mode, and a video tab). Prints the port."""
import json, math, os, sys, time
sys.path.insert(0, "src")
S = sys.argv[1]; LANG = os.environ.get("VTF_LANG", "en")
os.environ["XDG_CONFIG_HOME"] = f"{S}/cfgp_{LANG}"
os.environ["XDG_DATA_HOME"] = f"{S}/datap_{LANG}"
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scene
from PySide6.QtWidgets import QApplication
app = QApplication([])
from vtf.gui.main_window import MainWindow
from vtf.remote import RemoteServer
from vtf.i18n import tr
w = MainWindow(); d = w.ensure_patterns()
d.set_mode(2); d.start()
for _ in range(20): app.processEvents(); time.sleep(0.02)
d.nudge(tempo=5); d._tick()
st = d.remote_state(); d.stop()
st["device"] = tr("Gerät: {0}", tr("Simulation (Fredorch F21S)"))
st["safety"] = tr("Sicherheit an") + " · " + tr("Stopp in {0:.0f} min", 18)
st["running"] = True
curve = [[i / 60, 0.5 + 0.45 * math.sin(i / 2.2)] for i in range(61)]
names = (["Morning by the lake", "Evening session part 1", "Evening session part 2", "Long weekend", "Short and fast"]
         if LANG == "en" else ["Morgens am See", "Abendrunde Teil 1", "Abendrunde Teil 2", "Langes Wochenende", "Kurz und schnell"])
st["video"] = {"title": names[2], "playing": True, "pos": 754000, "dur": 1926000, "script": True,
               "items": names, "current": 2, "player": True, "curve": curve, "now": 0.2}
from vtf.thumbs import make_thumb, thumb_bytes
clips = Path(S) / f"phone_clips_{LANG}"; clips.mkdir(exist_ok=True)
for k, n in enumerate(names):                     # short clips, only for their preview pictures
    v = clips / f"{n}.avi"
    if not v.exists():
        scene.scene(v, seconds=1.5, seed=20 + k, warm=k % 2 == 1)
    make_thumb(v)
srv = RemoteServer(lambda: st, lambda c: None, "123456", 0, "127.0.0.1",
                   get_thumb=lambda i: thumb_bytes(names[i]) if 0 <= i < len(names) else None)
srv.start()
print(srv.port, flush=True)
time.sleep(40)
srv.stop(); os._exit(0)
