#!/usr/bin/env bash
# Baut eine eigenständige Programmdatei (ohne Python-Installation lauffähig)
# nach dist/vtf-gui/ mit PyInstaller.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
[ -x .venv/bin/python ] || ./install.sh
.venv/bin/python -m pip install -e ".[build]"
.venv/bin/pyinstaller --noconfirm --clean --windowed --name vtf-gui \
  --paths src --collect-submodules vtf scripts/vtf_gui_entry.py
echo "Fertig: dist/vtf-gui/vtf-gui"
