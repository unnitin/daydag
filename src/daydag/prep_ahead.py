"""Day-before prep for the calls he named, from rules he edits in the vault (#171).

USING IT
    rules = read_rules(folder.watchlist_path)       # his `## Prep rules`, or the seed
    target = next_working_day(today)                # fri -> mon
    matched, warnings = match(events, rules, roles, principal=me)
    plan_reads(matched, today=today, identities=ids)   # -> [{source, how, detail}]
    assemble(target, matched, payload, notes=...)   # -> DayBefore; .render()

    In the runner:
        python -m daydag.run plan prep-ahead                      # calendar step
        python -m daydag.run plan prep-ahead --calendar cal.json --log DB   # the reads
        python -m daydag.run render prep-ahead --log DB < payloads.json

CONTRACTS
    1. The rules are HIS. `## Prep rules` in `DayDAG/Watchlist.md` is read on
       every run and wins. A section he emptied stays empty - the seed is used
       only when the heading is absent altogether, and says so in one line.
    2. A malformed row is ONE warning line, never a crash, and never a silent
       drop. A typo that quietly stops a call being prepped is found the
       morning he walks in cold.
    3. This module FETCHES NOTHING (run.py contract 1). It names reads; the
       agent runs them; `assemble` puts the answers together.
    4. Attendee rules match REQUIRED, non-declined attendees by exact address,
       via the people directory's role key. An optional invitee is not in the
       call - Monday 9/28's sprint demo lists the cfo as optional.
    5. Every point carries a verbatim quote and its link (`prep.Point`), and
       suggestions are labelled as suggestions and appear only for a rule
       whose recipe asks for them. An unreadable deck is one line.
    6. DM only. This returns text; `daydag.delivery` is the only sender.

ROW GRAMMAR (one bullet per rule, fields split by `·` - never `|`, titles carry it)
    - title: Pod Steering · deck · day before
    - attendee: cfo, sponsor · past-notes
    - attendee: ceo · past-notes+ideas · day before
    - title: roadmap · attendee: cto · deck+past-notes · 2 days before · a note

    match   `title: <words>` (every word in the meeting title) and/or
            `attendee: <role>[, <role>]` (any of them, a directory key)
    recipe  `deck`, `past-notes`, `ideas`, joined with `+`
    lead    `day before` (default) or `N days before`, in working days
    Anything else after a `·` is a note for him, as in the rest of the file.

WHY IT EXISTS
    His words, 2026-09-25: prep pod steering from the deck the day before, the
    cfo's call from past notes, the ceo's from past notes plus ideas for
    conversation and follow-ups - "in the future there could be more calls to
    prepare for". So more calls are rows, not releases.

    The 30-minute ping (`prep.py`) is an interrupt and has to be narrow. This is
    a scheduled evening push, so it can afford the reading a deck needs.

KNOWN LIMIT
    Working days skip weekends only, not holidays. `next_working_day` is a
    local helper; the EOD wrap's Friday -> Monday fix (#170) has its own, and
    the two should become one recipe once both land.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from daydag import brief
from daydag.ledger import attendee_parts, is_resource, qualifies, title_from_gemini_subject
from daydag.prep import MAX_POINTS, Point, PrepError, point
from daydag.pulse import _BOLD_HEADING, _BULLET, _HEADING, _RULE
from daydag.recipes import RecipeError, gmail_gemini_notes, slack_search
from daydag.voice import WARN

__all__ = [
    "PAST_NOTES_DAYS",
    "RECIPES",
    "SEED_SECTION",
    "SLACK_DAYS",
    "DayBefore",
    "Matched",
    "Rule",
    "Rules",
    "assemble",
    "match",
    "next_working_day",
    "parse_rules",
    "plan_reads",
    "read_rules",
]

#: The three ingredients a rule can ask for.
RECIPES = frozenset({"deck", "past-notes", "ideas"})

#: How far back past notes are read. Longer than the ping's 28 days because the
#: calls he named are monthly ("1 x month" is in the labs invite itself), and
#: 28 days of a monthly series is one instance, sometimes none.
PAST_NOTES_DAYS = 56

#: Notion's meeting-notes DB lags about a week; newer notes come from Gmail.
NOTION_LAG_DAYS = 7

#: Slack with the people a rule named, and the window a deck search covers.
SLACK_DAYS = 14
DECK_SEARCH_DAYS = 14

#: The section he pastes into `DayDAG/Watchlist.md`. Also the fallback when the
#: heading is absent, so the feature works before he has pasted it.
SEED_SECTION = """## Prep rules

