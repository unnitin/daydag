"""Open is a verdict, not a default - in the morning brief too (#187).

The morning showed "owed to you" rows without reading a single reply, so an ask
answered in its thread an hour earlier went out as open - the 9/18 miss, back
in another loop. Now the plan names one read for each Slack-cited row the
brief will show (ranked the way the budget ranks them), and every chase row
the brief prints is marked: answered (closing quote, for him to strike), as it
is (read, still open), or "couldn't verify" - shown and not read is not open.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from daydag import budget, run
from daydag.people import People
from daydag.state import EventLog, StateFolder

NOW = datetime(2026, 9, 7, 13, 40, tzinfo=UTC)  # 06:40 PT
IDENTITIES = {"SLACK_USER_PRINCIPAL": "UPRINCIPAL1", "EMAIL_PRINCIPAL": "alex.rivera@example.com"}


def _slack(channel, stamp):
    """``stamp`` is the permalink's own `p` + 16 digits, written whole."""
    return f"https://example.slack.com/archives/{channel}/{stamp}"


OLDEST = _slack("CPOD000001", "p1757000000000050")
ANSWERED = _slack("CPOD000001", "p1757000000000100")
OPEN = _slack("CPOD000001", "p1757000000000200")
UNREAD = _slack("CPOD000001", "p1757000000000300")
MINE = _slack("DVPDATA001", "p1757000000000400")
CLOSING = _slack("CPOD000001", "p1757000100000100")
MAIL = "https://mail.example.com/#all/m1"

STATE = f"""# State

## Chase list

- vp-data · an ask the brief only counts · [slack]({OLDEST})
- vp-ai · cited by a note, not a thread · [source]({MAIL})
- vp-data · the compute plan · [slack]({UNREAD})
- vp-ai · the rollout date · [slack]({OPEN})
- eng-2 · the backfill numbers · [slack]({ANSWERED})
- Alex Rivera · send vp-data the deck · [slack]({MINE})
"""


def _reply(user, stamp, text, *, thread=None, permalink=None):
    record = {"user": user, "ts": stamp, "text": text}
    if thread:
        record["thread_ts"] = thread
    if permalink:
        record["permalink"] = permalink
    return record


def _vault(tmp_path, state=STATE):
    vault = tmp_path / "vault"
    folder = StateFolder.create(vault / "DayDAG")
    folder.state_path.write_text(state, encoding="utf-8")
    return {**IDENTITIES, "VAULT_ROOT": str(vault)}, folder


def _render(identities, folder, closure, **kwargs):
    payloads = {"calendar": [], "slack": [], "gmail": [], "vault": ""}
    if closure is not None:
        payloads["closure"] = closure
    return run.render(
        "morning", now=NOW, identities=identities, payloads=payloads, state=folder, **kwargs
    )


def _line(text, needle):
    return next(line for line in text.splitlines() if needle in line)


def _closure_keys(identities, loop="morning"):
    plan = run.plan(loop, now=NOW, identities=identities)
    return [step.detail["key"] for step in plan.steps if step.source == "closure"]


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------


def test_the_plan_reads_only_the_slack_cited_rows_the_brief_will_show(tmp_path):
    identities, _ = _vault(tmp_path)
    steps = [
        s
        for s in run.plan("morning", now=NOW, identities=identities).steps
        if s.source == "closure"
    ]

    # the three newest owed rows and his one; the oldest is only counted in a
    # fitted morning, and the mail-cited row has no thread to read
    assert sorted(s.detail["key"] for s in steps) == sorted([ANSWERED, OPEN, UNREAD, MINE])
    for step in steps:
        assert "thread" in step.how and "`closure`" in step.how
        digits = step.detail["key"].rsplit("/p", 1)[1]
        assert step.detail["oldest"] == f"{digits[:10]}.{digits[10:]}", "read from the ask on"


