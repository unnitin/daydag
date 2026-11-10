#!/usr/bin/env python3
"""Show what the reviewer changed by hand in a .pptx, beyond the yellow stickies.

Usage: python3 drift.py <his-version>.pptx <last-build>.pptx

Compares the two decks slide by slide (file order) and text box by text box (shape name),
ignoring yellow (FFFF00) sticky boxes, and prints every hand edit:

- text he retyped, added or deleted;
- formatting he changed on text that reads the same: size, bold, italic, underline,
  colour, highlight, alignment (a number he recoloured from bright green to dark green
  is feedback too: it says that number is not the one the slide should highlight);
- a box he moved or resized, or whose fill he changed.

Run it on his returned file against the file you last gave him, before applying the
stickies, and carry every line into the build script: a rebuild from the script silently
overwrites whatever it does not know about (his heading edits were lost on 25 Oct, his
recoloured impact number on 10 Nov). Formatting is compared by value, not by XML, so a
plain PowerPoint save (attribute order, self-closing tags, added font slots) is not
reported. Standard library only; slide order and sticky detection come from
`stickies.py` beside it, so the two never disagree about what a sticky is.
"""

# ruff: noqa: B905 - zip(strict=) needs Python 3.10; the skill runs on the system Python 3.9
from __future__ import annotations

import html
import re
import sys
import zipfile
from itertools import zip_longest

from stickies import shapes, slide_paths

STICKY = "FFFF00"
EMU = 914400
MOVE_TOL = 0.02  # inches; smaller shifts are rounding, not a hand edit
FMT_KEYS = ("sz", "b", "i", "u", "color", "highlight")


def _attr(tag: str, key: str) -> str | None:
    m = re.search(rf'\b{key}="([^"]*)"', tag)
    return m.group(1) if m else None


def _run_fmt(rpr: str) -> dict:
    """Formatting of one run, from its <a:rPr> element ('' when the run has none)."""
    open_tag = re.match(r"<a:rPr\b[^>]*>", rpr)
    tag = open_tag.group(0) if open_tag else ""
    body = rpr[len(tag) :]
    # the highlight has its own colour; take it out before reading the text colour
    hl = re.search(r"<a:highlight>.*?</a:highlight>", body, flags=re.S)
    hl_col = re.search(r'val="([0-9A-Fa-f]{6})"', hl.group(0)) if hl else None
    body = body.replace(hl.group(0), "") if hl else body
    col = re.search(r'<a:solidFill>\s*<a:srgbClr val="([0-9A-Fa-f]{6})"', body)
    sz = _attr(tag, "sz")
    return {
        "sz": f"{int(sz) / 100:g}" if sz else "inherit",
        "b": _attr(tag, "b") in ("1", "true"),
        "i": _attr(tag, "i") in ("1", "true"),
        "u": (_attr(tag, "u") or "none") != "none",
        "color": col.group(1).upper() if col else "inherit",
        "highlight": hl_col.group(1).upper() if hl_col else "none",
    }


def _paragraphs(sp: str) -> list[dict]:
    """Each paragraph as its alignment and a per-character formatting list."""
    out = []
    for p in re.findall(r"<a:p>.*?</a:p>|<a:p .*?</a:p>", sp, flags=re.S):
        ppr = re.search(r"<a:pPr\b[^>]*>", p)
        chars: list[tuple[str, tuple]] = []
        for r in re.findall(r"<a:r>.*?</a:r>", p, flags=re.S):
            rpr = re.search(r"<a:rPr\b[^>]*/>|<a:rPr\b.*?</a:rPr>", r, flags=re.S)
            fmt = _run_fmt(rpr.group(0) if rpr else "")
            key = tuple(fmt[k] for k in FMT_KEYS)
            for t in re.findall(r"<a:t>(.*?)</a:t>", r, flags=re.S):
                chars += [(c, key) for c in html.unescape(t)]
        if chars:
            out.append(
                {"algn": (_attr(ppr.group(0), "algn") if ppr else None) or "l", "chars": chars}
            )
    return out


def _geometry(sp: str) -> tuple | None:
    m = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"\s*/>\s*<a:ext cx="(\d+)" cy="(\d+)"', sp)
    return tuple(round(int(v) / EMU, 2) for v in m.groups()) if m else None


