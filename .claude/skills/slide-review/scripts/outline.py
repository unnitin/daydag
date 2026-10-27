#!/usr/bin/env python3
"""Read a .pptx as an argument: every slide's title, body, exhibit data, sources and notes.

Usage: python3 outline.py deck.pptx [--titles | --numbers | --json] [--summary N]
                                      [--today YYYY-MM-DD]

  (default)   every slide: title, body in reading order, table and chart data, source
              lines, speaker notes, his stickies, and the mechanical flags
  --titles    the titles alone, in order: the horizontal read
  --numbers   every figure (money, percentages, multiples, ranges kept whole) with the
              slides it appears on, then the bare counts; dates and #ids are left out.
              Slides after the appendix divider are marked "a"
  --json      the default view as JSON
  --summary N the executive summary is slide N; by default it is the first slide titled
              like one, else the first slide after the cover, marked "presumed"
  --today D   the date past-due checks compare against (default: today)

The title is the slide's title placeholder when it has one; otherwise the largest type
in the top band (above 1.4 in), which is where the house build puts it; failing that the
largest type on the slide, which catches a claim carried in a green left panel. Each
slide says which rule picked its title, so a wrong pick is visible.

The appendix starts at the first slide after the opening that reads as a divider (a page
of under 40 words with a line that starts "Appendix", "Backup" or "Annex"), else at the
first page tagged "A1". The decisions or next-steps page is looked for in the main flow
first. Table cells that hold only a fill colour print as [fill RRGGBB].

Yellow (FFFF00) text boxes are his stickies, not content: they are listed apart and left
out of the words, numbers and flags. Flags are heuristics worded as questions for the
reviewer, never verdicts. Standard library only, like the consulting-slides scripts.
"""

from __future__ import annotations

import html
import json
import re
import sys
import zipfile
from datetime import date
from posixpath import dirname, join, normpath

EMU = 914400
STICKY = "FFFF00"
TOP_BAND = 1.4  # inches; the house title sits at y 0.35, h 0.9
SKIP_PH = {"sldNum", "dt", "ftr"}
TOPIC_WORDS = {
    "overview",
    "background",
    "agenda",
    "approach",
    "summary",
    "introduction",
    "context",
    "status",
    "update",
    "appendix",
    "methodology",
    "findings",
    "results",
    "analysis",
    "options",
    "recommendations",
    "next",
    "timeline",
    "roadmap",
    "team",
    "scope",
}
HEDGES = re.compile(
    r"\b(may|might|could|potentially|possibly|perhaps|arguably|somewhat|fairly|relatively|"
    r"we believe|we think|it seems|appears to|seems to|likely)\b",
    re.I,
)
SOURCE_LINE = re.compile(r"^\s*(sources?|notes?|footnotes?)\b\s*[:.]", re.I)
UNIT = r"(?:%|x\b|\u00d7|pp\b|k\b|K\b|M\b|B\b|bn\b)"
ONE = r"[$€£]?\d(?:[\d,]*\d)?(?:\.\d+)?"
# one figure, or a range kept whole ("$29-48k", "20-35%", "2 to 4x"); not after # or /
NUMBER = re.compile(
    rf"(?<![\w.#/\-])[~≈<>+\-]?\s?{ONE}(?:\s?{UNIT})?(?:\s?(?:-|\u2013|to)\s?{ONE}(?:\s?{UNIT})?)?"
)
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
DATE = re.compile(rf"\b(\d{{1,2}})\s({MONTH})|\b({MONTH})\s(\d{{1,2}})\b(?!\d)", re.I)
DUE_HEADER = re.compile(r"\b(due|when|date|by|deadline|target)\b", re.I)
APPENDIX = re.compile(r"^\s*(appendix|backup|annex)\b", re.I)
SUMMARY_TITLE = re.compile(r"summary|at a glance|in brief|key messages|the answer", re.I)
CLOSE_TITLE = re.compile(r"next step|decision|we ask|asks?\b|what we need|owners?", re.I)
TODAY = date.today()