def test_his_own_rows_get_their_own_cap(tmp_path):
    rows = "".join(
        f"- Alex Rivera · step {i} · [slack]({_slack('DVPDATA001', f'p17570000000005{i:02d}')})\n"
        for i in range(4)
    )
    identities, _ = _vault(tmp_path, f"# State\n\n## Chase list\n\n{rows}")
    assert len(_closure_keys(identities)) == budget.CAPS["owed-by"]


def test_only_the_morning_plans_the_reads(tmp_path):
    identities, _ = _vault(tmp_path)
    assert _closure_keys(identities, "eod") == []


def test_the_plan_never_scaffolds_a_vault(tmp_path):
    missing = tmp_path / "not-synced-yet"
    assert _closure_keys({**IDENTITIES, "VAULT_ROOT": str(missing)}) == []
    assert not missing.exists(), "a plan must not create DayDAG/ in a vault that is not there"


def test_an_undecodable_state_md_costs_the_checks_not_the_morning(tmp_path):
    identities, folder = _vault(tmp_path)
    folder.state_path.write_bytes(b"# State\n\n## Chase list\n\n- vp-data \xb7 the numbers\n")

    assert _closure_keys(identities) == []
    assert _render(identities, folder, {}), "the brief still ships"


# --------------------------------------------------------------------------
# the render
# --------------------------------------------------------------------------


def test_each_shown_row_says_what_reading_its_thread_established(tmp_path):
    identities, folder = _vault(tmp_path)
    closure = {
        ANSWERED: [
            _reply("UENG20001", "1757000100.000100", "numbers are in the sheet", permalink=CLOSING)
        ],
        OPEN: [_reply("UPRINCIPAL1", "1757000100.000200", "bump")],
        MINE: [_reply("UVPDATA001", "1757000100.000400", "any update?")],
        # UNREAD left out: the agent could not read it
    }
    text = _render(identities, folder, closure)

    answered = _line(text, "the backfill numbers")
    assert answered.startswith("- ✓ looks answered, strike it? eng-2 · the backfill numbers")
    assert '"numbers are in the sheet"' in answered and answered.endswith(
        f"({ANSWERED}) ({CLOSING})"
    )
    assert _line(text, "the rollout date") == f"- vp-ai · the rollout date ({OPEN})", (
        "his own bump is not the owner's answer: read, still open, shown as it is"
    )
    assert (
        _line(text, "the compute plan")
        == f"- couldn't verify: vp-data · the compute plan ({UNREAD})"
    )
    assert _line(text, "the deck") == f"- Alex Rivera · send vp-data the deck ({MINE})", (
        "his own row is answered only by HIS reply"
    )
    assert _line(text, "cited by a note") == (
        f"- couldn't verify: vp-ai · cited by a note, not a thread ({MAIL})"
    ), "no thread to read: shown, so said"
    assert _line(text, "only counts").startswith("- couldn't verify: "), (
        "a row the plan did not read is not open when the brief prints it"
    )


def test_his_reply_answers_his_own_row(tmp_path):
    identities, folder = _vault(tmp_path)
    closure = {MINE: [_reply("UPRINCIPAL1", "1757000100.000400", "sent it")]}
    mine = _line(_render(identities, folder, closure), "the deck")
    assert mine.startswith("- ✓ looks answered, strike it?") and '"sent it"' in mine


def test_without_the_reads_no_row_reads_as_open(tmp_path):
    identities, folder = _vault(tmp_path)
    text = _render(identities, folder, None)
    for ask in (
        "the compute plan",
        "the rollout date",
        "the backfill numbers",
        "the deck",
        "only counts",
    ):
        assert _line(text, ask).startswith("- couldn't verify: "), ask


