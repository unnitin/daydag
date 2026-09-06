---
name: weekly-progress-reporting
description: "Produce the weekly Data & AI progress report: update the Priorities doc's Progress tab against goals, then draft the highlights email to the current sponsor. Use for progress report, goals-doc update, weekly update"
---

---

# Skill: Weekly Progress Reporting (Data & AI → sponsor)

The weekly ritual is two artifacts: **update the Progress tab** of the Priorities doc, then **draft the highlights email** to the current sponsor. The doc is the durable record mapped to the stated goals; the email is the cover note the sponsor actually reads. They share one body of evidence, so build the doc first and the email is a summarization of it.

This is the **backward-looking** companion to the `weekly-planning` skill. Planning synthesizes the week ahead from the Obsidian spine; this reports progress against the formal goals. Both pull from the same sources (Obsidian weekly notes, Slack, Gmail), so the evidence-gathering overlaps — if a planning pass already ran this week, reuse what it found.

> **Audience note (read first):** the report audience is **whoever the most recent send was addressed to — currently Sponsor.** **Former-Sponsor has departed CMG**; Sponsor is the interim sponsor and was added to the thread on the 6/19 send. Always confirm the current recipient from the live thread before drafting (see Threading); never hard-code "Former-Sponsor."

---

## The source of truth

**Priorities Google Doc** — `${GDOC_GOALS_PRIORITIES}`, `createmusic` Google account.
- It's a **Google Doc with tabs** (not a sheet, despite being called "the goals sheet" sometimes). Two tabs matter:
  - **Priorities tab** (`v0XXXXX`, e.g. `v021726`) — the stated goals. The stable spine. Don't edit unless asked.
  - **Progress tab** (`v0XXXXX`) — the report; this is what gets updated each week (one new dated version per week, e.g. `v053026`, `v060526`, …).
- Fetch the live doc with `Google Drive:read_file_content` (fileId above). **Always pull it fresh** — the Progress tab is edited directly between runs (tightened wording, marked done, corrected facts). Never report from a prior draft or from memory; pick up those tweaks.
- Editing the doc: a clean tab rewrite is messy for tabbed docs. In practice, **produce the updated Progress-tab markdown and present it for paste**, unless explicitly asked to write back. Confirm the write path the first time each cycle.

---

## Altitude: write at the level of the goals doc

**This is the most actively enforced rule.** The Priorities tab states goals at exec altitude ("assemble a deck that explains core facets of the business," "$1M opex reduction," "build a feature in CreateOS around artist discovery"). The progress report must answer at that same altitude — it is a response to the goals, not a log of the week.

- **One or two high-level moves per goal dimension.** Fewer things per week is correct, not a gap. A dimension with nothing goal-level to say carries a single status line.
- **Each bullet speaks to the goal itself** — did the stated goal advance, get delivered, or drift? Not "what the team did."
- **Sprint/ticket detail does not belong** — no PR numbers, pipeline names, NULL-handling fixes, auth mechanics, access grants. That lives in the threads; the source-basis line and "happy to expand any line" close are the pointer to it.
- Litmus test: every bullet should make sense to the sponsor reading only the goals doc. If it needs engineering context to land, it's too low.

Calibration (both true; the second is report-worthy):
- Too low: "Vendor-Contact shipped last-non-NULL carry-forward for Soundcharts; artist_id_bridge reprocessed to 89.54%; WIF secretless auth applied."
- Right: "A&R Discovery R1 reached a working end-to-end on dev this week — the goal doc still says 'Not Started.'"

---

## Sources for the week's evidence (priority order)
1. **Obsidian weekly notes** — `Weekly Notes/MMDD-MMDD.md`. The last 2–3 notes are the spine; they quote the underlying Slack/email/pod threads verbatim. Durable per-workstream context lives in `Fact Base/Workstreams.md` — **read it for status/owner/open-decision; do NOT write it.** Maintaining `Workstreams.md` is owned by the `weekly-planning` skill (its write-back step), so neither skill double-writes. Vault path: `${VAULT_ROOT}/`. Mon–Fri ranges.
2. **Slack** — channels from `Fact Base/Internal Links.md`. Pattern `in:#[channel] from:[name] [topic]`.
3. **Gmail** — recent threads in the week's goal areas (release notes, Finance reviews, hiring approvals, exec sign-offs, meeting recaps).
4. **Confluence** — corroborate only; restricted space "D" 404s are a permission restriction, not a bad ID — confirm via Slack.

**Pull actual source content, don't paraphrase from memory.** Gather low, report high.

---

## Output 1: Progress tab update

Structure **mirrors the Priorities-tab goal dimensions exactly**, so the report reads as a direct response to the goals:

```
# Data & AI — Progress Against Stated Goals
**For:** [current sponsor — e.g. Sponsor]  **As of:** [date]  **Mapped to:** Priorities doc ([version])
> Structure mirrors the goals doc. One or two high-level moves per goal — detail lives in the
> underlying threads. Where the real goal has moved beyond the doc text, flag ↑ Update vs. doc.

## Business understanding
## Business impact
   ### Opex reduction — $1M target (doc: [status])
   ### Data foundations (doc: [status])
   ### AI foundations in CreateOS — A&R discovery feature (doc: [status])
   ### Governance for Data and AI (doc: [status])
## Team building
## Emergent priorities not in the goals doc (flag for the next refresh)
*Source basis: [which weekly notes / release notes / threads]*
```

