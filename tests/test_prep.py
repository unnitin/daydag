"""Meeting prep (SPEC sections 3.2 and 3.8, issue #20).

Written before `daydag.prep` existed. Each test names the behaviour someone
wants, which is the only reason the module has the shape it has.

Two things here are easy to get subtly wrong and are therefore asserted hard:

* **Qualification is the ledger's, narrowed - never re-derived.** The ledger
  already decided what counts as a meeting at all, and the #2 audit paid for
  that rule in blood (requiring `accepted` dropped 61% of real meetings). Prep
  consumes rows and applies only section 3.2's *narrower* filter on top. A test
  below asserts a declined event never reaches prep, and it passes because the
  row never existed - not because prep checked a response status.

* **Verbatim quotes are evidence, not voice.** House rule 1 says quote
  verbatim; SPEC section 5 bans em dashes. Somebody else's Slack message may
  contain one, and mangling it to satisfy the voice rules would break the rule
  that matters more. So the voice check runs over what the agent *wrote*.

The second half of the file is the selector: naming the meeting he wants
prepped (`prep.select`), and asking rather than guessing when the name matches
two. Those tests moved here with `prep_selector` when it folded into `prep`.

The 30-minute ping these began as was never scheduled, and the code only a
poller needed retired (tag `pre-retirement`), with the tests of its due
window. Its guardrails did not: the one destination and the id's shape are in
`tests/test_guardrails.py`, against `delivery.deliver_push`, beside the
tripwire that replaced the interrupt gate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from daydag import prep
from daydag.config import Identities
from daydag.ledger import NON_MEETING_KINDS, Ledger, Row, attendee_parts
from daydag.prep import HORIZON_DAYS, Selection
from daydag.prep import select as _select
from daydag.recipes import RecipeError
from voice_check import voice_violations

PT = timezone(timedelta(hours=-7))
MONDAY_9AM = datetime(2026, 9, 7, 9, 0, tzinfo=PT)

#: Attendee tokens. Domains are `example.com` and `vendor.example`, which the
#: secret scanner allowlists precisely so fixtures can be realistic.
NITIN = "principal@example.com"
VP_DATA = "vp-data@example.com"
SPONSOR = "sponsor@example.com"
OUTSIDER = "partner@vendor.example"

AUDIENCE = prep.Audience(
    leadership=frozenset({SPONSOR}),
    internal_domains=frozenset({"example.com"}),
)


def _event(event_id, summary, start, *, attendees=(NITIN, VP_DATA), kind="meeting", response=None):
    event = {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": start + timedelta(hours=1),
        "attendees": list(attendees),
        "kind": kind,
    }
    if response is not None:
        event["response_status"] = response
    return event


def _rows(*events):
    ledger = Ledger()
    ledger.seed_day(events)
    return ledger.open_rows()


def _row(*args, **kwargs):
    return _rows(_event(*args, **kwargs))[0]


# ---------------------------------------------------------------------------
# qualification: the ledger's rows, narrowed
# ---------------------------------------------------------------------------


def test_a_one_on_one_qualifies():
    row = _row("11", "Nitin / VP-Data 1:1", MONDAY_9AM)
    assert prep.prep_worthy(row, AUDIENCE) is prep.Reason.ONE_ON_ONE


def test_two_attendees_is_a_one_on_one_even_with_a_bland_title():
    """He does not name every 1:1 "1:1"; half of them are two first names."""
    row = _row("sync", "Nitin / VP-Data", MONDAY_9AM)
    assert prep.prep_worthy(row, AUDIENCE) is prep.Reason.ONE_ON_ONE


def test_steering_and_pod_meetings_qualify():
    for summary in ("Pod Steering", "Discovery Pod Sync", "Data + AI Steering Committee"):
        row = _row("s", summary, MONDAY_9AM, attendees=(NITIN, VP_DATA, "eng@example.com"))
        assert prep.prep_worthy(row, AUDIENCE) is prep.Reason.STEERING, summary


def test_a_meeting_with_leadership_in_the_room_qualifies():
    row = _row(
        "rev",
        "Revenue Review",
        MONDAY_9AM,
        attendees=(NITIN, VP_DATA, SPONSOR),
    )
    assert prep.prep_worthy(row, AUDIENCE) is prep.Reason.LEADERSHIP


def test_a_meeting_with_an_outside_domain_qualifies():
    row = _row("vend", "Vendor Sync", MONDAY_9AM, attendees=(NITIN, VP_DATA, OUTSIDER))
    assert prep.prep_worthy(row, AUDIENCE) is prep.Reason.EXTERNAL


def test_an_opaque_attendee_is_not_guessed_to_be_external():
    """A calendar that hands back names rather than addresses says nothing.

    Treating "vp-data" as external because it has no domain would fire the one
    interrupt the system allows on a routine internal meeting.
    """
    row = _row("plain", "Weekly Sync", MONDAY_9AM, attendees=("nitin", "vp-data", "eng-2"))
    assert prep.prep_worthy(row, AUDIENCE) is None


def test_a_standup_never_qualifies_however_many_ways_it_is_spelled():
    for summary in ("DE Standup", "Daily Stand-up", "Discovery scrum"):
        row = _row("su", summary, MONDAY_9AM, attendees=(NITIN, VP_DATA))
        assert prep.prep_worthy(row, AUDIENCE) is None, summary


def test_a_standup_with_leadership_in_it_is_still_a_standup():
    """The skip is not a tiebreak, it is a veto. Section 3.2 lists it flat."""
    row = _row("su", "Leadership Standup", MONDAY_9AM, attendees=(NITIN, SPONSOR))
    assert prep.prep_worthy(row, AUDIENCE) is None


def test_prep_never_sees_a_declined_meeting_because_the_ledger_never_made_a_row():
    """Qualification is reused, not rewritten.

    If prep grew its own response-status check the two would drift, and the #2
    audit is what that costs: requiring `accepted` dropped 61% of real meetings.
    """
    rows = _rows(_event("no", "Nitin / VP-Data 1:1", MONDAY_9AM, response="declined"))
    assert rows == []


@pytest.mark.parametrize("kind", sorted(NON_MEETING_KINDS))
def test_focus_blocks_and_holds_are_the_ledgers_job_not_preps(kind):
    """Prep owns exactly one extra rule - standups. The rest is already gone."""
    assert _rows(_event("f", "Focus", MONDAY_9AM, kind=kind)) == []


def test_an_unanswered_invite_still_gets_prepped():
    row = _row("11", "Nitin / VP-Data 1:1", MONDAY_9AM, response="needsAction")
    assert prep.prep_worthy(row, AUDIENCE) is prep.Reason.ONE_ON_ONE


# ---------------------------------------------------------------------------
# sources: the File 2 recipe, as queries for one meeting
# ---------------------------------------------------------------------------


@pytest.fixture
def identities(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "SLACK_USER_PRINCIPAL=UPRINCIPAL1\n"
        "SLACK_CH_POD=CPODCHANNEL\n"
        "ORG_EMAIL_DOMAIN=example.com\n"
        f"PREP_LEADERSHIP={SPONSOR}\n",
        encoding="utf-8",
    )
    return Identities.from_file(env)


def test_sources_span_the_last_four_weeks(identities):
    row = _row("s", "Pod Steering", MONDAY_9AM, attendees=(NITIN, VP_DATA, "e@example.com"))
    plan = prep.sources(row, MONDAY_9AM, identities=identities)
    start, end = plan.window
    assert end == MONDAY_9AM.date()
    assert (end - start).days == prep.LOOKBACK_DAYS


def test_sources_ask_gmail_for_that_meetings_notes_by_exact_title(identities):
    """The #2 audit's correction, used where it pays: the subject is exact.

    Body matching cannot separate two back-to-back 1:1s, which is the case that
    actually breaks a prep ping - it prepares the wrong meeting.
    """
    row = _row("s", "Pod Steering", MONDAY_9AM, attendees=(NITIN, VP_DATA, "e@example.com"))
    plan = prep.sources(row, MONDAY_9AM, identities=identities)
    assert 'subject:"Pod Steering"' in plan.gemini
    assert "from:gemini-notes@google.com" in plan.gemini
    assert plan.degraded == ()


@pytest.mark.guardrail
def test_a_title_that_cannot_be_quoted_degrades_to_a_dated_sweep(identities):
    """Guardrail 6: a named degrade, never a query that silently matches nothing.

    `recipes.gmail_gemini_notes` refuses a title containing a quote rather than
    escaping it wrong. Prep must survive that, and say that it did.
    """
    row = _row("s", 'The "Dream" Steering', MONDAY_9AM, attendees=(NITIN, VP_DATA))
    plan = prep.sources(row, MONDAY_9AM, identities=identities)
    assert "subject:" not in plan.gemini
    assert "after:" in plan.gemini
    assert any("title" in reason for reason in plan.degraded)


def test_slack_queries_are_channel_scoped_by_id(identities):
    row = _row("s", "Pod Steering", MONDAY_9AM, attendees=(NITIN, VP_DATA, "e@example.com"))
    plan = prep.sources(row, MONDAY_9AM, identities=identities, channels=("${SLACK_CH_POD}",))
    assert len(plan.slack) == 1
    assert "in:<#CPODCHANNEL>" in plan.slack[0]
    assert "sort:" not in plan.slack[0]


@pytest.mark.guardrail
def test_an_unresolvable_channel_degrades_rather_than_dropping_the_ping(identities):
    row = _row("s", "Pod Steering", MONDAY_9AM, attendees=(NITIN, VP_DATA, "e@example.com"))
    plan = prep.sources(row, MONDAY_9AM, identities=identities, channels=("#pod-discovery",))
    assert plan.slack == ()
    assert any("pod-discovery" in reason for reason in plan.degraded)


def test_sources_name_the_weeks_meeting_prep_note(identities):
    row = _row("s", "Pod Steering", MONDAY_9AM, attendees=(NITIN, VP_DATA, "e@example.com"))
    plan = prep.sources(row, MONDAY_9AM, identities=identities)
    assert plan.prep_note.endswith("Meeting Prep/0907-0911.md")


def test_sources_names_the_meetings_week_not_the_caller_week(identities):
    """`prep_note` is `meeting_prep(row.start)`, never `meeting_prep(now)`.

    They agree for a same-day, 30-min ping - which is why this looked right -
    and disagree the first time `sources()` runs ahead of time, from a
    different week: the Sunday week-ahead (SPEC 3.6) builds Monday's prep the
    evening before, from the closing week. Naming `now`'s week there pointed
    at a real file, just the wrong one - Meeting Prep for the week ending, not
    the week the meeting is actually in.
    """
    sunday_before = MONDAY_9AM - timedelta(hours=15, minutes=30)  # sun 5:30pm
    row = _row("s", "Pod Steering", MONDAY_9AM, attendees=(NITIN, VP_DATA, "e@example.com"))
    plan = prep.sources(row, sunday_before, identities=identities)
    assert plan.prep_note.endswith("Meeting Prep/0907-0911.md"), (
        f"named the caller's week instead of the meeting's: {plan.prep_note}"
    )


def test_a_naive_meeting_start_names_its_own_wall_clock_day_not_the_hosts():
    """`_local_day` must not reinterpret a naive start via the host's zone.

    Found by `/code-review` on the fix above: `.astimezone()` called directly
    on a NAIVE value adopts whatever zone the runner has - the same failure
    class `brief._local` guards against for an event's start, and the reason
    that fix reads `start.replace(tzinfo=PACIFIC)` first rather than calling
    `.astimezone(PACIFIC)` on the naive value straight away. A midnight-thirty
    meeting is the case that catches a host-zone leak: under a host east of
    Pacific it would roll back to the PRIOR calendar day.
    """
    early = datetime(2026, 9, 7, 0, 30)  # naive - no host zone should move this
    assert prep._local_day(early) == date(2026, 9, 7)


def test_sources_refuse_a_row_that_does_not_qualify(identities):
    row = _row("su", "DE Standup", MONDAY_9AM)
    with pytest.raises(prep.PrepError):
        prep.sources(row, MONDAY_9AM, identities=identities)


def test_sources_refuse_a_naive_now(identities):
    """The 4-week window is bounded by local days, so the zone is load-bearing.

    Same failure as the overnight cutoff: a naive `now` passed on a Pacific
    laptop and was seven hours - and at the edges a whole day - wrong on a
    UTC runner.
    """
    row = _row("s", "Pod Steering", MONDAY_9AM, attendees=(NITIN, VP_DATA, "e@example.com"))
    with pytest.raises(prep.PrepError):
        prep.sources(row, MONDAY_9AM.replace(tzinfo=None), identities=identities)


def test_sources_never_perform_io(identities, monkeypatch):
    """Pure by construction, so the part that must be right is checkable."""
    monkeypatch.setattr(
        "socket.socket", lambda *a, **k: pytest.fail("prep.sources opened a socket")
    )
    row = _row("s", "Pod Steering", MONDAY_9AM, attendees=(NITIN, VP_DATA, "e@example.com"))
    prep.sources(row, MONDAY_9AM, identities=identities, channels=("${SLACK_CH_POD}",))


# ---------------------------------------------------------------------------
# points: a why-now each, evidence or an admission
# ---------------------------------------------------------------------------

QUOTE = "please answer if we already merged all PRs"
LINK = "https://example.com/archives/C01/p1757000000"


def _point(what="the cutover date", why_now="he owes an answer since thu", **kw):
    kw.setdefault("quote", QUOTE)
    kw.setdefault("permalink", LINK)
    return prep.point(what, why_now, **kw)


def test_a_point_without_a_why_now_is_dropped_not_padded():
    """SPEC 3.2: "sprint demo" is noise; "sprint demo - force the decision" is not."""
    assert prep.point("sprint demo", None, quote=QUOTE, permalink=LINK) is None
    assert prep.point("sprint demo", "   ", quote=QUOTE, permalink=LINK) is None


def test_a_point_keeps_its_why_now_in_the_rendered_line():
    point = _point("sprint demo", "frame the wins, force the teaser decision")
    assert "frame the wins, force the teaser decision" in point.render()


@pytest.mark.guardrail
def test_a_quote_without_a_permalink_is_refused():
    """Invariant 3, structural: a quote nobody can open is a paraphrase."""
    with pytest.raises(prep.PrepError):
        prep.point("the cutover date", "he owes an answer", quote=QUOTE, permalink=None)


@pytest.mark.guardrail
def test_a_permalink_without_a_quote_is_refused():
    with pytest.raises(prep.PrepError):
        prep.point("the cutover date", "he owes an answer", quote=None, permalink=LINK)


@pytest.mark.guardrail
def test_an_unsourced_point_says_so_rather_than_looking_sourced():
    point = prep.point("the cutover date", "still nothing on the board")
    assert not point.sourced
    assert prep.UNSOURCED in point.render()


def test_a_sourced_point_carries_the_quote_and_the_permalink():
    rendered = _point().render()
    assert QUOTE in rendered
    assert LINK in rendered


# ---------------------------------------------------------------------------
# the ping: 3-5 points, or an honest short one
# ---------------------------------------------------------------------------


def _ping(points, summary="Pod Steering"):
    row = _row("s", summary, MONDAY_9AM, attendees=(NITIN, VP_DATA, "e@example.com"))
    return prep.build(row, prep.Reason.STEERING, points)


def test_a_ping_caps_at_five_points():
    ping = _ping([_point(f"point {n}") for n in range(9)])
    assert len(ping.points) == prep.MAX_POINTS


def test_a_ping_of_two_stays_two_rather_than_padding_to_three():
    """Section 3.2 says 3-5; the instruction on top of it says drop, never pad.

    Padding is how a prep ping becomes something he skims, and a skimmed
    interrupt costs more than a missing one.
    """
    ping = _ping([_point("one"), _point("two")])
    assert len(ping.points) == 2


def test_points_without_a_why_now_are_filtered_out_of_the_ping():
    candidates = [_point("one"), prep.point("two", None), _point("three")]
    assert len(_ping(candidates).points) == 2


@pytest.mark.guardrail
def test_no_material_yields_a_short_honest_ping_not_silence_and_not_an_invention():
    """Guardrail 6 at the one moment it is most tempting to make something up."""
    ping = _ping([])
    assert ping.points == ()
    assert ping.gap == prep.NO_MATERIAL
    body = ping.render()
    assert "Pod Steering" in body
    assert prep.NO_MATERIAL in body
    assert body.strip(), "silence is not a degrade, it is a disappearance"


def test_the_headline_is_the_sanctioned_prep_push():
    ping = _ping([_point()])
    assert ping.headline() == "Pod Steering in 30 - talking points in thread"


def test_the_ping_records_why_the_meeting_qualified():
    ping = _ping([_point()])
    assert ping.reason is prep.Reason.STEERING


# ---------------------------------------------------------------------------
# voice
# ---------------------------------------------------------------------------


def _own_words(ping) -> str:
    """What the agent composed: the render minus each point's evidence line.

    `Point.render` puts a quote and its link on a line of their own, indented
    under the point. Those words are somebody else's (house rule 1), so the
    voice check runs over every other line of what `run` actually prints.
    """
    evidence = {f'  "{p.quote}" {p.permalink}' for p in ping.points if p.sourced}
    return "\n".join(line for line in ping.render().splitlines() if line not in evidence)


def test_the_ping_renders_clean_in_the_house_voice():
    ping = _ping([_point(), _point("the modeler handoff", "MODELER-OWNER is out from wed")])
    assert voice_violations(ping.render()) == [], ping.render()


def test_the_degraded_ping_renders_clean_in_the_house_voice():
    assert voice_violations(_ping([]).render()) == []


@pytest.mark.guardrail
def test_a_verbatim_quote_keeps_its_em_dash_and_the_agents_own_words_stay_clean():
    """House rule 1 outranks section 5 inside a quotation, and only there.

    Rewriting someone's message to satisfy a style rule is the paraphrase the
    quoting rule exists to prevent. So the em dash survives, and the voice check
    runs over what the agent wrote: the render `run` prints, with the quoted
    line taken out.
    """
    dashed = "we merged everything — ready for 10k"
    ping = _ping([_point(quote=dashed)])
    assert dashed in ping.render(), "the quote was silently rewritten"
    assert dashed not in _own_words(ping), "the quoted line was not excised"
    assert voice_violations(_own_words(ping)) == []
    assert any("dash" in v for v in voice_violations(ping.render()))


def test_an_unsanctioned_emoji_in_the_agents_own_words_is_caught():
    ping = _ping([_point("ship it \U0001f680")])
    assert voice_violations(_own_words(ping))


# ---------------------------------------------------------------------------
# no send path
# ---------------------------------------------------------------------------


@pytest.mark.guardrail
def test_prep_exposes_no_send_path():
    """Guardrail 1. Prep builds the text; nothing in the module can post it."""
    exported = {name for name in dir(prep) if not name.startswith("_")}
    assert not exported & {"send", "post", "dm", "deliver", "reply", "forward"}


# ---------------------------------------------------------------------------
# audience configuration
# ---------------------------------------------------------------------------


def test_audience_comes_from_config_not_from_the_source(identities):
    audience = prep.Audience.from_identities(identities)
    assert SPONSOR in audience.leadership
    # Equality, not `"example.com" in ...`. The membership form is correct here
    # (internal_domains is a frozenset, so `in` is an exact match, not a
    # substring test) but it is indistinguishable from the substring form that
    # CodeQL flags as py/incomplete-url-substring-sanitization - and the config
    # under test declares exactly one domain, so equality asserts more anyway.
    assert audience.internal_domains == frozenset({"example.com"})


@pytest.mark.guardrail
def test_a_missing_org_domain_means_nobody_is_declared_external(tmp_path):
    """Unset config must not turn every attendee into an outside party.

    Defaulting to "everything is external" would fire the interrupt on every
    meeting on the calendar, which is how the one interrupt gets muted.
    """
    env = tmp_path / ".env"
    env.write_text("SLACK_USER_PRINCIPAL=UPRINCIPAL1\n", encoding="utf-8")
    audience = prep.Audience.from_identities(Identities.from_file(env))
    row = _row("vend", "Vendor Sync", MONDAY_9AM, attendees=(NITIN, VP_DATA, OUTSIDER))
    assert prep.prep_worthy(row, audience) is None


def test_prep_errors_are_recipe_errors_so_one_catch_covers_the_seam(identities):
    """`recipes` raises `RecipeError`; a caller of prep should catch one type."""
    assert issubclass(prep.PrepError, RecipeError)


# ---------------------------------------------------------------------------
# naming the meeting: `prep.select`
#
# `prep` preps the NEXT qualifying meeting. Asking for a particular one is a
# different question - *"prep me for the Finance call"* - and it had nowhere to
# go.
#
# The hard part is not matching. It is what to do when the selector matches more
# than one meeting, which is the common case rather than the edge: "Wren" is in a
# recurring 1:1 and also in a steering meeting, and a week holds several of each.
# Invariant 5 says surface, do not resolve. Guessing which one he meant produces
# prep for the wrong meeting, and prep for the wrong meeting is worse than none -
# he reads it, trusts it, and walks into the other one cold.
#
# Names here are fixtures. The repo is public; real ones live in `.env` and
# arrive as arguments.
# ---------------------------------------------------------------------------

NOW = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
UNTIL = NOW + timedelta(days=HORIZON_DAYS)


def select(rows, selector, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("until", UNTIL)
    return _select(rows, selector, **kw)


def _row_at(summary: str, *attendees: str, hours: int = 4, event_id: str = "") -> Row:
    """Attendees may be bare addresses or `"Name <addr>"`; split the way
    `Ledger.seed_day` does, so these rows look like real ones."""
    start = NOW + timedelta(hours=hours)
    parts = [attendee_parts(a) for a in attendees]
    return Row(
        event_id=event_id or summary.lower().replace(" ", "-"),
        start=start,
        end=start + timedelta(minutes=30),
        summary=summary,
        attendees=[addr for addr, _ in parts],
        attendee_names=[name for _, name in parts],
    )


PRINCIPAL = "principal@example.com"


# --------------------------------------------------------------------------
# matching a person
# --------------------------------------------------------------------------


def test_a_first_name_matches_the_attendee_it_belongs_to():
    rows = [
        _row_at("1:1 Bi-Weekly | Nitin x Wren", PRINCIPAL, "wren.alder@example.com"),
        _row_at("Pod Steering", PRINCIPAL, "bo.finch@example.com", hours=6),
    ]

    found = select(rows, "wren", now=NOW)

    assert found.one is not None
    assert found.one.summary.endswith("Wren")


def test_a_full_name_needs_both_halves_to_match():
    """ "Bo Finch" must not match a different Bo. Two tokens, both required."""
    rows = [
        _row_at("Sync", PRINCIPAL, "bo.alder@example.com"),
        _row_at("Review", PRINCIPAL, "bo.finch@example.com", hours=6),
    ]

    found = select(rows, "bo finch", now=NOW)

    assert found.one is not None
    assert found.one.summary == "Review"


def test_a_name_in_the_title_matches_even_with_no_attendee_list():
    """An ATS interview lists only the principal - the other party comes
    through a different calendar - so the title is all there is."""
    rows = [_row_at("Interview - Wren Alder - Staff Engineer", PRINCIPAL)]

    assert select(rows, "wren alder", now=NOW).one is not None


# --------------------------------------------------------------------------
# matching a title
# --------------------------------------------------------------------------


def test_a_phrase_matches_the_title_loosely():
    rows = [
        _row_at("Finance x Data meeting", PRINCIPAL, "a@example.com"),
        _row_at("Pod Steering", PRINCIPAL, "b@example.com", hours=6),
    ]

    assert select(rows, "finance x data", now=NOW).one is not None


def test_matching_ignores_case_and_spacing():
    rows = [_row_at("Finance x Data meeting", PRINCIPAL, "a@example.com")]

    assert select(rows, "  FINANCE X DATA  ", now=NOW).one is not None


# --------------------------------------------------------------------------
# the case that matters: more than one
# --------------------------------------------------------------------------


def test_two_matches_are_surfaced_rather_than_resolved():
    """Invariant 5. Prep for the wrong meeting is worse than no prep - he reads
    it, trusts it, and walks into the other one cold."""
    rows = [
        _row_at("1:1 | Nitin x Wren", PRINCIPAL, "wren.alder@example.com", hours=4),
        _row_at("Pod Steering", PRINCIPAL, "wren.alder@example.com", hours=30),
    ]

    found = select(rows, "wren", now=NOW)

    assert found.one is None
    assert len(found.candidates) == 2


def test_the_same_meeting_twice_in_the_horizon_is_still_ambiguous():
    """A recurring 1:1 twice a week is the common case, not the edge. Silently
    taking the sooner one is a guess dressed as an answer."""
    rows = [
        _row_at("1:1 | 2x weekly | Nitin x Wren", PRINCIPAL, "wren.alder@example.com", hours=4),
        _row_at(
            "1:1 | 2x weekly | Nitin x Wren",
            PRINCIPAL,
            "wren.alder@example.com",
            hours=72,
            event_id="later",
        ),
    ]

    found = select(rows, "wren", now=NOW)

    assert found.one is None
    assert len(found.candidates) == 2


def test_nothing_matching_says_what_it_looked_for():
    rows = [_row_at("Pod Steering", PRINCIPAL, "a@example.com")]

    found = select(rows, "wren", now=NOW)

    assert found.one is None
    assert found.candidates == ()
    assert "wren" in found.render().lower()


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------


def test_a_meeting_already_over_is_never_selected():
    """Prep is for something coming up. A meeting this morning cannot be
    prepped for this afternoon."""
    rows = [_row_at("1:1 | Nitin x Wren", PRINCIPAL, "wren.alder@example.com", hours=-2)]

    assert select(rows, "wren", now=NOW).one is None


def test_a_meeting_past_the_horizon_is_not_selected():
    """Bounded so the answer stays about this week. Two weeks out, the Slack
    and mail evidence a prep is built from has not happened yet."""
    rows = [
        _row_at(
            "1:1 | Nitin x Wren",
            PRINCIPAL,
            "wren.alder@example.com",
            hours=24 * (HORIZON_DAYS + 2),
        )
    ]

    assert select(rows, "wren", now=NOW).one is None


def test_an_empty_selector_is_refused_rather_than_matching_everything():
    """`""` as a substring is in every title. Matching all of them and taking
    the first would look exactly like a working selector."""
    rows = [_row_at("Pod Steering", PRINCIPAL, "a@example.com")]

    with pytest.raises(ValueError):
        select(rows, "   ", now=NOW)


# --------------------------------------------------------------------------
# it does not leak the principal
# --------------------------------------------------------------------------


def test_the_principal_is_not_matchable():
    """He is on every meeting, so his own name selects all of them - which
    reads as "ambiguous" on every query and hides a real miss."""
    rows = [
        _row_at("Pod Steering", PRINCIPAL, "a@example.com"),
        _row_at("Finance x Data", PRINCIPAL, "b@example.com", hours=6),
    ]

    found = select(rows, "principal", now=NOW, principal=PRINCIPAL)

    assert found.one is None
    assert found.candidates == ()


def test_selection_renders_the_candidates_with_their_times():
    rows = [
        _row_at("1:1 | Nitin x Wren", PRINCIPAL, "wren.alder@example.com", hours=4),
        _row_at("Pod Steering", PRINCIPAL, "wren.alder@example.com", hours=30),
    ]

    rendered = select(rows, "wren", now=NOW).render()

    assert "Pod Steering" in rendered
    assert "1:1" in rendered
    assert isinstance(Selection((), "x").render(), str)


# --------------------------------------------------------------------------
# what the review found
# --------------------------------------------------------------------------


def test_a_selector_with_no_word_in_it_raises_rather_than_matching_the_week():
    """The guard is on the TOKENS. An empty token set is a subset of every
    title's, so "---" passed a guard on the string and matched everything -
    rendering "which one?" over the whole week instead of saying the selector
    was bad."""
    rows = [_row_at("Pod Steering", PRINCIPAL, "a@example.com")]

    for bad in ("---", "@@", "?", "  "):
        with pytest.raises(ValueError):
            select(rows, bad)


def test_a_partial_word_does_not_match():
    """No substring path. "fin" finding Finance is the fuzziness KNOWN LIMIT
    refuses, because it buys back the wrong-meeting failure."""
    rows = [_row_at("Finance x Data meeting", PRINCIPAL, "a@example.com")]

    assert select(rows, "fin").candidates == ()
    assert select(rows, "finance").one is not None


def test_a_display_name_never_reaches_the_address_the_principal_is_compared_to():
    """The shape that broke everything: a name folded into the address string
    made `"Nitin S <p@x>" == "p@x"` false and the skip died. Split at the seam,
    the address is bare and the skip holds while the name still matches."""
    rows = [_row_at("Sync", "Prin Cipal <principal@example.com>", "Wren Alder <wren@example.com>")]

    assert select(rows, "prin", principal=PRINCIPAL).candidates == ()
    assert select(rows, "wren alder", principal=PRINCIPAL).one is not None


def test_candidates_render_in_his_zone_and_his_register():
    """A 13:00 PT meeting delivered as 20:00Z listed as 20:00 while the brief
    said 1:00 for the same meeting - he picks off a list that disagrees with
    his calendar. Localised, lowercase, 12-hour, like every other push."""
    from zoneinfo import ZoneInfo

    pt = ZoneInfo("America/Los_Angeles")
    rows = [
        _row_at("Rowan / Nitin 1-1", PRINCIPAL, "r@example.com", hours=4),
        _row_at("D&T Program Review", PRINCIPAL, "r@example.com", hours=30),
    ]

    rendered = select(rows, "r", tz=pt).render()

    assert "20:00" not in rendered and "13:00" not in rendered
    assert "mon 14 sep" in rendered, rendered


def test_the_match_bound_is_the_end_of_the_fetched_window_not_a_rolling_instant():
    """`until` belongs to the caller: the plan fetched specific day windows and
    a match past the last one is a meeting nobody fetched."""
    rows = [_row_at("Late", PRINCIPAL, "a@example.com", hours=24 * HORIZON_DAYS - 1)]

    assert select(rows, "late").one is not None
    assert select(rows, "late", until=NOW + timedelta(days=HORIZON_DAYS - 1)).one is None


def test_the_domain_half_of_an_address_is_not_matchable():
    """Every colleague shares it, so a selector hitting the domain would match
    the whole invite list and read as "ambiguous" on every query."""
    rows = [
        _row_at("Pod Steering", PRINCIPAL, "wren.alder@example.com"),
        _row_at("Finance x Data", PRINCIPAL, "bo.finch@example.com", hours=6),
    ]

    found = select(rows, "example", now=NOW)

    assert found.candidates == ()


def test_the_principal_is_skipped_by_address_not_by_luck():
    """`attendees` are EMAILS, so the principal has to arrive as one. The first
    wiring passed a Slack id, which matches no address - the skip was dead
    while looking wired, and the tests passed because the fixture principal
    happened to be email-shaped.
    """
    rows = [_row_at("Pod Steering", "nitin.example@example.com", "wren.alder@example.com")]

    by_email = select(rows, "nitin", now=NOW, principal="nitin.example@example.com")
    # Deliberately not shaped like a real Slack id - the secret scanner cannot
    # tell a fixture from the real thing and is right not to try. What matters
    # is only that it is NOT an email address.
    by_slack_id = select(rows, "nitin", now=NOW, principal="a-slack-id-not-an-address")

    assert by_email.candidates == (), "the principal matched himself"
    assert by_slack_id.candidates != (), (
        "this must differ - it is what proves the skip depends on the address"
    )


def test_a_surname_that_exists_only_in_the_display_name_still_matches():
    """Real addresses are often a bare first name - `leopold@`, `alex@` - so
    the surname lives only in google's `displayName`. Shaped as the address
    alone, "leopold strauss" cannot match and bare "leopold" matches a
    DIFFERENT Leopold. `SKILL.md` requires the `"Full Name <addr>"` form.
    """
    rows = [
        _row_at("Label Summit", PRINCIPAL, "Leopold Strauss <leopold@example.com>"),
        _row_at("Pod Steering", PRINCIPAL, "leopold.other@example.com", hours=6),
    ]

    found = select(rows, "leopold strauss", now=NOW)

    assert found.one is not None
    assert found.one.summary == "Label Summit"


def test_a_bare_address_still_matches_its_first_name():
    """The display name is an addition, not a replacement - an attendee shaped
    the old way must not stop matching."""
    rows = [_row_at("Sync", PRINCIPAL, "wren.alder@example.com")]

    assert select(rows, "wren", now=NOW).one is not None
