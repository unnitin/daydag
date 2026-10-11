#!/usr/bin/env bash
# Promote a working deck to a dated version: rename the .pptx and its build script, point the
# script's output at the dated name, and move the render folder aside.
#
#   bash promote.sh <deck>.pptx [YYYY-MM-DD]
#
# <deck>.pptx with <deck>.build.js beside it becomes <stem>-<date>.pptx and
# <stem>-<date>.build.js, where <stem> is <deck> without a trailing "-draft" or an
# earlier "-YYYY-MM-DD", so promoting a dated deck again replaces the date. Nothing is
# deleted: the render folder is moved to a temp holding folder (path printed), and only
# when it carries render.sh's marker; any other folder of that name is left alone.
set -euo pipefail

src="${1:?usage: promote.sh deck.pptx [YYYY-MM-DD]}"
date="${2:-$(date +%F)}"
dir="$(cd "$(dirname "$src")" && pwd)"
base="$(basename "${src%.pptx}")"
stem="${base%-draft}"
stem="$(printf '%s' "$stem" | sed -E 's/-[0-9]{4}-[0-9]{2}-[0-9]{2}$//')"
new="$stem-$date"
marker=".rendered-by-consulting-slides"

[ -f "$dir/$base.pptx" ] || { echo "no $dir/$base.pptx" >&2; exit 1; }
[ "$new" = "$base" ] && { echo "$dir/$base.pptx already carries that date" >&2; exit 1; }
[ -e "$dir/$new.pptx" ] && { echo "$dir/$new.pptx already exists; pick another date" >&2; exit 1; }

mv "$dir/$base.pptx" "$dir/$new.pptx"
if [ -f "$dir/$base.build.js" ]; then
  mv "$dir/$base.build.js" "$dir/$new.build.js"
  # retarget the output file (either quote style) and the run hint; BSD and GNU sed differ
  # on -i, so rewrite through a temp file
  tmp="$(mktemp)"
  sed -e "s|'$base\.pptx'|'$new.pptx'|g" -e "s|\"$base\.pptx\"|\"$new.pptx\"|g" \
      -e "s|$base\.build\.js|$new.build.js|g" "$dir/$new.build.js" > "$tmp"
  mv "$tmp" "$dir/$new.build.js"
  if ! grep -q "$new.pptx" "$dir/$new.build.js"; then
    echo "warning: $new.build.js does not name $new.pptx; set its output path by hand" >&2
  fi
fi

pages="$dir/$base-pages"
if [ -d "$pages" ]; then
  if [ -f "$pages/$marker" ]; then
    hold="${TMPDIR:-/tmp}/consulting-slides-aside/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$hold"
    mv "$pages" "$hold/"
    echo "render folder moved to $hold/$base-pages"
  else
    echo "note: $pages was not made by render.sh; left in place" >&2
  fi
fi
echo "promoted to $dir/$new.pptx"
[ -f "$dir/$new.build.js" ] && echo "build script: $dir/$new.build.js"
