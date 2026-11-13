"""README screenshots, part 1: editor, tracking close-up, accents, player.
Run from the repo root: python shots_main.py WORKDIR OUTDIR (language from VTF_LANG)."""
import os, sys, shutil, traceback
from pathlib import Path
S = Path(sys.argv[1]); OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
LANG = os.environ.get("VTF_LANG", "en")
for k, d in (("XDG_CONFIG_HOME", "cfg"), ("XDG_CACHE_HOME", "cache")):
    shutil.rmtree(S / f"{d}_{LANG}", ignore_errors=True)
    os.environ[k] = str(S / f"{d}_{LANG}")
os.environ["VTF_NO_UPDATE"] = "1"
sys.path.insert(0, "src"); sys.path.insert(0, str(Path(__file__).resolve().parent))
import scene
from vtf.i18n import tr

vids = S / f"videos_{LANG}"; shutil.rmtree(vids, ignore_errors=True); vids.mkdir()
names = [tr("Szene") + f" {i:02d}" for i in (1, 2, 3)] if LANG == "de" else [f"Scene {i:02d}" for i in (1, 2, 3)]
scene.scene(vids / f"{names[0]}.avi", seconds=12, quiet_from=10.4)
scene.scene(vids / f"{names[1]}.avi", seconds=8, seed=3, rate=1.1, warm=False)
scene.scene(vids / f"{names[2]}.avi", seconds=8, seed=5, rate=0.7)

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QPainter, QImage, QColor, QFont
from PySide6.QtWidgets import QApplication
app = QApplication([])
from vtf.gui import theme
theme.apply(app)
from vtf.gui.main_window import MainWindow
from vtf.analysis import analyze_motion

w = MainWindow(); w.resize(1440, 900); w.set_player_mode(False); w.show()
def settle(n=6):
    for _ in range(n): app.processEvents()
def shot(name, widget=None):
    settle(); (widget or w).grab().save(str(OUT / name)); print("saved", name, flush=True)

# ---- editor: found automatically (outline, reference, arrow), analysed, cut, bookmarks, check
w.open_video(str(vids / f"{names[0]}.avi"))
w.panel.method.setCurrentIndex(w.panel.method.findData("track"))
w.seek_ms(0); settle()
w.auto_find(); settle()
found_msg = w.status_label.text()
w.add_bookmark_at(3600, "short strokes" if LANG == "en" else "kurze Hübe")
m = analyze_motion(w.video_path, w.panel.analysis_settings())
w._analysis_done(m)
w.add_cut(10_500, 12_000)
w.curve.view_ms = 12_000; w.curve.start_ms = 0; w.curve.follow = False
w.seek_ms(1150); settle()
w._side_scroll.verticalScrollBar().setValue(0)
w.status_label.setText(w.status_label.text().split(" · Deko")[0].split(" · deco")[0])
shot("editor.png")

# ---- tracking close-up: zoomed, found boxes, options open
w.seek_ms(0); settle()
w.status_label.setText(found_msg)
w.panel.options_btn.setChecked(True); settle()
w._side_scroll.verticalScrollBar().setValue(w._side_scroll.verticalScrollBar().maximum())
w.view._zoom_at(QPointF(w.view.width() * 0.42, w.view.height() * 0.45), 1.6); settle()
shot("tracking.png")
w.view.reset_zoom(); w.panel.options_btn.setChecked(False)
w._side_scroll.verticalScrollBar().setValue(0)

# ---- accents: the editor in four colours, 2 x 2
w.seek_ms(1150); settle()
tiles = []
w._side_scroll.verticalScrollBar().setValue(0)
for key in ("amber", "blue", "violet", "green"):
    theme.change_accent(app, theme.ACCENTS[key][1]); settle(10)
    tiles.append((theme.ACCENTS[key][0], w.grab().toImage()))
theme.change_accent(app, theme.DEFAULT_ACCENT); settle()
tw, th = 720, 450
img = QImage(tw * 2 + 12, th * 2 + 12, QImage.Format_ARGB32); img.fill(QColor(theme.DEEP))
p = QPainter(img); p.setRenderHint(QPainter.SmoothPixmapTransform)
for k, (label, im) in enumerate(tiles):
    x, y = (k % 2) * (tw + 12), (k // 2) * (th + 12)
    p.drawImage(QRect(x, y, tw, th), im)
p.end(); img.save(str(OUT / "accents.png")); print("saved accents.png")

# ---- player: playlist with three scenes, countdown
from vtf.funscript import Funscript
Funscript(list(w.curve.actions)).save(vids / f"{names[0]}.funscript")
for n in names[1:]:
    Funscript([a for a in w.curve.actions if a.at <= 8000]).save(vids / f"{n}.funscript")
w.dirty = False
w.player.add_paths([str(vids / f"{n}.avi") for n in names])
w.set_player_mode(True)
w.player.set_current(0); w.player.refresh()
w.seek_ms(2500); w.view.set_countdown(7); settle()
w._status(tr("Playlist {0}/{1}: {2}", 1, 3, f"{names[0]}.avi"))
shot("player.png")
w.view.set_countdown(None)
w.set_player_mode(False)
w.dirty = False
print("ok")
os._exit(0)
