#!/usr/bin/env bash
# The path for a deck's next versioned build: <stem>-YYYY-MM-DD-vNN.pptx (see references/feedback-loop.md,
# "Versions"). His rule, 16 Nov 2026: "i can start slides by whatever name but you add version suffix to them
# and once added keep them consistently updated"; "the latest slides should be the latest date and highest version".
#
#   nextver.sh <deck>.pptx [YYYY-MM-DD]        next build's path (today unless a date is given)
#   nextver.sh --latest <deck>.pptx            the newest existing version: the file he reads and marks up
#   nextver.sh --start N <deck>.pptx           first versioned build of a deck that already had N-1 builds
#
# <deck> can be any name he started with, a -draft, or any earlier version. <stem> drops a trailing
# -vNN, -YYYY-MM-DD and -draft. NN is one more than the highest vNN for that stem in the deck's folder, so
# versions never reset with a new day. Nothing is written; the caller builds to the printed path (OUT=...).
set -euo pipefail
latest=0; start=1
while [ $# -gt 0 ]; do
  case "$1" in
    --latest) latest=1; shift ;;
    --start) start="$2"; shift 2 ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || { echo "usage: nextver.sh [--latest | --start N] <deck>.pptx [YYYY-MM-DD]" >&2; exit 2; }
src="$1"; day="${2:-$(date +%F)}"
dir="$(cd "$(dirname "$src")" && pwd)"
base="$(basename "${src%.pptx}")"
stem="$(printf '%s' "$base" | sed -E 's/-v[0-9]+$//; s/-[0-9]{4}-[0-9]{2}-[0-9]{2}$//; s/-draft$//')"
best=""; bestn=0
shopt -s nullglob
for f in "$dir/$stem"-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-v[0-9]*.pptx; do
  n="$(basename "$f" .pptx | sed -E 's/.*-v([0-9]+)$/\1/')"
  n=$((10#$n))
  if [ "$n" -gt "$bestn" ]; then bestn="$n"; best="$f"; fi
done
if [ "$latest" = 1 ]; then
  [ -n "$best" ] || { echo "no versioned build of $stem in $dir yet" >&2; exit 1; }
  echo "$best"; exit 0
fi
next=$((bestn + 1)); [ "$bestn" -eq 0 ] && next="$start"
printf '%s/%s-%s-v%02d.pptx\n' "$dir" "$stem" "$day" "$next"
