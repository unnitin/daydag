"""Slack DM delivery (SPEC 3.1, issue #10).

Written before `daydag.delivery` existed, per CONTRIBUTING. Two things are
under test and neither is "does the string come out right":

1. **The brief reaches the principal's DM, and nowhere else can be named.**
   `deliver_push` takes no destination parameter - not a default, not an
   override - so there is nothing for a bug to get wrong.
2. **A send is a run**, and a failed one leaves a row that says so - the run
   log is what makes a silent delivery failure diagnosable at all.

The prep ping's threaded send (`deliver_prep_ping`) retired with the ping,
which nothing ever scheduled, and its six tests went with it (tag
`pre-retirement`). The guardrail-marked checks of the one destination live in
`tests/test_guardrails.py`.

The transport is injected throughout, exactly like `brief.py`'s sources -
nothing here imports a Slack client, so the suite runs offline and a real one
can be wired in without touching this module.
"""

from __future__ import annotations

import inspect
import sqlite3
from datetime import UTC, datetime

import pytest

from daydag import delivery
from daydag.config import Identities
from daydag.delivery import DeliveryError, deliver_push
from daydag.manifests import Registry, RegistryError
from daydag.runlog import DEGRADED, FAILED, OK, RunLog
from daydag.state import EventLog
from daydag.voice import Push

PRINCIPAL = "UPRINCIPAL1"
IDENTITIES = {"SLACK_USER_PRINCIPAL": PRINCIPAL}


def _runlog() -> RunLog:
    return RunLog(EventLog.open(":memory:"), clock=lambda: datetime(2026, 9, 8, 6, 40, tzinfo=UTC))


class _Recorder:
    """A fake `Transport`: records every call, hands back an incrementing ts."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self._fail = fail
        self._next = 1

    def __call__(self, *, channel: str, text: str, thread_ts: str | None = None):
        if self._fail:
            raise RuntimeError("slack said no")
        self.calls.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        ts = f"100.00{self._next}"
        self._next += 1
        return {"ok": True, "ts": ts}


# ---------------------------------------------------------------------------
# deliver_push: the brief (or any scheduled push) to the principal's DM
# ---------------------------------------------------------------------------


def test_deliver_push_posts_the_rendered_text_to_the_resolved_principal():
    transport = _Recorder()
    sent = delivery.deliver_push(
        "morning. tue - 4 meetings",
        kind=Push.MORNING_BRIEF,
        transport=transport,
        identities=IDENTITIES,
    )

    assert sent.channel == PRINCIPAL
    assert sent.text == "morning. tue - 4 meetings"
    assert sent.thread_ts is None
    assert transport.calls == [
        {"channel": PRINCIPAL, "text": "morning. tue - 4 meetings", "thread_ts": None}
    ]


def test_deliver_push_has_no_destination_parameter_at_all():
    """Guardrail 1, made structural: there is no kwarg to pass a different `to`."""
    params = set(inspect.signature(delivery.deliver_push).parameters)
    assert params.isdisjoint({"channel", "to", "destination", "recipient"})


@pytest.mark.parametrize(
    "identities",
    [
        {},
        {"SLACK_USER_PRINCIPAL": ""},
        {"SLACK_USER_PRINCIPAL": "not-a-slack-id"},
        {"SLACK_USER_PRINCIPAL": "${SLACK_USER_PRINCIPAL}"},
    ],
)
def test_deliver_push_refuses_a_bad_principal_id_rather_than_posting_to_it(identities):
    transport = _Recorder()
    with pytest.raises(delivery.DeliveryError):
        delivery.deliver_push(
            "hi", kind=Push.MORNING_BRIEF, transport=transport, identities=identities
        )
    assert transport.calls == [], "a bad destination must never reach the transport"


def test_deliver_push_never_echoes_the_bad_value_in_its_error():
    with pytest.raises(delivery.DeliveryError) as excinfo:
        delivery.deliver_push(
            "hi",
            kind=Push.MORNING_BRIEF,
            transport=_Recorder(),
            identities={"SLACK_USER_PRINCIPAL": "not-a-slack-id"},
        )
    assert "not-a-slack-id" not in str(excinfo.value)


def test_deliver_push_raises_when_the_transport_returns_no_ts():
    def no_ts_transport(*, channel, text, thread_ts=None):
        return {"ok": True}

    with pytest.raises(delivery.DeliveryError, match="ts"):
        delivery.deliver_push(
            "hi", kind=Push.MORNING_BRIEF, transport=no_ts_transport, identities=IDENTITIES
        )


# ---------------------------------------------------------------------------
# a delivery is a run (runlog integration)
# ---------------------------------------------------------------------------


def test_a_successful_push_leaves_an_ok_run_row():
    log = _runlog()
    delivery.deliver_push(
        "hi", kind=Push.MORNING_BRIEF, transport=_Recorder(), identities=IDENTITIES, runlog=log
    )
    row = log.last_run("delivery: morning_brief")
    assert row is not None
    assert row.outcome in (OK, DEGRADED)


def test_a_failed_send_leaves_a_failed_run_row_and_still_raises():
    """A failed send is exactly the silent failure `runlog` exists to make visible."""
    log = _runlog()
    with pytest.raises(RuntimeError, match="slack said no"):
        delivery.deliver_push(
            "hi",
            kind=Push.MORNING_BRIEF,
            transport=_Recorder(fail=True),
            identities=IDENTITIES,
            runlog=log,
        )
    row = log.last_run("delivery: morning_brief")
    assert row is not None
    assert row.outcome == FAILED
    assert "slack said no" in row.failure


def test_delivery_works_with_no_runlog_at_all():
    """`runlog` is optional - a caller with nowhere to log a row still gets a send."""
    sent = delivery.deliver_push(
        "hi", kind=Push.MORNING_BRIEF, transport=_Recorder(), identities=IDENTITIES, runlog=None
    )
    assert sent.channel == PRINCIPAL


# ---------------------------------------------------------------------------
# sensitivity routing: `Registry.route` is consulted, not bypassed
# ---------------------------------------------------------------------------


def test_deliver_push_consults_the_registry_when_given_one():
    registry = Registry.load(
        [{"name": "morning-brief", "daydag": {"writes": [], "sensitivity": "private"}}]
    )
    sent = delivery.deliver_push(
        "hi",
        kind=Push.MORNING_BRIEF,
        transport=_Recorder(),
        identities=IDENTITIES,
        registry=registry,
        skill="morning-brief",
    )
    assert sent.channel == PRINCIPAL, (
        "the DM is inside PRIVATE_SURFACES; a private skill may reach it"
    )


def test_deliver_push_fails_closed_for_an_unregistered_skill():
    """The guard applies here too, not only to callers who go through `route` directly."""
    registry = Registry.load([])
    with pytest.raises(RegistryError, match="unknown skill"):
        delivery.deliver_push(
            "hi",
            kind=Push.MORNING_BRIEF,
            transport=_Recorder(),
            identities=IDENTITIES,
            registry=registry,
            skill="ghost-skill",
        )


def test_deliver_push_skips_the_registry_check_when_no_skill_is_named():
    """Reading and drafting flows don't declare a skill; that must not be forced."""
    registry = Registry.load([])
    sent = delivery.deliver_push(
        "hi",
        kind=Push.MORNING_BRIEF,
        transport=_Recorder(),
        identities=IDENTITIES,
        registry=registry,
    )
    assert sent.channel == PRINCIPAL


