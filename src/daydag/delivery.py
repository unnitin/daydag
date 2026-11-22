"""The one autonomous send: a rendered push to the principal's DM (SPEC 3.1, issue #10).

USING IT
    identities = {"SLACK_USER_PRINCIPAL": "UPRINCIPAL1"}

    def transport(*, channel, text, thread_ts=None):
        return real_slack_client.post(channel=channel, text=text, thread_ts=thread_ts)

    sent = deliver_push(brief.render(), kind=Push.MORNING_BRIEF,
                         transport=transport, identities=identities)   # -> SentMessage
    sent.channel                                    # always the resolved principal id

    note = draft("hey vp-data - is this still blocked?", to=vp_data_slack_id)  # never sent

CONTRACTS - break one and the guarantee is gone
    1. `deliver_push` takes NO destination parameter. The channel is always
       this module's own resolved read of ``${SLACK_USER_PRINCIPAL}`` - not a
       default that happens to be that, something else entirely to pass.
       Guardrail 1 as a signature, not a runtime check: there is nothing to
       override. The id's SHAPE is checked too, so an empty value, a display
       name or an id with an inline comment glued on is refused before the
       transport is called.
    2. A message for anyone else is a :class:`Draft`, never a send. No
       function here accepts a `Draft` and a `Transport` together - a draft
       is data, and there is no path from that data to the wire in this
       module.
    3. No Slack client is imported here. `Transport` is a callable the caller
       injects, exactly like `brief.py`'s sources - what makes this module
       testable without a network, and what keeps the guardrail-suite tripwire
       in `tests/test_guardrails.py` meaningful.
    4. `runlog` is optional, and when given, a raised failure is recorded
       before it is re-raised. `RunLog.run` already does both halves; this
       module only has to hand it a `Run` to observe into.

WHY IT EXISTS
    Nothing in `src/daydag/` could deliver anything before this - every loop
    rendered text and stopped. SPEC 3.1 wants the brief in the principal's DM.
    This module is the seam between a rendered string and the one place it is
    allowed to go.

    It also carried the prep ping's send: the headline as a new top-level
    message and the talking points as a reply threaded under it (SPEC 3.2,
    principle 6). Nothing ever scheduled a ping, so `deliver_prep_ping` and
    its two result types retired on 2026-11-21 and are at tag
    `pre-retirement`. `Transport` still takes a `thread_ts`, so a threaded
    reply stays expressible to whoever wires one.

KNOWN LIMIT
    A `Draft` is a data object, and nothing stops a *different* module from
    building its own path from a `Draft` to a transport. What this module
    guarantees is narrower and, per the issue, exactly what matters: it holds
    no such path itself.

    No loop calls `deliver_push` and no `Transport` is implemented: a push is
    posted today by the agent that ran `render`, not by this module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, TypeVar

from daydag.config import resolve_reference
from daydag.manifests import DM_SURFACE, Registry
from daydag.payloads import error_text
from daydag.recipes import is_user_id
from daydag.runlog import REACHED, Run, RunLog
from daydag.voice import Push

_T = TypeVar("_T")

__all__ = [
    "DeliveryError",
    "Draft",
    "SentMessage",
    "Transport",
    "deliver_push",
    "draft",
]


class DeliveryError(RuntimeError):
    """A delivery was asked for something it must not do.

    Raised for a caller mistake - a misconfigured principal id, a transport
    that did not actually post - never used to represent a Slack-side error;
    those degrade like every other source (`brief.Reader`), and a delivery
    failure is loud on purpose (see the module docstring's contract 4).
    """


class Transport(Protocol):
    """The one capability this module needs: post a message, get its `ts` back.

    Injected by the caller and never imported here - see contract 3. A real
    implementation wraps whatever the Slack connector calls its send; this
    module never names it.
    """

    def __call__(
        self, *, channel: str, text: str, thread_ts: str | None = None
    ) -> Mapping[str, object]:
        """Post ``text`` into ``channel``, optionally into ``thread_ts``.

        Must return a mapping carrying the posted message's own ``ts`` - that
        is the only way a reply can later thread under it.
        """


@dataclass(frozen=True)
class SentMessage:
    """A message that was actually posted."""

    channel: str
    text: str
    ts: str
    thread_ts: str | None = None


@dataclass(frozen=True)
class Draft:
    """A message addressed to someone other than the principal.

    Guardrail 1's "drafts, not sends" as a type: this is what "send to
    someone else" is allowed to become, and it is data, not a pending action.
    Nothing in this module reads a `Draft` back in - see contract 2.
    """

    to: str
    text: str
    reason: str = ""


def _principal_channel(identities: Mapping[str, str]) -> str:
    """The one Slack id every autonomous send may address (guardrail 1).

    `brief._principal` and `run._principal` resolve this same reference with
    their own error type, and a shared helper would have to pick one caller's
    wording for every other caller's failure. Those two need the id to build
    a query; this is the only one that ADDRESSES anything with it, so it is
    the one that checks the shape as well as the presence: `.env` is
    hand-edited, and an empty value, a display name or a trailing inline
    comment glued onto the id would otherwise address a conversation that
    does not exist. Never echoes the value in an error - `config.ConfigError`
    holds the same rule, because printing the id defeats keeping it out of a
    public repo.
    """
    value = resolve_reference(
        "${SLACK_USER_PRINCIPAL}",
        identities,
        what="the delivery recipient",
        error=DeliveryError,
    )
    if not is_user_id(value):
        raise DeliveryError(
            "SLACK_USER_PRINCIPAL is not a Slack user id. Fix it in .env - and "
            "note that everything after the `=` is the value, inline comment included."
        )
    return value


def _posted_row(name: str) -> dict[str, str]:
    """One run-log row: a message went out. Fed to `run.observe`.

    Uses `runlog.REACHED` rather than a second literal "reached" - the row is
    the shape `Run.observe` takes for any source, and a send is the same kind
    of fact: something was asked of a source and it answered.
    """
    return {"name": name, "source": "slack", "status": REACHED, "reason": "", "detail": "posted"}


def _send(
    transport: Transport, *, channel: str, text: str, thread_ts: str | None = None
) -> SentMessage:
    """Call the transport once and turn its answer into a `SentMessage`.

    Never trusts "it did not raise" as proof of success: a transport that
    swallows its own failure and returns something without a `ts` is refused
    here rather than handed back as a message nothing can thread under.
    """
    response = transport(channel=channel, text=text, thread_ts=thread_ts)
    ts = response.get("ts") if isinstance(response, Mapping) else None
    if not ts:
        raise DeliveryError(
            "the transport returned no ts for the message it posted, so a reply "
            f"could not be threaded under it ({error_text(response) or 'no error given'})"
        )
    return SentMessage(channel=channel, text=text, ts=str(ts), thread_ts=thread_ts)


def _delivered(runlog: RunLog | None, loop: str, body: Callable[[Run | None], _T]) -> _T:
    """Run ``body``, observing into a real `Run` when ``runlog`` is given.

    `deliver_push`'s run-log branch, kept out of its body: do the send, and
    if a run log was handed to us, record what happened either way.
    ``body`` gets the live `Run` to observe into, or ``None`` when there is
    nowhere to record - it is responsible for calling ``run.observe(...)``
    itself, because only it knows what was actually reached.
    """
    if runlog is None:
        return body(None)
    with runlog.run(loop) as run:
        result = body(run)
    return result


def deliver_push(
    text: str,
    *,
    kind: Push,
    transport: Transport,
    identities: Mapping[str, str],
    runlog: RunLog | None = None,
    registry: Registry | None = None,
    skill: str | None = None,
) -> SentMessage:
    """Post one already-rendered push to the principal's DM. See contract 1.

    ``kind`` names which push this is (`Push.MORNING_BRIEF`, `Push.EOD_WRAP`,
    ...) for the run-log row only - it is rendered VERBATIM there, so only a
    fixed `Push` member reaches it, never text a connector or a note wrote
    (`runlog`'s contract 3).

    ``registry`` and ``skill`` are both optional and used together: give both
    to have this call `Registry.route` before sending, so a `sensitivity:
    private` skill is refused a surface with an audience above one even here
    - the DM is inside `PRIVATE_SURFACES`, so a private skill may always reach
    it, but an unregistered ``skill`` still fails closed. Neither is required
    for a caller with nothing to declare.

    Args:
        runlog: when given, this send is recorded as one run - a failed send
            is exactly the silent failure that module exists to make visible.
    """

    def _do(run: Run | None) -> SentMessage:
        # Resolved and routed INSIDE the run, not before it. Contract 4 says a
        # raised failure is recorded before it is re-raised, and that held for
        # a transport failure while its twin - a bad SLACK_USER_PRINCIPAL, a
        # stale skill name - raised with no row at all. A caller asking the run
        # log "did the brief even try to send" saw the previous success.
        channel = _principal_channel(identities)
        if registry is not None and skill is not None:
            registry.route(skill, to=DM_SURFACE)
        sent = _send(transport, channel=channel, text=text)
        if run is not None:
            run.observe([_posted_row("slack dm")])
        return sent

    return _delivered(runlog, f"delivery: {kind.value}", _do)


def draft(text: str, *, to: str, reason: str = "") -> Draft:
    """A message addressed to someone other than the principal. Never sent.

    Guardrail 1's other half: everything not bound for the principal's DM is
    a `Draft`, awaiting an explicit per-message yes somewhere else entirely -
    this function has no transport parameter, because drafting never sends.
    """
    if not (to or "").strip():
        raise DeliveryError("a draft needs a destination; deliver_push is what the principal gets")
    if not (text or "").strip():
        raise DeliveryError("a draft needs something to say")
    return Draft(to=to.strip(), text=text, reason=reason)
