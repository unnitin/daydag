"""Open is a verdict, not a default (`daydag.closure`).

On 2026-09-18 three items were reported open that were already closed in
Slack - a promise answered in the same DM three hours later, a recording
posted in the thread the day before, a decision answered in the thread it was
asked in. Each was matched on the ask's text; the reply under it was never
read. These tests describe the replacement: the runner plans the reads, and
an ask cannot render as open until they happened.
"""

from pathlib import Path

import pytest

from daydag import brief, closure
from daydag.closure import Ask, asks_in, closure_steps, judge, render_closure, slack_permalink

FIXTURE = Path(__file__).parent / "fixtures" / "state" / "hand_edited.md"
PRINCIPAL = "UPRINCIPAL1"

STATE = """# State

## Chase list

- vp-data · the compute consolidation plan · asked-on 2026-09-10 · status open
\t- his words: *"on it"*
\t- [slack](https://x.slack.com/archives/CCHASE0001/p1789000000000001)

- eng-2 · fruits metadata list · asked-on 2026-09-11
\t- no link, this came from a hallway conversation

## Owed by you

- **gov-lead** · your read on his framing · waiting since 2026-09-14 06:00
\t- [slack · his DM](https://x.slack.com/archives/DOWED00001/p1789390817581149?thread_ts=1789007120.252329&cid=DOWED00001)

### promises you made

- **to vp-ai** · *"will revert before eod today"* · said 2026-09-15 09:50
\t- [slack](https://x.slack.com/archives/DPROMISE01/p1789488602000000)
- ~~**to vp-data** · the behavioural doc~~ ✓ closed 2026-09-18
\t- [slack](https://x.slack.com/archives/DSTRUCK001/p1789488602000001)

## Done

- ~~vp-data · answer on the call · asked-on 2026-09-10~~ ✓ closed 2026-09-15
\t- [slack](https://x.slack.com/archives/CDONE00001/p1789000000000009)
"""

DECISIONS = (
    "# Pending decisions\n\n"
    "- **2026-09-17 · shazam attribution (TDE-676)** · asked by modeler-owner in #data-release"
    " · options 1-4 · [slack](https://x.slack.com/archives/CDECIDE001/p1789667238635309)"
    " · status: open\n"
)


# -- reading the vault -------------------------------------------------------


def test_a_permalink_yields_the_conversation_the_ts_and_the_thread():
    conversation, ts, thread, link = slack_permalink(
        "see [slack](https://x.slack.com/archives/CTEAMDE001/p1789784388620269"
        "?thread_ts=1789669365.360859&cid=CTEAMDE001) for it"
    )
    assert (conversation, ts, thread) == ("CTEAMDE001", "1789784388.620269", "1789669365.360859")
    assert link.startswith("https://x.slack.com/archives/CTEAMDE001/p1789784388620269")


def test_direction_comes_from_the_section_not_the_text():
    """ "will revert before eod" is a promise because it is FILED as one."""
    by_text = {ask.text: ask for ask in asks_in(STATE, DECISIONS)}

    assert (
        by_text[
            "vp-data · the compute consolidation plan · asked-on 2026-09-10 · status open"
        ].waiting_on
        == "owner"
    )
    assert (
        by_text[
            "**gov-lead** · your read on his framing · waiting since 2026-09-14 06:00"
        ].waiting_on
        == "principal"
    )
    promise = next(a for a in by_text.values() if "will revert" in a.text)
    assert promise.waiting_on == "principal", "a nested promise inherits Owed by you"
    decision = next(a for a in by_text.values() if "shazam" in a.text)
    assert decision.waiting_on == "principal" and decision.section == "Pending decisions"


def test_struck_lines_and_done_are_not_asks():
    keys = {ask.conversation for ask in asks_in(STATE, DECISIONS)}

    assert "DSTRUCK001" not in keys and "CDONE00001" not in keys


def test_the_permalink_is_read_from_the_sub_bullet():
    """The live file puts his wording on the head line and the citation under
    it. A top-level read finds no link on any row."""
    chase = next(a for a in asks_in(STATE) if a.section == "Chase list" and "compute" in a.text)

    assert chase.conversation == "CCHASE0001" and chase.ts == "1789000000.000001"


def test_the_live_fixture_parses_without_raising():
    asks = asks_in(FIXTURE.read_text(encoding="utf-8"))

    assert any(a.section == "Owed by you" for a in asks)


# -- planning the reads ------------------------------------------------------


