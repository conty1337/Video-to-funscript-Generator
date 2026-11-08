#!/usr/bin/env bash
# Baut dist/Video-to-Funscript-<version>-x86_64.AppImage
#   1. PyInstaller bündelt Python, NumPy, OpenCV und Qt in einen Ordner
#   2. mksquashfs packt den Ordner mit Starter, Icon und .desktop-Datei in ein
#      SquashFS-Abbild, davor kommt die AppImage-Runtime aus packaging/
#      (genau das macht appimagetool, nur ohne Downloads)
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
# OpenCV sucht "libOpenCL.so"; Fedora/Nobara liefern ohne -devel-Paket nur ".so.1"
if [ -z "${OPENCV_OPENCL_RUNTIME:-}" ]; then
  for lib in /usr/lib64/libOpenCL.so.1 /usr/lib/x86_64-linux-gnu/libOpenCL.so.1 /usr/lib/libOpenCL.so.1; do
    if [ -e "$lib" ]; then export OPENCV_OPENCL_RUNTIME="$lib"; break; fi
  done
fi
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

echo "==> SquashFS + Runtime"
RUNTIME="${APPIMAGE_RUNTIME:-packaging/runtime-${ARCH}}"
RUNTIME_SHA256="617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46"
[ -s "$RUNTIME" ] || { echo "Runtime fehlt: $RUNTIME (siehe packaging/RUNTIME.md)"; exit 1; }
if [ -z "${APPIMAGE_RUNTIME:-}" ] && [ "$ARCH" = "x86_64" ]; then
  echo "$RUNTIME_SHA256  $RUNTIME" | sha256sum -c --quiet - || { echo "Prüfsumme der Runtime falsch"; exit 1; }
fi
command -v mksquashfs >/dev/null || { echo "mksquashfs fehlt: sudo dnf install squashfs-tools"; exit 1; }
mkdir -p dist
rm -f build/app.squashfs "$OUT"
mksquashfs "$APPDIR" build/app.squashfs -root-owned -noappend -comp zstd -Xcompression-level 19 -no-progress >/dev/null
cat "$RUNTIME" build/app.squashfs > "$OUT"
chmod +x "$OUT"
echo "==> Fertig: $OUT ($(du -h "$OUT" | cut -f1))"
