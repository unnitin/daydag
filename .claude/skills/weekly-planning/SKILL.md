---
name: weekly-planning
description: "Synthesize the week ahead into a plan + per-meeting talking points (two MMDD-MMDD markdown files) and maintain the evergreen Workstreams file, from Obsidian, Calendar, Slack, Gmail. Use for weekly planning, next-week to-dos, or meeting prep."
daydag:
  # The weekly note, the per-meeting prep file and the evergreen Workstreams
  # store. Workstreams moves to DayDAG at the custody cut (#37) and not before -
  # until then a second `writes:` on it is a startup error, which is the point.
  writes:
    # A trailing `/` is a folder the skill owns; the registry matches ids exactly.
    - vault:Weekly Notes/
    - vault:Meeting Prep/
    - vault:Fact Base/Workstreams.md
  reads: [obsidian, calendar, slack, gmail, drive, atlassian]
  consumes: [evidence.pulse, evidence.slack, evidence.meetings]
  emits: [plan.week-ahead]
  sensitivity: shared
---

# Skill: Weekly Planning Synthesis

When asked to produce weekly planning, generate **two MMDD-MMDD markdown files** covering the upcoming Mon–Fri **and** maintain the evergreen `Fact Base/Workstreams.md` (the durable store the weekly note links into).

This is the **forward-looking** companion to the `weekly-progress-reporting` skill. Both pull from the same sources (Obsidian weekly notes, Slack, Gmail), so the evidence-gathering overlaps — if a reporting pass already ran this week, reuse what it found.

> **Format note (read first):** the weekly note uses the **Priorities** layout below — a single triaged task list (`🔴 High / 🟡 Medium / 🟢 Lower-ongoing`) with `(mine)/(tracking)/(x-team)` tags and links out to `[[Workstreams#…]]`. This **replaced an older "Section A–D" layout** (Section A action items → B context → C meetings → D done) used through ~0615-0619. Always match the **most recent** weekly note in the vault; if it has drifted again, follow the latest note, not this template.

---

## Files to produce

1. **`MMDD-MMDD.md`** — Week-ahead plan (Priorities + week-specific context + meetings scaffold).
   - **Vault destination:** `Weekly Notes/MMDD-MMDD.md`.
2. **`MMDD-MMDD-meetings.md`** — Per-meeting talking points.
   - **Vault destination:** `Meeting Prep/MMDD-MMDD.md` — the Meeting Prep folder uses a **plain `MMDD-MMDD.md`** name (NOT a `-meetings` suffix, and NOT in Weekly Notes). In the flat outputs folder, keep the `-meetings` suffix to disambiguate, but state the real vault destination in the file.

3. **`Workstreams.md`** — evergreen write-back (see "Workstreams write-back" below).
   - **Vault destination:** `Fact Base/Workstreams.md` (ONE living file; never per-week copies).

Output all files to the outputs folder and present via `present_files`. State each file's vault destination at the top.

---

## File 1 structure: `MMDD-MMDD.md`

Thin by design. Durable, per-workstream context lives in `Fact Base/Workstreams.md` and is **linked, not restated**. The weekly note carries only this week's actions + genuinely week-specific detail.

