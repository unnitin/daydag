"""The ingestion eval set is well-formed (issue #14, SPEC section 3.3).

This suite does not test a classifier - there isn't one yet, and that is the
point. It tests the *measuring instrument*: that the labelled set exists, that
every item carries exactly one gold label, that all five categories and all four
hard cases are present, and that nothing in it leaks a real name into a public
repository.

An eval set with a hole in it reports a precision it has not earned. These
assertions are what stop the hole opening quietly.
"""

from __future__ import annotations

import re

import pytest

from daydag.evalset import (
    DATE_FORMS,
    DECISION_MARKERS,
    HARD_CASES,
    LABELS,
    MEETING_KINDS,
    MODALITIES,
    OWNER_FORMS,
    ROLE_TOKENS,
    load_items,
    load_notes,
    score,
)

ITEMS = load_items()
NOTES = load_notes()
BY_ID = {note["note_id"]: note for note in NOTES}


def of_label(label):
    return [item for item in ITEMS if item["gold"] == label]


def of_hard_case(case):
    return [item for item in ITEMS if item["hard_case"] == case]


# -- the set exists and is the size the issue asked for --------------------


def test_the_set_is_about_twenty_notes():
    """The issue asked for ~20: enough to show a distribution, few enough to hand-label."""
    assert 18 <= len(NOTES) <= 24, f"{len(NOTES)} notes"


def test_every_note_contributed_at_least_one_item():
    """A note with no items was read and not labelled - an invisible gap in coverage."""
    labelled = {item["note_id"] for item in ITEMS}
    assert set(BY_ID) - labelled == set()


def test_every_item_belongs_to_a_known_note():
    assert {item["note_id"] for item in ITEMS} <= set(BY_ID)


def test_ids_are_unique():
    ids = [item["item_id"] for item in ITEMS]
    assert len(set(ids)) == len(ids)
    note_ids = [note["note_id"] for note in NOTES]
    assert len(set(note_ids)) == len(note_ids)


# -- exactly one gold label, from the five ---------------------------------


def test_every_item_has_exactly_one_gold_label():
    for item in ITEMS:
        assert isinstance(item["gold"], str), item["item_id"]
        assert item["gold"] in LABELS, item["item_id"]


def test_all_five_categories_are_represented():
    present = {item["gold"] for item in ITEMS}
    assert present == set(LABELS), f"missing: {set(LABELS) - present}"


def test_no_single_category_swamps_the_set():
    """A set that is 80% one label measures that label and nothing else."""
    for label in LABELS:
        share = len(of_label(label)) / len(ITEMS)
        assert 0.03 <= share <= 0.55, f"{label} is {share:.0%} of the set"


# -- the four hard cases the issue names -----------------------------------


@pytest.mark.parametrize("case", sorted(HARD_CASES))
def test_each_hard_case_is_present_and_marked(case):
    """One example is an anecdote. The classifier needs at least a pair to fail on."""
    assert len(of_hard_case(case)) >= 2, f"{case} has {len(of_hard_case(case))} example(s)"


def test_hard_cases_are_a_minority():
    """They are deliberately over-sampled relative to reality; not to the point of
    dominating it, or precision on the set stops predicting precision in the inbox."""
    marked = [item for item in ITEMS if item["hard_case"] is not None]
    assert len(marked) / len(ITEMS) <= 0.35


def test_an_unowned_ask_is_still_an_ask():
    """A missing owner is a gap to surface, never grounds to drop the item.

    Scoped over every `[The group]` item rather than only the marked ones, because
    `hard_case` is a single scalar: an unowned item that is *also* invented carries
    the other flag, and checking only the marked ones would let the invariant be
    violated by an item that simply changed its label. The invented ones are the
    one carve-out, and they are noise for a different reason - nobody said them.
    """
    unowned = [
        item
        for item in ITEMS
        if item["owner_form"] == "the_group" and item["section"] == "next_steps"
    ]
    assert len(unowned) >= 15, len(unowned)
    for item in unowned:
        if item["hard_case"] == "invented_next_step":
            continue
        assert item["hard_case"] in {"unowned_ask", None}, item["item_id"]
        assert item["gold"] != "noise", item["item_id"]


def test_a_third_party_commitment_is_never_the_principals():
    """The costliest false positive: someone else's plan logged as his own."""
    for item in of_hard_case("third_party_commitment"):
        assert item["mentions_principal"] is False, item["item_id"]
        assert item["gold"] != "principal_commitment", item["item_id"]


def test_an_invented_next_step_is_noise_and_ungrounded():
    """The whole `Suggested next steps` section is model-written. An item with no
    support anywhere in the body is the section inventing work, and logging it
    spends the principal's trust on something nobody said."""
    for item in of_hard_case("invented_next_step"):
        assert item["section"] == "next_steps", item["item_id"]
        assert item["grounded_in_body"] is False, item["item_id"]
        assert item["gold"] == "noise", item["item_id"]