### Rules for the Progress tab
- **One or two bullets per dimension, at goal altitude.** Each a self-contained statement of how the stated goal moved.
- **`↑ Update vs. doc`** marks where the real goal has drifted from the written goal — the single most valuable thing in the report. Recurring examples: Data foundations "separate analytics space + medallion revamp" → full Databricks platform build; AI foundations "Not Started" → live demoed build + dedicated pod; Opex "$1M savings" → value framework (Finance reframed the bar); Team building band/criteria → active build-out.
- **Carry the doc's own status tags** in parentheses — `(doc: IN PROGRESS)`, `(doc: Not Started)`, `(doc: DONE)` — so stated-vs-actual sit side by side.
- **Emergent-priorities section is mandatory** — the work not in the goals doc is where the quarter's bandwidth actually goes (DistroKid/Project Dream, the migration agent, streaming-analytics, Kiwi renegotiation, Google/GCP). Name the workstream + its open decision, not its mechanics.
- **Source-basis line at the bottom**; offer to expand any line.
- **Preserve edits.** Diff against the live tab; keep tightened wording and corrections; layer in only genuinely new movement.

---

## Output 2: Highlights email

A short, **bulleted, grouped** email — the cover note. Default to a **single tight variant**.

### Threading: reply to the prior week's send
**Draft as a REPLY on the existing weekly-progress thread, not a new email** — it runs as one continuous thread so the sponsor has the season's history in one place.
- Find the most recent sent "Data & AI — Weekly Progress" message (`subject:"Weekly Progress" in:sent`) and create the draft with `replyToMessageId` set to it. Subject stays `Re: Data & AI — Weekly Progress (week of [original Mon date])` — don't fight the threading.
- **Confirm recipients from that most recent send and match them.** As of mid-June the audience transitioned to **Sponsor** (Former-Sponsor departed); recent CC has been Enablement-Lead, VP-AI, VP-Data, Eng-3. If the latest send still listed a departed person in To:, drop them and address the current sponsor — and note that choice for the user.
- Only start a fresh thread if no prior send exists or you're told to reset (new quarter/goals version).

### Format (house style)
- **Open:** a one-line "Since last week:" delta (the 1–2 goal-level things that moved), then the standing line — "Weekly progress against our goals. Full detail in the Progress tab of the Priorities doc."
- **Grouped under plain headers** matching the dimensions: `Data foundations`, `A&R discovery`, `Opex / CreateOS Labs`, `Team building`, then `Not in the goals doc — flagging for the next refresh`.
- **One idea per bullet. Short sentences. Two bullets per group is the norm** — more compressed than the tab.
- **Same altitude rule** — every bullet speaks to a stated goal or a decision. No engineering mechanics.
- **Lead each dimension with the win**, then the scope-drift note, then any caveat.
- **`Decisions for you this week` block** at the bottom — the 1–3 things the sponsor needs to decide (e.g. Kiwi finalize-vs-RFP; SIA contractor-vs-FTE; Claude enterprise/GCP-bundled path; GCP/BigQuery cost-view refresh).
- **Close:** "Happy to expand any line into the underlying thread." Signed "Nitin." No emojis. Don't reproduce the whole tab.

Leave the email as a **draft** (`Gmail:create_draft` with `replyToMessageId`). **Never send** — Nitin reviews and sends himself. (If Nitin asks for the content as a markdown to refine first, produce that and skip/hold the draft.)

---

## Workflow
1. **Pull the live Progress tab** fresh; note edits since last week.
2. **Gather the week's evidence** — last 2–3 Obsidian weekly notes + `Workstreams.md` first; then targeted Slack/Gmail for what shipped, got approved, or got reframed. Reuse a planning pass if one ran.
3. **Update the Progress tab** at goal altitude (1–2 moves per dimension), re-check `↑ Update vs. doc`, refresh the emergent-priorities list + the as-of date. Present markdown for paste.
4. **Confirm the current recipient from the live thread**, then **draft the email as a reply** with the since-last-week delta up top and the decisions block at the bottom.
5. **Iterate** — expect "more elevated," "fewer things," "lead with the demo," "swap in the actual quote." The recurring correction is altitude.

---

## Key facts to keep straight
- **Doc ID:** `${GDOC_GOALS_PRIORITIES}` · **Account:** createmusic · it's a **Google Doc with tabs** (edit Progress, leave Priorities alone).
- **Goal dimensions (fixed order):** Business understanding · Business impact (Opex · Data foundations · AI foundations / A&R discovery · Governance) · Team building · Emergent priorities.
- **One continuous "Data & AI — Weekly Progress" thread;** each week replies to the last send.
- **Audience:** **Sponsor** (interim sponsor; Former-Sponsor departed CMG). Confirm from the live thread each time; CC has been Enablement-Lead, VP-AI, VP-Data, Eng-3.
- **Recurring decisions/asks that surface:** Kiwi finalize-vs-RFP (value-framing folded in); SIA contractor-vs-FTE; new Staff/Lead AI/ML band; Claude enterprise/GCP-bundled licensing (GCP multi-year commit now confirmed *not* signing); GCP/BigQuery cost-view refresh.
- **People:** Sponsor (sponsor), VP-Data (data platform/ingestion/devops/security), VP-AI (A&R Discovery + AI Platform), Enablement-Lead (Kiwi / CreateOS Labs), Gov-Lead (governance/roadmap), Analytics-Partner (analytics), Eng-3 (data eng), AJ (Kiwi), Michael Bale (Finance / value framework).
- **Atlassian space "D"** 404s are permissions, not bad IDs — corroborate via Slack.
