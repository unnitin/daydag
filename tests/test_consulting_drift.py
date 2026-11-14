"""drift.py reports his formatting and position edits, not only retyped text (10 Nov 2026:
a recoloured impact number was lost because only text was compared)."""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude/skills/consulting-slides/scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("drift", SCRIPTS / "drift.py")
drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drift)

PRES = (
    '<p:presentation xmlns:p="p" xmlns:r="r"><p:sldIdLst>'
    '<p:sldId id="256" r:id="rId2"/></p:sldIdLst></p:presentation>'
)
RELS = (
    '<Relationships><Relationship Id="rId2" Type="slide" Target="slides/slide1.xml"/>'
    "</Relationships>"
)


def _box(name: str, text: str, rpr: str, off=(914400, 914400), ext=(1828800, 457200)) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="{name}"/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{off[0]}" y="{off[1]}"/>'
        f'<a:ext cx="{ext[0]}" cy="{ext[1]}"/></a:xfrm></p:spPr>'
        f'<p:txBody><a:bodyPr/><a:p><a:pPr algn="l"/>'
        f"<a:r>{rpr}<a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def _deck(tmp: Path, name: str, *boxes: str) -> str:
    path = tmp / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", PRES)
        z.writestr("ppt/_rels/presentation.xml.rels", RELS)
        z.writestr(
            "ppt/slides/slide1.xml",
            "<p:sld><p:cSld><p:spTree>" + "".join(boxes) + "</p:spTree></p:cSld></p:sld>",
        )
    return str(path)


BRIGHT = (
    '<a:rPr lang="en-US" sz="1800" b="1" dirty="0">'
    '<a:solidFill><a:srgbClr val="29BA74"/></a:solidFill></a:rPr>'
)
DARK = (
    '<a:rPr lang="en-US" b="1"><a:solidFill><a:srgbClr val="197A56"/></a:solidFill>'
    '<a:latin typeface="Trebuchet MS"/></a:rPr>'
)


def _changes(his: str, mine: str) -> list[str]:
    h, m = drift.slides(his), drift.slides(mine)
    return drift.format_changes(h[0]["Text 1"], m[0]["Text 1"])


def test_a_recoloured_number_is_reported(tmp_path):
    mine = _deck(tmp_path, "build.pptx", _box("Text 1", "4-18%", BRIGHT))
    his = _deck(tmp_path, "his.pptx", _box("Text 1", "4-18%", DARK))
    out = _changes(his, mine)
    assert len(out) == 1
    assert "colour 29BA74 -> 197A56" in out[0] and "size 18 -> inherit" in out[0]


def test_a_plain_resave_is_not_reported(tmp_path):
    # PowerPoint reorders attributes, self-closes tags and adds font slots: not an edit
    resaved = (
        '<a:rPr b="1" sz="1800" lang="en-US"><a:solidFill><a:srgbClr val="29BA74"/>'
        '</a:solidFill><a:ea typeface="X"/></a:rPr>'
    )
    mine = _deck(tmp_path, "build.pptx", _box("Text 1", "4-18%", BRIGHT))
    his = _deck(tmp_path, "his.pptx", _box("Text 1", "4-18%", resaved))
    assert _changes(his, mine) == []


def test_a_moved_box_is_reported_and_a_nudge_under_tolerance_is_not(tmp_path):
    mine = _deck(tmp_path, "build.pptx", _box("Text 1", "Lead-in", BRIGHT))
    moved = _deck(tmp_path, "his.pptx", _box("Text 1", "Lead-in", BRIGHT, off=(914400, 1499616)))
    assert any(ln.startswith("box x,y,w,h") for ln in _changes(moved, mine))
    nudged = _deck(
        tmp_path, "his2.pptx", _box("Text 1", "Lead-in", BRIGHT, off=(914400, 914400 + 9144))
    )
    assert _changes(nudged, mine) == []


OPEN = (
    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Text 1"/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:p>'
)
CLOSE = "</a:p></p:txBody></p:sp>"


def test_only_the_restyled_words_are_named(tmp_path):
    two_runs = OPEN + f"<a:r>{BRIGHT}<a:t>Up to </a:t></a:r><a:r>{DARK}<a:t>18%</a:t></a:r>" + CLOSE
    one_run = OPEN + f"<a:r>{BRIGHT}<a:t>Up to 18%</a:t></a:r>" + CLOSE
    out = _changes(_deck(tmp_path, "his.pptx", two_runs), _deck(tmp_path, "build.pptx", one_run))
    assert len(out) == 1 and out[0].startswith('"18%"')
