#!/usr/bin/env python3
"""List the feedback stickies in a .pptx: yellow text boxes the reviewer dropped on slides.

Usage: python3 stickies.py deck.pptx [--color FFFF00] [--all]

Prints one block per sticky with the slide number (presentation order), the shape's
position in inches (so you can tell which element it points at) and its text. Nitin's
stickies are solid-yellow (FFFF00) text boxes; pass --color for another fill, or --all
to dump every text box with its fill so you can find the convention in use. Standard
library only.
"""

from __future__ import annotations

import html
import re
import sys
import zipfile

EMU = 914400


def slide_paths(z: zipfile.ZipFile) -> list[str]:
    pres = z.read("ppt/presentation.xml").decode("utf-8", "replace")
    rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8", "replace")
    rid2target: dict[str, str] = {}
    for m in re.finditer(r"<Relationship [^>]*>", rels):
        tag = m.group(0)
        rid = re.search(r'Id="([^"]+)"', tag)
        target = re.search(r'Target="([^"]+)"', tag)
        if rid and target:
            rid2target[rid.group(1)] = target.group(1)
    order = [rid2target[r] for r in re.findall(r'<p:sldId [^>]*r:id="(rId\d+)"', pres)]
    return [t if t.startswith("ppt/") else "ppt/" + t for t in order]


def shapes(xml: str):
    for sp in re.findall(r"<p:sp>.*?</p:sp>", xml, flags=re.S):
        name = re.search(r'<p:cNvPr [^>]*name="([^"]*)"', sp)
        fill = re.search(
            r"<p:spPr>.*?<a:solidFill><a:srgbClr val=\"([0-9A-Fa-f]{6})\"", sp, flags=re.S
        )
        off = re.search(r'<a:off x="(\d+)" y="(\d+)"', sp)
        paras = []
        for p in re.findall(r"<a:p>.*?</a:p>|<a:p .*?</a:p>", sp, flags=re.S):
            text = "".join(
                html.unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", p, flags=re.S)
            ).strip()
            if text:
                paras.append(text)
        yield {
            "name": name.group(1) if name else "?",
            "fill": fill.group(1).upper() if fill else "",
            "x": int(off.group(1)) / EMU if off else None,
            "y": int(off.group(2)) / EMU if off else None,
            "text": paras,
        }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    path = argv[0]
    color = "FFFF00"
    show_all = "--all" in argv
    if "--color" in argv:
        color = argv[argv.index("--color") + 1].upper()
    z = zipfile.ZipFile(path)
    paths = slide_paths(z)
    print(f"{path}: {len(paths)} slides")
    found = 0
    for i, sp_path in enumerate(paths, 1):
        xml = z.read(sp_path).decode("utf-8", "replace")
        for sh in shapes(xml):
            if not sh["text"]:
                continue
            if show_all or sh["fill"] == color:
                found += 1
                pos = f"at ({sh['x']:.1f}, {sh['y']:.1f}) in" if sh["x"] is not None else ""
                print(f"\n-- slide {i} | {sh['name']} | fill {sh['fill'] or 'none'} {pos}")
                for t in sh["text"]:
                    print("   " + t)
    if not found:
        print(f"\nno text boxes with fill {color}; try --all to see every text box and its fill")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
