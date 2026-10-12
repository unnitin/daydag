#!/usr/bin/env python3
"""Show what the reviewer changed by hand in a .pptx, beyond the yellow stickies.

Usage: python3 drift.py <his-version>.pptx <last-build>.pptx

Compares the two decks slide by slide (file order) and text box by text box (shape name),
ignoring yellow (FFFF00) sticky boxes, and prints every text that differs or exists in only
one of them. Run it on his returned file against the file you last gave him, before
applying the stickies: a heading he retyped in place is feedback too, and a rebuild from
the script would silently overwrite it. Standard library only; slide order and shape
parsing come from `stickies.py` beside it, so the two never disagree about what a sticky is.
"""

from __future__ import annotations

import sys
import zipfile
from itertools import zip_longest

from stickies import shapes, slide_paths

STICKY = "FFFF00"


def slides(path: str) -> list[list[tuple[str, str]]]:
    """Each slide's non-sticky text boxes as (shape name, text), in file order."""
    out: list[list[tuple[str, str]]] = []
    with zipfile.ZipFile(path) as z:
        for part in slide_paths(z):
            xml = z.read(part).decode("utf-8", "replace")
            out.append(
                [
                    (sh["name"], " ".join(sh["text"]))
                    for sh in shapes(xml)
                    if sh["text"] and sh["fill"] != STICKY
                ]
            )
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    his, mine = slides(sys.argv[1]), slides(sys.argv[2])
    if len(his) != len(mine):
        print(f"slide count differs: his {len(his)}, build {len(mine)}; comparing by position")
    changed = 0
    # by position on purpose: a slide in only one deck is reported against an empty one,
    # not a reason to stop (zip_longest, not zip(strict=): this runs on any Python 3)
    for i, (h, m) in enumerate(zip_longest(his, mine, fillvalue=[]), 1):
        hd, md = dict(h), dict(m)
        rows = [(k, hd[k], md.get(k)) for k in hd if hd[k] != md.get(k)]
        rows += [(k, None, md[k]) for k in md if k not in hd]
        if not rows:
            continue
        changed += len(rows)
        print(f"-- slide {i}: {len(rows)} text box(es) differ")
        for k, a, b in rows:
            print(f"   {k}")
            print(f"     his  : {a if a is not None else '<absent>'}")
            print(f"     build: {b if b is not None else '<absent>'}")
    if not changed:
        print("no hand edits: every text box matches the build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
