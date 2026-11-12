"""Player mode sidebar: the playlist.

Videos are played with the funscript lying next to them (<video>.funscript),
on the connected device, one after another.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..batch import VIDEO_EXTS
from ..i18n import plural, tr
from ..playlist import COUNTDOWN_RANGE_S, LEAD_MS, Playlist, has_script
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
        title = QLabel(tr("Playlist"))
        title.setObjectName("section")
        self.meta = QLabel()
        self.meta.setObjectName("caption")
        hl.addWidget(title)
        hl.addWidget(self.meta)
        lay.addWidget(head)

        self.list = PlaylistView()
        lay.addWidget(self.list, 1)
        self.empty = QLabel(tr("Videos oder Ordner hierher ziehen\noder mit + hinzufügen.\n\nAbgespielt wird das Funscript, das neben dem Video liegt."))
        self.empty.setObjectName("caption")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty, 1)

        tools = QHBoxLayout()
        tools.setContentsMargins(16, 8, 16, 8)
        tools.setSpacing(4)
        self.add_btn = self._tool("plus", tr("Videos hinzufügen"))
        self.folder_btn = self._tool("folders", tr("Ordner hinzufügen (mit Unterordnern)"))
        self.remove_btn = self._tool("trash", tr("Ausgewählte entfernen (Entf)"))
        tools.addWidget(self.add_btn)
        tools.addWidget(self.folder_btn)
        tools.addWidget(self.remove_btn)
        tools.addStretch(1)
        self.load_btn = QPushButton(tr("Laden"))
        self.save_btn = QPushButton(tr("Speichern"))
        for b, tip in ((self.load_btn, tr("Playlist aus .m3u laden")), (self.save_btn, tr("Playlist als .m3u speichern"))):
            b.setToolTip(tip)
            b.setStyleSheet("padding: 0 10px;")
            tools.addWidget(b)
        lay.addLayout(tools)

        footer = QWidget()
        footer.setObjectName("sidebarFooter")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(16, 8, 16, 16)
        fl.setSpacing(8)
        self.repeat_cb = QCheckBox(tr("Playlist wiederholen"))
        self.shuffle_cb = QCheckBox(tr("Zufällige Reihenfolge"))
        self.skip_cb = QCheckBox(tr("Videos ohne Script überspringen"))
        self.repeat_cb.setChecked(self.playlist.repeat)
        self.shuffle_cb.setChecked(self.playlist.shuffle)
        self.skip_cb.setChecked(self.playlist.skip_without_script)
        self.intro_cb = QCheckBox(tr("Ruhigen Anfang überspringen"))
        self.intro_cb.setToolTip(tr("Startet 10 s bevor das Script losgeht, statt am Anfang des Videos"))
        self.countdown_cb = QCheckBox(tr("Countdown vor dem Start"))
        self.countdown_cb.setToolTip(tr("Zählt die letzten 10 s herunter, bevor das Script losgeht – auch nach einem Schnitt oder einer Pause, wenn es weitergeht"))
        self.intro_cb.setChecked(str(settings.value("player/intro", "true")).lower() == "true")
        self.countdown_cb.setChecked(str(settings.value("player/countdown", "true")).lower() == "true")
        lo, hi = COUNTDOWN_RANGE_S
        self.countdown_s = QSpinBox()
        self.countdown_s.setRange(lo, hi)
        self.countdown_s.setSuffix(" s")
        self.countdown_s.setFixedWidth(72)
        self.countdown_s.setToolTip(tr("Wie lange der Countdown läuft. So weit vor dem ersten Hub startet das Video, und so weit vor dem nächsten Hub geht es nach einer übersprungenen Pause weiter"))
        try:
            self.countdown_s.setValue(int(settings.value("player/countdown_s", LEAD_MS // 1000)))
        except (TypeError, ValueError):
            self.countdown_s.setValue(LEAD_MS // 1000)
        self.cuts_cb = QCheckBox(tr("Schnitte überspringen"))
        self.cuts_cb.setToolTip(tr("Im Editor markierte Schnitte (Taste X) werden beim Abspielen übersprungen"))
        self.cuts_cb.setChecked(str(settings.value("player/cuts", "true")).lower() == "true")
        self.quiet_cb = QCheckBox(tr("Pausen ohne Hübe überspringen"))
        self.quiet_cb.setToolTip(tr("Bewegt sich das Script 15 s oder länger nicht, geht es die Countdown-Länge vor dem nächsten Hub weiter; nach dem letzten Hub zum nächsten Video"))
        self.quiet_cb.setChecked(str(settings.value("player/quiet", "false")).lower() == "true")
        for cb in (self.repeat_cb, self.shuffle_cb, self.skip_cb, self.intro_cb, self.countdown_cb,
                   self.cuts_cb, self.quiet_cb):
            fl.addWidget(cb)
            cb.toggled.connect(self._options_changed)
            if cb is self.countdown_cb:          # its length right below it
                row = QHBoxLayout()
                row.setContentsMargins(22, 0, 0, 0)    # under the checkbox text: box 14 + gap 8
                cap = QLabel(tr("Länge"))
                cap.setObjectName("caption")
                row.addWidget(cap)
                row.addWidget(self.countdown_s)
                row.addStretch(1)
                fl.addLayout(row)
        self.countdown_s.valueChanged.connect(self.save)
        self.play_btn = theme.accent_icon(QPushButton(), "play", tr("Playlist abspielen"))
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

    def lead_ms(self) -> int:
        """Countdown length (ms): how far before a stroke the player starts / comes back."""
        return int(self.countdown_s.value()) * 1000

    def save(self) -> None:
        pl = self.playlist
        self.qs.setValue("player/items", list(pl.items))
        self.qs.setValue("player/current", pl.current)
        self.qs.setValue("player/repeat", "true" if pl.repeat else "false")
        self.qs.setValue("player/shuffle", "true" if pl.shuffle else "false")
        self.qs.setValue("player/skip", "true" if pl.skip_without_script else "false")
        self.qs.setValue("player/intro", "true" if self.intro_cb.isChecked() else "false")
        self.qs.setValue("player/countdown", "true" if self.countdown_cb.isChecked() else "false")
        self.qs.setValue("player/countdown_s", int(self.countdown_s.value()))
        self.qs.setValue("player/cuts", "true" if self.cuts_cb.isChecked() else "false")
        self.qs.setValue("player/quiet", "true" if self.quiet_cb.isChecked() else "false")

    def refresh(self) -> None:
        pl = self.playlist
        self.list.blockSignals(True)
        self.list.clear()
        n_script = 0
        for i, path in enumerate(pl.items):
            script = has_script(path)
            n_script += script
            name = Path(path).name
            item = QListWidgetItem(name if script else tr("{0}   · kein Script", name))
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
        self.meta.setText(plural(n, "{0} Video · {1} mit Script", "{0} Videos · {1} mit Script", n_script) if n else tr("Leer"))
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
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Videos hinzufügen"), self.qs.value("last_dir", ""),
                                                tr("Videos ({0});;Alle Dateien (*)", exts))
        if paths:
            self.qs.setValue("last_dir", str(Path(paths[0]).parent))
            self.add_paths(paths)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, tr("Ordner hinzufügen"), self.qs.value("last_dir", ""))
        if d:
            self.qs.setValue("last_dir", d)
            self.add_paths([d])

    def remove_selected(self):
        rows = sorted((self.list.row(it) for it in self.list.selectedItems()), reverse=True)
        for r in rows:
            self.playlist.remove(r)
        self.refresh()

    def load_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Playlist laden"), self.qs.value("last_dir", ""),
                                              tr("Playlist (*.m3u *.m3u8);;Alle Dateien (*)"))
        if path:
            self.playlist.load_m3u(path)
            self.refresh()

    def save_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Playlist speichern"),
                                              str(Path(self.qs.value("last_dir", "") or ".") / "playlist.m3u"),
                                              tr("Playlist (*.m3u)"))
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
