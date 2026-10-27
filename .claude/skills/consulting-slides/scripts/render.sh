#!/usr/bin/env bash
# Render a .pptx to PDF and per-page JPEGs for visual QA.
#
#   bash render.sh deck.pptx [dpi]
#
# Writes one folder next to the deck, deck-pages/, holding deck.pdf, page-NN.jpg (default
# 80 dpi, enough to judge layout; pass 150 for a close read) and a marker file that says
# this script made the folder. Nothing is deleted: a previous render folder is moved aside
# to a temp holding folder and the path printed, and a folder of that name that this
# script did not make is left alone and the render refused. Uses the pptx skill's
# LibreOffice wrapper when it is installed (it handles the headless profile), else soffice.
set -euo pipefail

deck="${1:?usage: render.sh deck.pptx [dpi]}"
dpi="${2:-80}"
dir="$(cd "$(dirname "$deck")" && pwd)"
base="$(basename "${deck%.*}")"
out="$dir/$base-pages"
marker=".rendered-by-consulting-slides"

if [ -e "$out" ] && [ ! -f "$out/$marker" ]; then
  echo "$out exists and was not made by render.sh; move it and rerun" >&2
  exit 1
fi

work="$(mktemp -d)"
wrapper="$(find "$HOME/.claude/skills" -path '*pptx/scripts/office/soffice.py' 2>/dev/null | head -1 || true)"
if [ -n "$wrapper" ]; then
  py="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)"
  "$py" "$wrapper" --headless --convert-to pdf --outdir "$work" "$dir/$base.pptx" >/dev/null 2>&1 || true
fi
if [ ! -f "$work/$base.pdf" ]; then
  soffice --headless --convert-to pdf --outdir "$work" "$dir/$base.pptx" >/dev/null 2>&1
fi
[ -f "$work/$base.pdf" ] || { echo "render failed: no PDF produced for $deck" >&2; exit 1; }
pdftoppm -jpeg -r "$dpi" "$work/$base.pdf" "$work/page"
: > "$work/$marker"

if [ -e "$out" ]; then
  hold="${TMPDIR:-/tmp}/consulting-slides-aside/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$hold"
  mv "$out" "$hold/"
  echo "previous render moved to $hold/$base-pages"
fi
mv "$work" "$out"
ls -1 "$out"/page-*.jpg

# A build is not handed over until slide-review has read it (SKILL.md step 6). Said here
# because this script runs after every build; a review's own render of a scratch copy
# lands under a temp folder and does not need the reminder.
case "$dir" in
  /tmp/* | /private/tmp/* | /var/folders/* | "${TMPDIR:-/nonexistent}"*) ;;
  *) echo "next: slide-review in a fresh agent before handing this over (consulting-slides step 6)" ;;
esac