- title: Pod Steering · deck · day before
- attendee: cfo, sponsor · past-notes · day before
- attendee: ceo · past-notes+ideas · day before
"""

_SECTION = "prep rules"
_LEAD = re.compile(r"^(?:(?:the\s+)?day|(?P<n>[1-5])\s+(?:working\s+)?days?)\s+before$")
_WORDS = re.compile(r"[^a-z0-9]+")
#: `·` only, not the `|` the repos block also takes: meeting titles carry pipes
#: (" Status | CreateOS Labs"), and splitting on one turned `title: Status |
#: CreateOS Labs` into a rule matching every meeting with "status" in it.
_FIELDS = re.compile(r"·")
_DECK_URL = re.compile(
    r"https://(?:docs\.google\.com/(?:presentation|document|spreadsheets)|drive\.google\.com)"
    r"/[^\s\"'<>)]+"
)
#: Meet's notes doc and Drive recordings ride on the invite but are not decks
#: (`ledger._NOTES_DOC_MARKER` explains the marker).
_NOT_A_DECK = ("usp=meet_tnfm_calendar",)


def _words(text: str) -> tuple[str, ...]:
    return tuple(part for part in _WORDS.split(str(text).casefold()) if part)


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """One row of `## Prep rules`."""

    title: tuple[str, ...] = ()
    attendees: tuple[str, ...] = ()
    recipe: frozenset[str] = frozenset()
    lead_days: int = 1
    #: The row as he wrote it, for the "why" on each prep.
    row: str = ""

    def label(self) -> str:
        parts = []
        if self.title:
            parts.append(f'"{" ".join(self.title)}"')
        if self.attendees:
            parts.append("/".join(self.attendees))
        return " + ".join(parts)


@dataclass(frozen=True)
class Rules:
    rules: tuple[Rule, ...] = ()
    #: One line per row that did not parse (contract 2).
    warnings: tuple[str, ...] = ()
    #: True when the heading was absent and the seed stood in.
    seeded: bool = False
    #: Informational lines - the seed fallback, an unreadable file.
    notes: tuple[str, ...] = ()


def _parse_row(body: str) -> Rule | str:
    """A rule, or the reason the row is not one."""
    fields = [f.strip() for f in _FIELDS.split(body.replace("`", ""))]
    title: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    recipe: frozenset[str] | None = None
    lead = 1
    for entry in fields:
        lowered = entry.casefold()
        if lowered.startswith("title:"):
            title = _words(entry[len("title:") :])
            if not title:
                return "title: has no words"
        elif lowered.startswith("attendee:") or lowered.startswith("attendees:"):
            value = entry.split(":", 1)[1]
            roles = tuple(r.strip().casefold() for r in re.split(r"[,/]", value) if r.strip())
            if not roles:
                return "attendee: names no role"
        elif lowered.endswith("before"):
            parsed = _LEAD.match(" ".join(lowered.split()))
            if parsed is None:
                return f"lead {entry!r} - use `day before` or `N days before`"
            lead = int(parsed["n"] or 1)
        elif "+" in lowered or lowered in RECIPES:
            parts = frozenset(p.strip() for p in lowered.split("+"))
            if not parts <= RECIPES:
                bad = ", ".join(sorted(parts - RECIPES))
                return f"recipe {bad!r} - use deck, past-notes, ideas"
            recipe = (recipe or frozenset()) | parts
        # anything else is his note (the file's own convention)
    if not title and not roles:
        return "no `title:` or `attendee:` to match on"
    if recipe is None:
        # A recipe-looking word that is not one ("slides") reads as a note, so
        # this is where an unknown recipe surfaces.
        return "no recipe - use deck, past-notes, ideas"
    return Rule(title=title, attendees=roles, recipe=recipe, lead_days=lead, row=body.strip())


def parse_rules(text: str | None) -> Rules:
    """The `## Prep rules` section of ``text``; the seed when it has none.

    ``None`` means the file could not be read, which also falls back to the
    seed - with a line saying so, because silence would read as "his rules".
    """
    if text is None:
        seed = parse_rules(SEED_SECTION)
        return Rules(
            seed.rules,
            seeded=True,
            notes=("couldn't read Watchlist.md - used the seed prep rules",),
        )
    found = False
    inside = False
    rules: list[Rule] = []
    warnings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        heading = _HEADING.match(stripped) or _BOLD_HEADING.match(stripped)
        if heading:
            inside = heading["name"].strip().casefold().rstrip(":") == _SECTION
            found = found or inside
            continue
        if _RULE.fullmatch(stripped):
            inside = False
            continue
        bullet = _BULLET.match(stripped) if inside else None
        if bullet is None:
            continue
        parsed = _parse_row(bullet["body"])
        if isinstance(parsed, Rule):
            rules.append(parsed)
        else:
            warnings.append(
                f"{WARN} prep rule not understood, ignored: {bullet['body']} ({parsed})"
            )
    if not found:
        seed = parse_rules(SEED_SECTION)
        return Rules(
            seed.rules,
            seeded=True,
            notes=(SEEDED_NOTE,),
        )
    return Rules(tuple(rules), tuple(warnings))


def read_rules(path: str | Path | None) -> Rules:
    """`parse_rules` over the file at ``path``; unreadable degrades to the seed."""
    if path is None:
        return parse_rules(None)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return parse_rules(None)
    return parse_rules(text)


# ---------------------------------------------------------------------------
# which day
# ---------------------------------------------------------------------------


def next_working_day(day: date, n: int = 1) -> date:
    """The ``n``-th weekday after ``day``. Friday's next is Monday.

    Local on purpose - #170 is fixing the same Friday -> Monday bug in the EOD
    wrap in parallel, and a shared helper belongs on main once one lands.
    """
    if n < 1:
        raise ValueError("n counts working days ahead and starts at 1")
    current = day
    while n:
        current += timedelta(days=1)
        if current.weekday() < 5:
            n -= 1
    return current


def target_days(today: date, rules: Rules) -> list[date]:
    """Every day some rule preps for tonight, soonest first."""
    return sorted({next_working_day(today, rule.lead_days) for rule in rules.rules}) or [
        next_working_day(today)
    ]


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Matched:
    """A meeting some rule wants prepped, and everything the rules asked for."""

    event: Mapping[str, Any]
    rules: tuple[Rule, ...]
    #: The directory entries an attendee rule matched, for the Slack reads.
    people: tuple[Any, ...] = ()

    @property
    def event_id(self) -> str:
        return str(self.event.get("id", ""))

    @property
    def title(self) -> str:
        return str(self.event.get("summary", "")).strip()

    @property
    def recipe(self) -> frozenset[str]:
        out: frozenset[str] = frozenset()
        for rule in self.rules:
            out |= rule.recipe
        return out

    @property
    def start(self) -> datetime | None:
        value = self.event.get("start")
        if isinstance(value, Mapping):
            value = value.get("dateTime") or value.get("date_time")
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None


def _own_response(event: Mapping[str, Any]) -> str:
    if event.get("response_status"):
        return str(event["response_status"])
    for attendee in event.get("attendees") or []:
        if isinstance(attendee, Mapping) and attendee.get("self"):
            return str(attendee.get("responseStatus") or "needsAction")
    return "needsAction"


def _shaped(event: Mapping[str, Any]) -> dict[str, Any]:
    """Enough of the SKILL's calendar shaping for `ledger.qualifies` to judge."""
    kind = {"FOCUS_TIME": "focus", "OUT_OF_OFFICE": "ooo"}.get(str(event.get("eventType", "")))
    shaped = {**event, "response_status": _own_response(event)}
    if kind and "kind" not in event:
        shaped["kind"] = kind
    return shaped


