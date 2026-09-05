---
name: weekly-planning
description: Synthesize the week ahead into a plan + per-meeting talking points (two MMDD-MMDD markdown files) from Obsidian, Calendar, Slack, Gmail. Use for weekly planning, next-week to-dos, or meeting prep.
---

# Skill: Weekly Planning Synthesis

When asked to produce weekly planning, generate **two markdown files** in `MMDD-MMDD` format covering the upcoming Mon–Fri.

This is the **forward-looking** companion to the `weekly-progress-reporting` skill. Both pull from the same sources (Obsidian weekly notes, Slack, Gmail), so the evidence-gathering overlaps — if a reporting pass already ran this week, reuse what it found.

---

## Files to produce

1. **`MMDD-MMDD.md`** — Week-ahead plan (action items + context)
2. **`MMDD-MMDD-meetings.md`** — Per-meeting talking points

Output to `/mnt/user-data/outputs/` and present via `present_files`.

---

## Sources (in priority order)

1. **Obsidian vault**: most recent weekly note in `Weekly Notes/` (filename `MMDD-MMDD.md`) is the spine. Vault path: `${VAULT_ROOT}/`.
2. **`Fact Base/Important Links.md`** — Slack channel IDs reference.
3. **Google Calendar** — meetings for the week ahead.
4. **Slack** — channels from Important Links. Pattern `in:#[channel] from:[name] [topic]` outperforms broad cross-channel searches.
5. **Gmail** — recent threads relevant to the week's topics.

**Atlassian gotcha**: pages in restricted space "D" return 404 even with valid IDs — corroborate via Slack instead of retrying.

---

## File 1 structure: `MMDD-MMDD.md`

Four sections, lettered A–D. **Section A** holds the action items; **Section B** holds the context every action item links down to; **Section C** scaffolds this week's meeting notes; **Section D** collects items as they close. All wiki-links resolve to headings in Section B.

```
# Week of [Mon Date]–[Fri Date], YYYY
> Spine note + key context (holidays, demo shifts, etc.)
> Ownership map for this week (who owns what)

## Needs attention before Monday EOD
- 3–6 highest-priority items linked to context

# Section A — Action Items

## 1. Directly responsible (mine to drive)
### {General / cross-cutting}
### {DistroKid DD / Project Dream}      [or current major workstream]
### {Hiring / staffing}
### {Roadmap / org}
### {Personal}

## 2. Tracking (VP-AI / Enablement-Lead / VP-Data own; my visibility)
### A&R Discovery + AI Platform → VP-AI
### Agentic Enablement → Enablement-Lead
### Data Platform + Ingestion + DevOps + Security → VP-Data

## 3. Cross-teaming (Former-Sponsor / Analytics-Partner / CTO / Gov-Lead)
### → Former-Sponsor
### → Analytics-Partner
### → CTO
### → Gov-Lead

# Section B — Context (linked from above)
> Where every wiki-link resolves. Commentary lives here; Section A bullets stay clean.
### [Heading matching wiki-link target exactly]
Source: [provenance]
[Verbatim quotes, decisions, open questions, this week's actions, forcing functions]

# Section C — Meeting notes (this week)
> Scaffold by day, em-dash for inline notes as the week unfolds.
### Mon [Date]
- **[Time] [Meeting]** —
[... Tue, Wed, Thu, Fri]

# Section D — Done this week
<!-- Move action items here as they close. Keep wiki-link to its Section B context block intact. -->
```

The sub-headings under each Section A subsection mirror the prior weekly note's domains (General, Data Platform, A&R, BCG, Personal, etc.) and drift week to week — confirm them against the spine note rather than assuming last week's set.

### Ownership map (default)

- **VP-AI** → A&R Discovery + AI Platform
- **Enablement-Lead** → Agentic Enablement (Kiwi / CreateOS Labs)
- **VP-Data** → Data Platform, Ingestion, DevOps, Security
- **Cross-teaming**: Former-Sponsor, Analytics-Partner, CTO, Gov-Lead
- **Direct responsibility**: cross-cutting / Former-Sponsor-facing / DK / hiring / strategy / personal

Confirm the map at the start if it's drifted from last week.

---

## File 2 structure: `MMDD-MMDD-meetings.md`

```
# Meeting Talking Points — Week of [dates], YYYY
> Spine + week context

## Monday, [Date]
### [Time] — [Meeting Name]
[Context: who runs it, recurring meetings' last 4-week thread]
**Talking points / what to raise:**
- Self-contained items with inline source quotes
- Decisions to force, blockers, follow-ups
[... continue through Friday]
```

Per meeting: list talking points self-contained with inline context. For recurring meetings, pull notes from the last 4 weeks of that meeting plus relevant Slack threads and emails from the same window.

---

## Formatting rules

### Wiki-links
Use Obsidian syntax `[[#Heading Name|display text]]`. Never use HTML `<a id>` anchors — they don't render in Obsidian. Heading text must match the wiki-link target **exactly**. Every link resolves to a `### Heading` inside **Section B**.

### Action item bullets
- Tight, action-oriented — what to do, not backstory
- Mark high-priority with `[ATTN]`
- Consolidate facets of the same workstream into one bullet (see "Consolidation" below)
- Each task links down to its context section in Section B

### Section B context blocks
- One `### Heading` per linked task
- **Source line first**: what email thread / Slack channel / Obsidian note / calendar invite
- **Verbatim quotes** from primary sources (NOT paraphrase from memory)
- **Decisions made + open questions + forcing functions**
- This is where commentary lives; Section A bullets stay clean

