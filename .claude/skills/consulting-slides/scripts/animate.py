#!/usr/bin/env python3
"""Add click-to-build animation to one slide of a .pptx built with bcg_style.js.

Usage: python3 animate.py deck.pptx --slide 5 --prefix b3s --steps 4

Every shape on that slide whose name starts with "<prefix><k>" (k = 1..steps) gets an
Appear entrance effect; all shapes of step k appear together on the k-th click, and
earlier steps stay visible. Shapes are named from the build script with pptxgenjs's
`objectName` option. The slide's existing timing is replaced. Standard library only;
the file is rewritten in place and a copy of the original is left beside it as
<deck>.pre-anim.pptx the first time this runs.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path


def build_timing(steps: list[list[str]]) -> str:
    nid = [1]

    def n() -> int:
        nid[0] += 1
        return nid[0]

    clicks = []
    for spids in steps:
        effects = []
        for i, spid in enumerate(spids):
            kind = "clickEffect" if i == 0 else "withEffect"
            effects.append(
                f'<p:par><p:cTn id="{n()}" presetID="1" presetClass="entr" presetSubtype="0" '
                f'fill="hold" grpId="0" nodeType="{kind}">'
                '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
                f'<p:childTnLst><p:set><p:cBhvr><p:cTn id="{n()}" dur="1" fill="hold">'
                '<p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>'
                f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl>'
                "<p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst>"
                '</p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set>'
                "</p:childTnLst></p:cTn></p:par>"
            )
        clicks.append(
            f'<p:par><p:cTn id="{n()}" fill="hold">'
            '<p:stCondLst><p:cond delay="indefinite"/></p:stCondLst>'
            f'<p:childTnLst><p:par><p:cTn id="{n()}" fill="hold">'
            '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
            f"<p:childTnLst>{''.join(effects)}</p:childTnLst>"
            "</p:cTn></p:par></p:childTnLst></p:cTn></p:par>"
        )
    builds = "".join(f'<p:bldP spid="{sp}" grpId="0" animBg="1"/>' for st in steps for sp in st)
    return (
        "<p:timing><p:tnLst><p:par>"
        '<p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">'
        '<p:childTnLst><p:seq concurrent="1" nextAc="seek">'
        f'<p:cTn id="{n()}" dur="indefinite" nodeType="mainSeq">'
        f"<p:childTnLst>{''.join(clicks)}</p:childTnLst></p:cTn>"
        '<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond>'
        "</p:prevCondLst>"
        '<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond>'
        "</p:nextCondLst>"
        "</p:seq></p:childTnLst></p:cTn></p:par></p:tnLst>"
        f"<p:bldLst>{builds}</p:bldLst></p:timing>"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deck")
    ap.add_argument("--slide", type=int, required=True, help="1-based slide number in file order")
    ap.add_argument(
        "--prefix", required=True, help="objectName prefix; step k shapes are named <prefix><k>..."
    )
    ap.add_argument("--steps", type=int, required=True)
    a = ap.parse_args()
    deck = Path(a.deck)
    part = f"ppt/slides/slide{a.slide}.xml"
    backup = deck.with_suffix(".pre-anim.pptx")
    if not backup.exists():
        shutil.copy2(deck, backup)

    with zipfile.ZipFile(deck) as z:
        names = z.namelist()
        if part not in names:
            print(f"no {part} in {deck}", file=sys.stderr)
            return 1
        data = {nm: z.read(nm) for nm in names}
        infos = {nm: z.getinfo(nm) for nm in names}

    xml = data[part].decode("utf-8")
    steps: list[list[str]] = []
    for k in range(1, a.steps + 1):
        ids = re.findall(rf'<p:cNvPr id="(\d+)" name="{re.escape(a.prefix)}{k}[^"]*"', xml)
        if not ids:
            print(f"step {k}: no shapes named {a.prefix}{k}* on slide {a.slide}", file=sys.stderr)
            return 1
        steps.append(ids)
    xml = re.sub(r"<p:timing>.*?</p:timing>", "", xml, flags=re.S)
    if "</p:sld>" not in xml:
        print("unexpected slide xml", file=sys.stderr)
        return 1
    xml = xml.replace("</p:sld>", build_timing(steps) + "</p:sld>")
    data[part] = xml.encode("utf-8")

    tmp = deck.with_suffix(".anim-tmp.pptx")
    with zipfile.ZipFile(tmp, "w") as out:
        for nm in names:
            info = infos[nm]
            out.writestr(
                zipfile.ZipInfo(nm, date_time=info.date_time),
                data[nm],
                compress_type=zipfile.ZIP_DEFLATED,
            )
    tmp.replace(deck)
    summary = ", ".join(f"click {k + 1}: {len(st)} shapes" for k, st in enumerate(steps))
    print(f"animated slide {a.slide}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
