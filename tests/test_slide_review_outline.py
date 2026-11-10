"""slide-review's outline flags an ask left in slide text: a who and a when in brackets.

consulting-slides puts asks in the reply and the chase list, never on the slide
(`references/detail-placement.md`); he cut nine of them by hand from two decks. A
placeholder for a value still due (`[Mon]`, `[owner]`) is the house convention and is
not an ask. The script lives in the skill, not the package, so it is loaded by path.
"""

import importlib.util
from datetime import date
from pathlib import Path

OUTLINE = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "slide-review" / "scripts"
) / "outline.py"
TODAY = date(2026, 11, 10)


def _load():
    spec = importlib.util.spec_from_file_location("slide_review_outline", OUTLINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


outline = _load()


def _slide(body=(), cells=(), sources=()):
    return {
        "n": 1,
        "title": "Pricing on the model frees up to 20% of each contract's capital",
        "body": list(body),
        "tables": [{"rows": [list(cells)]}] if cells else [],
        "charts": [],
        "sources": list(sources),
        "words": 40,
        "figures": [],
        "counts": [],
    }


def _ask_flags(slide):
    outline.flag([slide], TODAY)
    return [f for f in slide["flags"] if f.startswith("bracketed ask")]


def test_an_owner_and_a_date_in_brackets_is_flagged_with_the_bracket_quoted():
    flags = _ask_flags(_slide(body=["Next: baseline cycle time [ops lead, 4 Dec]; accuracy"]))
    assert len(flags) == 1
    assert "[ops lead, 4 Dec]" in flags[0]


def test_an_ask_is_flagged_by_its_verb_or_in_a_table_cell():
    assert _ask_flags(_slide(body=["Revenue: not yet sized [ask Finance]"]))
    assert _ask_flags(_slide(cells=["Owner", "Retire the old tables [date: VP-Data, 4 Dec]"]))
    assert _ask_flags(_slide(body=["Security review [security lead, late Jan]"]))


def test_a_placeholder_for_a_value_still_due_is_not_an_ask():
    slide = _slide(
        body=["Revenue last 12 months: [Mon]", "Due [by 4 Dec]", "Run by [owner]"],
        cells=["[fill BFE3CF]", "[Thu, 13 Nov]", "[$2.1M, estimate]"],
    )
    assert _ask_flags(slide) == []


def test_the_source_line_may_say_who_and_when():
    slide = _slide(sources=["Source: billing export [Finance, 4 Dec]; team analysis"])
    assert _ask_flags(slide) == []
