#!/usr/bin/env bash
# Render a .pptx to PDF and per-page JPEGs for visual QA.
#
#   bash render.sh deck.pptx [dpi]
#
# Writes deck.pdf next to the deck and deck-pages/page-NN.jpg (default 80 dpi, enough
# to judge layout; pass 150 for a close read). Uses the pptx skill's LibreOffice wrapper
# when it is installed (it handles the headless profile), else plain soffice.
set -euo pipefail

deck="${1:?usage: render.sh deck.pptx [dpi]}"
dpi="${2:-80}"
dir="$(cd "$(dirname "$deck")" && pwd)"
base="$(basename "${deck%.*}")"
out="$dir/$base-pages"

wrapper="$(find "$HOME/.claude/skills" -path '*pptx/scripts/office/soffice.py' 2>/dev/null | head -1 || true)"
if [ -n "$wrapper" ]; then
  py="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)"
  "$py" "$wrapper" --headless --convert-to pdf --outdir "$dir" "$deck" >/dev/null 2>&1 || true
fi
if [ ! -f "$dir/$base.pdf" ]; then
  soffice --headless --convert-to pdf --outdir "$dir" "$deck" >/dev/null 2>&1
fi
[ -f "$dir/$base.pdf" ] || { echo "render failed: no $dir/$base.pdf" >&2; exit 1; }

mkdir -p "$out"
rm -f "$out"/page-*.jpg
pdftoppm -jpeg -r "$dpi" "$dir/$base.pdf" "$out/page"
ls -1 "$out"/page-*.jpg
