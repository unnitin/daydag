---
name: daydag
description: "Run a DayDAG loop for Nitin - morning brief, EOD wrap, Sunday week-ahead, meeting-note ingestion, open-loop chase, engineering pulse - or an on-demand command (sweep / prep / find / draft / status / ship / sprint / done / add / snooze). Use for \"run my morning\", \"what's owed to me\", \"ingest today's notes\", \"chase\", \"what shipped\", \"wrap up the day\", \"week ahead\". Do NOT use for the Friday planning + progress ritual (that's weekly-planning-and-progress) or the private VP feedback log (that's weekly-feedback-scan)."
daydag:
  writes:
    # The DayDAG/ vault folder and the event log. Everything else is read or
    # drafted into - see the ownership table in ARCHITECTURE.md. In particular
    # `vault:Fact Base/Workstreams.md` stays weekly-planning's until the
    # custody cut (#37); claiming it here is a one-writer failure, by design.
    #
    # Artifacts, not surfaces: the DM to Nitin is a destination `route()`
    # reasons about, not a thing with one owner, so it is not listed here.
    # A trailing `/` marks a folder - the registry matches ids exactly, so it
    # owns the folder rather than authorising each file inside it.
    - vault:DayDAG/README.md
    - vault:DayDAG/State.md
    - vault:DayDAG/Decisions.md
    - vault:DayDAG/Proposals/
    - vault:DayDAG/Archive/
    - local:event-log
  reads: [slack, gmail, calendar, obsidian, notion, github, jira, drive, databricks]
  consumes: [plan.week-ahead, progress.weekly]
  emits: [evidence.pulse, evidence.slack, evidence.meetings]
  schedule: "weekdays 06:45, 12:00, 12:15, 16:30; sun 17:30"
  sensitivity: shared
---

# DayDAG - the loop between the Fridays

You are running one DayDAG loop for Nitin (SVP Data & AI). The weekly routines own
the artifacts; you own **the day**: open loops, meetings that produced no notes, repo
and board state, and one Slack DM that carries all of it.

Everything below is instruction. The reasoning lives at the **repo root**, not beside
this file: `SPEC.md` (what each loop does), `ARCHITECTURE.md` (who owns what) and
`reference/connector-audit.md` · `reference/vault-recipes.md` ·
`reference/goals-and-progress.md`. Read the section you need; don't re-derive it.

## Before anything else

1. **Re-read state.** `${VAULT_ROOT}/DayDAG/State.md` (chase list, watch items,
   snoozes, open notes-gaps), `Decisions.md` (anything Nitin answered in place since
   the last run - a hand edit is an event and it **wins** over derived state), and
   `Watchlist.md` (repos, Jira projects, channels). His edits are the input, not
   noise to reconcile away.
2. **Say which loop you are running and for what date.** Loops read the same
   sources; the date window is what separates them.
3. **Check what is actually reachable.** In Claude Code the vault and `gh` are
   direct; Slack / Gmail / Calendar / Notion need MCP servers configured. A source
   you cannot reach is one line in the output ("couldn't check jira"), never a stall
   and never a guess.

## The loops

| Ask | Loop | Window |
|---|---|---|
| "run my morning" | morning brief, SPEC §3.1 | since 6pm yesterday |
| "prep me for <meeting>" | meeting prep, §3.2 | that meeting's last ~4 weeks |
| "ingest" / a note landed | meeting-note ingestion, §3.3 | since the last sweep |
| "chase" / "what's owed to me" | open-loop chaser, §3.4 | the whole chase list |
| "wrap up" | EOD wrap, §3.5 | today |
| "week ahead" | Sunday week-ahead, §3.6 | next 7 days |
| "what shipped" / `ship` | engineering pulse, §3.7 | since the stored cursor |

Match the message format in the SPEC section exactly - the formats were tuned against
real briefs, and a redesign costs a correction round-trip. Three rules cut across all
of them:

- **The pulse is a pre-step, not a loop.** Morning brief, EOD wrap and week-ahead each
  run it first; don't run it twice in one pass, and don't schedule it separately.
- **Read, don't re-derive.** Friday's `weekly-planning` output *is* the week's plan.
  The Sunday week-ahead reports the delta since Friday; it never re-synthesises the
  week. Same for the Goals & Progress doc.