# ---------------------------------------------------------------------------
# draft: a different return type, not a flag - and it never touches a transport
# ---------------------------------------------------------------------------


def test_draft_never_calls_the_transport():
    transport = _Recorder()
    note = delivery.draft(
        "hey vp-data - can you take a look?", to="UVPDATA01", reason="chase nudge"
    )

    assert isinstance(note, delivery.Draft)
    assert note.to == "UVPDATA01"
    assert note.text == "hey vp-data - can you take a look?"
    assert transport.calls == []


def test_no_function_in_the_module_accepts_a_draft_and_a_transport_together():
    """Unrepresentable, not merely unused: no signature names both types."""
    for name, member in vars(delivery).items():
        if not inspect.isfunction(member) or member.__module__ != delivery.__name__:
            continue
        hints = inspect.signature(member).parameters
        rendered = " ".join(str(p.annotation) for p in hints.values())
        assert not ("Draft" in rendered and "Transport" in rendered), (
            f"{name} accepts both a Draft and a Transport in one call"
        )


def test_draft_refuses_an_empty_destination():
    with pytest.raises(delivery.DeliveryError):
        delivery.draft("hi", to="")


def test_draft_refuses_empty_text():
    with pytest.raises(delivery.DeliveryError):
        delivery.draft("   ", to="UVPDATA01")


def test_a_preflight_failure_also_leaves_a_run_row(tmp_path):
    """Contract 4 says a raised failure is recorded before it is re-raised.

    That held for a transport failure and not for its twin. The guardrail
    checks - `_principal_channel`, `registry.route` - ran BEFORE
    `runlog.run(...)` was entered, so a bad `SLACK_USER_PRINCIPAL` or a stale
    skill name raised with no row at all.
    A caller reading `last_run("delivery: morning_brief")` to answer "did the
    brief even try to send" saw the previous success, which is the silent
    failure the run log exists to break.
    """
    env = tmp_path / ".env"
    env.write_text("SLACK_USER_PRINCIPAL=not-an-id\n", encoding="utf-8")
    log = RunLog(EventLog(sqlite3.connect(":memory:")), clock=lambda: datetime(2026, 9, 11))

    with pytest.raises(DeliveryError):
        deliver_push(
            "morning brief",
            kind=Push.MORNING_BRIEF,
            transport=lambda **kw: None,
            identities=Identities.from_file(env),
            runlog=log,
        )

    rows = log.rows()
    assert rows, "a pre-flight failure left no run row at all"
    assert rows[-1].outcome == FAILED
