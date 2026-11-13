"""README screenshots, part 2: dialogs (device, wizard, patterns, editors, library, stats, remote).
python shots_dialogs.py WORKDIR OUTDIR (language from VTF_LANG)."""
import datetime as dt, json, math, os, shutil, sys
from pathlib import Path
S = Path(sys.argv[1]); OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
LANG = os.environ.get("VTF_LANG", "en")
for k, d in (("XDG_CONFIG_HOME", "cfgd"), ("XDG_CACHE_HOME", "cached")):
    shutil.rmtree(S / f"{d}_{LANG}", ignore_errors=True)
    os.environ[k] = str(S / f"{d}_{LANG}")
os.environ["VTF_NO_UPDATE"] = "1"
sys.path.insert(0, "src")
EN = LANG == "en"

# ---- data: a library folder and some statistics
lib = S / f"library_{LANG}"; shutil.rmtree(lib, ignore_errors=True); lib.mkdir()
titles = (["Evening session", "Slow build-up", "Short and fast", "Long weekend", "Morning by the lake", "Two scenes"]
          if EN else ["Abendrunde", "Langsam steigern", "Kurz und schnell", "Langes Wochenende", "Morgens am See",
                      "Zwei Szenen"])
spec = [(14, 55, 0.9, 5), (22, 40, 0.6, 4), (6, 110, 1.0, 3), (41, 70, 0.8, 5), (9, 45, 0.5, 0), (17, 80, 0.7, 4)]
from vtf.funscript import Action, Funscript
from vtf.library import save_rating
for title, (minutes, bpm, depth, stars) in zip(titles, spec):
    (lib / f"{title}.mp4").write_bytes(b"\0")                       # the library only lists, it does not decode
    acts, t, k = [], 0.0, 0
    while t < minutes * 60_000:
        wave = 0.5 + 0.5 * math.sin(t / 90_000 * 2 * math.pi)
        tempo = bpm * (0.6 + 0.8 * wave)
        acts.append(Action(int(t), 100 if k % 2 == 0 else int(100 - 100 * depth * (0.5 + 0.5 * wave))))
        t += 30_000 / tempo; k += 1
    Funscript(acts).save(lib / f"{title}.funscript")
    if stars:
        save_rating(lib / f"{title}.mp4", stars)
today = dt.date.today()
days = {}
for back, secs, strokes in ((0, 1900, 1450), (1, 2600, 2010), (3, 1200, 830), (6, 3300, 2600), (9, 900, 610),
                            (15, 2400, 1700), (24, 1800, 1300)):
    days[(today - dt.timedelta(days=back)).isoformat()] = {
        "seconds": secs, "strokes": strokes,
        "videos": {titles[0]: secs * 0.4, titles[3]: secs * 0.3, titles[1]: secs * 0.1},
        "patterns": {"Herzschlag": secs * 0.1, "Treppe": secs * 0.06, "Necken": secs * 0.04}}
cfg = Path(os.environ["XDG_CONFIG_HOME"]) / "video-to-funscript"; cfg.mkdir(parents=True, exist_ok=True)
(cfg / "stats.json").write_text(json.dumps({"days": days}))

from PySide6.QtWidgets import QApplication
app = QApplication([])
from vtf.gui import theme
theme.apply(app)
from vtf.gui.main_window import MainWindow
from vtf import patterns as P
w = MainWindow(); w.resize(1440, 900); w.set_player_mode(False); w.show()
def settle(n=8):
    for _ in range(n): app.processEvents()
def shot(name, widget):
    settle(); widget.grab().save(str(OUT / name)); print("saved", name, widget.size().toTuple(), flush=True)

# device (simulation connected)
w.devices.connect_device("sim", "")
for _ in range(40): settle(2)
w.devices.open_dialog(); dd = w.devices.dialog
dd.kind.setCurrentIndex(dd.kind.findData("sim")); dd.refresh(); dd.resize(470, 860)
shot("device.png", dd); dd.close()

# wizard, top speed page
from vtf.gui.setup_wizard import SetupWizard
wz = SetupWizard(w.devices, w); wz.show(); wz.go(4)
shot("wizard.png", wz); wz.close()

# pattern control: patterns, one selected
d = w.ensure_patterns(); d.show(); d.resize(1040, 640)
d.set_mode(0); d._select_name("Herzschlag")
shot("patterns.png", d)
# programs: "One hour" playing as preview
d.set_mode(1); d._select_program("Eine Stunde"); d.start()
for _ in range(30): settle(2)
d.prunner.started_at = getattr(d.prunner, "started_at", None)
shot("program.png", d); d.stop()
# editors
from vtf.gui.pattern_dialog import PatternEditor, ProgramEditor
pe = PatternEditor(d.find_pattern("Galopp").copy(P.label("Galopp") + (" (own)" if EN else " (eigen)")), 70, 0, 100, d)
pe.show(); pe.resize(900, 700)
if pe.blocks.count() > 1:
    pe.blocks.setCurrentRow(1)
shot("pattern-editor.png", pe); pe.close()
pr = ProgramEditor(P.BUILTIN_PROGRAMS[0].copy(P.BUILTIN_PROGRAMS[0].label), d.all_patterns(), d.find_pattern, d); pr.show(); pr.resize(940, 680)
shot("program-editor.png", pr); pr.close()
d.close()

# library
from vtf.gui.library_dialog import LibraryDialog
ld = LibraryDialog(w.qsettings, w); ld.set_folder(str(lib)); ld.show(); ld.resize(1080, 560)
ld.sort.setCurrentIndex(ld.sort.findData("rating"))
ld.tree.setCurrentItem(ld.tree.topLevelItem(0))
ld.folder_label.setText("~/Videos")
from vtf.i18n import tr
ld.where.setText(tr("Scripts: neben dem Video und in {0}", "~/Scripts"))
shot("library.png", ld); ld.close()

# statistics
from vtf.gui.stats_dialog import StatsDialog
sd = StatsDialog(w); sd.show()
shot("stats.png", sd); sd.close()

# phone remote window with QR code
w.remote.urls = lambda: ["http://192.168.0.20:8765"]
w.remote.set_enabled(True)
from vtf.gui.remote_ui import RemoteDialog
rd = RemoteDialog(w.remote, w); rd.show(); rd.refresh()
shot("remote-dialog.png", rd); rd.close()
w.remote.shutdown()
w.devices.shutdown()
w.dirty = False
print("ok")
os._exit(0)
