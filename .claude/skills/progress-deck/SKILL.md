---
name: progress-deck
description: "Keep the Data & AI progress deck for the sponsor ready before every sponsor 1:1: hunt the calendar for calls with the Sponsor, and the weekend before one, read what moved (Jira, GitHub, Slack, the rollout sheet, the last call's notes, the Sponsor DM, his stickies), refresh the deck's numbers, dates and status into its next -YYYY-MM-DD-vNN version through consulting-slides, review it, build a pre-read copy that is never sent, and DM Nitin what changed plus talking points. Runs unattended on Saturday and Sunday mornings; use it by hand for \"update the progress deck\", \"refresh the deck for my 1:1 with the sponsor\", \"prep the business progress deck\", \"what moved since the last version\". Do NOT use for the weekly highlights email or the Goals doc (weekly-progress-reporting), a new deck from scratch (consulting-slides), or a review only (slide-review)."
daydag:
  # The deck's own folder: each refresh adds a new versioned file, a pre-read
  # copy and a line in the deck's pending file. Nothing in the vault: style rules
  # go through consulting-slides (it owns DayDAG/Style.md), meeting prep stays
  # weekly-planning's, and the talking points ride in the DM.
  writes:
    - local:${PROGRESS_DECK}
  reads: [calendar, slack, gmail, jira, github, drive, notion]
  emits: [deck.progress]
  schedule: "sat 07:13-12:43 every 30m, sun catch-up; only when a Sponsor 1:1 falls in the coming week"
  sensitivity: shared
---

# Progress deck - ready the weekend before every Sponsor 1:1

The Sponsor asked for a monthly Data & AI plan he can go deep on, and the deck that answers
it goes stale within a week: percentages move, dates firm up, placeholders get answered.
This skill keeps it current on the Sponsor's rhythm. It finds every 1:1 with the Sponsor,
and on the weekend before one it rebuilds the deck from what the live sources say moved,
so Nitin opens Sunday or Monday with the next version, a list of what changed and why,
and talking points. **Facts refresh on their own; structure waits for his go.**

## Where everything lives

| What | Where |
|---|---|
| The deck | `${PROGRESS_DECK}` names it; its folder holds every version. The current one is `bash .claude/skills/consulting-slides/scripts/nextver.sh --latest "$PROGRESS_DECK"`, the next one is the same command without `--latest`. Copies named `-preread-`, `-proofread-` or `-WIP-` sit outside the version chain on purpose |
| Build script and content | `<stem>-draft.build.js` (layout), `<stem>-draft.content.js` and `<stem>-draft.map.js` (all text, bars, percentages, footnotes, speaker notes). The content files are the source; edit them, never the .pptx |
| The queue | `<stem>-pending.md` beside the deck: changes he asked for that are not built, open calls that are his, placeholders, and a Done list with the version each shipped in. Read it before every build |
| His format | `context/` in the deck folder: the Sponsor's own priority lists and the formats Nitin chose |
| Last builds and his saved copies | `~/.local/state/daydag/stickies/<name>-built.pptx` and `<name>-his-<stamp>.pptx`, the bases for every drift check |
| Style | `.venv/bin/python -m daydag.style show --project "Data & AI progress"` - his rules for this deck and the house rows. They outrank this file |
| The Sponsor | `.venv/bin/python -m daydag.people show sponsor --log ~/.local/state/daydag/events.db`: email (for the calendar), Slack id, the 1:1 DM |

## Two ways it runs

- **Unattended, the weekend before.** launchd fires `scripts/progress-deck-gate.sh` on Saturday
  mornings, with a Sunday catch-up, and the gate opens `scripts/progress-deck.sh` in the
  morning's faceless app (see USAGE.md, "The weekend progress deck"). The run hunts the
  coming Monday to Sunday. No Sponsor 1:1 there: it stands down and posts nothing. One
  there: it runs every step below and posts to Nitin's DM. It never asks a question; what
  needs him goes under "needs you" in the DM. Nobody is there to approve a command, so
  every Bash call is one command with literal paths: no shell variables, `$(...)`, loops,
  `;`, `&&` or pipes (run `nextver.sh` alone and paste the path it prints), and slide
  text is read with `slide-review/scripts/outline.py`, never `python -c`. The run's
  allowlist is in `scripts/progress-deck.sh`; `DAYDAG_SHOW_ALLOWED=1` prints it.
- **By hand, any day.** First refresh the local repos (his call, 2026-11-20: every recap
  does): `.venv/bin/python -m daydag.repos sync --log ~/.local/state/daydag/events.db`.
  The unattended run gets this from `scripts/progress-deck.sh` before Claude starts.
  Then the same steps, for the next Sponsor 1:1 in the coming 14 days, or the
  call he names. In a session, reply in chat instead of the DM. If he asks to see the
  changes first ("show me the list before building"), stop after step 4 with the list,
  as he has asked on earlier rounds.

## 1. Hunt the calls

1. Look up the Sponsor's email in the people directory. With none there, say so and stop:
   searching by display name fails silently.
