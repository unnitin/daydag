"""Meeting prep pings (SPEC section 3.2, issue #20).

Written before `daydag.prep` exists. Each test names the behaviour someone
wants, which is the only reason the module below has the shape it has.

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
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daydag import prep
from daydag.config import Identities
from daydag.ledger import NON_MEETING_KINDS, Ledger
from daydag.recipes import RecipeError
from daydag.voice import Push, voice_violations

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
# timing: 30 minutes before, and worthless after
# ---------------------------------------------------------------------------


def test_the_ping_is_due_thirty_minutes_before_the_meeting():
    row = _row("11", "Nitin / VP-Data 1:1", MONDAY_9AM)
    assert prep.due_at(row) == MONDAY_9AM - timedelta(minutes=30)


def test_nothing_is_due_before_the_lead_time():
    schedule = prep.Schedule()
    row = _row("11", "Nitin / VP-Data 1:1", MONDAY_9AM)
    assert schedule.due([row], MONDAY_9AM - timedelta(minutes=45), AUDIENCE) == []


def test_the_ping_is_dropped_once_the_meeting_has_started():
    """Time-boxed by definition: after the meeting it is worthless.

    A late interrupt is worse than none - it spends the one interruption budget
    the architecture allows on something already overtaken by events.
    """
    schedule = prep.Schedule()
    row = _row("11", "Nitin / VP-Data 1:1", MONDAY_9AM)
    assert schedule.due([row], MONDAY_9AM + timedelta(minutes=1), AUDIENCE) == []


def test_a_due_ping_fires_once_and_not_again():
    schedule = prep.Schedule()
    row = _row("11", "Nitin / VP-Data 1:1", MONDAY_9AM)
    now = MONDAY_9AM - timedelta(minutes=30)
    assert [r.event_id for r, _ in schedule.due([row], now, AUDIENCE)] == ["11"]
    assert schedule.due([row], now + timedelta(minutes=5), AUDIENCE) == []


def test_a_recurring_meeting_is_keyed_on_the_instance_not_the_series():
    """`ledger.Row.key` carries the start, and prep must key on the same thing.

    A weekly 1:1 that fires once and never again is the failure mode; keying on
    `event_id` alone produces exactly that and looks correct for a week.
    """
    schedule = prep.Schedule()
    this_week = _row("11", "Nitin / VP-Data 1:1", MONDAY_9AM)
    next_week = _row("11", "Nitin / VP-Data 1:1", MONDAY_9AM + timedelta(days=7))
    assert this_week.event_id == next_week.event_id
    assert schedule.due([this_week], MONDAY_9AM - timedelta(minutes=30), AUDIENCE)
    assert schedule.due(
        [next_week], MONDAY_9AM + timedelta(days=7) - timedelta(minutes=30), AUDIENCE
    )


def test_a_standup_is_never_due():
    schedule = prep.Schedule()
    row = _row("su", "DE Standup", MONDAY_9AM)
    assert schedule.due([row], MONDAY_9AM - timedelta(minutes=30), AUDIENCE) == []


def test_due_refuses_a_naive_now():
    schedule = prep.Schedule()
    row = _row("11", "Nitin / VP-Data 1:1", MONDAY_9AM)
    with pytest.raises(prep.PrepError):
        schedule.due([row], datetime(2026, 9, 7, 8, 30), AUDIENCE)


def test_a_naive_meeting_start_fails_by_name_not_as_a_typeerror():
    """A naive `Row.start` must not surface as a datetime comparison error.

    `ledger.seed_day` passes `event["start"]` through untouched, so a calendar
    read that loses its offset reaches here. Comparing it against an aware
    `now` raises `TypeError` from inside the loop, which aborts the whole batch
    of due pings with a message that names neither the meeting nor the cause.
    """
    schedule = prep.Schedule()
    row = _row("11", "Nitin / VP-Data 1:1", datetime(2026, 9, 7, 9, 0))
    with pytest.raises(prep.PrepError, match="VP-Data"):
        schedule.due([row], MONDAY_9AM - timedelta(minutes=30), AUDIENCE)


# ---------------------------------------------------------------------------
# sources: the File 2 recipe, run just in time
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
    assert "sort:timestamp" in plan.slack[0]


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
    runs over what the agent wrote.
    """
    dashed = "we merged everything — ready for 10k"
    ping = _ping([_point(quote=dashed)])
    assert dashed in ping.render(), "the quote was silently rewritten"
    assert voice_violations(ping.authored_text()) == []
    assert any("dash" in v for v in voice_violations(ping.render()))


def test_an_unsanctioned_emoji_in_the_agents_own_words_is_caught():
    ping = _ping([_point("ship it \U0001f680")])
    assert voice_violations(ping.authored_text())


# ---------------------------------------------------------------------------
# the one interrupt
# ---------------------------------------------------------------------------


@pytest.mark.guardrail
def test_the_prep_ping_is_the_only_push_allowed_off_schedule():
    """ARCHITECTURE's interaction model, as a check rather than a paragraph."""
    assert prep.may_interrupt(Push.PREP_PING) is True
    for kind in Push:
        if kind is not Push.PREP_PING:
            assert prep.may_interrupt(kind) is False, kind


@pytest.mark.guardrail
def test_the_ping_goes_to_the_principals_own_dm_and_nowhere_else(identities):
    assert prep.recipient(identities) == "UPRINCIPAL1"


@pytest.mark.guardrail
@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "nitin",
        "@nitin",
        "UPRINCIPAL1        # the person the agent works for",
    ],
)
def test_a_principal_id_that_is_not_an_id_is_refused(tmp_path, value):
    """Guardrail 1's one permitted destination is the one that must be checked.

    `slack_search` already refuses a non-id because a display name matches
    nothing *silently*. The DM target had no such floor, so a hand-edited `.env`
    - the last line of the file, with an inline comment glued on, which is how
    `.env.example` teaches the format - addressed the ping at a conversation id
    that is not one, instead of failing loudly.
    """
    env = tmp_path / ".env"
    env.write_text(f"SLACK_USER_PRINCIPAL={value}\n", encoding="utf-8")
    with pytest.raises(prep.PrepError):
        prep.recipient(Identities.from_file(env))


@pytest.mark.guardrail
def test_a_missing_principal_id_is_an_error_not_a_guess(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SLACK_CH_POD=CPODCHANNEL\n", encoding="utf-8")
    with pytest.raises(prep.PrepError):
        prep.recipient(Identities.from_file(env))


@pytest.mark.guardrail
def test_prep_exposes_no_send_path():
    """Guardrail 1. Prep builds the ping; something else, later, delivers it."""
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
