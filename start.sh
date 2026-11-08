#!/usr/bin/env bash
# Startet die grafische Oberfläche. Führt bei Bedarf zuerst install.sh aus.
set -euo pipefail
APP_DIR="$(dirname "$(readlink -f "$0")")"
if [ ! -x "$APP_DIR/.venv/bin/vtf-gui" ]; then
  "$APP_DIR/install.sh"
fi
# Mesa rusticl-OpenCL für AMD-GPUs (z. B. RX 5700 XT) aktivieren, falls installiert
export RUSTICL_ENABLE="${RUSTICL_ENABLE:-radeonsi}"
# OpenCV sucht "libOpenCL.so"; Fedora/Nobara liefern ohne -devel-Paket nur ".so.1"
if [ -z "${OPENCV_OPENCL_RUNTIME:-}" ] && [ -e /usr/lib64/libOpenCL.so.1 ]; then
  export OPENCV_OPENCL_RUNTIME=/usr/lib64/libOpenCL.so.1
fi
exec "$APP_DIR/.venv/bin/vtf-gui" "$@"
