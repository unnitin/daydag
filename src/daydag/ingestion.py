"""The meeting-note ingestion classifier (SPEC 3.3) - five labels, bias to precision.

USING IT
    from daydag.ingestion import classify_items, as_predictions
    from daydag.evalset import load_items, score

    batch = classify_items(load_items())     # list[Classification], item order preserved
    result = score(as_predictions(batch))    # per-label precision/recall
    [c.item_id for c in batch if c.label is None]   # unplaced - surface, don't guess

CONTRACTS - break one and the guarantee is gone
    1. ``classify`` returns one of :data:`daydag.evalset.LABELS`, or ``None``.
       ``None`` means *unplaced* - a shape this classifier has no evidence for -
       never a sixth label and never a guess dressed up as one.
    2. Pure and total. No vault, no network, no connector client (this package
       has none but the git mirror) - a caller can call this from anywhere and
       it either returns a label or returns ``None``, never raises on a
       malformed item. A missing or unrecognised field abstains; it does not
       crash the sweep over the other nineteen items in the note.
    3. Precision over recall, per the bar in reference/ingestion-eval.md. Every
       rule below either fires on a structural feature this repo measured
       across 22 real notes, or on a short, specific multi-word phrase checked
       against the full 155-item eval set for collisions before it was kept.
       An item that matches nothing falls to ``noise`` (body) or
       ``assigned_ask`` (next_steps, per invariant below) rather than the rarer,
       costlier labels - see WHY IT EXISTS.
    4. Every ``next_steps`` item with a named owner - including the literal
       ``[The group]`` - is real work, never ``noise``, unless it is *also*
       ungrounded (no support anywhere in the note's body - the one shape
       reference/ingestion-eval.md calls "invented_next_step"). Dropping an
       unowned ask loses real work; inventing an owner is worse.
    5. This module classifies items **independently**. Two decisions in the
       same note that contradict each other (the ``reversed_decision`` hard
       case) each classify correctly on their own structural merits - but
       *noticing the contradiction* is a note-level comparison this module does
       not make. Per guardrail 4, that discrepancy is surfaced by whatever reads
       a note's items as a set, not resolved here and not silently dropped
       either. See KNOWN LIMIT.
    6. No write path. This decides what a note contains; #16 (blocked on
       decision #24) does the write-back. Nothing here touches
       ``daydag.vault`` or ``daydag.state``, and nothing here should start to.

WHY IT EXISTS
    Issue #14 read 22 real Gemini notes by hand and found SPEC 3.3 wrong on four
    points (reference/ingestion-eval.md): there is no ``Decisions`` section, so
    a decision is found by verb, in prose, not by heading. There is no
    first-person signal - Gemini never writes "I'll" - so modality carries what
    grammatical person would have. "Owner + what + when" is usually two of
    three: dates are rare and ``[The group]`` is common. And the note cannot
    say who *assigned* an ask, only who owns it - so a next-step is his
    commitment only when he is the named owner, not whenever he is mentioned.

    The asymmetry SPEC states directly: a wrongly-logged commitment costs more
    trust than a missed one, because the missed one still surfaces in Slack
    anyway. So every rule here is written to fire only where the eval set gives
    it clean support, and the fallback for everything else is the label that
    costs the least to get wrong.

KNOWN LIMIT
    - ``principal_commitment`` clears its precision floor on **6** examples
      (reference/ingestion-eval.md calls this "a thin basis for a 0.95
      precision claim" in so many words). The rule it clears on is structural,
      not fixture wording, but six counter-examples would still overturn it.
    - ``new_workstream`` recall leans on :data:`CREATION_LANGUAGE`, a short
      lexicon of multi-word creation phrases ("stand up", "parallel run",
      "build a/an", ...) read off the 22 notes. It will not recognise creation
      language phrased differently, and defaults such an item to ``noise``
      rather than guessing - measured recall is well above the 0.50 bar on this
      set, but the set is 9 examples and the lexicon will not generalise to
      every phrasing.
    - Reversed decisions (contract 5) are not detected as a pair here.
    - Sensitivity routing (SPEC guardrail 7) is not this module's job - the
      ``sensitive`` field passes through the input untouched for whatever reads
      the classification next.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from daydag.evalset import DECISION_MARKERS, MODALITIES, OWNER_FORMS

__all__ = [
    "CREATION_LANGUAGE",
    "Classification",
    "as_predictions",
    "classify",
    "classify_items",
    "unplaced",
]

#: The two note sections a labelled item can come from (evalset schema).
_SECTIONS = frozenset({"body", "next_steps"})

#: Owner forms that name someone, including the ungrouped ``[The group]``
#: (reference/ingestion-eval.md: 20 of 149 next steps carry this owner form,
#: and dropping it loses real work). ``none`` is deliberately excluded - a body
#: line with no owner at all names nobody to ask or chase.
_NAMED_OWNER = OWNER_FORMS - {"none"}

#: Owner forms specific enough to be *his*, jointly or alone.
_HIS_OWNER = frozenset({"principal", "named_people"})

#: "Move the bug fix to done on the board" is real, owned, and worth nothing on
#: a chase list, because the board already carries it (reference/ingestion-eval.md,
#: "Board hygiene is noise"). Two words, not one substring: "board" alone also
#: matches a real ask ("Get access to the board..."), so a status word must
#: also be present. Checked for collisions against the full 155-item eval set.
_BOARD_WORD = re.compile(r"\bboard\b", re.IGNORECASE)
_BOARD_STATUS_WORD = re.compile(r"\b(?:done|closed|resolved|complete|completed)\b", re.IGNORECASE)

#: Multi-word phrases that mark something being created wholesale - a system,
#: a programme, a parallel track - rather than a task against something that
#: already exists. Each phrase was checked individually against the full
#: 155-item eval set and matches only the ``new_workstream`` item(s) it was
#: read from; single words ("build", "workflow", "platform", "programme") were
#: tried and rejected because they also appear throughout ``noise``,
#: ``decision`` and ``assigned_ask`` items in this domain's ordinary vocabulary,
#: and firing on them would trade away precision on three other labels to buy
#: recall on the thinnest one.
CREATION_LANGUAGE = re.compile(
    r"\bstand(?:s|ing)?\s+up\b"  # "stand up a system of record"
    r"|\bparallel run\b"  # "a three-month parallel run"
    r"|\bstart building\b"  # "Start building the in-house module"
    r"|\bbring\b.{0,60}\b(?:programme|program)\b"  # "bring ... under a single programme"
    r"|\bbuild(?:ing)?\s+(?:a|an)\b"  # "Build an agent" (vs "Build the ...", a task)
    r"|\b(?:auto-)?creat(?:e|ing)\s+(?:a|an)\b",  # "auto-create a workspace"
    re.IGNORECASE,
)

#: "Principal asked the group to flag anything ..." - a delegation reported in
#: body prose rather than listed as a next step. Requires ``mentions_principal``
#: as well, so a body line about someone *else* asking a favour is not read as
#: his ask (the note cannot say who assigned an ask; his own mention is the one
#: case it can).
PRINCIPAL_DELEGATION = re.compile(r"\basked\b.{0,20}\bto\b", re.IGNORECASE)


def _text(item: Mapping[str, Any]) -> str:
    """The item's sentence, however the caller's field is named.

    The eval fixtures call it ``paraphrase`` because a public repo cannot hold
    real meeting text (reference/ingestion-eval.md); a live pipeline would call
    it something like ``text``. Both are read so this module is not coupled to
    which one a given caller happens to use.
    """
    return str(item.get("paraphrase") or item.get("text") or "")


def _is_board_hygiene(text: str) -> bool:
    return bool(_BOARD_WORD.search(text) and _BOARD_STATUS_WORD.search(text))


def classify(item: Mapping[str, Any]) -> str | None:
    """One of :data:`daydag.evalset.LABELS`, or ``None`` if unplaced.

    ``item`` follows the schema in ``tests/fixtures/ingestion/items.jsonl``:
    the output of an earlier parsing step, not raw meeting text. Only
    ``section`` and ``owner_form`` are read as required; every other field is
    read with a conservative default so a caller missing one does not crash -
    it just gets the more cautious answer. An unrecognised ``section`` or
    ``owner_form`` abstains (``None``) rather than guessing which of the two it
    is closer to.

    Args:
        item: at minimum ``{"section": ..., "owner_form": ...}``; see the
            fixture schema for the full optional set this reads
            (``mentions_principal``, ``modality``, ``decision_marker``,
            ``grounded_in_body``, and the item's text under ``paraphrase`` or
            ``text``).
    """
    section = item.get("section")
    owner_form = item.get("owner_form")
    if section not in _SECTIONS or owner_form not in OWNER_FORMS:
        return None  # an upstream shape this classifier has never seen

    mentions_principal = bool(item.get("mentions_principal", False))
    modality = item.get("modality")
    if modality is not None and modality not in MODALITIES:
        return None
    decision_marker = item.get("decision_marker")
    if decision_marker is not None and decision_marker not in DECISION_MARKERS:
        return None
    grounded = bool(item.get("grounded_in_body", True))
    text = _text(item)

    # The whole `Suggested next steps` section is model-written suggestion,
    # not transcript (reference/ingestion-eval.md). An unowned, ungrounded
    # step is the section inventing work nobody said - a named owner with no
    # body support can still be real (a next step the summariser chose not to
    # carry into the body), so this carve-out is narrower than "ungrounded".
    if section == "next_steps" and not grounded and owner_form == "the_group":
        return "noise"

    if _is_board_hygiene(text):
        return "noise"

    # Creation language overrides both the decision-marker rule below and the
    # next-steps default: "decided to bring X under one programme" opens a
    # workstream, it does not settle one, however much it reads like a decision
    # verb (reference/ingestion-eval.md, "Creating beats settling").
    if CREATION_LANGUAGE.search(text):
        return "new_workstream"

    # His own commitment: only a next-steps item, only imperative, only when
    # he is named among the owners. Body-section items naming him are always
    # something else - a status line about work already done, or (below) a
    # delegation reported in prose - never a next step, which is what makes
    # this rule precise rather than "mentions_principal, therefore his".
    if (
        section == "next_steps"
        and modality == "imperative"
        and mentions_principal
        and owner_form in _HIS_OWNER
    ):
        return "principal_commitment"

    # There is no `Decisions` section (reference/ingestion-eval.md) - a decision
    # lives in body prose and is found by verb. Two of 33 gold decisions carry
    # no marker at all; this rule does not catch those, by design, rather than
    # guessing from an unmarked declarative sentence.
    if section == "body" and decision_marker is not None:
        return "decision"

    if section == "body" and mentions_principal and PRINCIPAL_DELEGATION.search(text):
        return "assigned_ask"

    # Everything left in next_steps has a named owner (the OWNER_FORMS check
    # above already excluded "none" is impossible to reach here since
    # next_steps never carries it in the eval set - but the check stays
    # explicit rather than assumed) and no reason yet to call it anything but
    # what it looks like: a step someone owns.
    if section == "next_steps" and owner_form in _NAMED_OWNER:
        return "assigned_ask"

    # A next-steps item with no owner (owner_form == "none") is a shape absent
    # from the 155-item eval set - no basis to guess, so it is left unplaced.
    if section == "next_steps":
        return None

    # Body, no decision marker, no creation language, no delegation: measured
    # over the eval set this is noise 29 times out of 34 - a status update, a
    # completed task, someone else's future plan reported in passing
    # (reference/ingestion-eval.md, "Someone else's self-reported commitment is
    # noise"). The remaining cases are real misses, accepted per the bar: a
    # missed item still surfaces in Slack.
    return "noise"


@dataclass(frozen=True)
class Classification:
    """One item's verdict. ``label`` is ``None`` for "unplaced - needs a look"."""

    item_id: str
    label: str | None