def test_a_reversed_decision_is_marked_on_the_superseded_item():
    """Both halves are in the set: the decision as first stated, and what displaced it.

    Only the first half carries the flag, and it stays labelled `decision` - it was
    a real decision when it was made, and it is what someone in the room will
    remember agreeing to. The displacing item must live in the same note and come
    later in it, because a note is the only unit in which "reversed later in the
    same meeting" means anything.
    """
    by_id = {item["item_id"]: item for item in ITEMS}
    reversed_items = of_hard_case("reversed_decision")
    assert reversed_items
    for item in reversed_items:
        assert item["gold"] == "decision", item["item_id"]
        target = by_id.get(item["superseded_by"])
        assert target is not None, item["item_id"]
        assert target["item_id"] != item["item_id"]
        assert target["note_id"] == item["note_id"], item["item_id"]
        assert target["hard_case"] != "reversed_decision", item["item_id"]
        # Document order: the body precedes the next-steps section.
        order = (0 if item["section"] == "body" else 1, item["position"])
        target_order = (0 if target["section"] == "body" else 1, target["position"])
        assert target_order > order, (item["item_id"], target["item_id"])


# -- structural findings the set encodes -----------------------------------


def test_no_decision_is_labelled_from_the_next_steps_section():
    """Measured over 22 real notes: Gemini never puts a decision in `Suggested next
    steps`. Decisions live in prose only, so a classifier that reads sections rather
    than sentences will find none. SPEC 3.3's "parse Summary / Decisions / Next
    steps" describes a `Decisions` section that does not exist."""
    for item in of_label("decision"):
        assert item["section"] == "body", item["item_id"]


def test_nearly_every_decision_carries_a_marker_verb_and_the_rest_are_rare():
    """The signal is the verb - `decided`, `agreed`, `reached a consensus`.

    `DECISION_MARKERS` includes None, so membership alone asserts nothing; this
    counts instead. Two of the gold decisions carry no marker at all ("priorities
    shifted so the rollout targets Q4"), which is the harder read and the reason
    a verb allowlist cannot be the whole classifier. If that share grows, the
    "identifiable by verb" claim in reference/ingestion-eval.md stops holding.
    """
    decisions = of_label("decision")
    marked = [item for item in decisions if item["decision_marker"] is not None]
    assert len(decisions) - len(marked) <= 4, [
        item["item_id"] for item in decisions if item["decision_marker"] is None
    ]
    assert marked


def test_a_principal_commitment_mentions_the_principal():
    """Sole ownership is the common form, but a joint item naming him among others
    is still his to show up to - so `owner_form` alone cannot decide this."""
    for item in of_label("principal_commitment"):
        assert item["mentions_principal"] is True, item["item_id"]
        assert item["owner_form"] in {"principal", "named_people"}, item["item_id"]


def test_dates_and_date_forms_agree():
    for item in ITEMS:
        assert (item["date_form"] is not None) == item["has_date"], item["item_id"]


def test_positions_are_within_their_section():
    for item in ITEMS:
        assert 1 <= item["position"] <= item["position_of"], item["item_id"]


def test_body_items_name_their_body_section_and_next_steps_do_not():
    for item in ITEMS:
        expected = item["section"] == "body"
        assert (item["body_section_index"] is not None) is expected, item["item_id"]


# -- schema ----------------------------------------------------------------

REQUIRED_ITEM_KEYS = {
    "item_id",
    "note_id",
    "section",
    "position",
    "position_of",
    "body_section_index",
    "owner_form",
    "owner_role",
    "mentions_principal",
    "modality",
    "has_date",
    "date_form",
    "decision_marker",
    "grounded_in_body",
    "token_count",
    "sensitive",
    "paraphrase",
    "gold",
    "hard_case",
    "superseded_by",
    "rationale",
}


def test_every_item_carries_the_full_feature_schema():
    """A missing feature is a silently unmeasurable one. Absent means null, not gone."""
    for item in ITEMS:
        assert set(item) == REQUIRED_ITEM_KEYS, (item["item_id"], set(item) ^ REQUIRED_ITEM_KEYS)


def test_enumerated_features_use_the_declared_vocabulary():
    for item in ITEMS:
        assert item["section"] in {"body", "next_steps"}, item["item_id"]
        assert item["owner_form"] in OWNER_FORMS, item["item_id"]
        assert item["modality"] in MODALITIES, item["item_id"]
        assert item["date_form"] in DATE_FORMS, item["item_id"]
        assert item["decision_marker"] in DECISION_MARKERS, item["item_id"]
        assert item["hard_case"] in HARD_CASES | {None}, item["item_id"]