def _in_the_call(event: Mapping[str, Any], principal: str) -> set[str]:
    """Required, non-declined attendee addresses, the principal excluded."""
    present: set[str] = set()
    for attendee in event.get("attendees") or []:
        if is_resource(attendee):
            continue
        if isinstance(attendee, Mapping):
            if attendee.get("optional") or attendee.get("optionalAttendee"):
                continue
            if str(attendee.get("responseStatus", "")).casefold() == "declined":
                continue
        address, _ = attendee_parts(attendee)
        if address and address.casefold() != principal.casefold():
            present.add(address.casefold())
    return present


def match(
    events: Iterable[Mapping[str, Any]],
    rules: Rules,
    roles: Mapping[str, Any],
    *,
    principal: str = "",
) -> tuple[list[Matched], list[str]]:
    """The meetings the rules want prepped, and one warning per dead role.

    ``roles`` maps a directory key to a `people.Person` (anything with
    ``emails``). A role the directory does not know, or knows with no address,
    can never match a calendar attendee - that is a warning, because a rule
    that silently never fires looks exactly like a quiet day.
    """
    warnings: list[str] = []
    addresses: dict[str, set[str]] = {}
    for rule in rules.rules:
        for role in rule.attendees:
            if role in addresses:
                continue
            person = roles.get(role)
            addresses[role] = {e.casefold() for e in getattr(person, "emails", ()) or ()}
        # Only a rule with NO matchable role is dead. The seed's `cfo, sponsor`
        # names one person twice, and the real directory holds `sponsor` with
        # a dm and no address - warning about that every evening would be the
        # noise that teaches him to skip the warnings that matter.
        if rule.attendees and not any(addresses[r] for r in rule.attendees):
            names = ", ".join(rule.attendees)
            warnings.append(
                f"{WARN} prep rule `{rule.row}` can't match anyone - the directory has no"
                f" address for {names}"
            )

    matched: list[Matched] = []
    for event in events:
        if not isinstance(event, Mapping) or not qualifies(_shaped(event)):
            continue
        title = set(_words(str(event.get("summary", ""))))
        present = _in_the_call(event, principal)
        fired: list[Rule] = []
        people: list[Any] = []
        for rule in rules.rules:
            if rule.title and not set(rule.title) <= title:
                continue
            if rule.attendees:
                hits = [r for r in rule.attendees if addresses.get(r, set()) & present]
                if not hits:
                    continue
                people += [roles[r] for r in hits if roles[r] not in people]
            fired.append(rule)
        if fired:
            matched.append(Matched(event=event, rules=tuple(fired), people=tuple(people)))
    matched.sort(key=lambda m: (m.start is None, m.start.timestamp() if m.start else 0.0))
    return matched, warnings