2. Query the calendar **one day at a time** over the window (a week in one call overflows
   the output), with the Sponsor's email as `fullText`. For each event read the attendees,
   not the title:
   - **A 1:1:** the required attendees (not optional, not a room) are exactly Nitin and
     the Sponsor. An assistant who organises it as an optional guest does not change that.
     A moved instance of the recurring series counts at its new time.
   - Skip it when it is cancelled, or when the Sponsor or Nitin declined it. Say so in
     the DM ("tue 1:1 declined by the Sponsor, deck not refreshed"), because a skipped
     refresh he expected is worse than a noisy one.
   - **Small calls** with both of them and at most eight required attendees (a program
     review, a domain review): list them under "also w/ the Sponsor next week" with the
     deck pages they touch. They do not trigger a refresh on their own.
3. Find **the last 1:1 held**: the most recent past instance with both accepted, or the
   last date the series ran. Its date is the cut-off for "since the last call".

## 2. Read what has already been said

Do all of it before touching a number. Each one has cost a round when skipped.

1. **His latest version.** Snapshot it first
   (`zsh scripts/progress-deck.sh --snapshot his <latest>.pptx`), then list his stickies
   (`python3 .claude/skills/consulting-slides/scripts/stickies.py <latest>.pptx`) and diff
   it against the build it came from
   (`python3 .claude/skills/consulting-slides/scripts/drift.py <latest>.pptx <stickies>/<latest>-built.pptx`).
   Every retyped word, recolour and moved box is his and goes into the content files
   first. A sticky he left is a request: apply it if it is a fact or a clear wording
   call, queue it if it changes structure (step 4).
