# Planning skills — moved here from Cowork

All planning work is now orchestrated from this repo. Three skills, copied **verbatim** on 2026-09-05:

| Skill | Role | Came from |
|---|---|---|
| `weekly-planning-and-progress` | orchestrator (order + cross-cutting guardrails only) | `~/Documents/Claude/Scheduled/weekly-planning-and-progress/` |
| `weekly-planning` | forward-looking: week-ahead plan + per-meeting talking points + Workstreams write-back | Cowork skills-plugin (live) |
| `weekly-progress-reporting` | backward-looking: Goals & Progress doc + highlights email | Cowork skills-plugin (live) |
| `weekly-feedback-scan` | private weekly VP feedback log for VP-AI & VP-Data → Drive | Cowork skills-plugin (live) |

## Which copy is live — resolved 2026-09-05

Two versions of each content skill existed. The first import took the wrong one.

| | `~/Downloads` copy | Cowork skills-plugin copy |
|---|---|---|
| date | 2026-05-31 | **2026-06-28** |
| `Priorities` / `🔴` / `(mine)` | 0 / 0 / 0 | 10 / 10 / 8 |
| `Section A` | 6 (as its output spec) | 4 (only as history: *"replaced an older Section A–D layout"*) |
| audience | Former-Sponsor ×8 | Sponsor, with an explicit *"Former-Sponsor has departed; never hard-code Former-Sponsor"* note |

The real weekly note (`0817-0821.md`) uses `# Priorities` with 🔴🟡🟢 and `*(mine)*`. **The Cowork copy is what populates Obsidian**; the Downloads copy is its pre-0622 ancestor. Both repo skills now come from:

```
~/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/
  246f9f7d-…/339338ef-…/skills/          ← 339338ef… matches ownerAccountId in cowork-enabled-cli-ops.json
```

The Downloads copies are stale and should not be re-imported.

`weekly-feedback-scan` was imported later, on 2026-09-05: it was **not present** when that directory was first listed (13 skills, no feedback one) and appeared after a sync. If a routine seems missing from this mirror, re-list before concluding it doesn't exist.

**It carries the strictest handling rule of anything here** — *"send nothing to anyone… VP-AI and VP-Data must never receive anything from this workflow"* — and its content is personnel data about named reports. See SPEC §10.1 before wiring it into any loop; in particular its carry-forward items must not be written into the vault's `CoS State.md`.

### Bug fixed on import

The live `weekly-planning/SKILL.md` was **456 lines containing its own body twice**, with a second YAML frontmatter block spliced mid-line into `- ${SLACK_CH_PROJECT_DREAM} — project-dream---`. Deduplicated to 227 lines here. Worth fixing at the Cowork source too — as shipped it doubles the skill's token cost on every run and embeds a stray frontmatter block mid-document. `weekly-progress-reporting` is unaffected.

**Copied, not moved.** Originals are still in place — the Cowork routine may still be scheduled server-side (the local folder looks like a mirror, last touched Jun 28), and two sibling routines are tagged `[DISABLED — superseded by …]` where the named supersessor has no local folder at all. Deleting the source before confirming what actually runs on Friday would be a guess. Retire the Cowork schedule deliberately once this copy is working.

They are unedited so the diff stays reviewable. Everything below is what needs changing, and nothing has been changed yet.

## Porting deltas — Cowork mechanics that don't exist here

| Reference | Where | Replacement in Claude Code |
|---|---|---|
| `/mnt/user-data/outputs/` | `weekly-planning:19` | vault path directly, or a repo `outputs/` dir |
| `present_files` | `weekly-planning:19`, orchestrator `:26` | terminal output / write to vault |
| `obsidian-search:search_notes`, `get_note_content` | `weekly-progress-reporting:27`, orchestrator `:13,:18` | filesystem read — CLAUDE.md §3 designates direct vault access as the Claude Code path |
| Slack / Gmail / Calendar / Notion as ambient connectors | throughout | present as MCP in this session; **Atlassian is not authorized** (#1) |

## Guardrail conflict to resolve deliberately

Orchestrator `:13–14` says use pinned read MCPs, never reconstruct vault content via shell/filesystem, and treat the vault as READ-ONLY absent dedicated write tools. `CLAUDE.md` §3 says the opposite for this environment: *"Obsidian vault (direct filesystem access in Claude Code — no MCP needed)."*

Both are right for their own runtime. The rule is environment-scoped, not a vault-wide policy — so the ported orchestrator needs the guardrail rewritten conditionally rather than deleted. This is the same question as #24.

## Staleness — mostly resolved by taking the live copy

Both "bugs" recorded here initially (the Former-Sponsor audience, and the Section A/B/C output format) were artifacts of the wrong copy. The live skills handle both correctly: the progress skill opens with *"Former-Sponsor has departed CMG; Sponsor is the interim sponsor… never hard-code 'Former-Sponsor'"*, and the planning skill documents the Section A–D → Priorities migration and instructs matching the latest note.

What remains:

1. **`(x-team)` doesn't exist in the vault.** Both live skills describe tags as `(mine)/(tracking)/(x-team)`, but no note uses `(x-team)` — cross-team work is a `**Cross-teaming (…)**` sub-header under 🟢. Minor, but it is the one place the live skill still describes something the vault doesn't do. See `reference/vault-recipes.md`.
2. **The orchestrator was not refreshed** — it still comes from `~/Documents/Claude/Scheduled/` (Jun 28) and may itself have a newer Cowork version that isn't mirrored locally.
3. `dt-leadership-monitor` (not moved) is built around Former-Sponsor as organizer. Same departure, not yet fixed.
