"""The ingestion eval set: the labelled ground truth ingestion is scored against.

Built before the classifier (issue #14 before #15) so precision and recall are
measured rather than asserted. The fixtures under ``tests/fixtures/ingestion/``
are the frozen data; nothing here touches the network.

The repository is public, so the fixtures hold **structural features and gold
labels**, never meeting text. Where a snippet was needed to make a case
comprehensible it was paraphrased into a neutral equivalent that preserves the
linguistic feature under test - tense, modality, whether an owner is named,
whether a date is present. People are role tokens. See
``reference/ingestion-eval.md``.

``score()`` is the whole point of the module: it turns a classifier's output
into per-label precision and recall, with silence counted as a miss rather than
an abstention.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ingestion"

#: The five categories of SPEC section 3.3 step 2. Exactly one per item.
LABELS = (
    "principal_commitment",
    "assigned_ask",
    "decision",
    "new_workstream",
    "noise",
)

#: The four cases issue #14 names, deliberately over-sampled. Each is a way the
#: obvious rule gets it wrong: the owner is someone else, the decision did not
#: survive the meeting, the owner is nobody, or nobody said it at all.
HARD_CASES = frozenset(
    {
        "third_party_commitment",
        "reversed_decision",
        "unowned_ask",
        "invented_next_step",
    }
)

#: How the note names an owner. `the_group` is Gemini's literal `[The group]`,
#: and it is common enough that treating it as "no owner, therefore not an ask"
#: would discard a large share of the real asks.
OWNER_FORMS = frozenset(
    {"principal", "named_person", "named_people", "first_name_only", "the_group", "none"}
)

#: Gemini writes everything in the third person, so the first-person signal
#: SPEC section 3.3 illustrates with "I'll intro Daniel to CTO" never appears.
#: Modality is what is left to separate a commitment from a description.
MODALITIES = frozenset(
    {
        "imperative",
        "past_declarative",
        "present_declarative",
        "future_will",
        "progressive",
        "hedged",
    }
)

DATE_FORMS = frozenset(
    {"weekday", "relative_day", "relative_week", "quarter", "absolute", "end_of_period", None}
)

#: The verbs that mark a decision in the prose. There is no `Decisions` section
#: to read - see reference/ingestion-eval.md.
DECISION_MARKERS = frozenset(
    {
        "decided",
        "agreed",
        "consensus",
        "established",
        "adopted",
        "resolved",
        "finalized",
        "prioritized",
        "confirmed",
        None,
    }
)

MEETING_KINDS = frozenset(
    {
        "one_to_one",
        "standup",
        "steering",
        "review",
        "sprint_planning",
        "retro",
        "office_hours",
        "external_demo",
        "working_session",
        "program_review",
        "cross_team_sync",
    }
)

#: Role tokens, per CLAUDE.md section 2. The only person-shaped strings allowed
#: in a fixture. `Principal` is the note owner himself.
ROLE_TOKENS = frozenset(
    {
        "Principal",
        "VP-Data",
        "VP-AI",
        "CTO",
        "Sponsor",
        "Gov-Lead",
        "Enablement-Lead",
        "Analytics-Partner",
        "Modeler-Owner",
        "ML-Lead",
        "Revenue-Lead",
        "Product-1",
        "Eng-Sr",
        "Eng-2",
        "Eng-3",
        "Eng-4",
        "Eng-5",
        "Eng-6",
        "Eng-7",
        "Eng-8",
        "DataEng-1",
        "DataEng-2",
        "Stakeholder-1",
        "Stakeholder-2",
        "Stakeholder-3",
    }
)


def _read(name: str) -> list[dict[str, Any]]:
    path = FIXTURES / name
    if not path.exists():
        # The wheel packages `src/daydag` only, so the fixtures ship with the
        # source tree and not with an installed copy. Say that, rather than
        # letting a bare FileNotFoundError read as a corrupt install.
        raise FileNotFoundError(
            f"{path} is missing. The eval fixtures live in the source tree, not in the "
            "wheel - run against a checkout or an editable install."
        )
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_items() -> list[dict[str, Any]]:
    """Every labelled item, in fixture order."""
    return _read("items.jsonl")


def load_notes() -> list[dict[str, Any]]:
    """Note-level metadata: meeting kind, layout, section and owner counts."""
    return _read("notes.jsonl")


def score(predictions: dict[str, str]) -> dict[str, dict[str, float]]:
    """Per-label precision and recall of ``{item_id: predicted_label}``.

    An item absent from ``predictions`` counts as a miss, not an abstention.
    That is deliberate: a classifier that declines to answer has still failed to
    surface the commitment, and the eval set should say so.

    Precision is the number that matters here. SPEC's bias is that a wrongly
    logged commitment costs more trust than a missed one, because the missed one
    still surfaces in Slack - so a rule that buys recall with false positives
    should look worse, and does.

    Two things are errors rather than zeroes, because scoring them softly is how
    a bar gets cleared without being met:

    * Predicting nothing for a label that has support scores **0.0** precision,
      not 1.0. An empty numerator over an empty denominator is not perfection,
      and treating it as such lets a classifier that emits nothing clear every
      precision floor in reference/ingestion-eval.md.
    * An ``item_id`` that is not in the set raises. A typo would otherwise read
      as an abstention, and five hundred invented ids would read as silence.
    """
    truth = {item["item_id"]: item["gold"] for item in load_items()}
    unknown = sorted(set(predictions) - set(truth))
    if unknown:
        raise KeyError(f"predictions for item ids not in the eval set: {unknown[:5]}")
    for item_id, label in predictions.items():
        if label not in LABELS:
            raise ValueError(f"{item_id}: {label!r} is not one of {LABELS}")

    out: dict[str, dict[str, float]] = {}
    for label in LABELS:
        predicted = {i for i, p in predictions.items() if p == label}
        actual = {i for i, g in truth.items() if g == label}
        hits = len(predicted & actual)
        if predicted:
            precision = hits / len(predicted)
        else:
            precision = 1.0 if not actual else 0.0
        out[label] = {
            "precision": precision,
            "recall": hits / len(actual) if actual else 1.0,
            "support": float(len(actual)),
        }
    return out
