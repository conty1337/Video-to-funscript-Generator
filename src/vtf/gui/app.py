"""GUI entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Video-to-Funscript Generator")
    app.setOrganizationName("video-to-funscript")
    app.setDesktopFileName("video-to-funscript")
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        win.open_video(sys.argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