def test_every_checkable_ask_gets_a_read_of_its_thread():
    """A top-level read misses every reply, and a reply is where an answer
    lands - all three 2026-09-18 misses were replies."""
    steps = {s.key: s for s in closure_steps(asks_in(STATE, DECISIONS))}

    owed = next(s for k, s in steps.items() if "DOWED00001" in k)
    assert owed.thread_ts == "1789007120.252329", "a reply reads the thread it sits in"
    promise = next(s for k, s in steps.items() if "DPROMISE01" in k)
    assert promise.thread_ts == promise.oldest == "1789488602.000000", (
        "a parent reads its own thread"
    )
    assert all("thread" in s.how for s in steps.values())


def test_an_ask_with_no_permalink_plans_no_read():
    steps = closure_steps(asks_in(STATE))

    assert not any("fruits" in s.key for s in steps)


# -- judging -----------------------------------------------------------------


def _ask(**over) -> Ask:
    base = dict(
        text="**to vp-ai** · will revert before eod",
        section="Owed by you",
        waiting_on="principal",
        conversation="DPROMISE01",
        ts="1789488602.000000",
        permalink="https://x.slack.com/archives/DPROMISE01/p1789488602000000",
    )
    base.update(over)
    return Ask(**base)


def _msg(user: str, ts: str, text: str = "…") -> dict:
    return {"user": user, "ts": ts, "text": text, "permalink": f"https://x.slack.com/p{ts}"}


@pytest.mark.guardrail
def test_an_ask_whose_conversation_was_not_read_is_never_open():
    """Contract 1. `None` is "not fetched", `[]` is "fetched, nothing there".
    Reporting the first as open is the 2026-09-18 failure exactly."""
    ask = _ask()

    (skipped,) = judge([ask], None, principal=PRINCIPAL)
    (absent,) = judge([ask], {}, principal=PRINCIPAL)
    (empty,) = judge([ask], {ask.key: []}, principal=PRINCIPAL)

    assert skipped.status == "unchecked" and absent.status == "unchecked"
    assert empty.status == "open", "an empty read IS evidence of absence"


def test_the_principals_reply_after_the_ask_answers_a_promise():
    ask = _ask()
    fetched = {
        ask.key: [
            _msg("UOTHER", "1789488602.000000", "the ask"),
            _msg(PRINCIPAL, "1789499598.000000", "here is my read"),
            _msg("UOTHER", "1789502000.000000", "helpful, aligned"),
        ]
    }

    (verdict,) = judge([ask], fetched, principal=PRINCIPAL)

    assert verdict.status == "answered"
    assert verdict.quote == "here is my read" and verdict.by == PRINCIPAL
    assert "1789499598" in verdict.permalink, "the CLOSING message is the citation"


def test_the_principals_own_ask_does_not_answer_itself():
    """He posted the ask; only something AFTER it counts."""
    ask = _ask()
    fetched = {ask.key: [_msg(PRINCIPAL, ask.ts, "the ask itself")]}

    (verdict,) = judge([ask], fetched, principal=PRINCIPAL)

    assert verdict.status == "open"


def test_someone_else_replying_does_not_answer_what_the_principal_owes():
    ask = _ask()
    fetched = {ask.key: [_msg("UOTHER", "1789499598.000000", "bump")]}

    (verdict,) = judge([ask], fetched, principal=PRINCIPAL)

    assert verdict.status == "open"


def test_a_chase_item_is_answered_by_the_owner_not_the_principal():
    ask = _ask(section="Chase list", waiting_on="owner", text="vp-data · the compute plan")
    only_him = {ask.key: [_msg(PRINCIPAL, "1789499598.000000", "circling back")]}
    owner = {ask.key: [_msg("UVPDATA", "1789499598.000000", "done, see PR")]}

    (nudged,) = judge([ask], only_him, principal=PRINCIPAL)
    (moved,) = judge([ask], owner, principal=PRINCIPAL)

    assert nudged.status == "open", "his own nudge is not their answer"
    assert moved.status == "answered" and moved.quote == "done, see PR"


def test_a_known_owner_id_excludes_bystanders():
    ask = _ask(section="Chase list", waiting_on="owner", text="vp-data · the compute plan")
    fetched = {ask.key: [_msg("UBYSTANDER", "1789499598.000000", "+1")]}

    (verdict,) = judge([ask], fetched, principal=PRINCIPAL, owners={"vp-data": "UVPDATA"})

    assert verdict.status == "open"


def test_a_message_with_no_ts_is_ignored_rather_than_crashing():
    ask = _ask()
    fetched = {ask.key: [{"user": PRINCIPAL, "text": "no stamp"}, "not even a mapping"]}

    (verdict,) = judge([ask], fetched, principal=PRINCIPAL)

    assert verdict.status == "open"


# -- rendering ---------------------------------------------------------------


