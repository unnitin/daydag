#!/usr/bin/env python3
"""Print a one-line catalogue of every slide in a .pptx: position, text, picture count.

Usage: python3 catalog_template.py deck.pptx [--full]

Reads the package with the standard library only, so it runs on any Python 3. Slides
are listed in presentation order (the order in <p:sldIdLst>), which is what the
pattern library's slide numbers refer to. --full prints every paragraph.
"""

from __future__ import annotations

import html
import re
import sys
import zipfile


def main(path: str, full: bool = False) -> None:
    z = zipfile.ZipFile(path)
    names = z.namelist()
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

    size = re.search(r'<p:sldSz cx="(\d+)" cy="(\d+)"', pres)
    if size:
        w, h = (int(size.group(1)) / 914400, int(size.group(2)) / 914400)
        print(f"{path}: {len(order)} slides, {w:.2f} x {h:.2f} in")
    for theme in [n for n in names if re.match(r"ppt/theme/theme1\.xml$", n)]:
        x = z.read(theme).decode("utf-8", "replace")
        major = re.search(r'<a:majorFont><a:latin typeface="([^"]*)"', x)
        minor = re.search(r'<a:minorFont><a:latin typeface="([^"]*)"', x)
        colours = re.findall(
            r"<a:(dk1|lt1|dk2|lt2|accent\d)>.*?"
            r'(?:srgbClr val="([0-9A-Fa-f]{6})"|lastClr="([0-9A-Fa-f]{6})")',
            x,
            flags=re.S,
        )
        print(
            "theme fonts:",
            major.group(1) if major else "?",
            "/",
            minor.group(1) if minor else "?",
            "colours:",
            ", ".join(f"{a}={b or c}" for a, b, c in colours),
        )

    for i, target in enumerate(order, 1):
        name = target if target.startswith("ppt/") else "ppt/" + target
        x = z.read(name).decode("utf-8", "replace")
        paras = []
        for p in re.findall(r"<a:p>.*?</a:p>|<a:p .*?</a:p>", x, flags=re.S):
            text = "".join(
                html.unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", p, flags=re.S)
            ).strip()
            if text:
                paras.append(text)
        pics = len(re.findall(r"<p:pic>", x))
        if full:
            print(f"\n--- slide {i} ({pics} pictures) ---")
            for t in paras:
                print("  " + t)
        else:
            head = " | ".join(t[:60] for t in paras[:4])
            more = f" | +{len(paras) - 4}" if len(paras) > 4 else ""
            print(f"{i:4d} [{pics} pic] {head}{more}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], full="--full" in sys.argv[2:])
