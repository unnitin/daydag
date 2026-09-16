"""What `plan` asks for is what `render` reads (#131).

The runner has two halves that have to agree on a key, and nothing held them
to it. `plan eod` emitted the current weekly note under `vault`; `eod_wrap`
read it out of `vault_notes`. The payload was there, carrying 17,703 real
characters, and every wrap rendered

    ⚠ no weekly note for 0914-0918

with `wrap: 0 closed, 0 moved` under it, on a note sitting at the path the plan
had printed. It read as a quiet day rather than as a runner asking for the
wrong key - the same silence-reads-as-health shape as #128.

These tests close the loop the only way that stays closed: build the payloads
FROM the plan's own output, the way an agent following it does, and assert the
render used them.
"""

from datetime import UTC, datetime

import pytest

from daydag import run
from daydag.config import Identities

#: Friday, so `eod` emits its next-week `vault_notes` step as well as `vault` -
#: the two keys whose disagreement is #131.
FRIDAY = datetime(2026, 9, 18, 17, 0, tzinfo=UTC)

#: A weekly note with one closed red item, so "the wrap read the note" is
#: observable in the output rather than only in the absence of a warning.
WEEKLY_NOTE = """# 0914-0918

## Priorities

- [x] 🔴 land the ingestion backfill
- [ ] 🔴 compute consolidation plan
"""


@pytest.fixture
def identities(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"SLACK_USER_PRINCIPAL=UPRINCIPAL1\nEMAIL_PRINCIPAL=principal@x.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n",
        encoding="utf-8",
    )
    (tmp_path / "vault" / "Weekly Notes").mkdir(parents=True)
    return Identities.from_file(env)


def payloads_for(plan: run.Plan) -> dict:
    """Exactly what an agent following ``plan`` hands back to ``render``.

    One branch per source the plan can name, keyed the way that step says to
    key it. Nothing here knows which module consumes what - that is the point.
    """
    out: dict = {}
    for step in plan.steps:
        if step.source == "vault":
            out["vault"] = WEEKLY_NOTE
        elif step.source == "vault_notes":
            out.setdefault("vault_notes", {}).update(
                {path: "# a note\n" for path in step.detail.get("paths", [])}
            )
        else:
            out.setdefault(step.source, [])
    return out


def test_the_wrap_reads_the_weekly_note_the_plan_asked_for(identities):
    """THE #131 REGRESSION. Verified by hand on 2026-09-14 in both directions:
    under `vault` the warning fired, under `vault_notes` it did not."""
    built = run.plan("eod", now=FRIDAY, identities=identities)
    assert any(step.source == "vault" for step in built.steps), (
        "the plan stopped asking for the weekly note under `vault`; this test "
        "is no longer testing the disagreement it was written for"
    )

    text = run.render("eod", now=FRIDAY, identities=identities, payloads=payloads_for(built))

    assert "no weekly note" not in text, f"the wrap ignored the payload the plan asked for:\n{text}"
    assert "land the ingestion backfill" in text, f"the note was read but not used:\n{text}"


@pytest.mark.guardrail
@pytest.mark.parametrize("loop", run.LOOPS)
def test_every_loop_can_read_back_the_payloads_its_own_plan_asks_for(loop, identities):
    """Guardrail 6 says a source that could not be reached gets one line. A
    source the agent DID reach and handed back under the plan's own key must
    never produce that line - it reports a dead connector that is alive, and
    the loop silently drops whatever it was carrying."""
    built = run.plan(loop, now=FRIDAY, identities=identities)
    asked = {step.source for step in built.steps}

    text = run.render(loop, now=FRIDAY, identities=identities, payloads=payloads_for(built))

    missed = [
        line
        for line in text.splitlines()
        if line.startswith("- couldn't check") or "no weekly note" in line
    ]
    detail = "\n".join(missed)
    assert not missed, f"{loop} asked for {sorted(asked)} and then could not read it:\n{detail}"