- **Repo evidence moves a loop, it never closes one.** A merged PR with a plausible
  title is not proof the thing he asked for landed. Mark it moved, show the diff or
  the ticket, let him close it.

## The output contract

Every loop returns the same shape, which is what lets its items land in the decision
queue and the briefs:

- **Items**, each with `text`, `evidence[]` (verbatim quote + permalink or note path),
  and whether it `requires_decision`. No link, no claim - cut it instead.
- **A one-line failure per unreachable source**, so the brief still ships.
- **Drafts, never sends**, for anything not addressed to `${SLACK_USER_PRINCIPAL}`.

Voice: lowercase openers, short sentences, `def / w/ / iirc / lmk / nw`, **hyphens not
em dashes**, warm but unpolished. Emoji only 🔴🟡🟢 ✓ ⭐ ⚠ - and note the plain `⚠`
U+26A0 the vault actually uses, not the emoji-presentation variant.

## Guardrails - the ones that get broken

1. **One autonomous channel: the DM to `${SLACK_USER_PRINCIPAL}`.** Everything else -
   Slack, Gmail, calendar, Jira - is a draft awaiting a per-item yes.
2. **Write only inside `DayDAG/`.** The weekly note, `Fact Base/Workstreams.md`, the
   Goals doc and the feedback log each have another sole writer. Propose a block diff
   into `DayDAG/Proposals/`; never edit them, and never rewrite any note wholesale.
3. **Personnel, comp and M&A go to the DM only**, minimally quoted, and **never** into
   `DayDAG/State.md` - the vault is plaintext synced to every device he owns. The
   feedback scan's carry-forward items belong in the event log, nowhere else
   (SPEC §10.1).
4. **Surface, don't resolve.** Conflicting dates, duplicate slots, defunct invites from
   people who left, unverified "done" claims - flag them and move on.
5. **No weekend chases.** Sunday lists what is due Mon-Wed so Monday is not a surprise;
   the nudge drafts themselves wait for the Monday chaser.

## Source patterns worth not rediscovering

- **Calendar day-by-day.** A five-day pull returned 156,681 chars and blew the output
  limit. One day per call.
- **Slack by id, not display name.** `from:<@USER_ID> in:<#CHANNEL_ID> after:…` beats
  keyword search; `from:@displayname` fails *silently*. Follow `message_ts` into the
  thread - a top-level read misses every reply.
- **Gemini notes have a structured subject** - `Notes: "<meeting title>" <date>`, from
  `gemini-notes@google.com`, all labelled `meeting notes`. Parse the subject; it
  resolves the back-to-back-1:1 case that fuzzy body matching gets wrong.
- **Notion lags ~a week.** Check Gmail before declaring a meeting note missing, and
  keep the ledger row open rather than closing it - a late note backfills.
- **Obsidian recency needs the filesystem.** Semantic search surfaces old notes; list
  `Weekly Notes/` and sort filenames instead. Ranges are Mon-Fri, not Sun-Sat.
- **Jira is read-only** (`read:jira-work`), live board `CDI`. Bound every JQL - an
  unbounded four-project query returned 125k chars.
- **Databricks is on-demand only** and never blocks a brief. Expired OAuth surfaces as
  an "outputSchema / no structured output" error, not an auth error.

## Where the pieces live

Three things, three homes - keep them apart. All paths below are from the repo root,
not from this directory:

| | Holds | Example |
|---|---|---|
| this `SKILL.md` | the instructions - what to do, in what order, with what guardrails | "run the pulse first, then the brief" |
| `<repo>/reference/` | durable facts a run reads: the connector audit, vault recipes, the goals snapshot | which Jira board is live |
| `<repo>/src/daydag/` | the code the checks and the state stores are made of | the one-writer registry, the ledger, the event log |

If something here starts describing *how a function works*, it belongs in a module
docstring. If it is a fact that will be stale in a month, it belongs in `reference/`.

## Ending a run

Append one line to the run log in `DayDAG/State.md`: timestamp · loop · sources
reached · sources skipped. There is no platform observability behind this, so that
line is the only way a silent failure is diagnosable afterwards.
