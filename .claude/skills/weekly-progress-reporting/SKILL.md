---
name: weekly-progress-reporting
description: Produce the weekly Data & AI progress report for Former-Sponsor: update the Priorities doc's Progress tab against goals, then draft highlights email. Use for progress report, goals-doc update, Former-Sponsor update.
---

# Skill: Weekly Progress Reporting (Data & AI → Former-Sponsor)

The weekly ritual is two artifacts: **update the Progress tab** of the Priorities doc, then **draft the highlights email** to Former-Sponsor. The doc is the durable record mapped to the stated goals; the email is the cover note Former-Sponsor actually reads. They share one body of evidence, so build the doc first and the email is a summarization of it.

This is the **backward-looking** companion to the `weekly-planning` skill. Planning synthesizes the week ahead from the Obsidian spine; this reports progress against the formal goals. Both pull from the same sources (Obsidian weekly notes, Slack, Gmail), so the evidence-gathering overlaps — if a planning pass already ran this week, reuse what it found.

---

## The source of truth

**Priorities Google Doc** — `${GDOC_GOALS_PRIORITIES}`, `createmusic` Google account.
- It's a **Google Doc with tabs** (not a sheet, despite being called "the goals sheet" sometimes). Two tabs matter:
  - **Priorities tab** (`v0XXXXX`, currently `v021726`) — the stated goals. The stable spine. Don't edit unless asked.
  - **Progress tab** (`v0XXXXX`, currently `v053026`) — the report. This is what gets updated each week.
- Fetch the live doc with `Google Drive:read_file_content` (fileId above). **Always pull it fresh** — Nitin edits the Progress tab directly between runs (tightens wording, marks things done, corrects facts). Never report from a prior draft or from memory; pick up his tweaks.
- Editing the doc: a clean tab rewrite via `Google Drive:create_file` semantics is messy for tabbed docs. In practice, **produce the updated Progress-tab markdown and present it for Nitin to paste**, unless he explicitly asks you to write back to the doc. Confirm the write path with him the first time each cycle.

---

## Sources for the week's evidence (priority order)

1. **Obsidian weekly notes** — `Weekly Notes/MMDD-MMDD.md`. The last 2–3 notes (≈3 weeks back) are the spine; they already quote the underlying Slack/email/pod threads verbatim. Vault path: `${VAULT_ROOT}/`. Search via `obsidian-search:search_notes`, then read full notes with `get_note_content`. Weekly notes are Mon–Fri ranges, not Sun–Sat.
2. **Slack** — channels from `Fact Base/Important Links.md`. Pattern `in:#[channel] from:[name] [topic]` beats broad cross-channel search. Use for sprint demos, pod updates, escalations, and the verbatim quotes that back each claim.
3. **Gmail** — recent threads relevant to the week's goal areas (release notes, Finance reviews, hiring approvals, exec sign-offs).
4. **Confluence** — corroborate only. Pages in restricted space "D" return 404 even with valid IDs — that's a permission restriction, not a bad ID; confirm via Slack instead of retrying.

**Pull actual source content, don't paraphrase from memory.** The whole point is that every progress claim is backed by something real that shipped. Nitin actively corrects paraphrase — quote the thread.

---

## Output 1: Progress tab update

Structure **mirrors the Priorities-tab goal dimensions exactly**, so the report reads as a direct response to the goals. Keep the same headings the goals doc uses:

```
# Data & AI — Progress Against Stated Goals
**For:** Former-Sponsor  **As of:** [date]  **Mapped to:** Priorities doc ([version])
> Structure mirrors the goals doc. Where the real goal has moved beyond the doc text,
> flag it as ↑ Update vs. doc so the next refresh can catch up.

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

- **Bullets under each dimension**, each a self-contained statement of what moved this week. Tight, factual, legible without prior context.
- **`↑ Update vs. doc`** marks any place the real goal has drifted from the written goal — this is the single most valuable thing in the report. The doc text lags reality; surfacing the drift is how the goals get refreshed. Examples that have recurred:
  - Data foundations: "separate analytics space + medallion revamp" → full data-platform build on Databricks
  - AI foundations: doc says "Not Started" → actually a live, demoed build + dedicated pod
  - Opex: "$1M savings target" → a value framework (Finance reframed the measurement bar)
  - Team building: band/criteria definition → active team build-out
- **Carry the doc's own status tags** in parentheses on each goal — `(doc: IN PROGRESS)`, `(doc: Not Started)`, `(doc: DONE)` — so the reader sees stated-vs-actual side by side.
- **Emergent-priorities section is mandatory.** The work that isn't in the goals doc is where the quarter's bandwidth actually goes (DistroKid/Project Dream, the migration agent, streaming-analytics brief, Google commit). Listing it is how the goals doc gets told what to add.
- **Source-basis line at the bottom** — name the weekly notes and key threads the report draws on, and offer to expand any line.
- **Preserve Nitin's edits.** When you pull the live tab, diff against what you'd write. Keep his tightened wording and corrections; only layer in genuinely new movement.

---

## Output 2: Highlights email to Former-Sponsor

A short, **bulleted, grouped** email — the cover note. Compose with the `message_compose_v1` tool (`kind: email`). Default to a **single tight variant**; offer a fuller by-dimension version only if Nitin wants more altitude.

### Format (this is the house style Nitin landed on)

- **Open:** one line — "Weekly progress against our goals. Full detail in the Progress tab of the Priorities doc."
- **Grouped under plain headers** matching the goal dimensions (no bold-heavy headers): `Data foundations`, `A&R discovery`, `Opex / CreateOS Labs`, `Team building`, then `Not in the goals doc — flagging for the next refresh`.
- **One idea per bullet. Short sentences.** Break compound bullets into two. This is the explicit preference — terse over dense.
- **Lead each dimension with the win**, then the scope-drift note, then any caveat. For A&R, lead with the concrete proof point (e.g. "R1 was demoed to you Friday 5/29 — on time").
- **`Decisions for you this week` block at the bottom** — the 1–3 things Nitin needs from Former-Sponsor (e.g. AJ/Kiwi sign-off; refresh the GCP/BigQuery cost view). This makes the email actionable, not just status.
- **Close:** "Happy to expand any line into the underlying thread." Sign "Nitin."
- **Subject:** `Data & AI — Weekly Progress (week of [Mon date])`.
- No emojis. Don't reproduce the whole Progress tab — the email is the 5-group skim; the doc is the detail.

### Reference example (the 0530 send, abridged)

```
Hi Former-Sponsor,