def test_answered_items_render_with_the_closing_quote_and_never_under_open():
    ask = _ask()
    fetched = {ask.key: [_msg(PRINCIPAL, "1789499598.000000", "here is my read")]}
    sections = render_closure(judge([ask], fetched, principal=PRINCIPAL), section="Owed by you")
    text = "\n".join(s.render() for s in sections if s.lines)

    assert "answered, strike?" in text and "here is my read" in text
    assert "open (0)" not in text, "empty buckets do not render"
    assert "will revert" in text


def test_unchecked_items_say_why_and_carry_their_permalink():
    ask = _ask()
    sections = render_closure(judge([ask], None, principal=PRINCIPAL), section="Owed by you")
    text = "\n".join(s.render() for s in sections if s.lines)

    assert "couldn't verify (1)" in text and "not read this run" in text
    assert ask.permalink in text


def test_every_rendered_line_is_sourced_or_admits_it_is_not():
    """Guardrail 3, applied to this module's output the way it is applied to
    every other push."""
    asks = asks_in(STATE, DECISIONS)
    fetched = {a.key: [_msg(PRINCIPAL, "1799999999.000000", "answered")] for a in asks if a.key}
    verdicts = judge(asks, fetched, principal=PRINCIPAL)
    text = "\n".join(
        s.render()
        for name in ("Chase list", "Owed by you", "Pending decisions")
        for s in render_closure(verdicts, section=name)
        if s.lines
    )

    assert brief.unsourced_claims(text) == [], brief.unsourced_claims(text)


def test_closure_knows_when_the_run_skipped_every_read():
    asks = asks_in(STATE)
    skipped = closure.Closure(tuple(judge(asks, None, principal=PRINCIPAL)))
    read = closure.Closure(tuple(judge(asks, {a.key: [] for a in asks}, principal=PRINCIPAL)))

    assert skipped.read_nothing and skipped.open_count == 0
    assert not read.read_nothing and read.open_count == 3


# -- through the runner ------------------------------------------------------


@pytest.fixture
def vault(tmp_path):
    from daydag.state import StateFolder

    env = tmp_path / ".env"
    env.write_text(
        f"SLACK_USER_PRINCIPAL={PRINCIPAL}\nEMAIL_PRINCIPAL=principal@x.com\n"
        f"VAULT_ROOT={tmp_path / 'vault'}\n",
        encoding="utf-8",
    )
    folder = StateFolder.create(tmp_path / "vault" / "DayDAG")
    folder.state_path.write_text(STATE, encoding="utf-8")
    folder.decisions_path.write_text(DECISIONS, encoding="utf-8")
    from daydag.config import Identities

    return Identities.from_file(env)


NOW = __import__("datetime").datetime(2026, 9, 18, 20, 0, tzinfo=__import__("datetime").UTC)


def test_plan_chase_names_one_read_per_ask_and_its_thread(vault):
    from daydag import run

    steps = run.plan("chase", now=NOW, identities=vault).steps
    reads = [s for s in steps if s.source == "slack" and s.detail.get("key")]

    assert {s.detail["conversation"] for s in reads} == {
        "CCHASE0001",
        "DOWED00001",
        "DPROMISE01",
        "CDECIDE001",
    }
    assert all(s.detail["thread_ts"] for s in reads), "every read names the thread to follow"
    assert not any(s.source == "calendar" for s in steps)


@pytest.mark.guardrail
def test_render_chase_without_the_reads_never_calls_anything_open(vault):
    """The 2026-09-18 failure, end to end: a run that skipped the reads used
    to list every line as owed. Now it says the reads were skipped."""
    from daydag import run

    text = run.render("chase", now=NOW, identities=vault, payloads={})

    assert "0 open" in text.splitlines()[0], text
    assert "couldn't check the replies" in text
    assert "compute consolidation" in text and "couldn't verify" in text


def test_render_chase_moves_an_answered_promise_out_of_open(vault):
    from daydag import run

    promise_key = "https://x.slack.com/archives/DPROMISE01/p1789488602000000"
    fetched = {
        promise_key: [
            _msg(PRINCIPAL, "1789499598.000000", "here is my read on it"),
            _msg("UOTHER", "1789502000.000000", "helpful, aligned"),
        ],
        "https://x.slack.com/archives/CCHASE0001/p1789000000000001": [],
    }

    text = run.render("chase", now=NOW, identities=vault, payloads={"closure": fetched})

    assert "1 open, verified" in text.splitlines()[0], text
    assert "will revert" in text and "answered, strike?" in text
    assert "here is my read on it" in text, "the closing message is quoted"
    assert "compute consolidation" in text and "nothing from them" in text
    assert "couldn't check" not in text, "reads happened - no skipped-reads line"