```
# Week of [Mon Date]–[Fri Date], YYYY

> Spine note: [[prev MMDD-MMDD]]. Durable context lives in [[Workstreams]] — this note stays thin:
> action items link out to [[Workstreams#…]], and only week-specific detail sits in **Week-specific context**.
>
> **This week's frame:** the 1–3 things that shape the week (holidays/OOO, demo shifts, a live renegotiation,
> a departed stakeholder, etc.). Quote the source.
>
> **Ownership map (unchanged):** VP-AI → A&R Discovery + AI Platform · Enablement-Lead → Agentic Enablement (Kiwi /
> CreateOS Labs) · VP-Data → Data Platform, Ingestion, DevOps, Security. Everything else sits with me directly.
>
> **How this file works:** action items live under **Priorities**, sorted High → Medium → Lower, tagged
> `(mine)`/`(tracking)`/`(x-team)`. Each links to its workstream in [[Workstreams]] for durable context; the
> bullet itself carries this week's action. Close an item → strike it (~~…~~ ✓) and move the bullet (link
> intact) to **Done**.

---

# Priorities

## 🔴 High
- [ ] **Title** *(mine)* — this week's action, one or two sentences. → [[Workstreams#<heading>|label]]
	- [ ] optional sub-task (checkbox)
	- [x] closed sub-task

## 🟡 Medium
- [ ] **Title** *(tracking: VP-Data)* — … → [[Workstreams#<heading>|label]]

## 🟢 Lower / ongoing

**Mine**
- [ ] …

**Tracking (VP-AI / Enablement-Lead / VP-Data own; my visibility)**
- [ ] **Workstream** *(VP-AI)* — … → [[Workstreams#<heading>|label]]

**Cross-teaming (Analytics-Partner / CTO / Gov-Lead)**
- [ ] **Analytics-Partner** — what I'm bringing this week; see [[#Week-specific context|below]].
- [ ] **Gov-Lead** — …

---

# Week-specific context

> Only this week's specifics that aren't durable workstream context. Everything else resolves in [[Workstreams]].

### [Heading matching a wiki-link target, or a standalone week note]
[Source line first; verbatim quotes from the primary thread; the decision/open question/forcing function for THIS week.]

---

# Meetings (this week)

> Capture takeaways + follow-ups here as the week unfolds. Per-meeting talking points live in `Meeting Prep/MMDD-MMDD.md`.

### Mon [Date]
- **[Time] [Meeting]** · **[Time] [Meeting]** · **[Time] [Meeting]** —
[… Tue, Wed, Thu, Fri — use the middle-dot `·` to group same-day meetings; trailing em-dash for inline notes]

---

# Done this week
<!-- Move closed action items here. Strike the bullet (~~…~~ ✓) and keep its wiki-link to its Workstreams/context block intact. -->
```

