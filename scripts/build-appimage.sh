#!/usr/bin/env bash
# Baut dist/Video-to-Funscript-<version>-x86_64.AppImage
#   1. PyInstaller bündelt Python, NumPy, OpenCV und Qt in einen Ordner
#   2. appimagetool packt den Ordner zusammen mit Starter, Icon und .desktop-Datei
# Nutzt .venv, falls vorhanden, sonst das aktuelle Python (z. B. in der CI).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
ROOT="$(pwd)"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi
VERSION="$("$PY" -c 'import sys; sys.path.insert(0, "src"); import vtf; print(vtf.__version__)')"
ARCH="$(uname -m)"
OUT="dist/Video-to-Funscript-${VERSION}-${ARCH}.AppImage"

echo "==> Python: $("$PY" --version), Version $VERSION"
"$PY" -m pip install --quiet -e ".[build]"

echo "==> PyInstaller"
rm -rf build/pyinstaller dist/vtf-gui
"$PY" -m PyInstaller --noconfirm --clean --windowed --name vtf-gui \
  --distpath dist --workpath build/pyinstaller --specpath build/pyinstaller \
  --paths "$ROOT/src" --collect-submodules vtf \
  --add-data "$ROOT/src/vtf/gui/resources:vtf/gui/resources" \
  --exclude-module tkinter --exclude-module pytest \
  "$ROOT/scripts/vtf_gui_entry.py"

echo "==> AppDir"
APPDIR=build/AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a dist/vtf-gui "$APPDIR/usr/lib-vtf"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
# Mesa rusticl-OpenCL für AMD-GPUs (z. B. RX 5700 XT) aktivieren, falls installiert
export RUSTICL_ENABLE="${RUSTICL_ENABLE:-radeonsi}"
exec "$HERE/usr/lib-vtf/vtf-gui" "$@"
RUN
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/video-to-funscript.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=Video-to-Funscript Generator
Comment=Videos analysieren und Funscripts erstellen
Exec=vtf-gui %f
Icon=video-to-funscript
Terminal=false
Categories=AudioVideo;Video;
MimeType=video/mp4;video/x-matroska;video/webm;video/quicktime;video/x-msvideo;
DESK
cp "$APPDIR/video-to-funscript.desktop" "$APPDIR/usr/share/applications/"
cp packaging/vtf.png "$APPDIR/video-to-funscript.png"
cp packaging/vtf.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/video-to-funscript.png"
ln -sf video-to-funscript.png "$APPDIR/.DirIcon"

echo "==> appimagetool"
# Downloads mit Wiederholungen: GitHub-Releases antworten gelegentlich mit 5xx
fetch() { curl -fsSL --retry 6 --retry-delay 5 --retry-all-errors -o "$2" "$1"; }
mkdir -p build
TOOL="${APPIMAGETOOL:-}"
if [ -z "$TOOL" ]; then
  TOOL=build/appimagetool
  if [ ! -x "$TOOL" ]; then
    fetch "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage" "$TOOL"
    chmod +x "$TOOL"
  fi
fi
RUNTIME="${APPIMAGE_RUNTIME:-build/runtime-${ARCH}}"
if [ ! -s "$RUNTIME" ]; then
  fetch "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-${ARCH}" "$RUNTIME"
fi
# ohne FUSE lauffähig (CI, Container)
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$TOOL" --no-appstream --runtime-file "$RUNTIME" "$APPDIR" "$OUT"
echo "==> Fertig: $OUT ($(du -h "$OUT" | cut -f1))"
