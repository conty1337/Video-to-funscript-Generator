"""Start menu entry for the AppImage (freedesktop .desktop file + icon).

On start the AppImage registers itself in ~/.local/share/applications, pointing
at its own path. The updater replaces the file in place, so the entry stays
valid. If another tool (e.g. Gear Lever) already integrated this AppImage,
nothing is added.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .video import system_env

APP_ID = "video-to-funscript"
RESOURCES = Path(__file__).resolve().parent / "gui" / "resources"
ICON_SRC = RESOURCES / "vtf.svg"
ICON_PNG_SRC = RESOURCES / "vtf.png"


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def desktop_file() -> Path:
    return data_home() / "applications" / f"{APP_ID}.desktop"


def icon_file() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


def icon_png_file() -> Path:
    return data_home() / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png"


def _quote(path: str) -> str:
    # Exec key quoting (freedesktop spec): wrap in quotes, escape " ` $ \
    out = path
    for ch in ("\\", '"', "`", "$"):
        out = out.replace(ch, "\\" + ch)
    return f'"{out}"'


def entry_text(appimage: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Video-to-Funscript Generator\n"
        "GenericName=Funscript-Generator\n"
        "Comment=Videos analysieren und Funscripts erstellen\n"
        f"Exec={_quote(str(appimage))} %f\n"
        f"TryExec={appimage}\n"
        # full path instead of a theme name: Plasma caches theme icons and often
        # shows a fallback until the next login for icons it has not indexed yet
        f"Icon={icon_png_file()}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Video;\n"
        "MimeType=video/mp4;video/x-matroska;video/webm;video/quicktime;video/x-msvideo;\n"
        f"StartupWMClass={APP_ID}\n"
        "X-VTF-Managed=true\n"
    )


def integrated_elsewhere(appimage: Path) -> bool:
    """True if some other .desktop file (not ours) already launches this AppImage."""
    apps = data_home() / "applications"
    if not apps.is_dir():
        return False
    needle = str(appimage)
    for f in apps.glob("*.desktop"):
        if f.name == desktop_file().name:
            continue
        try:
            if needle in f.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def is_installed(appimage: Path) -> bool:
    try:
        return desktop_file().read_text(encoding="utf-8") == entry_text(appimage)
    except OSError:
        return False


def install(appimage: Path) -> bool:
    """Create or update the entry. Returns True if something was written."""
    if is_installed(appimage) and icon_png_file().is_file():
        return False
    df = desktop_file()
    df.parent.mkdir(parents=True, exist_ok=True)
    for src, dst in ((ICON_SRC, icon_file()), (ICON_PNG_SRC, icon_png_file())):
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    tmp = df.with_suffix(".desktop.tmp")
    tmp.write_text(entry_text(appimage), encoding="utf-8")
    os.chmod(tmp, 0o755)
    os.replace(tmp, df)
    _refresh_menus()
    return True


def uninstall() -> bool:
    removed = False
    for f in (desktop_file(), icon_file(), icon_png_file()):
        try:
            f.unlink()
            removed = True
        except FileNotFoundError:
            pass
    if removed:
        _refresh_menus()
    return removed


def _refresh_menus() -> None:
    """Ask desktop environments to re-read menus; all optional and best effort."""
    for cmd in (["update-desktop-database", str(data_home() / "applications")],
                ["kbuildsycoca6", "--noincremental"], ["kbuildsycoca5", "--noincremental"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, env=system_env())
            except OSError:
                pass


def auto_integrate(appimage: Path | None) -> str | None:
    """Called on start: returns a status message if the entry was added or updated."""
    if appimage is None or os.environ.get("VTF_NO_DESKTOP_INTEGRATION"):
        return None
    if integrated_elsewhere(appimage):
        return None
    try:
        existed = desktop_file().is_file()
        if install(appimage):
            return "Startmenü-Eintrag aktualisiert" if existed else "Ins Startmenü aufgenommen"
    except OSError:
        return None
    return None