### Rules for File 1
- **Triage, don't enumerate.** Sort into 🔴 High / 🟡 Medium / 🟢 Lower-ongoing. High = needs my hand this week; Lower-ongoing splits into **Mine**, **Tracking** (VP-AI/Enablement-Lead/VP-Data own), **Cross-teaming** (Analytics-Partner/CTO/Gov-Lead).
- **Every item is a checkbox** (`- [ ]`), tagged `(mine)` / `(tracking: <name>)` / `(x-team: <names>)`, and ends with `→ [[Workstreams#<heading>|label]]` so durable context is one click away and **not** restated in the note.
- **Keep the note thin.** If a fact is durable (status/owner/open decision/sources), it belongs in `[[Workstreams]]`, not here. `Week-specific context` holds only this-week quotes and forcing functions.
- **`Week-specific context`** blocks: source line first, verbatim quotes (never paraphrase from memory), the week's decision/open question.
- **Cross-teaming** is a *view* (one bullet per partner of what I'm bringing), pointing at the same Workstreams/context — not a separate source of truth.
- **Carry an explicit disposition when the week is unusual** (e.g. an OOO week): tag each bullet `[close]` / `[handoff→name]` / `[defer→date]` inline. Don't change the structure for it.
- **Done**: strike + move closed items, link intact, so the audit trail survives.

---

## File 2 structure: `MMDD-MMDD-meetings.md`

```
# Meeting Talking Points — Week of [dates], YYYY
> Vault destination: Meeting Prep/MMDD-MMDD.md
> Spine + week context (one or two lines; mirror the weekly note's frame)

## Monday, [Date]
### [Time] — [Meeting Name]
[Context: who runs it; for recurring meetings, the last ~4-week thread]
**Talking points / what to raise:**
- Self-contained items with inline source quotes
- Decisions to force, blockers, follow-ups
[… continue through Friday]
```

Per meeting: talking points self-contained with inline context. For recurring meetings, pull notes from the last ~4 weeks of that meeting plus relevant Slack threads / emails from the same window. Flag conflicts and defunct/stale invites.

---

## Workstreams write-back (`Fact Base/Workstreams.md`)

This skill **owns** maintaining the evergreen `Workstreams.md` — it's the durable store the weekly note links into, so it must be refreshed in the same pass. Do this **last**, after the plan + meetings are drafted, using the evidence already gathered (the Gemini-notes Decisions / Next steps are the cleanest input for `Last moved` / `Open decision`).

- **ONE living file** — never per-week copies. Diff each block against this week's evidence; **touch only blocks whose `Status / Owner / Last moved / Open decision / Sources` actually changed**, and leave untouched blocks **byte-for-byte**.
- **Block schema:** `Status (Active/Watching/Blocked/Parked/Done) · Owner · Last moved · Open decision · Sources`.
- **Append** genuinely new workstreams; **flip** closed ones to `Status: Done`/`Parked` (never delete).
- **Bump the frontmatter `updated:` date.**
- **Capture the base version** of the file at the start of the run (you read it as an input to the plan anyway) — it's the `base_version` for a conflict-safe write.
- **Write path:**
  - If Obsidian write tools exist (e.g. `obsidian_upsert_section`, `obsidian_patch_frontmatter`): upsert each MOVED block with `dry_run:true` first to preview the diff, then commit, passing the `base_version` for conflict safety. On conflict, do **not** clobber — surface the diff.
  - If write tools are **not** available, the vault is read-only: **emit the full updated `Workstreams.md` to the outputs folder for paste** — no bash/filesystem writes to the vault.
- **Produce a one-line-per-block changelog** (moved / new / unchanged) for the delivery summary.

> Keep the weekly note thin: durable status lives here, not in the note. The note's bullets link in via `[[Workstreams#<heading>]]`; this write-back is what keeps those links truthful.

---

## Sources (in priority order)
1. **Obsidian vault** — most recent weekly note in `Weekly Notes/MMDD-MMDD.md` is the spine; the evergreen `Fact Base/Workstreams.md` is the durable store. Vault path: `${VAULT_ROOT}/`. Read via `obsidian-search` (`search_notes` → `get_note_content`). Weekly notes are Mon–Fri ranges, not Sun–Sat.
2. **`Fact Base/Internal Links.md`** (and `Important Links.md`) — canonical Slack channel IDs.
3. **Google Calendar** — the week ahead, queried **day-by-day** (a full-week pull exceeds the output limit).
4. **Slack** — channels from the links files. Pattern `in:#[channel] from:[name] [topic]` beats broad cross-channel search.
5. **Gmail** — recent threads relevant to the week's topics (release notes, approvals, exec sign-offs, meeting recaps).
6. **Google "Notes by Gemini" docs** (Drive / calendar-event attachments) — meeting Summary / Decisions / Next steps; treat as evidence to verify, not gospel.

**Atlassian gotcha:** pages in restricted space "D" return 404 even with valid IDs — corroborate via Slack instead of retrying. CMG cloud ID `${ATLASSIAN_CLOUD_ID}`, domain `createmusic.atlassian.net`.

---

## Workflow patterns

### Start by finding the latest weekly note — and match its format
Work backward by filename (`MMDD-MMDD.md`) to find the most recent note; note any gap. **Render to that note's actual structure**, not to this template if they've diverged — the layout evolves (it moved from Section A–D to the Priorities layout on ~0622). The current note documents its own conventions in its header; follow them.

### Travel / OOO weeks (detect early, plan around it)
Before drafting, scan the week-ahead calendar for **OOO blocks, flights, or "Nitin OOO" events** and check Gmail for an **"Upcoming OOO"** notice. (Learned the hard way: a half-day "flight Thu" can actually be a multi-week OOO — confirm the real return date.) If part or all of the week is out:
- **Compress to the actual working days** and say so in the frame (e.g. "live window Mon–Wed; fly Thu; back ~Jul 15").
- **Tag every action** `[close]` (finish before out), `[handoff→name]`, or `[defer→<return date>]`.
- **Name deputies** from the ownership map — default **VP-Data** (data/general, primary), **VP-AI** (A&R/product), **Enablement-Lead** (Labs/Kiwi) — and state reachability ("true fires only" unless told otherwise).
- **Fold the handover into the note** (the disposition tags + a one-line deputy per thread under Tracking / Cross-teaming). Only produce a separate delegation doc if asked.
- **Flag meetings in the OOO window** to move / async / skip, and defunct invites from departed people to cancel/re-point.
- **Add an OOO autoresponder pointer** with the deputy contacts.

### Carryover verification (before drafting)
Targeted Slack/email checks to confirm what closed vs forward:
- Items framed "done in flight" — confirm via Slack/email.
- Items with target dates landing this week — verify status.
- Discrepancies (conflicting dates, duplicate meeting slots, defunct invites from departed people) — **surface, don't silently resolve.**

### Source content over paraphrase
Pull actual content and quote verbatim with attribution. The user corrects paraphrase-from-memory — quoting directly saves a round-trip.

### Consolidation over duplication
Merge facets of one workstream into one bullet + one Workstreams link (e.g. "SIA scope" + "SIA sourcing" + "SIA placement" → one bullet). When consolidating, the Workstreams block (not the weekly note) carries the "why these are one thread" context.

### Embedded commentary over bare task lists
Each bullet says *why* it needs attention and what the real decision/risk is — not "Sprint demo Tue 8a" but "Sprint demo Tue 8a: frame the wins, decide on the Revenue-domain teaser."

### Iterative refinement is the norm
Expect several rounds: mark done → strike + move to Done; add workstreams (ask for source pointers); reframe/consolidate; swap in actual source quotes. When given a pointer ("see the X thread w/ Y"), fetch it before responding.

---

## Cross-teaming section (under 🟢 Lower / ongoing)
One bullet per cross-functional partner — **Analytics-Partner, CTO, Gov-Lead** by default (Sponsor is the current sponsor/audience; Former-Sponsor has departed CMG). Each bullet: what I'm bringing to that person this week, linking to the same `[[Workstreams#…]]` / week-specific context as elsewhere. Note the 1:1 slot (or "slot TBD"). Mark high-priority items `[ATTN]` or surface them up in 🔴 High instead.

---

## Common discrepancies to surface (don't silently resolve)
- Meeting-slot duplication (e.g. two live 1:1 instances — pick the live one).
- Defunct invites from departed stakeholders (e.g. recurring 1:1s with someone who has left — flag to cancel/re-point).
- Conflicting target dates (e.g. June 5 vs June 11 for a handover).
- Pod/owner ambiguity; scope reframings landed late in a thread.

---

## Tone & voice
- Self-contained items — bullets legible without recalled prior context.
- Source quotes in italics inside `>` blockquotes with attribution.
- No emojis except the priority markers (🔴🟡🟢), ✓ for done, ⭐ for a flagged meeting.
- No bullet-points-of-bullet-points beyond 2 levels.
- Action verbs first ("Confirm…", "Restart the thread on…", "Force the decision on…").

---

## Key colleagues reference
- **Sponsor** — interim audience / sponsor (Former-Sponsor departed CMG).
- **VP-Data** — data platform / ingestion / devops / security (engineering lead).
- **VP-AI** — A&R discovery + AI platform. **Enablement-Lead** — Kiwi / CreateOS Labs. **Gov-Lead** — governance / roadmap.
- **Analytics-Partner** — analytics / data product. **CTO** — security / software / cross-functional. **Eng-3** — data engineering. **Kale** — BCG.
- **Engineers**: Eng-5, Rishi, Revenue-Lead, Yang, Sergey, Jessie, Ryan, Modeler-Owner, Eng-Sr, Stakeholder-1, Former-Report, Eng-4, Ricardo, Vendor-Contact, Amardeep.

## Slack channel IDs (check `Fact Base/Internal Links.md` for the canonical list — IDs change)
- `${SLACK_CH_TEAM_DATA_ENGINEERING}` — team_data_engineering · `${SLACK_CH_ENG_PAIR}` — Eng-3/VP-Data
- `${SLACK_CH_POD_DISCOVERY}` — ar-tooling-dev-team · `${SLACK_CH_LUMINATE_REPLACEMENT_FEEDBACK}` — luminate-replacement-feedback · `${SLACK_DM_VP_AI}` — DM w/ VP-AI
- `${SLACK_CH_STATEMENT_INGESTION_A}`, `${SLACK_CH_STATEMENT_INGESTION_B}` — statement ingestion · `${SLACK_CH_DATA_AI_LEADS}` — data-ai · `${SLACK_CH_DT_LEADERSHIP}` — dt-leadership · `${SLACK_CH_DNT_LEADERSHIP}` — dnt-leadership
- `${SLACK_CH_PROJECT_DREAM}` — project-dream
