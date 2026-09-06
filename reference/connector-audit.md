# Connector audit

> Issue #2. Every row of SPEC §4 confirmed or corrected against the live
> connectors on 2026-09-06. Shapes, counts and formats only — no content from
> any calendar, inbox or board is recorded here.

## Scoreboard

| Source | Status | Finding |
|---|---|---|
| Google Calendar | ✅ confirmed | Day-by-day is not a preference, it is forced |
| Gmail | ⚠️ **SPEC was wrong** | Gemini subjects *are* structured and parseable |
| Slack | ✅ confirmed | Id-first search resolves; display names unreliable |
| Notion | ✅ confirmed | Authenticated; meeting-notes DB reachable |
| Atlassian / Jira | ✅ authorized | **Read-only token** — stronger than the design assumed |
| GitHub | ✅ confirmed | Exercised throughout the build |
| Databricks | ⚠️ untested | Tools present, never exercised. On-demand only, so not blocking |
| Granola | ❌ not connected | Deliberate: Gemini covers the corpus, Granola adds only unrecorded meetings |

## The corrections that changed code

### Gmail — the subject is structured

SPEC §4 said: *"search by sender + body keywords, not subject — Gemini uses the
meeting title as body, not a consistent subject."*

Measured: **201 notes in a 30-day window, every one** with the shape

```
Notes: “<meeting title>” <date>
```

and every one carrying the Gmail label `meeting notes`. Parsing the subject
gives an *exact* title, which resolves the back-to-back-1:1 ambiguity that fuzzy
body matching cannot. Implemented as `title_from_gemini_subject()` in
`ledger.py`, where an exact title short-circuits the fuzzy path entirely.

### Calendar — day-by-day, and the qualification rule was wrong

The day-by-day rule is confirmed by measurement: a **5-day** pull returned
**156,681 characters** and exceeded the output limit outright.

The more expensive finding was in the event shapes. Of 77 `DEFAULT` events over
those five days, only **23 were `accepted`** — but **60 were real meetings**.
Most invites are simply never answered; `needsAction` is the norm, not the
exception.

The ledger's original rule required `accepted`, so it would have dropped **61%
of meetings** — including standups with 13 and 21 attendees — and dropped them
*silently*. A meeting with no ledger row can never be reported as a notes gap,
which is the ledger's entire purpose, so the failure was invisible by design.
Only `declined` disqualifies now.

Event types seen: `DEFAULT`, `FOCUS_TIME`, `FROM_GMAIL`. No `OUT_OF_OFFICE` in
the sampled window, so SPEC's OOO-detection advice remains unverified rather
than confirmed.

### Jira — read-only, and the board is not where we guessed

The token carries **`read:jira-work` and Confluence read scopes only. There is
no write scope.** The agent cannot transition, comment on, or create a ticket
even if a bug tried to. SPEC §3.7 rule 4 ("read the board, never drive it") and
guardrail 4 are therefore enforced at the token rather than in a prompt — the
strongest form available, and testable.

Queried `DED`, `CING`, `CDI` and `DCTF` over 14 days: **all 50 results came from
`CDI` (CreateOS | DevOps & Infrastructure)**. The others returned nothing — they
are dormant, not merely quiet. Statuses in use: `Backlog`,
`Selected for Development`, `In Progress`, `Done`, `Won't Do`.

50 projects are visible in total, and that is page one.

## The recurring trap: output limits

Three sources blew the token limit during this audit — Calendar (156k chars for
5 days), Jira (125k chars for a 14-day 4-project query), and the Jira project
list (60k chars). This is not a Calendar quirk that SPEC happened to record; it
is the normal case for any unbounded query here.

Every recipe (#7) must bound its page size and select explicit fields. `*all` is
never correct.

## What remains unverified

- **Databricks** — tools are present, no query was run. On-demand only per SPEC,
  so it cannot block a brief.
- **OOO/flight event shapes** — none appeared in the sampled window.
- **GitHub against real remotes** — the mirrors have only ever been exercised
  against local `git init` fixtures. URL shape, the `.wiki.git` convention and
  wiki-absence detection are taken from documentation, not observation.
