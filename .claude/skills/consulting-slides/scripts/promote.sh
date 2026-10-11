#!/usr/bin/env bash
# Promote a working deck to a dated version: rename the .pptx and its build script, point the
# script's output at the dated name, and drop the render clutter (pages folder, pdf).
#
#   bash promote.sh <deck>.pptx [YYYY-MM-DD]
#
# <deck>.pptx with <deck>.build.js beside it becomes <stem>-<date>.pptx and
# <stem>-<date>.build.js, where <stem> is <deck> without a trailing "-draft".
set -euo pipefail

src="${1:?usage: promote.sh deck.pptx [YYYY-MM-DD]}"
date="${2:-$(date +%F)}"
dir="$(cd "$(dirname "$src")" && pwd)"
base="$(basename "${src%.pptx}")"
stem="${base%-draft}"
new="$stem-$date"

[ -f "$dir/$base.pptx" ] || { echo "no $dir/$base.pptx" >&2; exit 1; }
[ -e "$dir/$new.pptx" ] && { echo "$dir/$new.pptx already exists; pick another date" >&2; exit 1; }

mv "$dir/$base.pptx" "$dir/$new.pptx"
if [ -f "$dir/$base.build.js" ]; then
  mv "$dir/$base.build.js" "$dir/$new.build.js"
  # retarget the output file and the run hint inside the script
  sed -i '' "s/'$base.pptx'/'$new.pptx'/g; s/$base.build.js/$new.build.js/g" "$dir/$new.build.js"
fi
rm -rf "$dir/$base-pages" "$dir/$base.pdf"
echo "promoted to $dir/$new.pptx"
[ -f "$dir/$new.build.js" ] && echo "build script: $dir/$new.build.js"
