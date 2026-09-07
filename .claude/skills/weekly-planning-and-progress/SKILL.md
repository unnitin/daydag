---
name: weekly-planning-and-progress
description: "Every Friday 1pm PT: read durable context, run weekly-progress-reporting then weekly-planning, and update Workstreams for Nitin (Data & AI, CMG)."
daydag:
  # Orchestration only: it owns the order, the two skills own every artifact.
  # This is the row behind ARCHITECTURE's "Fri 13:00 weekly planning + progress".
  writes: []
  reads: [obsidian]
  consumes: []
  emits: []
  schedule: "fri 13:00"
  sensitivity: shared
---

You are running Nitin Srivastava's weekly Data & AI workflow for Create Music Group (CMG). Run the steps IN ORDER. Today is the run date; "week ahead" = next Mon–Fri, "past week" = the Mon–Fri just ending.

SINGLE SOURCE OF TRUTH — this task is ORCHESTRATION ONLY (order + cross-cutting guardrails). All content mechanics — formats, the Priorities doc ID, audience, key people, Slack/Atlassian IDs, the Workstreams write-back — live in the two skills. Follow the skills; do NOT restate their mechanics here. If this file and a skill conflict on mechanics, the skill wins.
  - `weekly-progress-reporting` — Progress-tab structure, email house-style, threading + audience, Priorities doc ID, key facts/people.
  - `weekly-planning` — current weekly-note format, meeting talking points, the Workstreams write-back, and the calendar/Slack/Atlassian source pins.

GUARDRAILS (every step):
  - Use the pinned read MCPs (obsidian-search for the vault; Google Drive/Calendar/Gmail; Slack search). Never reconstruct vault content from memory or via shell/filesystem.
  - Obsidian vault is READ-ONLY unless dedicated Obsidian write tools exist; otherwise emit updated files to outputs for Nitin to paste.
  - Slack read-only; Gmail draft only. NEVER send an email or post to Slack on Nitin's behalf without explicit approval.
  - Pull actual source content and quote verbatim; surface discrepancies rather than silently resolving them.

STEP 0 — Read durable context first (via obsidian-search): the most recent `Weekly Notes/MMDD-MMDD.md` (work backward by filename; note any gap; MATCH its current format — it evolves, see the `weekly-planning` skill), `Fact Base/Workstreams.md` (capture its version now as the STEP 3 `base_version`), and `Fact Base/Important Links.md` / `Internal Links.md` for canonical Slack channel IDs.

STEP 1 — Weekly progress reporting (backward-looking; run BEFORE planning so the forward plan reflects what closed). Invoke `weekly-progress-reporting` and follow it fully. Orchestration reminders only: pull the live Priorities doc fresh (don't report from memory); present the Progress-tab markdown for paste (don't write the doc unless confirmed); leave the highlights email as a Gmail DRAFT to the current audience — never send.

STEP 2 — Weekly planning (forward-looking; run AFTER progress, not re-listing closed items). Invoke `weekly-planning` and follow it fully — this includes its Workstreams write-back (STEP 3). Reuse STEP 0–1 evidence; additionally pull the week-ahead Calendar DAY-BY-DAY and recurring-meeting history (~last 4 weeks) from Obsidian `Meeting Prep/` + Google "Notes by Gemini" docs + Gmail recaps. Produce the two planning files at the vault destinations the skill specifies; save and present.

STEP 3 — Workstreams write-back (done LAST; mechanics owned by `weekly-planning`). Orchestration reminders only: pass the STEP 0 `base_version` for the conflict-safe write; emit the full file to outputs for paste if vault write tools are absent; surface the one-line-per-block changelog for DELIVERY.

DELIVERY — present all markdown via present_files and post a short thread summary: spine note used, Progress-tab status, the two planning files + their vault destinations, the Workstreams changelog + how applied (upserted vs emitted-for-paste), and confirmation the email is a draft (not sent) and to whom.