def slides(path: str) -> list[dict[str, dict]]:
    """Each slide's non-sticky text boxes by shape name: text, paragraphs, geometry, fill."""
    out: list[dict[str, dict]] = []
    with zipfile.ZipFile(path) as z:
        for part in slide_paths(z):
            xml = z.read(part).decode("utf-8", "replace")
            boxes: dict[str, dict] = {}
            for sp, sh in zip(re.findall(r"<p:sp>.*?</p:sp>", xml, flags=re.S), shapes(xml)):
                if not sh["text"] or sh["fill"] == STICKY:
                    continue
                boxes[sh["name"]] = {
                    "text": " ".join(sh["text"]),
                    "paras": _paragraphs(sp),
                    "geom": _geometry(sp),
                    "fill": sh["fill"],
                }
            out.append(boxes)
    return out


def _describe(old: tuple, new: tuple) -> str:
    names = {
        "sz": "size",
        "b": "bold",
        "i": "italic",
        "u": "underline",
        "color": "colour",
        "highlight": "highlight",
    }
    return ", ".join(f"{names[k]} {a} -> {b}" for k, a, b in zip(FMT_KEYS, old, new) if a != b)


def format_changes(his: dict, mine: dict) -> list[str]:
    """Formatting differences between two boxes whose text reads the same."""
    lines = []
    for hp, mp in zip(his["paras"], mine["paras"]):
        if hp["algn"] != mp["algn"]:
            lines.append(f"alignment {mp['algn']} -> {hp['algn']}")
        if [c for c, _ in hp["chars"]] != [c for c, _ in mp["chars"]]:
            continue  # same words, different characters (spacing): the text diff covers it
        i, n = 0, len(hp["chars"])
        while i < n:
            if hp["chars"][i][1] == mp["chars"][i][1]:
                i += 1
                continue
            j = i
            while j < n and (hp["chars"][j][1], mp["chars"][j][1]) == (
                hp["chars"][i][1],
                mp["chars"][i][1],
            ):
                j += 1
            seg = "".join(c for c, _ in hp["chars"][i:j]).strip()
            if seg:
                short = seg if len(seg) <= 60 else seg[:57] + "..."
                lines.append(f'"{short}": {_describe(mp["chars"][i][1], hp["chars"][i][1])}')
            i = j
    hg, mg = his["geom"], mine["geom"]
    if hg and mg and any(abs(a - b) > MOVE_TOL for a, b in zip(hg, mg)):
        lines.append(f"box x,y,w,h {mg} -> {hg} in")
    if his["fill"] != mine["fill"]:
        lines.append(f"fill {mine['fill'] or 'none'} -> {his['fill'] or 'none'}")
    return lines


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
    for i, (h, m) in enumerate(zip_longest(his, mine, fillvalue={}), 1):
        text_rows = [
            (k, h[k]["text"], m[k]["text"] if k in m else None)
            for k in h
            if k not in m or h[k]["text"] != m[k]["text"]
        ]
        text_rows += [(k, None, m[k]["text"]) for k in m if k not in h]
        fmt_rows = [
            (k, format_changes(h[k], m[k])) for k in h if k in m and h[k]["text"] == m[k]["text"]
        ]
        fmt_rows = [(k, f) for k, f in fmt_rows if f]
        # a retyped box can also be restyled or moved: report that beside the text
        for k, a, b in text_rows:
            if a is not None and b is not None:
                extra = [ln for ln in format_changes(h[k], m[k]) if not ln.startswith('"')]
                if extra:
                    fmt_rows.append((k, extra))
        if not text_rows and not fmt_rows:
            continue
        changed += len(text_rows) + len(fmt_rows)
        print(
            f"-- slide {i}: {len(text_rows)} text change(s), {len(fmt_rows)} formatting change(s)"
        )
        for k, a, b in text_rows:
            print(f"   {k}")
            print(f"     his  : {a if a is not None else '<absent>'}")
            print(f"     build: {b if b is not None else '<absent>'}")
        for k, lines in fmt_rows:
            print(f"   {k} (formatting, build -> his)")
            for ln in lines:
                print(f"     {ln}")
    if not changed:
        print("no hand edits: every text box matches the build, text and formatting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
