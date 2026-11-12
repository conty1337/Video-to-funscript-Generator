"""GUI entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if sys.argv[1:2] == ["--check-update"]:   # ohne Oberfläche, z. B. zur Fehlersuche
        return _check_update_cli()
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from . import theme
    from .main_window import MainWindow

    app = QApplication(sys.argv)
    theme.apply(app)
    app.setApplicationName("Video-to-Funscript Generator")
    app.setOrganizationName("video-to-funscript")
    app.setDesktopFileName("video-to-funscript")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("resources") / "vtf.svg")))
    args = sys.argv[1:]
    selftest = None
    if len(args) >= 3 and args[0] == "--selftest":   # vtf-gui --selftest VIDEO OUT.png
        selftest = args[2]
        args = args[1:2]
        os.environ["VTF_NO_UPDATE"] = "1"
    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    if args:
        win.open_video(args[0])
    if selftest:
        _run_selftest(app, win, selftest)
    code = app.exec()
    _leave(win, code)
    return code                       # not reached


def _leave(win, code: int) -> None:
    """End the process right after the window has closed and saved everything.

    The normal way out lets Python and Qt tear down all their objects in an order
    neither controls; with a Qt library inside an AppImage that occasionally crashed
    on closing (signal 7 / 11 in libQt6Core). Everything that matters is written by
    then (settings, statistics, scripts, preparation files are written at once and
    atomically), so the teardown is skipped."""
    try:
        win.qsettings.sync()
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(int(code))


def _run_selftest(app, win, out: str) -> None:
    """Analyse the opened video, save a screenshot and quit (exit code 1 on failure)."""
    from PySide6.QtCore import QTimer

    def finish():
        sound = win.audio.available            # Qt Multimedia mitgepackt?
        ok = bool(win.curve.actions) and sound
        win.grab().save(out)
        track = "mit Tonspur" if win.audio.has_audio else "ohne Tonspur"
        dec = (win.motion.meta.get("decoder") if win.motion is not None else None) or "?"
        print(f"selftest: {len(win.curve.actions)} Punkte, Ton {'ok' if sound else 'FEHLT'} ({track}), "
              f"Dekodierung {dec}, Screenshot {out}")
        if not sound:
            from .audio import IMPORT_ERROR
            print(f"selftest: Qt Multimedia nicht ladbar: {IMPORT_ERROR or 'unbekannter Fehler'}")
        win.dirty = False
        app.exit(0 if ok else 1)

    def start():
        win.start_analysis()
        if win.worker is None:
            app.exit(1)
            return
        win.worker.finished.connect(lambda: QTimer.singleShot(200, finish))

    QTimer.singleShot(300, start)
    QTimer.singleShot(120_000, lambda: app.exit(2))


if __name__ == "__main__":
    sys.exit(main())


def _check_update_cli() -> int:
    from .. import __version__, updater

    try:
        rel = updater.fetch_latest()
    except updater.UpdateError as e:
        print(f"Fehler bei der Update-Suche: {e}")
        return 1
    if updater.is_newer(rel.version):
        print(f"Neue Version {rel.version} verfügbar (installiert: {__version__})")
    else:
        print(f"Version {__version__} ist aktuell (neuestes Release: {rel.tag})")
    return 0