def test_a_post_outside_the_asks_thread_is_not_an_answer(tmp_path):
    """The read covers the channel after the ask; a later top-level post by
    someone else ("lunch is here") is not the owner answering it."""
    identities, folder = _vault(tmp_path)
    thread = "1757000000.000100"
    elsewhere = _reply(
        "UENG50001", "1757000100.000900", "lunch is here", thread="1757000099.000000"
    )
    in_thread = _reply("UENG20001", "1757000100.000100", "done", thread=thread)

    stray = _line(_render(identities, folder, {ANSWERED: [elsewhere]}), "backfill")
    assert not stray.startswith("- ✓"), stray
    assert _line(
        _render(identities, folder, {ANSWERED: [elsewhere, in_thread]}), "backfill"
    ).startswith("- ✓ looks answered")


def test_when_the_owner_is_known_only_the_owner_answers(tmp_path):
    identities, folder = _vault(tmp_path)
    log = tmp_path / "events.db"
    People(EventLog.open(log)).remember("eng-2", slack_id="UENG20001", email="eng.two@example.com")
    someone = [_reply("UOTHER0001", "1757000100.000100", "i think it's done")]
    owner = [_reply("UENG20001", "1757000100.000100", "done")]

    assert not _line(
        _render(identities, folder, {ANSWERED: someone}, log=log), "backfill"
    ).startswith("- ✓")
    assert _line(_render(identities, folder, {ANSWERED: owner}, log=log), "backfill").startswith(
        "- ✓"
    )


def test_two_rows_with_one_head_keep_their_own_verdicts(tmp_path):
    first, second = (
        _slack("CPOD000001", "p1757000000000100"),
        _slack("CPOD000002", "p1757000000000200"),
    )
    state = (
        "# State\n\n## Chase list\n\n"
        f"- vp-data · follow up\n\t- [slack]({first})\n"
        f"- vp-data · follow up\n\t- [slack]({second})\n"
    )
    identities, folder = _vault(tmp_path, state)
    closure = {first: [_reply("UVPDATA001", "1757000100.000100", "here you go")], second: []}
    lines = [
        line for line in _render(identities, folder, closure).splitlines() if "follow up" in line
    ]

    assert sorted(line.startswith("- ✓") for line in lines) == [False, True]


@pytest.mark.parametrize(
    "value",
    [
        {"user": "UENG20001", "ts": "1757000100.000100", "text": "x"},
        "error: channel_not_found",
        ["done"],
        0,
    ],
    ids=["a-dict", "an-error-string", "bare-strings", "a-number"],
)
def test_a_malformed_read_is_not_a_read(tmp_path, value):
    identities, folder = _vault(tmp_path)
    assert _line(_render(identities, folder, {ANSWERED: value}), "backfill").startswith(
        "- couldn't verify: "
    )


def test_a_closure_payload_that_is_not_a_mapping_reads_nothing(tmp_path):
    identities, folder = _vault(tmp_path)
    assert _line(_render(identities, folder, [ANSWERED]), "backfill").startswith(
        "- couldn't verify: "
    )


def test_the_mark_survives_a_fitted_morning(tmp_path):
    """A fitted morning clips each row's words from the end; the mark leads."""
    long = "the silver-layer backfill window for the label-engine migration and who signs off " * 2
    filler = "".join(
        f"- vp-ai · filler {i} {'words ' * 40}· [slack]({_slack('CPOD000009', stamp)})\n"
        for i, stamp in ((i, f"p17570000000009{i:02d}") for i in range(60))
    )
    state = f"# State\n\n## Chase list\n\n{filler}- eng-2 · {long}· [slack]({ANSWERED})\n"
    identities, folder = _vault(tmp_path, state)
    closure = {
        ANSWERED: [_reply("UENG20001", "1757000100.000100", "signed off", permalink=CLOSING)]
    }
    text = _render(identities, folder, closure)
    head = text.split("\n\n--- thread ---", 1)[0]

    assert len(head) <= budget.PUSH_BUDGET, "this morning is fitted"
    assert _line(head, "silver-layer").startswith("- ✓ looks answered, strike it? eng-2")