# ---------------------------------------------------------------------------
# the reads
# ---------------------------------------------------------------------------


def _deck_urls(event: Mapping[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in event.get("attachments") or []:
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("fileUrl") or "")
        if not url or any(marker in url for marker in _NOT_A_DECK):
            continue
        if str(item.get("mimeType", "")).startswith(("video/", "audio/")):
            continue
        if url not in urls:
            urls.append(url)
    for url in _DECK_URL.findall(str(event.get("description") or "")):
        if url not in urls and not any(marker in url for marker in _NOT_A_DECK):
            urls.append(url)
    return urls


def _step(source: str, how: str, **detail: Any) -> dict[str, Any]:
    return {"source": source, "how": how, "detail": detail}


def plan_reads(
    matched: Sequence[Matched], *, today: date, identities: Mapping[str, str]
) -> list[dict[str, Any]]:
    """One read per ingredient per matched meeting, each keyed to where it goes.

    Every result goes under ``prep_ahead.<event_id>.<put_under>`` in the
    payloads file, so `assemble` can tell "read, found nothing" (``[]``) from
    "not read" (key absent).
    """
    steps: list[dict[str, Any]] = []
    for m in matched:
        eid, title = m.event_id, m.title
        if "past-notes" in m.recipe or "ideas" in m.recipe:
            try:
                query = gmail_gemini_notes(
                    title=title, after=today - timedelta(days=PAST_NOTES_DAYS), before=today
                )
            except RecipeError:
                # A quote in the title cannot be a subject search; the date
                # sweep still bounds it, and the agent filters by title.
                query = gmail_gemini_notes(
                    after=today - timedelta(days=PAST_NOTES_DAYS), before=today
                )
            steps.append(
                _step(
                    "gmail",
                    "search, then get each thread in PLAIN_TEXT; keep subject, date, a permalink"
                    " (the thread's viewUrl) and the text. subject: matches WORDS, so drop any"
                    " note whose quoted title is a different meeting",
                    event_id=eid,
                    put_under="notes",
                    query=query,
                )
            )
            steps.append(
                _step(
                    "notion",
                    "meeting-notes DB for this title, older than a week (gmail has the recent"
                    " ones); append to the same notes list with the page url as permalink",
                    event_id=eid,
                    put_under="notes",
                    title=title,
                    after=str(today - timedelta(days=PAST_NOTES_DAYS)),
                    before=str(today - timedelta(days=NOTION_LAG_DAYS)),
                )
            )
        if "deck" in m.recipe:
            urls = _deck_urls(m.event)
            if urls:
                steps.append(
                    _step(
                        "drive",
                        "read each file's content; a file you cannot open goes in as"
                        ' {"url", "error"} - never guess at what it says',
                        event_id=eid,
                        put_under="deck",
                        urls=urls,
                    )
                )
            else:
                steps.append(
                    _step(
                        "drive",
                        "no deck on the invite - search Drive for the title, modified since"
                        " the date given; read the newest deck. none found is []",
                        event_id=eid,
                        put_under="deck",
                        search=title,
                        modified_after=str(today - timedelta(days=DECK_SEARCH_DAYS)),
                    )
                )
        for person in m.people:
            window = {"after": today - timedelta(days=SLACK_DAYS), "before": today}
            try:
                if getattr(person, "dm", None):
                    query = slack_search(
                        channel=person.dm, ascending=False, identities=identities, **window
                    )
                elif getattr(person, "slack_id", None):
                    query = slack_search(
                        sender=person.slack_id, ascending=False, identities=identities, **window
                    )
                else:
                    continue
            except RecipeError:
                continue
            steps.append(
                _step(
                    "slack",
                    "search verbatim, newest first; follow any thread with replies. keep text"
                    " and permalink",
                    event_id=eid,
                    put_under="slack",
                    role=person.key,
                    query=query,
                )
            )
        ideas = (
            ' and 1-3 "ideas" (conversation ideas / follow-ups, labelled suggestions)'
            if "ideas" in m.recipe
            else ""
        )
        steps.append(
            _step(
                "prep_ahead",
                "from what you read, write up to 5 points as"
                ' {"what", "why_now", "quote", "permalink"} - the quote verbatim from the'
                f" source the link opens{ideas}. put them under prep_ahead.<event_id>",
                event_id=eid,
                title=title,
                start=m.start.isoformat() if m.start else "",
                recipe=sorted(m.recipe),
                why=[rule.row for rule in m.rules],
            )
        )
    return steps


# ---------------------------------------------------------------------------
# the push
# ---------------------------------------------------------------------------


def _day_label(day: date) -> str:
    return f"{day.strftime('%a').casefold()} {day.month}/{day.day}"


def _clock(start: datetime | None) -> str:
    if start is None:
        return ""
    return start.strftime("%-I:%M%p").casefold().replace(":00", "")


def _records(value: Any) -> list[Mapping[str, Any]] | None:
    if not isinstance(value, list):
        return None
    return [v for v in value if isinstance(v, Mapping)]


def _series_notes(entry: Mapping[str, Any], title: str) -> list[Mapping[str, Any]] | None:
    """The notes that belong to THIS meeting's series; None if none were read.

    Gmail's `subject:"..."` matches words, not the phrase: on 2026-09-25 the
    query for "Status | CreateOS Labs" returned the notes of "Relaunching
    CreateOS labs (mandatory)". A Gemini subject carries the exact title, so a
    note whose parsed title is a different meeting is dropped. A subject that
    does not parse (a Notion page) is kept - there is nothing to compare.
    """
    notes = _records(entry.get("notes"))
    if notes is None:
        return None
    wanted = title.strip().casefold()
    kept = []
    for note in notes:
        parsed = title_from_gemini_subject(str(note.get("subject") or ""))
        if parsed is None or parsed.strip().casefold() == wanted:
            kept.append(note)
    return kept


def _points(entry: Mapping[str, Any], title: str = "") -> list[Point]:
    """His agent's points first; quoted notes and slack as the fallback."""
    out: list[Point] = []
    for raw in _records(entry.get("points")) or []:
        try:
            made = point(
                str(raw.get("what") or ""),
                str(raw.get("why_now") or ""),
                quote=brief.short(str(raw.get("quote") or "")) or None,
                permalink=raw.get("permalink") or None,
                source=raw.get("source") or None,
            )
        except PrepError:
            continue  # a quote without its link (or the reverse) is not evidence
        if made is not None:
            out.append(made)
    if out:
        return out[:MAX_POINTS]
    for note in (_series_notes(entry, title) or [])[:2]:
        subject = str(note.get("subject") or "").strip()
        if subject and note.get("permalink"):
            made = point(
                "last time", "read before the call", quote=subject, permalink=note["permalink"]
            )
            if made is not None:
                out.append(made)
    for message in _records(entry.get("slack")) or []:
        text = brief.short(str(message.get("text") or ""))
        if text and message.get("permalink"):
            made = point(
                text, "raised since you last met", quote=text, permalink=message["permalink"]
            )
            if made is not None:
                out.append(made)
    return out[:MAX_POINTS]


#: The one note that is not news: with no `## Prep rules` section the seed
#: rules apply, which is how it has always worked. Every other note - a rule
#: warning, an unreadable Watchlist, no directory - is why nothing matched.
SEEDED_NOTE = "no `## Prep rules` in Watchlist.md yet - used the seed rules"


@dataclass(frozen=True)
class DayBefore:
    """Tonight's prep for the next working day."""

    day: date
    sections: tuple[brief.Section, ...] = ()
    notes: tuple[str, ...] = ()
    matched: int = 0

    def render(self) -> str:
        if not self.matched and all(note == SEEDED_NOTE for note in self.notes):
            # Nothing matched and nothing to warn about: say NOTHING. The
            # scheduled run posts this verbatim, and an empty string is the one
            # "nothing to post" a headless agent cannot misread. The CLI says so
            # on stderr for a run by hand.
            return ""
        if not self.matched:
            header = f"prep: nothing on {_day_label(self.day)} matches a prep rule"
        else:
            calls = "call" if self.matched == 1 else "calls"
            header = f"prep for {_day_label(self.day)} - {self.matched} {calls} worth reading up on"
        extra = [brief.Section("", tuple(self.notes))] if self.notes else []
        return brief.render_push(header, [*self.sections, *extra], ()).replace("\n\n\n", "\n\n")


def _section(m: Matched, entry: Any) -> brief.Section:
    why = "; ".join(rule.label() for rule in m.rules)
    heading = f"*{m.title}* {_clock(m.start)} - {why} ({', '.join(sorted(m.recipe))})".replace(
        "  ", " "
    )
    if not isinstance(entry, Mapping):
        return brief.Section(
            heading, ("- couldn't read anything for this one - no reads came back",)
        )
    lines: list[str] = []
    if "deck" in m.recipe:
        decks = _records(entry.get("deck"))
        if decks is None:
            lines.append("- couldn't check for a deck")
        elif not decks:
            lines.append("- no deck on the invite or in drive")
        for deck in decks or []:
            url = str(deck.get("url") or deck.get("permalink") or "")
            if deck.get("error") or not str(deck.get("text") or "").strip():
                lines.append(f"- couldn't open the deck ({url or 'no link'})")
    series = _series_notes(entry, m.title)
    if ("past-notes" in m.recipe or "ideas" in m.recipe) and series == []:
        lines.append(f"- no notes from this series in the last {PAST_NOTES_DAYS // 7} weeks")
    elif "past-notes" in m.recipe and series is None:
        lines.append("- couldn't read past notes")
    lines += [p.render() for p in _points(entry, m.title)]
    if "ideas" in m.recipe:
        ideas = _records(entry.get("ideas")) or []
        if ideas:
            lines.append("ideas / follow-ups - suggestions, not from the notes:")
            for idea in ideas[:3]:
                what = str(idea.get("what") or "").strip()
                if what:
                    why_now = str(idea.get("why_now") or idea.get("why") or "").strip()
                    text = f"suggestion: {what}" + (f" - {why_now}" if why_now else "")
                    lines.append(brief.claim(text, idea.get("permalink") or None))
    if not lines:
        lines.append("- nothing to go on - going in cold. keep me honest")
    return brief.Section(heading, tuple(lines))


def assemble(
    day: date,
    matched: Sequence[Matched],
    payload: Any,
    *,
    notes: Sequence[str] = (),
) -> DayBefore:
    """The push, from the matched meetings and what the agent read for each."""
    per_meeting = payload if isinstance(payload, Mapping) else {}
    sections = tuple(_section(m, per_meeting.get(m.event_id)) for m in matched)
    return DayBefore(day=day, sections=sections, notes=tuple(notes), matched=len(matched))