### Done section
When items close, strike through `~~[[link]]~~` ✓ and move to Section D. Keep the wiki-link to the Section B context block intact so the audit trail survives.

---

## Workflow patterns

### Start every session by finding the latest weekly note
Use filename naming convention. Search systematically — try `0XYZ-0ABC.md` working backward by week. Don't assume the most recent note is the most recent file mtime-wise. Confirm with user before proceeding if there's a gap.

### Carryover verification (before drafting)
Spend 5 min on targeted Slack/email searches to verify what closed vs forward:
- Items framed as "done in flight" — confirm via Slack/email
- Items with target dates landing this week — verify status
- Discrepancies (e.g., June 5 vs June 11) — surface as discrepancies, don't pick

Then ask user to confirm which carryover items moved to Done before rebuilding.

### Source content over paraphrase
**Pull actual content from Slack/email/Obsidian rather than paraphrasing from memory.** Quote verbatim with attribution. The user actively corrects when context is paraphrased — saves a round-trip to quote directly.

### Consolidation over duplication
When two items are facets of the same workstream, merge them into one bullet and one context block. Pattern: items started on different days under different framings often turn out to be one thread. Examples surfaced this iteration:
- "Catalog acquisition pipeline" + "Systems Integration Analyst" → one workstream
- "SIA scope clarity" + "SIA sourcing" + "SIA placement" → one bullet

When user signals consolidation, the merged context block needs a *"Why these are one thread"* opener explaining the consolidation.

### Embedded commentary over bare task lists
Action items should include inline explanation of *why* something needs attention and what the real decision or risk is. Not just "Sprint 3 demo Tue 8a" — instead "Sprint 3 demo Tue 8a: frame the actual wins, decide on Revenue domain teaser."

### Iterative refinement is the norm
Expect 5–10 rounds of:
- Mark items done → move to Section D, strike through with ✓
- Add new workstreams → user provides context; ask for source pointers
- Reframe / consolidate → merge bullets and context blocks
- Swap in actual source content → fetch from Slack/email/Obsidian

When user provides a pointer like *"see the X thread w/ Y"*, immediately fetch it via Gmail/Slack search before responding.

---

## Cross-teaming section (Section A #3)

One subsection per cross-functional partner: **Former-Sponsor, Analytics-Partner, CTO, Gov-Lead** by default. Each subsection:

- Notes the 1:1 slot for the week (or "slot TBD" if multiple instances)
- Lists items I'm bringing to that person this week
- Each bullet links to the same Section B context as elsewhere (this is a *view*, not a source of truth)
- Mark high-priority with `[ATTN]`

This view exists so I can glance at "what am I bringing to each of them this week" without scanning the whole file. Per-meeting talking points still live in the meetings file.

---

## Common discrepancies to surface (don't silently resolve)

- Meeting slot duplication (e.g., 3 instances of Former-Sponsor 1:1 — pick live one)
- Conflicting target dates (e.g., June 5 vs June 11 for handover)
- Pod assignment ambiguity (e.g., Vini framed as AI Platform 5/19 → "Ingestion" 5/21)
- Scope reframings landed late in a thread (e.g., Former-Sponsor's 5/14 "migration agent" reframe of the SIA JD)

Flag these explicitly and let the user resolve.

---

## Tone & voice

- Self-contained items — bullets should be legible without recalled prior context
- Source quotes in italics inside `>` blockquotes with attribution
- No emojis except ✓ for done items and ⭐ for meeting flag
- No bullet-points-of-bullet-points beyond 2 levels
- Action verbs first ("Confirm…", "Pull thread on…", "Force decision on…")

---

## Key colleagues reference

- **VP-Data** — engineering lead
- **Eng-3** — data engineering
- **VP-AI** — A&R discovery
- **Enablement-Lead** — cross-functional partner (Kiwi / CreateOS Labs)
- **Gov-Lead** — senior stakeholder
- **Former-Sponsor** — senior stakeholder / direct manager
- **Analytics-Partner** — analytics / data product
- **CTO** — security / software / cross-functional
- **Kale** — BCG consultant
- **Engineers**: Eng-5, Rishi, Revenue-Lead, Yang, Sergey, Eugene, Jessie, Ryan, Modeler-Owner, Eng-Sr, Stakeholder-1, Former-Report, Eng-4, Ricardo, Vendor-Contact

---

## Slack channel IDs (frequently used)

- `${SLACK_CH_TEAM_DATA_ENGINEERING}` — team_data_engineering
- `${SLACK_CH_ENG_PAIR}` — ar-tooling-dev-team
- `${SLACK_DM_VP_AI}` — DM thread
- `${SLACK_CH_POD_DISCOVERY}`, `${SLACK_CH_STATEMENT_INGESTION_A}` — statement ingestion
- `${SLACK_CH_LUMINATE_REPLACEMENT_FEEDBACK}` — luminate
- `${SLACK_CH_STATEMENT_INGESTION_B}` — (frequently referenced)

Always check `Fact Base/Important Links.md` for the canonical list — IDs change.

---

## CMG Atlassian

- Cloud ID: `${ATLASSIAN_CLOUD_ID}`
- Domain: `createmusic.atlassian.net`
- Space "D" returns 404 on direct fetch (permission restriction, not invalid ID) — corroborate via Slack