def test_notes_use_the_declared_meeting_kinds():
    for note in NOTES:
        assert note["meeting_kind"] in MEETING_KINDS, note["note_id"]
        assert note["body_layout"] in {"quick_notes", "summary"}, note["note_id"]


def test_meeting_kinds_are_varied():
    """One-to-ones and standups classify very differently. A set drawn from one
    kind of meeting measures one kind of meeting."""
    assert len({note["meeting_kind"] for note in NOTES}) >= 6


def test_note_level_counts_agree_with_the_items():
    """The two files must not drift apart.

    `notes.jsonl` carries aggregates - how many items were labelled, how long each
    section of the source note was - and nothing else checks them. An item added
    without bumping `labelled_items`, or a `position_of` past a section's real
    length, is exactly the quiet hole this suite exists to close.
    """
    for note in NOTES:
        mine = [item for item in ITEMS if item["note_id"] == note["note_id"]]
        assert note["labelled_items"] == len(mine), note["note_id"]
        for item in mine:
            expected = note["body_items"] if item["section"] == "body" else note["next_steps"]
            assert item["position_of"] == expected, item["item_id"]
            assert item["position"] <= expected, item["item_id"]


def test_a_note_with_no_next_steps_contributes_no_next_step_items():
    """One note in the set is a meeting that was cancelled: a body line, no
    `Suggested next steps` section at all. The ledger still needs the row - a note
    that says nothing is not a missing note."""
    empty = [note for note in NOTES if note["next_steps"] == 0]
    assert empty, "the cancelled-meeting note is gone"
    for note in empty:
        mine = [item for item in ITEMS if item["note_id"] == note["note_id"]]
        assert mine and all(item["section"] == "body" for item in mine), note["note_id"]


# -- privacy: the part scan_secrets.py cannot check ------------------------

#: Capitalised tokens a paraphrase may legitimately contain. Everything else
#: capitalised mid-sentence is a name or a codename, which is the failure this
#: file exists to prevent. `scripts/scan_secrets.py` matches identifier *shapes*
#: and would pass a real surname without comment.
ALLOWED_CAPITALISED = ROLE_TOKENS | {
    # Initialisms and repo vocabulary.
    "AI",
    "API",
    "Active",
    "CI",
    "DM",
    "DM-only",
    "EOD",
    "PR",
    "PRs",
    "SPEC",
    "SQL",
    "UI",
    "UPC",
    "Watching",
    "Workstreams",
    # Calendar words, in full, so a refreshed fixture with a different weekday or
    # month does not fail as a false leak.
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
    "Q1",
    "Q2",
    "Q3",
    "Q4",
}

#: Every word that opens a sentence anywhere in the fixtures, other than a role
#: token. It is a long list of ordinary English because the first slot of a
#: paraphrase is usually an imperative verb - and it has to be enumerated rather
#: than skipped, because the *other* thing that fills that slot is the owner
#: ("DataEng-2 is moving the workspaces..."), which is precisely where a real name
#: would hide. Adding a verb here is a one-line diff; adding a surname would be
#: conspicuous, which is the property that makes the list worth its length.
SENTENCE_OPENERS = {
    "A", "Add", "Aggregate", "Agreement", "Analyse", "Ask", "Attend", "Build", "Capacity",
    "Centralising", "Check", "Close", "Collect", "Compile", "Data", "Decide", "Define", "Demo",
    "Discussion", "Draft", "Early", "Extend", "Finish", "Fix", "Fold", "Frame", "Gateway", "Get",
    "Give", "Import", "Initial", "Integration", "Leadership", "List", "Make", "Meet", "Merge",
    "Message", "Modelling", "Move", "Negotiate", "Open", "Outline", "Participants", "Partners",
    "Ping", "Post", "Prepare", "Present", "Priorities", "Provision", "Publish", "Push", "Put",
    "Reach", "Record", "Reliability", "Review", "Rework", "Run", "Scheduling", "Send", "Set",
    "Share", "Stakeholders", "Start", "Structure", "Submit", "Surface", "Talk", "Teams", "The",
    "Tool", "Update", "Walk", "Work", "Write",
}  # fmt: skip

#: Hyphenated role tokens must match whole - a pattern that lets `VP-Data` match as
#: `Data` reports its own vocabulary as a leak and teaches you to widen the allowlist.
_CAPITALISED = re.compile(r"(?<![\w'-])[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")


def _capitals(text: str):
    """Every capitalised token, tagged with whether it opens a sentence.

    Both positions are checked. Sentence-initial is the harder half - ordinary
    English fills it - but it is also where the owner goes in most of these
    paraphrases, so skipping it would leave the field most likely to hold a real
    name as the one field never examined.
    """
    for match in _CAPITALISED.finditer(text):
        before = text[max(0, match.start() - 2) : match.start()].strip()
        opens = match.start() == 0 or before in {".", "!", "?"}
        yield match.group(0), opens


