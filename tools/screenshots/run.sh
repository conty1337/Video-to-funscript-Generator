#!/usr/bin/env bash
# Recreate the README screenshots in docs/images/{en,de} (run from the repo root).
#   tools/screenshots/run.sh WORKDIR [PERSON_VIDEO]
# WORKDIR holds the generated demo videos and settings. Without PERSON_VIDEO the AI pictures
# are skipped; they also need the AI models under $XDG_DATA_HOME/video-to-funscript/models.
# The phone pages need Node with Playwright.
set -euo pipefail
W=$(realpath "$1"); PERSON=${2:-}
T=$(dirname "$(realpath "$0")")
mkdir -p "$W"
for L in en de; do
  export VTF_LANG=$L QT_QPA_PLATFORM=offscreen
  OUT=docs/images/$L
  .venv/bin/python "$T/shots_main.py" "$W" "$OUT"
  .venv/bin/python "$T/shots_dialogs.py" "$W" "$OUT"
  if [ -n "$PERSON" ]; then .venv/bin/python "$T/shots_ai.py" "$W" "$OUT" "$(realpath "$PERSON")"; fi
  rm -f "$W/port_$L.txt"
  .venv/bin/python "$T/phone_server.py" "$W" > "$W/port_$L.txt" 2>/dev/null &
  until [ -s "$W/port_$L.txt" ]; do sleep 1; done
  NODE_PATH=$(npm root -g) node "$T/phone_shot.js" "$(head -1 "$W/port_$L.txt")" "$(realpath "$OUT")"
done
