#!/usr/bin/env bash
# Spiegelt die CodeMirror-5-Dateien (Core, Themes, Addons, Modes) von
# cdn.jsdelivr.net nach static/vendor/codemirror/ — für jede Maschine
# reproduzierbar (die Vendor-Dateien sind gitignored).
#
# Aufruf: bash scripts/sync_vendor_codemirror.sh
#
# Version: pinned (5.65.21). Bei einem Upgrade: Version hier anpassen und
# die base.html-Skript-Tags prüfen.
set -euo pipefail

VERSION="${1:-5.65.21}"
BASE="https://cdn.jsdelivr.net/npm/codemirror@${VERSION}"
DEST="static/vendor/codemirror"

# Relativ zum Repo-Root ausführen (Skript aus scripts/ heraus)
cd "$(dirname "$0")/.."

FILES=(
  # Core (im npm-Paket unter lib/, im Mirror flach abgelegt)
  "lib/codemirror.min.js|codemirror.min.js"
  "lib/codemirror.min.css|codemirror.min.css"
  # Theme
  theme/dracula.min.css
  # Addons
  addon/dialog/dialog.min.css
  addon/dialog/dialog.min.js
  addon/mode/overlay.min.js
  addon/edit/closebrackets.min.js
  addon/search/searchcursor.min.js
  addon/search/search.min.js
  addon/search/jump-to-line.min.js
  # Modes
  mode/python/python.min.js
  mode/xml/xml.min.js
  mode/css/css.min.js
  mode/javascript/javascript.min.js
  mode/htmlmixed/htmlmixed.min.js
  mode/markdown/markdown.min.js
  mode/gfm/gfm.min.js
  # Workspace-Presets (C/C++, Shell, YAML, Nginx)
  mode/clike/clike.min.js
  mode/shell/shell.min.js
  mode/yaml/yaml.min.js
  mode/nginx/nginx.min.js
)

for entry in "${FILES[@]}"; do
  url_path="${entry%%|*}"
  out_path="${entry#*|}"
  [ "${out_path}" = "${entry}" ] && out_path="${url_path}"  # ohne | → gleicher Pfad
  out="${DEST}/${out_path}"
  mkdir -p "$(dirname "$out")"
  if curl -fsSL --max-time 60 -o "$out" "${BASE}/${url_path}"; then
    echo "OK  ${out_path} ($(wc -c < "$out") Bytes)"
  else
    echo "FAIL ${url_path}" >&2
    rm -f "$out"
    exit 1
  fi
done

echo
echo "Fertig: ${#FILES[@]} Dateien nach ${DEST}/"
echo "Hinweis: static/js/codemirror-mode-nasm.js ist ein Repo-File (kein Mirror)."
