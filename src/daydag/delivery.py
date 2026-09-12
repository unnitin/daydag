"""The one autonomous send, and the threaded prep reply (SPEC 3.1/3.2, issue #10).

USING IT
    identities = {"SLACK_USER_PRINCIPAL": "UPRINCIPAL1"}

    def transport(*, channel, text, thread_ts=None):
        return real_slack_client.post(channel=channel, text=text, thread_ts=thread_ts)

    sent = deliver_push(brief.render(), kind=Push.MORNING_BRIEF,
                         transport=transport, identities=identities)   # -> SentMessage
    sent.channel                                    # always the resolved principal id

    delivered = deliver_prep_ping(ping, transport=transport, identities=identities)
    delivered.detail.thread_ts == delivered.headline.ts   # the reply, in the ping's own thread

    note = draft("hey vp-data - is this still blocked?", to=vp_data_slack_id)  # never sent

CONTRACTS - break one and the guarantee is gone
    1. `deliver_push` and `deliver_prep_ping` take NO destination parameter.
       The channel is always this module's own resolved read of
       ``${SLACK_USER_PRINCIPAL}`` - not a default that happens to be that,
       something else entirely to pass. Guardrail 1 as a signature, not a
       runtime check: there is nothing to override.
    2. A message for anyone else is a :class:`Draft`, never a send. No
       function here accepts a `Draft` and a `Transport` together - a draft
       is data, and there is no path from that data to the wire in this
       module.
    3. A prep ping's headline is the interrupt - a new top-level message, per
       `prep.may_interrupt`. Its detail is a reply threaded under that
       headline's own `ts`, never a second top-level DM. If the headline posts
       and the detail then fails, the headline is not lost: it is observed
       into `runlog` before the raise, and the raised `DeliveryError` carries
       it as `error.headline` - a retry replies into that thread rather than
       resending the interrupt.
    4. No Slack client is imported here. `Transport` is a callable the caller
       injects, exactly like `smoke.py`'s probes and `brief.py`'s sources -
       what makes this module testable without a network, and what keeps the
       guardrail-suite tripwire in `tests/test_guardrails.py` meaningful.
    5. `runlog` is optional, and when given, a raised failure is recorded
       before it is re-raised. `RunLog.run` already does both halves; this
       module only has to hand it a `Run` to observe into.

WHY IT EXISTS
    Nothing in `src/daydag/` could deliver anything before this - every loop
    rendered text and stopped. SPEC 3.1 wants the brief in the principal's DM;
    3.2 wants a prep ping's detail one tap away in a thread rather than
    front-loaded (principle 6, "thin pushes"). This module is the seam between
    a rendered string and the one place it is allowed to go.

KNOWN LIMIT
    A `Draft` is a data object, and nothing stops a *different* module from
    building its own path from a `Draft` to a transport. What this module
    guarantees is narrower and, per the issue, exactly what matters: it holds
    no such path itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, TypeVar

from daydag.config import resolve_reference
from daydag.payloads import error_text
from daydag.prep import PrepPing, may_interrupt
from daydag.recipes import is_user_id
from daydag.registry import DM_SURFACE, Registry
from daydag.runlog import Run, RunLog
from daydag.smoke import REACHED
from daydag.voice import Push

_T = TypeVar("_T")

__all__ = [
    "DeliveryError",
    "Draft",
    "PartialPrepDelivery",
    "PrepDelivery",
    "SentMessage",
    "Transport",
    "deliver_prep_ping",
    "deliver_push",
    "draft",
]


class DeliveryError(RuntimeError):
    """A delivery was asked for something it must not do.

    Raised for a caller mistake - a misconfigured principal id, a transport
    that did not actually post - never used to represent a Slack-side error;
    those degrade like every other source (`daydag.smoke`), and a delivery
    failure is loud on purpose (see the module docstring's contract 5).
    """


class PartialPrepDelivery(DeliveryError):
    """The headline posted; its threaded detail did not.

    A typed field rather than an attribute bolted onto a plain `DeliveryError`
    at the raise site. That form needed a `type: ignore[attr-defined]`, existed
    on exactly one raise path, and left `error.headline` invisible to a type
    checker - so any later branch raising a bare `DeliveryError` from the same
    function would hand a caller an object it reasonably expects to carry one.
    `pulse.MirrorUnavailable` and `vault.ConflictError` already carry their
    context this way; this follows them.

    ``headline`` is the message that DID go out. A retry must reply into its
    thread, never resend the interrupt.
    """

    def __init__(self, message: str, *, headline: SentMessage) -> None:
        super().__init__(message)
        self.headline = headline


class Transport(Protocol):
    """The one capability this module needs: post a message, get its `ts` back.

    Injected by the caller and never imported here - see contract 4. A real
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
class PrepDelivery:
    """One prep ping, delivered: the interrupt and the reply threaded under it."""

    headline: SentMessage
    detail: SentMessage


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

    Mirrors `prep.recipient` and `brief._principal` - both already resolve
    this same reference with their own error type, and a shared helper would
    have to pick one caller's wording for every other caller's failure. The
    shape is checked as well as the presence: `.env` is hand-edited, and an
    empty value or a display name would otherwise address a conversation that
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
    """One `smoke`-shaped observation: a message went out. Fed to `run.observe`.

    Reuses `smoke`'s own vocabulary (`REACHED`) rather than a second literal
    "reached" - the run log already reads this shape from `smoke.as_rows()`,
    and a send is the same kind of fact: something was asked of a source and
    it answered.
    """
    return {"name": name, "source": "slack", "status": REACHED, "reason": "", "detail": "posted"}


def _send(
    transport: Transport, *, channel: str, text: str, thread_ts: str | None = None
) -> SentMessage:
    """Call the transport once and turn its answer into a `SentMessage`.

    Never trusts "it did not raise" as proof of success (the same lesson
    `smoke` encodes for every other source): a transport that swallows its
    own failure and returns something without a `ts` is refused here rather
    than handed back as a message nothing can thread under.
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

    The one place `deliver_push` and `deliver_prep_ping` share: both need "do
    the send, and if a run log was handed to us, record what happened either
    way" - without this they each carried their own copy of the same
    ``if runlog is None: ... else: with runlog.run(...) as run: ...`` branch.
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
        # Resolved and routed INSIDE the run, not before it. Contract 5 says a
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


def deliver_prep_ping(
    ping: PrepPing,
    *,
    transport: Transport,
    identities: Mapping[str, str],
    runlog: RunLog | None = None,
) -> PrepDelivery:
    """Post ``ping``'s headline as the interrupt, its detail as the reply.

    Uses `prep.may_interrupt` rather than restating which push kind is
    allowed to arrive off-schedule - see contract 3. Raises if that gate ever
    stops naming `Push.PREP_PING`, because this function's whole justification
    is that it is the one push permitted to skip the decision queue.

    If the headline posts but the threaded detail then fails, the headline is
    NOT lost: it was already observed into ``runlog`` (so the run row shows a
    partial send rather than a blank one), and the raised `DeliveryError`
    carries it as ``error.headline`` - a caller must not resend the headline
    on retry, only reply into the thread that already exists.
    """

    def _do(run: Run | None) -> PrepDelivery:
        # Gate and channel INSIDE the run, for the same reason as deliver_push:
        # a refusal is a failed attempt and has to leave a row saying so.
        if not may_interrupt(Push.PREP_PING):
            raise DeliveryError(
                "Push.PREP_PING is no longer flagged as the one interrupt "
                "(prep.may_interrupt); a push that cannot arrive off-schedule "
                "must not jump the decision queue by being sent here"
            )
        channel = _principal_channel(identities)
        headline = _send(transport, channel=channel, text=ping.headline())
        if run is not None:
            run.observe([_posted_row("slack dm")])
        try:
            detail = _send(transport, channel=channel, text=ping.detail(), thread_ts=headline.ts)
        except Exception as exc:
            # The interrupt already reached the principal - a retry must not
            # repeat it. Attached to the exception because there is no return
            # value to attach it to, and dropping it here is exactly the
            # "which one went out" question a caller has no other way to ask.
            raise PartialPrepDelivery(
                f"the ping's headline posted (ts={headline.ts}) but its threaded "
                f"detail did not ({exc}); do not resend the headline, reply into "
                "its thread instead",
                headline=headline,
            ) from exc
        if run is not None:
            # `slack dm` was already observed above - `Run.observe` keys by
            # name and the last write wins, so only the new fact goes here.
            run.observe([_posted_row("slack thread")])
        return PrepDelivery(headline=headline, detail=detail)

    return _delivered(runlog, f"delivery: {Push.PREP_PING.value}", _do)


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