def _unrecognised(text: str):
    for word, opens in _capitals(text):
        allowed = ALLOWED_CAPITALISED | SENTENCE_OPENERS if opens else ALLOWED_CAPITALISED
        if word not in allowed:
            yield word


@pytest.mark.parametrize("field", ["paraphrase", "rationale"])
def test_no_authored_text_carries_an_unrecognised_proper_noun(field):
    """Role tokens only, in both hand-written fields.

    `rationale` is English prose shipped to a public repository just as much as
    `paraphrase` is, so it gets the same scan rather than only a check for one
    specific name.
    """
    offenders = [(item["item_id"], word) for item in ITEMS for word in _unrecognised(item[field])]
    assert offenders == [], offenders


def test_the_proper_noun_scan_actually_catches_things():
    """A privacy check that cannot fail is worse than none - it reads as coverage."""
    assert list(_unrecognised("The team asked Fernanda to check the contracts.")) == ["Fernanda"]
    assert list(_unrecognised("Move the pipelines onto Blacksmith runners.")) == ["Blacksmith"]
    assert list(_unrecognised("Fernanda owns the contract mapping.")) == ["Fernanda"]
    assert list(_unrecognised("VP-Data will ask Eng-3 about it on Thursday.")) == []


def test_owner_roles_are_role_tokens():
    for item in ITEMS:
        assert item["owner_role"] is None or item["owner_role"] in ROLE_TOKENS, item["item_id"]


def test_the_principals_own_name_appears_nowhere():
    """He is `Principal` in this file, like everyone else is a role."""
    for item in ITEMS:
        assert "Nitin" not in item["paraphrase"], item["item_id"]
        assert "Nitin" not in item["rationale"], item["item_id"]


def test_sensitive_items_exist_and_stay_thin():
    """Personnel and M&A lines are in real notes, so the set must contain them or
    it never measures them. SPEC guardrail 5 says they are quoted minimally -
    here that is a hard cap on how much of one is written down at all."""
    sensitive = [item for item in ITEMS if item["sensitive"]]
    assert sensitive, "no sensitive item - the hardest routing decision is untested"
    for item in sensitive:
        assert len(item["paraphrase"]) <= 90, item["item_id"]


# -- the set is usable as a measuring instrument ---------------------------


def test_a_perfect_prediction_scores_one():
    truth = {item["item_id"]: item["gold"] for item in ITEMS}
    result = score(truth)
    for label in LABELS:
        assert result[label]["precision"] == 1.0
        assert result[label]["recall"] == 1.0


def test_a_false_positive_costs_precision_and_a_miss_costs_recall():
    """The asymmetry SPEC cares about, made arithmetic: over-logging shows up in
    precision, under-logging in recall, and the bar weights them differently."""
    noise = of_label("noise")[0]["item_id"]
    predictions = {item["item_id"]: item["gold"] for item in ITEMS}
    predictions[noise] = "principal_commitment"

    result = score(predictions)
    assert result["principal_commitment"]["precision"] < 1.0
    assert result["principal_commitment"]["recall"] == 1.0
    assert result["noise"]["recall"] < 1.0


def test_unpredicted_items_are_counted_as_missed_not_ignored():
    """Silence is not abstention. An item the classifier never emitted is a miss.

    And an empty numerator over an empty denominator is not perfection: emitting
    nothing must not clear the precision floors, which are the hard half of the
    bar in reference/ingestion-eval.md.
    """
    result = score({})
    assert all(result[label]["recall"] == 0.0 for label in LABELS)
    assert all(result[label]["precision"] == 0.0 for label in LABELS)


def test_never_emitting_one_label_does_not_score_full_marks_on_it():
    """The single-label version of the same hole: skip `principal_commitment`
    entirely and its precision must not read 1.0."""
    predictions = {
        item["item_id"]: item["gold"] for item in ITEMS if item["gold"] != "principal_commitment"
    }
    result = score(predictions)
    assert result["principal_commitment"]["precision"] == 0.0
    assert result["principal_commitment"]["recall"] == 0.0


def test_a_prediction_for_an_unknown_item_is_an_error():
    """A typo'd or hallucinated id must not read as an abstention - that is how a
    classifier scores 1.0 while emitting five hundred rows of nonsense."""
    with pytest.raises(KeyError):
        score({"n99-s99": "noise"})


def test_a_prediction_outside_the_label_vocabulary_is_an_error():
    with pytest.raises(ValueError):
        score({ITEMS[0]["item_id"]: "commitment"})


def test_fixtures_are_offline():
    """No network in tests - the fixtures are the frozen data (issue #14)."""
    for item in ITEMS:
        assert "http://" not in item["paraphrase"], item["item_id"]
        assert "https://" not in item["paraphrase"], item["item_id"]
