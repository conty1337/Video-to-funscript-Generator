"""README screenshots, part 3: the AI section. python shots_ai.py WORKDIR OUTDIR PERSON_VIDEO
(language from VTF_LANG; XDG_DATA_HOME must hold the AI models). PERSON_VIDEO: a clip with a
whole figure, e.g. a rendered wooden mannequin (no real footage)."""
import os, sys, shutil
from pathlib import Path
S = Path(sys.argv[1]); OUT = Path(sys.argv[2])
LANG = os.environ.get("VTF_LANG", "en")
os.environ["XDG_CONFIG_HOME"] = str(S / f"cfg_ki_{LANG}"); os.environ["XDG_CACHE_HOME"] = str(S / f"cache_ki_{LANG}")
os.environ["VTF_NO_UPDATE"] = "1"
shutil.rmtree(S / f"cfg_ki_{LANG}", ignore_errors=True); shutil.rmtree(S / f"cache_ki_{LANG}", ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
VID = "Mannequin.avi" if LANG == "en" else "Gliederpuppe.avi"      # the file name shows in the title bar
shutil.copy(sys.argv[3], S / VID)
sys.path.insert(0, "src")
from PySide6.QtCore import QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QApplication
app = QApplication([])
from vtf.gui import theme
theme.apply(app)
from vtf.gui.main_window import MainWindow
from vtf.analysis import analyze_motion
from vtf import pose
w = MainWindow(); w.resize(1440, 900); w.set_player_mode(False); w.show()
def settle():
    for _ in range(8): app.processEvents()
for f in S.glob(VID.replace(".avi", ".*")):
    if f.suffix != ".avi": f.unlink()
w.open_video(str(S / VID))
w.panel.method.setCurrentIndex(w.panel.method.findData("track"))
w.seek_ms(0); settle()
sc = w._side_scroll
sc.ensureWidgetVisible(w.panel.tracking.ai_check, 0, 40); settle()
people = pose.PoseEstimator().people(w._frame)

def mark(img, widget, label, rect=None):
    """Amber ring + number around a widget (or a rect in it) in the screenshot."""
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
    tl = widget.mapTo(w, QPoint(0, 0))
    r = (rect.toRect() if rect is not None else widget.rect()).translated(tl).adjusted(*((-16, -16, 16, 16) if rect is not None else (-5, -5, 5, 5)))
    p.setPen(QPen(QColor("#38d6ff"), 3)); p.drawRoundedRect(r, 6, 6)
    p.setBrush(QColor("#38d6ff")); p.setPen(QColor("#1a1a1a"))
    f = QFont(); f.setBold(True); f.setPixelSize(15); p.setFont(f)
    b = r.topRight() + QPoint(-4, -12)
    p.drawEllipse(b, 13, 13); p.drawText(b.x() - 13, b.y() - 13, 26, 26, 0x84, label)
    p.end()

# 1: menu after "KI-Vorschlag"
menu = w._suggestion_menu(people, w._frame.shape)
btn = w.panel.tracking.ai_btn
menu.popup(btn.mapToGlobal(btn.rect().bottomLeft())); settle()
img = w.grab().toImage()
mpos = menu.mapTo(w, QPoint(0, 0)) if menu.parent() else w.mapFromGlobal(menu.pos())
mark(img, btn, "1")
p = QPainter(img); p.drawPixmap(mpos, menu.grab()); p.end()
img.save(str(OUT / "ai-suggestion.png"))
# choose "Becken / Schritt"
from vtf.i18n import tr
act = next(a for a in menu.actions() if a.text().startswith(tr("Becken / Schritt").split(" /")[0]))
menu.close(); act.trigger(); settle()
w.panel.tracking.ai_check.setChecked(True); settle()
sc.ensureWidgetVisible(w.panel.tracking.ai_check, 0, 40); settle()
from PySide6.QtCore import QPointF
box = w.panel.tracking.sections[0].boxes[0]
r0 = w.view._image_rect()
c = w.view._norm_rect(r0, box[:4]).center()
w.view._zoom_at(c, 2.0); settle()
img = w.grab().toImage()
mark(img, w.view, "2", w.view._norm_rect(w.view._image_rect(), box[:4]))
mark(img, w.panel.tracking.ai_check, "3")
img.save(str(OUT / "ai-box.png"))
w.view.reset_zoom(); settle()
# skeleton: pick a line yourself (thigh), then the turned box along it
w._show_skeleton(people, w._frame.shape)
w.view._skel_hover = (0, (12, 14)); w.view.update(); settle()
img = w.grab().toImage()
img.save(str(OUT / "ai-skeleton.png"))
w.view._skel_hover = None
w.view.set_skeleton(None); w._skeleton_picked(0, (12, 14)); settle()
print("boxes", w.panel.tracking.sections[0].boxes)
pass
# 3: after the analysis with the AI check
m = analyze_motion(w.video_path, w.panel.analysis_settings())
w._analysis_done(m)
w.curve.view_ms = 8000; w.curve.start_ms = 0
w.seek_ms(2900); settle()
w.status_label.setText(w.status_label.text().split(" · Deko")[0].split(" · deco")[0]); w.grab().save(str(OUT / "ai-analysis.png"))
w.dirty = False
print("ok", len(people), m.meta.get("ai_moves")); os._exit(0)