2. **The pending file.** Queued items, open calls, placeholders and their due dates.
3. **The Sponsor since the last call.** His 1:1 DM (the directory's `dm`), and his messages
   anywhere: `from:<@ID>` (the directory's `slack_id`) with `after:` the last 1:1, in
   Slack's filter fields, each thread followed to its replies. Quote what he said about Data & AI, the
   format or the deck. The format notes he gives on a work-in-progress copy are his
   requests, not the reviewer's; they queue for Nitin's go like any structural change.
4. **The last call's notes.** Gmail from `gemini-notes@google.com` with the 1:1's exact
   title in the subject, then the Notion meeting notes. Often there are none: say "no notes
   of the <date> call" and ask in the DM what the Sponsor asked for, rather than guessing.
5. **Answers to the placeholders.** For each bracketed placeholder or open input in the
   pending file (dates an owner promised, a proofread due back, a count to confirm), read
   that owner's DM with Nitin and the thread it was asked in. An answer fills the
   placeholder with its quote; a due date that has passed with nothing goes under
   "needs you" as overdue, and the placeholder stays.
6. **His own open asks of the Sponsor.** Search the 1:1 DM with no date bound for what he
   asked the Sponsor and has not had answered (a decision, an approval, and the like). Unresolved ones
   lead the talking points, ahead of the work. They never go on a page.

## 3. Refresh the evidence, row by row

The deck's speaker notes name the source behind every row: Jira epics and sprints,
GitHub roadmap issues, the label rollout sheet, Slack threads, Notion plans. Re-read each
one now; never carry a number from a previous round.

- **Est. done** keeps the counting rule printed in the page's note line (today: tickets
  done or in review, plus work Slack shows built before Jira moved). GitHub roadmap
  issues: `gh api repos/<org>/<repo>/issues/<n> --jq .sub_issues_summary` gives closed and
  total. Jira: bound every JQL to the row's epic and the sprint window. A % moves only on
  a count read in this run, and the notes show the count.
- **Dates** move only when a live source restates them: a sprint end, an owner's message,
  a plan document. A month nobody has dated stays dashed and gets flagged; never invent
  a date to fill February to May.
- **Nothing closes on its own.** A merged PR or a closed ticket is not the deliverable seen
  in use. A row reaches done or 100% only when a source shows it used (a meeting run on
  it, a named person working from it, a number reconciled); otherwise it is "built, not
  confirmed" and goes under "needs you". Pressure-test any 0% or 100% (Style.md).
- **Counts, not adjectives:** "7 of 18 labels", never "most".
- **Titles and the summary page** carry numbers too. Where a title is his wording, change
  only the number and say so; the sentence stays his.
- **Discrepancies are surfaced, not resolved.** Two sources disagreeing on a date or an
  owner both go in the notes and under "needs you".
- **A source you cannot reach** is one line ("couldn't check jira") and its rows keep last
  version's values, marked as not rechecked in the notes.

Write each change as `page · row · old -> new · source quote · permalink`. That list is
the backbone of the notes, the pending file and the DM.

## 4. Sort the changes: facts move, structure waits

| Goes into this build | Queued in the pending file for his go |
|---|---|
| his retyped words and edits from the drift check | new or removed pages, rows or columns |
| est. done, counts, dates, status and risk text that a source moved | page order, layout, a new format the Sponsor asked for |
| a placeholder an owner answered, with the quote | a title rewritten beyond its numbers |
| footnotes and sources dated to this run | an ask added to or dropped from the asks page |
| a sticky that is a fact or a clear wording call | anything the reviewer suggests that changes the argument |

Nothing moved since the latest version was built, and no sticky or drift: build nothing.
Say "vNN is current, nothing moved since <date>" and still send the talking points.

## 5. Build through consulting-slides

Follow `.claude/skills/consulting-slides/SKILL.md` steps 5 to 7 and its
`references/feedback-loop.md`; this skill adds only the order and the guards.

1. Edit the content files with the change list, touching only what moved and leaving
   every other line byte for byte. At the top of each changed page's speaker notes add a
   paragraph in the deck's existing round style:
   `REFRESH <date>, for the <call date> 1:1. <row>: <old> -> <new> (<quote>, <permalink>). ...`
2. `OUT="$(bash .claude/skills/consulting-slides/scripts/nextver.sh "$PROGRESS_DECK")"`, then
   `zsh scripts/progress-deck.sh --build "$OUT"`. The helper refuses a path that exists or
   sits outside the deck's folder: every build is a new file and never the one he has open.
3. Render (`bash .claude/skills/consulting-slides/scripts/render.sh "$OUT"`), look at every
   changed page, and run
   `.venv/bin/python -m daydag.style check <build.js> --project "Data & AI progress"`.
   Then `drift.py <his latest> "$OUT"`: only this round's changes may remain.
4. Review: `slide-review` in a fresh agent with the consulting-slides step 6 brief.
   Audience the Sponsor, a live 1:1 with the deck as pre-read, scope the pages this run
   changed plus the titles-only pass (the whole deck if a title or the order changed).
   Fix blockers and majors that are facts in your hands; ones that need his call become
   placeholders and "needs you" lines; anything that conflicts with his stickies, his
   wording or Style.md loses. Drift his file again, rebuild to the next version, send the
   changed pages back to the same reviewer once. At most two passes.
5. Snapshot the final build: `zsh scripts/progress-deck.sh --snapshot built "$OUT"`.
   The next round's drift check needs it.

## 6. The pre-read copy - built, never sent

Build `<stem>-preread-<date>-vNN.pptx` beside it with
`zsh scripts/progress-deck.sh --build <that path> NO_NOTES=1 NO_CONFIRM=1` and render it,
which leaves a PDF in its `-pages/` folder. The speaker notes carry Slack links,
internal quotes and personnel lines, so the Sponsor's copy never has them. Any bracketed
placeholder still on a page means the copy is not ready: say how many and where. Nothing
is sent and, unattended, nothing is drafted: he says "draft it" and a session puts a
note in the Sponsor DM as a draft, in standard English per `## House` in Style.md.

## 7. Update the pending file

Append, never rewrite: the version under Done with a one-line summary
(`v16 (21 Nov, weekend refresh for the 24 Nov 1:1): 6 numbers, 2 dates, 1 placeholder answered`),
each queued structural change under Queued with who asked and the quote, and any
placeholder still open with its owner and due date. Change "Latest version" and
"Next build" at the top.

## 8. Tell him

Unattended: one DM to Nitin (`SLACK_USER_PRINCIPAL`) and nowhere else, then one reply in
its thread. DayDAG's own register: lowercase openers, short lines, `w/`, `lmk`. Hyphens,
never em dashes. Only the sanctioned emoji.

```
progress deck ready for your 1:1 w/ the Sponsor - tue 24 nov 3pm
v16: <path to the .pptx>  (v15 + 9 changes, built sat 21 nov 08:20)
pre-read: <path to the PDF> - not sent; 2 placeholders still on p6

what moved since v15
- p3 discovery: labels live 6 -> 7 - "<quote>" <permalink>
- p4 streams: est. done 75% -> 82% - 103 of 126 done or in review (jira, 21 nov)
...
needs you
- queued, not built: <structural change> - <who asked, quote>
- overdue: <owner>'s february-to-may dates, due 20 nov - nothing in their DM or the thread
review: <verdict in one line>; fixed <n>, left <what and why>
also w/ the Sponsor next week: <small call, day time> (pages <n>)
```

Thread reply, the talking points, numbered because the order is the point:

```
1:1 talking points
1. <his own open ask of the Sponsor> - last said <date> <permalink>
2. what the Sponsor asked last call - <quote and permalink, or "no notes of the <date> call, what did he ask?">
3. the asks page - <each ask, and what changed for it>
4. the three biggest moves since last call
```

No 1:1 in the window: post nothing. A step that failed: post one line naming it, the
version built so far if any, and "run /progress-deck in a session to finish".

## Guardrails

- **Drafts, not sends.** Unattended, the only message is Nitin's own DM. The deck, the
  pre-read and any note to the Sponsor go nowhere until he says so.
- **His file is never written.** New versions only; a `~$` lock file beside his copy is
  normal and changes nothing, because nothing writes to it.
- **Live sources, not the vault.** Status comes from Jira, GitHub, Slack, Gmail and the
  calendar; Obsidian records what he had time to write down.
- **Quote, don't paraphrase.** Every change carries its quote and permalink in the notes.
- **Personnel, comp and his own asks of the Sponsor** stay in the DM and the talking points, never
  on a page and never in the pre-read.
- **Rules his feedback teaches** go to Style.md through consulting-slides step 8, not here.
