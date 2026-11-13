"""Funscript library: every video with a script in a folder, with heatmap preview,
search, sorting and a rating. Double click plays it."""

from __future__ import annotations

import threading

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QCursor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..i18n import tr
from ..library import SORTS, filtered, save_rating, scan
from ..thumbs import get_thumb, make_thumb, publish
from . import theme
from .heatbar import heat_color

COL_THUMB, COL_NAME, COL_LEN, COL_RATE, COL_TEMPO, COL_STARS, COL_HEAT = range(7)
THUMB_W, THUMB_H = 64, 36    # preview picture in the list
ENTRY_ROLE = Qt.UserRole + 1
STAR = 18                # px per star


def _fmt_len(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class _HeatDelegate(QStyledItemDelegate):
    """The heatmap of a script as a thin bar."""

    def paint(self, p: QPainter, opt, index):
        e = index.data(ENTRY_ROLE)
        if e is None or not e.heat:
            return
        r = QRectF(opt.rect).adjusted(4, opt.rect.height() / 2 - 4, -4, -(opt.rect.height() / 2 - 4))
        n = len(e.heat)
        w = r.width() / n
        for i, v in enumerate(e.heat):
            p.fillRect(QRectF(r.x() + i * w, r.y(), w + 0.6, r.height()), heat_color(v))


class _ThumbDelegate(QStyledItemDelegate):
    """The video's preview picture (thumbs.py), kept as pixmaps once loaded."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache: dict[str, QPixmap] = {}

    def sizeHint(self, opt, index):
        return QSize(THUMB_W + 8, THUMB_H + 8)

    def paint(self, p: QPainter, opt, index):
        e = index.data(ENTRY_ROLE)
        if e is None:
            return
        r = QRectF(opt.rect.x() + 4, opt.rect.center().y() - THUMB_H / 2, THUMB_W, THUMB_H)
        path = get_thumb(e.name)
        pm = None
        if path is not None:
            key = f"{path}|{path.stat().st_mtime_ns}"
            pm = self.cache.get(key)
            if pm is None:
                pm = QPixmap(str(path)).scaled(THUMB_W * 2, THUMB_H * 2, Qt.KeepAspectRatioByExpanding,
                                               Qt.SmoothTransformation)
                self.cache[key] = pm
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(r, 4, 4)
        p.setClipPath(clip)
        p.fillRect(r, theme.qcolor(theme.RAISED))
        if pm is not None and not pm.isNull():
            src = QRectF(0, 0, pm.width(), pm.height())
            scale = max(r.width() / src.width(), r.height() / src.height())
            w, h = r.width() / scale, r.height() / scale
            p.drawPixmap(r, pm, QRectF((src.width() - w) / 2, (src.height() - h) / 2, w, h))
            if e.missing:
                p.fillRect(r, theme.qcolor(theme.BG, 0.55))
        p.restore()


class _StarDelegate(QStyledItemDelegate):
    """Five stars; clicking a star sets the rating (handled by the dialog)."""

    def paint(self, p: QPainter, opt, index):
        e = index.data(ENTRY_ROLE)
        if e is None:
            return
        p.save()
        f = p.font()
        f.setPixelSize(16)
        p.setFont(f)
        for k in range(5):
            on = k < e.rating
            p.setPen(theme.qcolor(theme.ACCENT if on else theme.MUTED, 1.0 if on else 0.5))
            p.drawText(QRectF(opt.rect.x() + 4 + k * STAR, opt.rect.y(), STAR, opt.rect.height()),
                       Qt.AlignCenter, "★" if on else "☆")
        p.restore()


class LibraryDialog(QDialog):
    playRequested = Signal(str)     # video path: play it (player)
    queueRequested = Signal(list)   # video paths: add to the playlist
    editRequested = Signal(str)     # video path: open in the editor
    thumbsMade = Signal()           # background: missing preview pictures were created

    def __init__(self, qsettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Bibliothek"))
        self.resize(980, 640)
        self.qs = qsettings
        self.entries: list = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.folder_label = QLabel()
        self.folder_label.setObjectName("meta")
        pick = QPushButton(theme.icon("folder-open"), tr("Ordner"))
        pick.clicked.connect(self._pick)
        rescan = QPushButton(theme.icon("rotate-ccw"), "")
        rescan.setToolTip(tr("Ordner neu einlesen"))
        rescan.clicked.connect(self.reload)
        scripts = QPushButton(theme.icon("save"), tr("Speicherorte…"))
        scripts.setToolTip(tr("Wo Scripts und Video-Daten gespeichert und gesucht werden: neben dem Video und/oder in eigenen Ordnern"))
        scripts.clicked.connect(self._scripts)
        top.addWidget(pick)
        top.addWidget(rescan)
        top.addWidget(self.folder_label, 1)
        top.addWidget(scripts)
        lay.addLayout(top)
        self.where = QLabel()
        self.where.setObjectName("caption")
        lay.addWidget(self.where)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Suchen"))
        self.search.textChanged.connect(self.refresh)
        self.sort = QComboBox()
        for key, (label, _k, _r) in SORTS.items():
            self.sort.addItem(label, key)
        self.sort.currentIndexChanged.connect(self.refresh)
        self.min_stars = QComboBox()
        for k, text in enumerate((tr("Alle"), tr("ab ★"), tr("ab ★★"), tr("ab ★★★"), tr("ab ★★★★"), "★★★★★")):
            self.min_stars.addItem(text, k)
        self.min_stars.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.search, 1)
        bar.addWidget(QLabel(tr("Sortieren")))
        bar.addWidget(self.sort)
        bar.addWidget(self.min_stars)
        lay.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setObjectName("library")
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setHeaderLabels(["", tr("Video"), tr("Länge"), tr("Hübe/min"), tr("Tempo"), tr("Bewertung"), tr("Heatmap")])
        self.thumb_delegate = _ThumbDelegate(self.tree)
        self.tree.setItemDelegateForColumn(COL_THUMB, self.thumb_delegate)
        self.tree.setItemDelegateForColumn(COL_HEAT, _HeatDelegate(self.tree))
        self.tree.setItemDelegateForColumn(COL_STARS, _StarDelegate(self.tree))
        h = self.tree.header()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        h.setSectionResizeMode(COL_HEAT, QHeaderView.Fixed)
        self.tree.headerItem().setToolTip(COL_TEMPO, tr("Mittleres Tempo in Positionen pro Sekunde"))
        self.tree.headerItem().setToolTip(COL_RATE, tr("Hübe pro Minute, solange sich das Script bewegt"))
        for c, w in ((COL_THUMB, THUMB_W + 8), (COL_LEN, 72), (COL_RATE, 84), (COL_TEMPO, 72), (COL_STARS, 104),
                     (COL_HEAT, 240)):
            self.tree.setColumnWidth(c, w)
        self.thumbsMade.connect(self.tree.viewport().update)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._play(it))
        self.tree.itemClicked.connect(self._clicked)
        lay.addWidget(self.tree, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.count = QLabel()
        self.count.setObjectName("meta")
        self.count.setFont(theme.mono_font(12))
        edit = QPushButton(theme.icon("pencil"), tr("Im Editor öffnen"))
        edit.clicked.connect(self._edit)
        queue = QPushButton(theme.icon("list-video"), tr("Zur Playlist"))
        queue.clicked.connect(self._queue)
        play = theme.accent_icon(QPushButton(), "play", tr("Abspielen"))
        play.setObjectName("primary")
        play.clicked.connect(lambda: self._play(self.tree.currentItem()))
        foot.addWidget(self.count, 1)
        foot.addWidget(edit)
        foot.addWidget(queue)
        foot.addWidget(play)
        lay.addLayout(foot)
        hint = QLabel(tr("Doppelklick spielt ab · Klick auf die Sterne bewertet (oder Taste 1–5, 0 = keine)"))
        hint.setObjectName("caption")
        lay.addWidget(hint)

        self.folder = str(self.qs.value("library/folder", "") or "")
        self.reload()
        if not self.folder:
            self.folder_label.setText(tr("Noch kein Ordner gewählt"))

    # ------------------------------------------------------------ data
    def set_folder(self, folder: str) -> None:
        self.folder = folder
        self.qs.setValue("library/folder", folder)
        self.reload()

    def reload(self) -> None:
        from .scripts_ui import describe

        self.where.setText(describe())
        if not self.folder:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.entries = scan(self.folder)
        finally:
            QApplication.restoreOverrideCursor()
        self.folder_label.setText(self.folder)
        self.refresh()
        self._make_missing_thumbs()

    def _make_missing_thumbs(self) -> None:
        """Create the preview pictures that are missing, in the background (a few at a time),
        and hand them to the file manager for the script files (thumbs.publish)."""
        found = [e for e in self.entries if not e.missing]
        todo = [e.video for e in found if get_thumb(e.name) is None]
        if not found or getattr(self, "_thumb_thread", None) is not None:
            return

        def run():
            for i, v in enumerate(todo):
                make_thumb(v)
                if i % 4 == 3 or i == len(todo) - 1:
                    try:
                        self.thumbsMade.emit()
                    except RuntimeError:            # the window is gone
                        return
            for e in found:
                publish(e.script, e.video)
            self._thumb_thread = None
        self._thumb_thread = threading.Thread(target=run, daemon=True, name="library-thumbs")
        self._thumb_thread.start()

    def refresh(self) -> None:
        shown = filtered(self.entries, self.search.text(), self.sort.currentData(), self.min_stars.currentData())
        self.tree.clear()
        for e in shown:
            name = e.name + tr(" · Video fehlt") if e.missing else e.name
            it = QTreeWidgetItem(["", name, _fmt_len(e.duration_ms), f"{e.per_min:.0f}",
                                  f"{e.avg_speed:.0f}", "", ""])
            if e.missing:
                it.setForeground(COL_NAME, theme.qcolor(theme.MUTED))
                it.setToolTip(COL_NAME, tr("Video nicht gefunden (verschoben oder umbenannt?) – Doppelklick sucht es\n{0}",
                                           e.script))
            else:
                it.setToolTip(COL_NAME, str(e.video))
            it.setToolTip(COL_TEMPO, tr("mittleres Tempo {0:.0f} Pos./s, höchstes {1:.0f}", e.avg_speed, e.max_speed))
            for c in (COL_LEN, COL_RATE, COL_TEMPO):
                it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
                it.setFont(c, theme.mono_font(12))
            it.setData(COL_THUMB, ENTRY_ROLE, e)
            it.setData(COL_STARS, ENTRY_ROLE, e)
            it.setData(COL_HEAT, ENTRY_ROLE, e)
            self.tree.addTopLevelItem(it)
        total = len(self.entries)
        self.count.setText(tr("{0} von {1} Scripts", len(shown), total) if total else tr("Keine Videos mit Script in diesem Ordner"))

    def _entry(self, it):
        return None if it is None else it.data(COL_STARS, ENTRY_ROLE)

    def rate(self, it, stars: int) -> None:
        e = self._entry(it)
        if e is None or e.missing:
            return
        e.rating = max(0, min(5, stars))
        save_rating(e.video, e.rating)
        self.tree.viewport().update()

    # ------------------------------------------------------------ actions
    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, tr("Ordner der Bibliothek"), self.folder or "")
        if d:
            self.set_folder(d)

    def _scripts(self):
        from .scripts_ui import ScriptFolderDialog

        ScriptFolderDialog(self).exec()
        self.reload()

    def _clicked(self, it, col):
        if col != COL_STARS:
            return
        x = self.tree.viewport().mapFromGlobal(QCursor.pos()).x() - self.tree.columnViewportPosition(COL_STARS)
        k = int((x - 4) // STAR) + 1
        if 1 <= k <= 5:
            e = self._entry(it)
            self.rate(it, 0 if e is not None and e.rating == k else k)      # same star again: clear

    def keyPressEvent(self, ev):
        if Qt.Key_0 <= ev.key() <= Qt.Key_5 and self.tree.currentItem() is not None:
            for it in self.tree.selectedItems() or [self.tree.currentItem()]:
                self.rate(it, ev.key() - Qt.Key_0)
            return
        super().keyPressEvent(ev)

    def _play(self, it):
        e = self._entry(it)
        if e is not None:                   # video missing: the script, the window looks for its video
            self.playRequested.emit(str(e.script if e.missing else e.video))
            self.accept()

    def _queue(self):
        paths = [str(self._entry(it).video) for it in self.tree.selectedItems()
                 if self._entry(it) and not self._entry(it).missing]
        if paths:
            self.queueRequested.emit(paths)
            self.count.setText(tr("{0} zur Playlist hinzugefügt", len(paths)))

    def _edit(self):
        e = self._entry(self.tree.currentItem())
        if e is not None:
            self.editRequested.emit(str(e.script if e.missing else e.video))
            self.accept()