def text_of(fragment: str) -> list[str]:
    paras = []
    for p in re.findall(r"<a:p>.*?</a:p>|<a:p .*?</a:p>", fragment, flags=re.S):
        p = re.sub(r"<a:br\b[^>]*/>|<a:br\b.*?</a:br>", "<a:t> </a:t>", p, flags=re.S)
        t = "".join(html.unescape(x) for x in re.findall(r"<a:t>(.*?)</a:t>", p, flags=re.S))
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            paras.append(t)
    return paras


def rels_of(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    path = join(dirname(part), "_rels", part.rsplit("/", 1)[1] + ".rels")
    if path not in z.namelist():
        return {}
    xml = z.read(path).decode("utf-8", "replace")
    out = {}
    for m in re.finditer(r"<Relationship [^>]*>", xml):
        rid = re.search(r'Id="([^"]+)"', m.group(0))
        target = re.search(r'Target="([^"]+)"', m.group(0))
        if rid and target:
            out[rid.group(1)] = normpath(join(dirname(part), target.group(1)))
    return out


def slide_paths(z: zipfile.ZipFile) -> list[str]:
    pres = z.read("ppt/presentation.xml").decode("utf-8", "replace")
    rid2 = rels_of(z, "ppt/presentation.xml")
    return [rid2[r] for r in re.findall(r'<p:sldId [^>]*r:id="(rId\d+)"', pres)]


def position(xml: str) -> tuple[float, float, float, float]:
    off = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"', xml)
    ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', xml)
    x, y = (int(off.group(1)) / EMU, int(off.group(2)) / EMU) if off else (0.0, 0.0)
    w, h = (int(ext.group(1)) / EMU, int(ext.group(2)) / EMU) if ext else (0.0, 0.0)
    return round(x, 2), round(y, 2), round(w, 2), round(h, 2)


def text_shapes(xml: str) -> list[dict]:
    out = []
    for sp in re.findall(r"<p:sp>.*?</p:sp>", xml, flags=re.S):
        ph = re.search(r"<p:ph\b([^>]*)/?>", sp)
        ph_type = None
        if ph:
            t = re.search(r'type="([^"]+)"', ph.group(1))
            ph_type = t.group(1) if t else "body"
        if ph_type in SKIP_PH:
            continue
        paras = text_of(sp)
        if not paras:
            continue
        sppr = re.search(r"<p:spPr[^>]*>(.*?)</p:spPr>", sp, flags=re.S)
        fill = re.search(
            r'<a:solidFill><a:srgbClr val="([0-9A-Fa-f]{6})"', sppr.group(1) if sppr else ""
        )
        sizes = [int(s) / 100 for s in re.findall(r'<a:(?:rPr|endParaRPr)[^>]*\bsz="(\d+)"', sp)]
        x, y, w, h = position(sp)
        out.append(
            {
                "ph": ph_type,
                "fill": fill.group(1).upper() if fill else "",
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "size": max(sizes) if sizes else 0.0,
                "text": paras,
            }
        )
    return out


def chart_of(z: zipfile.ZipFile, part: str) -> dict:
    xml = z.read(part).decode("utf-8", "replace")
    kind = re.search(r"<c:(bar|line|pie|doughnut|area|scatter|bubble|radar)Chart>", xml)
    bar_dir = re.search(r'<c:barDir val="(\w+)"', xml)
    series = []
    for ser in re.findall(r"<c:ser>.*?</c:ser>", xml, flags=re.S):
        name = re.search(r"<c:tx>.*?<c:v>(.*?)</c:v>", ser, flags=re.S)
        cat = re.search(r"<c:(?:cat|xVal)>(.*?)</c:(?:cat|xVal)>", ser, flags=re.S)
        val = re.search(r"<c:(?:val|yVal)>(.*?)</c:(?:val|yVal)>", ser, flags=re.S)
        cats = re.findall(r"<c:v>(.*?)</c:v>", cat.group(1), flags=re.S) if cat else []
        vals = re.findall(r"<c:v>(.*?)</c:v>", val.group(1), flags=re.S) if val else []
        series.append(
            {
                "name": html.unescape(name.group(1)) if name else "",
                "points": [
                    [html.unescape(cats[i]) if i < len(cats) else "", v] for i, v in enumerate(vals)
                ],
            }
        )
    title = (
        text_of(re.search(r"<c:title>.*?</c:title>", xml, flags=re.S).group(0))
        if "<c:title>" in xml
        else []
    )
    label = (kind.group(1) if kind else "chart") + (f" ({bar_dir.group(1)})" if bar_dir else "")
    return {"kind": label, "title": " ".join(title), "series": series}


def cell_text(tc: str) -> str:
    text = " / ".join(text_of(tc))
    if text:
        return text
    tcpr = re.search(r"<a:tcPr\b.*?</a:tcPr>", tc, re.S)
    # borders (lnL, lnR, lnT, lnB, diagonals) carry their own solidFill; the cell's comes after
    own = re.sub(r"<a:ln\w*\b.*?</a:ln\w*>", "", tcpr.group(0) if tcpr else "", flags=re.S)
    fill = re.search(r'<a:solidFill><a:srgbClr val="([0-9A-Fa-f]{6})"', own)
    return f"[fill {fill.group(1).upper()}]" if fill and fill.group(1).upper() != "FFFFFF" else ""


def frames(z: zipfile.ZipFile, part: str, xml: str) -> tuple[list[dict], list[dict]]:
    rels = rels_of(z, part)
    tables, charts = [], []
    for gf in re.findall(r"<p:graphicFrame>.*?</p:graphicFrame>", xml, flags=re.S):
        x, y, _, _ = position(gf)
        if "<a:tbl>" in gf:
            rows = []
            for tr in re.findall(r"<a:tr\b.*?</a:tr>", gf, flags=re.S):
                cells = re.findall(r"<a:tc\b.*?</a:tc>", tr, flags=re.S)
                rows.append([cell_text(c) for c in cells])
            tables.append({"x": x, "y": y, "rows": rows})
        ref = re.search(r'<c:chart [^>]*r:id="([^"]+)"', gf)
        if ref and ref.group(1) in rels and rels[ref.group(1)] in z.namelist():
            charts.append(dict(chart_of(z, rels[ref.group(1)]), x=x, y=y))
    return tables, charts


def notes_of(z: zipfile.ZipFile, part: str) -> list[str]:
    for target in rels_of(z, part).values():
        if "notesSlide" in target and target in z.namelist():
            xml = z.read(target).decode("utf-8", "replace")
            out = []
            for sp in re.findall(r"<p:sp>.*?</p:sp>", xml, flags=re.S):
                if re.search(r'<p:ph [^>]*type="body"', sp):
                    out += text_of(sp)
            return out
    return []


def pick_title(shapes: list[dict]) -> tuple[dict | None, str]:
    for s in shapes:
        if s["ph"] in {"title", "ctrTitle"}:
            return s, "placeholder"
    # an eyebrow or tracker in the top band is small type; a title is 16 pt or more (or inherits)
    top = [
        s
        for s in shapes
        if s["y"] < TOP_BAND
        and not SOURCE_LINE.match(s["text"][0])
        and (s["size"] == 0 or s["size"] >= 16)
    ]
    if top:
        return max(top, key=lambda s: (s["size"], -s["y"])), "top band"
    if shapes:
        return max(shapes, key=lambda s: (s["size"], -s["y"])), "largest type"
    return None, "none"


def figures(line: str) -> tuple[list[str], list[str]]:
    """(figures with a currency or unit, bare counts); dates, years and step numbers out."""
    real, bare = [], []
    dates = [m.span() for m in DATE.finditer(line)]
    for m in NUMBER.finditer(line):
        if any(m.start() < b and m.end() > a for a, b in dates):  # part of a date
            continue
        f = m.group(0).strip()
        if re.fullmatch(r"[~≈<>+\-]?\s?(19|20)\d\d", f):  # a year
            continue
        if re.fullmatch(r"\d{1,2}", f) and line.strip() == f:  # a numbered circle or step
            continue
        (real if re.search(rf"[$€£]|{UNIT}", f) else bare).append(f)
    return real, bare


def past_due(text: str, today: date) -> list[str]:
    out = []
    for m in DATE.finditer(text):
        day = m.group(1) or m.group(4)
        mon = (m.group(2) or m.group(3))[:3].lower()
        try:
            when = date(today.year, MONTHS.index(mon) + 1, int(day))
        except ValueError:
            continue
        if when < today and today.month - when.month < 6:
            out.append(m.group(0))
    return out


def norm(f: str) -> str:
    return re.sub(r"[\s,~≈]", "", f).lower().replace("\u00d7", "x")


def appendix_start(slides: list[dict]) -> int | None:
    for s in slides[2:]:
        lines = [s["title"], *s["body"]]
        if s["words"] < 40 and any(APPENDIX.match(t) for t in lines):
            return s["n"]
    for s in slides[2:]:
        if any(t.strip() == "A1" for t in s["body"]):
            return s["n"]
    return None


def read(path: str) -> list[dict]:
    z = zipfile.ZipFile(path)
    slides = []
    for n, part in enumerate(slide_paths(z), 1):
        xml = z.read(part).decode("utf-8", "replace")
        shapes = text_shapes(xml)
        stickies = [s for s in shapes if s["fill"] == STICKY]
        content = [s for s in shapes if s["fill"] != STICKY]
        title_shape, how = pick_title(content)
        body, sources = [], []
        for s in content:  # document order: the build order, which beats position for columns
            if s is title_shape:
                continue
            for t in s["text"]:
                (sources if SOURCE_LINE.match(t) else body).append(t)
        tables, charts = frames(z, part, xml)
        table_text = [c for t in tables for r in t["rows"] for c in r if c]
        title = " ".join(title_shape["text"]) if title_shape else ""
        stated = [title, *body, *table_text]
        slides.append(
            {
                "n": n,
                "title": title,
                "title_from": how,
                "body": body,
                "tables": tables,
                "charts": charts,
                "sources": sources,
                "notes": notes_of(z, part),
                "stickies": [
                    {"x": s["x"], "y": s["y"], "text": " / ".join(s["text"])} for s in stickies
                ],
                "words": sum(len(t.split()) for t in stated),
                "figures": [[f, t] for t in stated for f in figures(t)[0]],
                "counts": [[f, t] for t in stated for f in figures(t)[1]],
            }
        )
    return slides


def flag(slides: list[dict], today: date) -> None:
    for s in slides:
        f, t = [], s["title"]
        words = t.split()
        if not t:
            f.append("no title found: is the claim anywhere on the page?")
        if len(words) > 15:
            f.append(f"title is {len(words)} words: two lines at most?")
        if re.search(r"\s(and|&)\s", t, re.I):
            f.append('"and" in the title: two messages?')
        generic = words and words[0].lower().strip(":") in TOPIC_WORDS
        if t and ((len(words) <= 5 and not re.search(r"\d", t)) or generic):
            f.append("short or generic title: a topic label rather than a claim?")
        if t.endswith("?"):
            f.append("question title: is the answer on the slide?")
        if s["words"] > 120:
            f.append(f"{s['words']} words: a text wall, or two slides?")
        exhibits = len(s["tables"]) + len(s["charts"])
        if exhibits > 1:
            f.append(f"{exhibits} tables/charts: one exhibit per slide?")
        if (s["figures"] or s["charts"]) and not s["sources"]:
            f.append("figures and no Source line: where do they come from?")
        if s["charts"] and not re.search(r"\d", t):
            f.append("chart under a title with no figure: does the title say what the chart shows?")
        hedges = sorted({m.group(0).lower() for x in [t, *s["body"]] for m in HEDGES.finditer(x)})
        if hedges:
            f.append("hedges (" + ", ".join(hedges) + "): say it or cut it?")
        fills = sum(c.startswith("[fill ") for tb in s["tables"] for r in tb["rows"] for c in r)
        if fills:
            f.append(f"{fills} table cells carry only a colour: status by colour alone?")
        due = []
        for tb in s["tables"]:
            head = tb["rows"][0] if tb["rows"] else []
            cols = [i for i, c in enumerate(head) if DUE_HEADER.search(c)]
            for r in tb["rows"][1:]:
                due += [d for i in cols if i < len(r) for d in past_due(r[i], today)]
        if due:
            f.append(
                f"dates in a due column already past on {today}: {', '.join(due)}; still true?"
            )
        s["flags"] = f


def words_of(row: list[str]) -> set[str]:
    return set(re.findall(r"[a-z]{4,}", " ".join(row).casefold()))


def shared_tables(slides: list[dict]) -> list[str]:
    """Tables on two slides whose rows match by wording: the same list told twice."""
    tables = [(s["n"], [words_of(r) for r in tb["rows"][1:]]) for s in slides for tb in s["tables"]]
    out = []
    for i, (n1, a) in enumerate(tables):
        for n2, b in tables[i + 1 :]:
            if n1 == n2:
                continue
            matched = sum(any(len(ra & rb) >= 3 for rb in b) for ra in a)
            if matched >= 3:
                out.append(
                    f"tables on slides {n1} and {n2} share about {matched} rows ({len(a)} and"
                    f" {len(b)} in all): one list or two, and do owners, dates and status agree?"
                )
    return out


def deck_view(slides: list[dict], summary_n: int | None = None) -> dict:
    how = "given"
    summary = next((s for s in slides if s["n"] == summary_n), None)
    if not summary:
        how = "by title"
        summary = next((s for s in slides if SUMMARY_TITLE.search(s["title"])), None)
    if not summary and len(slides) > 3:
        how, summary = "presumed", slides[1]
    start = appendix_start(slides)
    main = [s for s in slides if not start or s["n"] < start]
    close = next((s for s in reversed(main) if CLOSE_TITLE.search(s["title"])), None)
    close = close or next((s for s in reversed(slides) if CLOSE_TITLE.search(s["title"])), None)
    seen: dict[str, list[int]] = {}
    for s in slides:
        for f, _ in s["figures"]:
            seen.setdefault(norm(f), [])
            if s["n"] not in seen[norm(f)]:
                seen[norm(f)].append(s["n"])
    orphan = []
    if summary:
        for f, ctx in summary["figures"]:
            if seen.get(norm(f)) == [summary["n"]]:
                orphan.append([f, ctx])
    titles = [s["title"] for s in slides if s["title"]]
    dupes = sorted({t for t in titles if titles.count(t) > 1})
    questions = sum(t.endswith("?") for t in titles)
    notes = []
    if start:
        notes.append(f"appendix from slide {start}: the main flow is slides 1 to {start - 1}")
    if close and start and close["n"] >= start:
        notes.append(
            f"the only decisions or next-steps page is in the appendix (slide {close['n']})"
        )
    notes += shared_tables(slides)
    if how == "presumed":
        notes.append(
            f"no slide is titled as a summary; slide {summary['n']} is presumed to carry the answer"
            " (pass --summary N if another does): does the reader get the answer up front?"
        )
    if not close:
        notes.append("no decisions or next-steps slide found by title: who does what by when?")
    if dupes:
        notes.append("titles used twice: " + "; ".join(dupes))
    if 0 < questions < len(titles) - 2:
        notes.append(f"{questions} question titles among statements: one grammar across the deck?")
    if orphan:
        notes.append(
            "summary figures no other slide carries (or writes differently): "
            + ", ".join(f for f, _ in orphan)
        )
    return {
        "appendix": start,
        "summary": summary["n"] if summary else None,
        "summary_from": how if summary else None,
        "close": close["n"] if close else None,
        "figures": seen,
        "summary_orphans": orphan,
        "notes": notes,
    }


def show(path: str, slides: list[dict], mode: str, summary_n: int | None = None) -> None:
    deck = deck_view(slides, summary_n)
    start = deck["appendix"]

    def tag(n: int) -> str:
        return f"{n}a" if start and n >= start else str(n)

    if mode == "json":
        print(
            json.dumps(
                {"deck": path, "slides": slides, "overall": deck}, indent=1, ensure_ascii=False
            )
        )
        return
    print(f"# {path}: {len(slides)} slides")
    if mode == "titles":
        for s in slides:
            print(f"{tag(s['n']):>4}  {s['title'] or '(no title)'}")
        for n in deck["notes"]:
            print(f"  ? {n}")
        return
    if mode == "numbers":
        where = {s["n"]: s for s in slides}
        for key, ns in sorted(deck["figures"].items(), key=lambda kv: kv[1][0]):
            ctx = next(t for f, t in where[ns[0]]["figures"] if norm(f) == key)
            print(f"{key:>12}  slides {', '.join(map(tag, ns)):<14} {ctx[:90]}")
        print("\ncounts and other bare numbers, by slide:")
        for s in slides:
            if s["counts"]:
                print(f"{tag(s['n']):>4}  " + ", ".join(dict.fromkeys(f for f, _ in s["counts"])))
        if deck["summary"]:
            print(f"\nexecutive summary: slide {deck['summary']} ({deck['summary_from']})")
            for f, ctx in deck["summary_orphans"]:
                print(f"  ? {f} appears only on the summary: {ctx[:90]}")
        return
    found = deck["summary_from"] or "deck too short"
    print(
        f"executive summary: {deck['summary'] or 'none'} ({found}) · "
        f"decisions / next steps: {deck['close'] or 'none found by title'}"
    )
    for n in deck["notes"]:
        print(f"  ? {n}")
    for s in slides:
        print(f"\n## {tag(s['n'])} · {s['title'] or '(no title)'}")
        size = f"{len(s['title'].split())} words; {s['words']} words on the slide"
        print(f"   [title from {s['title_from']}, {size}]")
        for t in s["body"]:
            print(f"   - {t}")
        for t in s["tables"]:
            print(f"   table, {len(t['rows'])} rows:")
            for r in t["rows"]:
                print("     | " + " | ".join(r))
        for c in s["charts"]:
            print(f"   chart, {c['kind']}{': ' + c['title'] if c['title'] else ''}")
            for ser in c["series"]:
                pts = ", ".join(f"{a}={b}" if a else b for a, b in ser["points"][:24])
                print(f"     {ser['name'] or 'series'}: {pts}")
        for t in s["sources"]:
            print(f"   source: {t}")
        for t in s["notes"]:
            print(f"   notes: {t}")
        for st in s["stickies"]:
            print(f"   STICKY at ({st['x']}, {st['y']}): {st['text']}")
        for f in s["flags"]:
            print(f"   ? {f}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print(__doc__)
        sys.exit(1)
    mode = next((a[2:] for a in args[1:] if a in {"--titles", "--numbers", "--json"}), "full")
    summary_n = int(args[args.index("--summary") + 1]) if "--summary" in args else None
    today = date.fromisoformat(args[args.index("--today") + 1]) if "--today" in args else TODAY
    slides = read(args[0])
    flag(slides, today)
    show(args[0], slides, mode, summary_n)
