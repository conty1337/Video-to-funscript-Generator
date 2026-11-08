#!/usr/bin/env bash
# Installiert den Video-to-Funscript Generator in eine lokale virtuelle Umgebung (.venv)
# und legt einen Startmenü-Eintrag (KDE/GNOME) sowie den Befehl "vtf-gui" an.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
APP_DIR="$(pwd)"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { warn "python3 fehlt: sudo dnf install python3"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { warn "Python >= 3.10 benötigt."; exit 1; }

if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  warn "FFmpeg nicht gefunden (empfohlen, schneller + Hardware-Dekodierung):"
  warn "    sudo dnf install ffmpeg"
  warn "Ohne FFmpeg wird OpenCV zum Dekodieren verwendet."
fi

say "Erstelle virtuelle Umgebung in $APP_DIR/.venv"
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel >/dev/null
say "Installiere Abhängigkeiten (NumPy, OpenCV, PySide6) …"
.venv/bin/python -m pip install -e ".[dev]"

say "Lege Startbefehle an"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$APP_DIR/start.sh" "$HOME/.local/bin/vtf-gui"
ln -sf "$APP_DIR/.venv/bin/vtf" "$HOME/.local/bin/vtf"
cat > "$HOME/.local/share/applications/video-to-funscript.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Video-to-Funscript Generator
Comment=Videos analysieren und Funscripts erstellen
Exec="$APP_DIR/start.sh" %f
Icon=$APP_DIR/packaging/vtf.png
Terminal=false
Categories=AudioVideo;Video;
MimeType=video/mp4;video/x-matroska;video/webm;video/quicktime;video/x-msvideo;
DESK
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true

say "Prüfe GPU/OpenCL …"
RUSTICL_ENABLE="${RUSTICL_ENABLE:-radeonsi}" .venv/bin/vtf info || true
if ! RUSTICL_ENABLE="${RUSTICL_ENABLE:-radeonsi}" .venv/bin/python -c 'import cv2,sys; sys.exit(0 if cv2.ocl.haveOpenCL() else 1)'; then
  warn "Kein OpenCL gefunden. Optional für die RX 5700 XT (eine Variante genügt):"
  warn "    sudo dnf install mesa-libOpenCL      # Mesa rusticl (wird von start.sh aktiviert)"
  warn "    sudo dnf install rocm-opencl         # AMD ROCm"
  warn "Die Software funktioniert auch ohne OpenCL (CPU)."
fi

say "Fertig! Starten über das Startmenü (\"Video-to-Funscript Generator\"), mit ./start.sh oder vtf-gui."
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "~/.local/bin ist nicht im PATH – ggf. neu anmelden.";; esac