Weekly progress against our goals. Full detail in the Progress tab of the Priorities doc.

Data foundations
• Scope broadened: from "separate analytics space + medallion revamp" to a full data-platform build on Databricks.
• Sprint 3 shipped, Sprint 4 in flight.
• Gold performance access is open to Analytics-Partner/Eng-8.

A&R discovery
• Doc says "Not Started." In fact, R1 was demoed to you Friday 5/29 — on time.
• Scope grew from "a feature" to a product + a dedicated AI Platform pod under VP-AI.

Opex / CreateOS Labs
• Action for you: the AJ/Kiwi build-out agreement is in front of you for sign-off.
• Finance reframed the bar — avoid broad "$1M opex-savings" claims unless volume-based.

[Team building; Not in the goals doc …]

Decisions for you this week
• AJ/Kiwi sign-off.
• Whether to refresh the GCP/BigQuery cost view for the partnership decision.

Happy to expand any line into the underlying thread.
Thanks,
Nitin
```

Leave the email as a draft (compose tool or `Gmail:create_draft` if asked). **Never send** — Nitin reviews and sends himself.

---

## Workflow

1. **Pull the live Progress tab** (`Google Drive:read_file_content`). Note Nitin's edits since last week.
2. **Gather the week's evidence** — last 2–3 Obsidian weekly notes first; then targeted Slack/Gmail for anything that shipped, got approved, or got reframed. If a planning pass ran this week, reuse its findings.
3. **Update the Progress tab** — refresh each dimension's bullets, re-check `↑ Update vs. doc` flags, update the emergent-priorities list and the as-of date. Present the markdown for paste (or write back if Nitin confirmed that path).
4. **Once Nitin is happy with the doc, draft the email** — summarize the tab into the 5-group tight format with the decisions block.
5. **Iterate.** Expect rounds: "format tighter," "smaller sentences," "lead with the demo," "swap in the actual quote." Mark things done, reframe, consolidate, pull real source content. This mirrors the planning skill's iterative norm.

### Week-over-week delta (once there's a prior send)
Lead the email with a one-line "since last week" delta so Former-Sponsor sees movement, not the standing picture re-stated. Pull the prior week's email/Progress-tab version to diff against.

---

## Key facts to keep straight

- **Doc ID:** `${GDOC_GOALS_PRIORITIES}` · **Account:** createmusic
- **The artifact is a Google Doc with tabs**, not a spreadsheet. Edit the Progress tab; leave the Priorities tab alone.
- **Goal dimensions (fixed order):** Business understanding · Business impact (Opex reduction · Data foundations · AI foundations / A&R discovery · Governance) · Team building · Emergent priorities.
- **Recurring decisions/asks that surface for Former-Sponsor:** AJ/Kiwi CreateOS Labs sign-off; GCP/BigQuery cost-view refresh; restating the "$1M opex" target as a value framework; new Staff/Lead AI/ML salary band.
- **People:** Former-Sponsor (manager / report audience), VP-Data (data platform/ingestion/devops/security), VP-AI (A&R Discovery + AI Platform), Enablement-Lead (Agentic Enablement / Kiwi / CreateOS Labs), Gov-Lead (governance/roadmap), Analytics-Partner (analytics), Eng-3 (data eng), AJ + Abhishek (Kiwi), Michael Bale (Finance review of the value framework).
- **Atlassian space "D"** 404s are permissions, not bad IDs — corroborate via Slack.