def classify_items(items: Iterable[Mapping[str, Any]]) -> list[Classification]:
    """Every item classified, in the order given. Never raises - see contract 2.

    `item["item_id"]` took the whole batch down on one item missing the field,
    which is the failure contract 2 says cannot happen, one layer above the
    `classify` that gets it right. An item with no id comes back UNPLACED
    rather than under a made-up key: it cannot be attributed to anything, so a
    label for it would be a claim about a row nobody can find.
    """
    out: list[Classification] = []
    for item in items:
        item_id = str(item.get("item_id", "") or "")
        out.append(Classification(item_id=item_id, label=classify(item) if item_id else None))
    return out


def as_predictions(classifications: Iterable[Classification]) -> dict[str, str]:
    """``{item_id: label}`` for :func:`daydag.evalset.score`, unplaced items dropped.

    Dropping rather than guessing is deliberate and costs nothing extra:
    ``score`` already counts an absent id as a miss (evalset.py), which is
    exactly what an unplaced item is - a real item the classifier declined to
    guess at, not a hit and not a false positive either.
    """
    return {c.item_id: c.label for c in classifications if c.label is not None}


def unplaced(classifications: Iterable[Classification]) -> list[str]:
    """Ids the classifier could not place, oldest-first as given - for a human."""
    return [c.item_id for c in classifications if c.label is None]
