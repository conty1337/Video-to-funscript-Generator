"""Player mode sidebar: the playlist.

Videos are played with the funscript lying next to them (<video>.funscript),
on the connected device, one after another.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from ..batch import VIDEO_EXTS
from ..playlist import Playlist, has_script
from . import theme

PATH_ROLE = Qt.UserRole


def _settings_list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def videos_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


class PlaylistView(QListWidget):
    """List with internal reordering and file/folder drops from outside."""
    filesDropped = Signal(list)
    reordered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("playlist")
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setIconSize(QSize(14, 14))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.ElideMiddle)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragMoveEvent(ev)

    def dropEvent(self, ev):
        if ev.mimeData().hasUrls():
            self.filesDropped.emit([u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()])
            ev.acceptProposedAction()
            return
        super().dropEvent(ev)
        self.reordered.emit()


class PlayerPanel(QWidget):
    playRequested = Signal(int)          # index in the playlist

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.qs = settings
        self.playlist = Playlist(
            repeat=str(settings.value("player/repeat", "false")).lower() == "true",
            shuffle=str(settings.value("player/shuffle", "false")).lower() == "true",
            skip_without_script=str(settings.value("player/skip", "true")).lower() == "true")
        self.playlist.add(p for p in _settings_list(settings.value("player/items")) if Path(p).is_file())
        try:
            cur = int(settings.value("player/current", -1))
        except (TypeError, ValueError):
            cur = -1
        self.playlist.current = cur if 0 <= cur < len(self.playlist.items) else -1

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(16, 16, 16, 8)
        hl.setSpacing(2)
        title = QLabel("Playlist")
        title.setObjectName("section")
        self.meta = QLabel()
        self.meta.setObjectName("caption")
        hl.addWidget(title)
        hl.addWidget(self.meta)
        lay.addWidget(head)

        self.list = PlaylistView()
        lay.addWidget(self.list, 1)
        self.empty = QLabel("Videos oder Ordner hierher ziehen\noder mit + hinzufügen.\n\n"
                            "Abgespielt wird das Funscript, das neben dem Video liegt.")
        self.empty.setObjectName("caption")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty, 1)

        tools = QHBoxLayout()
        tools.setContentsMargins(16, 8, 16, 8)
        tools.setSpacing(4)
        self.add_btn = self._tool("plus", "Videos hinzufügen")
        self.folder_btn = self._tool("folders", "Ordner hinzufügen (mit Unterordnern)")
        self.remove_btn = self._tool("trash", "Ausgewählte entfernen (Entf)")
        tools.addWidget(self.add_btn)
        tools.addWidget(self.folder_btn)
        tools.addWidget(self.remove_btn)
        tools.addStretch(1)
        self.load_btn = QPushButton("Laden")
        self.save_btn = QPushButton("Speichern")
        for b, tip in ((self.load_btn, "Playlist aus .m3u laden"), (self.save_btn, "Playlist als .m3u speichern")):
            b.setToolTip(tip)
            b.setStyleSheet("padding: 0 10px;")
            tools.addWidget(b)
        lay.addLayout(tools)

        footer = QWidget()
        footer.setObjectName("sidebarFooter")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(16, 8, 16, 16)
        fl.setSpacing(8)
        self.repeat_cb = QCheckBox("Playlist wiederholen")
        self.shuffle_cb = QCheckBox("Zufällige Reihenfolge")
        self.skip_cb = QCheckBox("Videos ohne Script überspringen")
        self.repeat_cb.setChecked(self.playlist.repeat)
        self.shuffle_cb.setChecked(self.playlist.shuffle)
        self.skip_cb.setChecked(self.playlist.skip_without_script)
        for cb in (self.repeat_cb, self.shuffle_cb, self.skip_cb):
            fl.addWidget(cb)
            cb.toggled.connect(self._options_changed)
        self.play_btn = QPushButton(theme.icon("play", theme.ON_ACCENT), "Playlist abspielen")
        self.play_btn.setObjectName("primary")
        fl.addSpacing(4)
        fl.addWidget(self.play_btn)
        lay.addWidget(footer)

        self.add_btn.clicked.connect(self.add_dialog)
        self.folder_btn.clicked.connect(self.add_folder_dialog)
        self.remove_btn.clicked.connect(self.remove_selected)
        self.load_btn.clicked.connect(self.load_dialog)
        self.save_btn.clicked.connect(self.save_dialog)
        self.play_btn.clicked.connect(self._play_clicked)
        self.list.itemDoubleClicked.connect(lambda it: self.playRequested.emit(self.list.row(it)))
        self.list.filesDropped.connect(self.add_paths)
        self.list.reordered.connect(self._reordered)
        self.refresh()

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _tool(icon: str, tip: str) -> QPushButton:
        b = QPushButton(theme.icon(icon, theme.TEXT_2, 16), "")
        b.setObjectName("transport")
        b.setToolTip(tip)
        return b

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected()
        else:
            super().keyPressEvent(ev)

    def save(self) -> None:
        pl = self.playlist
        self.qs.setValue("player/items", list(pl.items))
        self.qs.setValue("player/current", pl.current)
        self.qs.setValue("player/repeat", "true" if pl.repeat else "false")
        self.qs.setValue("player/shuffle", "true" if pl.shuffle else "false")
        self.qs.setValue("player/skip", "true" if pl.skip_without_script else "false")

    def refresh(self) -> None:
        pl = self.playlist
        self.list.blockSignals(True)
        self.list.clear()
        n_script = 0
        for i, path in enumerate(pl.items):
            script = has_script(path)
            n_script += script
            name = Path(path).name
            item = QListWidgetItem(name if script else f"{name}   · kein Script")
            item.setData(PATH_ROLE, path)
            item.setToolTip(path)
            if i == pl.current:
                item.setIcon(theme.icon("play", theme.ACCENT, 14))
                item.setForeground(theme.qcolor(theme.TEXT))
            elif not script:
                item.setForeground(theme.qcolor(theme.MUTED))
            self.list.addItem(item)
        self.list.blockSignals(False)
        n = len(pl.items)
        self.meta.setText(f"{n} Video{'s' if n != 1 else ''} · {n_script} mit Script" if n else "Leer")
        self.list.setVisible(n > 0)
        self.empty.setVisible(n == 0)
        self.play_btn.setEnabled(pl.first_playable() is not None)
        self.remove_btn.setEnabled(n > 0)
        self.save_btn.setEnabled(n > 0)
        self.save()

    def set_current(self, index: int) -> None:
        self.playlist.current = index
        self.refresh()
        if 0 <= index < self.list.count():
            self.list.scrollToItem(self.list.item(index))

    # ------------------------------------------------------------ actions
    def add_paths(self, paths) -> int:
        files = []
        for p in map(Path, paths):
            if p.is_dir():
                files += videos_in(p)
            elif p.suffix.lower() in VIDEO_EXTS:
                files.append(p)
            elif p.suffix.lower() == ".m3u" or p.suffix.lower() == ".m3u8":
                self.playlist.load_m3u(p)
        n = self.playlist.add(files)
        self.refresh()
        return n

    def add_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(self, "Videos hinzufügen", self.qs.value("last_dir", ""),
                                                f"Videos ({exts});;Alle Dateien (*)")
        if paths:
            self.qs.setValue("last_dir", str(Path(paths[0]).parent))
            self.add_paths(paths)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Ordner hinzufügen", self.qs.value("last_dir", ""))
        if d:
            self.qs.setValue("last_dir", d)
            self.add_paths([d])

    def remove_selected(self):
        rows = sorted((self.list.row(it) for it in self.list.selectedItems()), reverse=True)
        for r in rows:
            self.playlist.remove(r)
        self.refresh()

    def load_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Playlist laden", self.qs.value("last_dir", ""),
                                              "Playlist (*.m3u *.m3u8);;Alle Dateien (*)")
        if path:
            self.playlist.load_m3u(path)
            self.refresh()

    def save_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Playlist speichern",
                                              str(Path(self.qs.value("last_dir", "") or ".") / "playlist.m3u"),
                                              "Playlist (*.m3u)")
        if path:
            if not path.lower().endswith((".m3u", ".m3u8")):
                path += ".m3u"
            self.playlist.save_m3u(path)

    def _play_clicked(self):
        pl = self.playlist
        i = pl.current if pl.playable(pl.current) else pl.first_playable()
        if i is not None:
            self.playRequested.emit(i)

    def _options_changed(self):
        pl = self.playlist
        pl.repeat = self.repeat_cb.isChecked()
        pl.shuffle = self.shuffle_cb.isChecked()
        pl.skip_without_script = self.skip_cb.isChecked()
        self.refresh()

    def _reordered(self):
        self.playlist.set_items([self.list.item(i).data(PATH_ROLE) for i in range(self.list.count())])
        self.refresh()
