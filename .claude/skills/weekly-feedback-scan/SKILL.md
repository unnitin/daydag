---
name: weekly-feedback-scan
description: "Run Nitin's weekly feedback scan for his three direct reports — VP-AI, VP-Data and SrDir-Agentic. Scans the past 7 days of Slack and Gmail for evidence of progress and coaching moments, calibrates against their expectations baseline, and writes a dated Feedback Log Google Doc to the private Drive folder. Use whenever Nitin asks for a feedback scan, a feedback log, \"how are VP-AI and VP-Data doing\", \"how is SrDir-Agentic doing\", weekly VP feedback, direct-report progress review, or when this runs as the scheduled weekly routine. Do NOT use for the team-wide progress report to sponsors (that's weekly-progress-reporting) or for planning Nitin's own week (weekly-planning)."
daydag:
  # SPEC 10.1, mechanical rather than prose. `sensitivity: private` is what lets
  # the registry REFUSE to route this skill's output to any surface with an
  # audience above one - a brief, a channel draft, or DayDAG/State.md, which is
  # plaintext in an iCloud-synced vault. Its carry-forward items live in the
  # event log instead. Do not relax this key; a guardrail test asserts it.
  #
  # The one artifact below is an id, not a surface. It lives on the
  # `drive:private` surface, which is the name `route()` reasons about and one
  # of the three in registry.PRIVATE_SURFACES - so this skill's own output is
  # routable and nothing else is.
  writes:
    - drive:${GDRIVE_FEEDBACK_LOG_FOLDER}/
  reads: [slack, gmail, drive]
  consumes: [evidence.pulse, evidence.slack]
  emits: []
  schedule: "weekly"
  sensitivity: private
---

# Weekly Feedback Scan — VP-AI & VP-Data

Produce Nitin's private weekly feedback log for his three direct reports. This log is a management tool for Nitin only: **read from Slack/Gmail/Drive, write one Google Doc, send nothing to anyone.** No Slack messages, no emails, no sharing the doc. VP-AI, VP-Data and SrDir-Agentic must never receive anything from this workflow.

## The three people and their bars

| Person | Email | Role | Bar |
|---|---|---|---|
| VP-AI | ${EMAIL_VP_AI} | VP of AI | Tenured BCG Project Leader: owns the answer end-to-end — delivery quality, predictable releases, verified (not asserted) quality; growing edge is strategic/Principal-facing contribution |
| VP-Data | ${EMAIL_VP_DATA} | VP of Data | Early BCG Principal: judged on outcomes of work he doesn't personally touch — portfolio direction, senior stakeholders coming to him, institution-building, commercial framing |
| SrDir-Agentic | ${EMAIL_SRDIR_AGENTIC} | Sr. Director, Agentic Automation | **DRAFT BAR — Nitin to correct.** Early BCG Project Leader: owns one workstream end-to-end and is measured on automation others adopt and keep running, not on demos; growing edge is turning one-off agent wins into enablement another pod can run without him |

The key calibration: VP-AI is measured on the quality and reliability of delivery he directly drives (with Principal-facing stretch); VP-Data is measured on direction-setting, senior-relationship ownership, and the strength of people/systems he builds — without a delivery discount. Feedback that would be praise at one level can be developmental at the other (e.g., VP-Data personally fixing a pipeline is a portfolio-ownership flag, not a win).

## Step 1 — Read the reference docs (every run; they evolve)

1. **Expectations baseline** — Google Doc ID `${GDOC_VP_EXPECTATIONS}` ("VP Expectations — VP-AI (AI) & VP-Data (Data) — 2026"). This defines each person's mandate, the per-section expectations, and year-end success criteria. Read it in full; map every feedback item back to a specific section of it.
2. **Feedback methodology** — Doc ID `${GDOC_FEEDBACK_METHODOLOGY}` ("FeedbackExpectations.md"). Non-negotiables: example-driven (no linked example, no feedback), about the work never the person, constructive with a path forward, calibrated to level, tagged to one of the four dimensions — Domain Expertise, Practicality, Problem Solving, Communication.
3. **Prior logs** — list Drive folder `${GDRIVE_FEEDBACK_LOG_FOLDER}` ("Feedback Logs — VP-AI & VP-Data") with a `parentId` search and read the most recent log. Carry every still-open item forward with an updated status (closed with linked evidence / still open / re-scope or escalate — anything open past two reviews gets flagged for re-scope per the methodology). If the folder is empty, this is the first log — say so in the doc.

## Step 2 — Scan the past 7 days

Compute the date window with `date` first; search both sources for each person, by name and by email. Cast a wide net, then keep only what's high-signal.

**Slack** (use `slack_search_public_and_private` — much of this traffic is in private channels/DMs):
- `from:` each person, `after:` the window start — what did they ship, announce, decide, escalate?
- Their names as keywords — what are others saying about their work (stakeholder reactions, complaints, praise)?
- Known hot channels: #ar-tooling-dev-team, #team_devops, pod update channels, release/incident channels. Read full threads (`slack_read_thread`) for anything that looks like a release, incident, demo, stakeholder friction, or missed date — the thread, not the snippet, is where the signal lives.
- Capture the **permalink** for every message you might cite.

**Gmail**: search Nitin's threads mentioning any of the three (`from:`, `to:`, or name in body) within the window. Read full threads (`get_thread`, PLAIN_TEXT) for anything involving stakeholders, escalations, or commitments. Note thread subject + date for citation.

What counts as signal, mapped to the expectations doc: release/cutoff execution against communicated dates; eval/quality gates; early vs. late risk flagging (with or without proposed mitigation); demo/stakeholder performance; roadmap or strategic proposals; delegation vs. personal heroics (especially for VP-Data); feedback-log/ladder discipline with their own reports; hiring; cost/value framing.

## Step 3 — Write the log doc

Create a Google Doc **inside folder `${GDRIVE_FEEDBACK_LOG_FOLDER}`** titled `Feedback Log — VP-AI & VP-Data — YYYY-MM-DD` (today's date). Do not share it with anyone.

Structure — one section per person, each with:

**PROGRESS** — notable movement against their expectations doc. Every claim cites a linked example (Slack permalink, email thread, doc, PR). *No link, no claim* — if you can't cite it, cut it.

**FEEDBACK** — developmental and reinforcing items. Each item contains:
- the specific example, linked
- which expectation section and which of the four dimensions it maps to
- why it matters at *their* bar
- what success would look like
- a proposed next step (concrete, dated where possible)
- carried-forward items from the prior log, each with current status

Keep it tight: a handful of high-signal items per person beats an exhaustive list. If a week is quiet for one person, write that explicitly — never manufacture feedback to fill space. Reinforcing feedback gets logged with the same rigor as developmental (it's the promotion evidence base).

## Step 4 — Close out

Give Nitin a 3–5 bullet summary of the week's most important signals. When running as a scheduled/unattended routine, deliver that summary via PushNotification inside `<routine_summary>` tags (lead with the single most important sentence); when Nitin is present, the summary goes in the reply. If the run itself fails (no access, searches erroring), that failure is the notification — don't fail silently.
