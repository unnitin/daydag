# Planning skills — moved here from Cowork

All planning work is now orchestrated from this repo. Three skills, copied **verbatim** on 2026-09-05:

| Skill | Role | Came from |
|---|---|---|
| `weekly-planning-and-progress` | orchestrator (order + cross-cutting guardrails only) | `~/Documents/Claude/Scheduled/weekly-planning-and-progress/` |
| `weekly-planning` | forward-looking: week-ahead plan + per-meeting talking points + Workstreams write-back | `~/Downloads/weekly-planning/` |
| `weekly-progress-reporting` | backward-looking: Priorities-doc Progress tab + highlights email | `~/Downloads/weekly-progress-reporting.zip` |

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

## Staleness found on arrival — fix before trusting the output

1. **The audience departed.** `weekly-progress-reporting` names **Former-Sponsor** 10 times (including in its own frontmatter description) and `weekly-planning` 8 times — as report audience, email recipient, and a `→ Former-Sponsor` cross-teaming bucket. CLAUDE.md §2: *"Sponsor is the executive sponsor/audience (Former-Sponsor departed)."* The orchestrator already patches around this at `:20` ("the current audience"), which means the underlying skill has been knowingly stale for a while.

2. **`weekly-planning`'s output format no longer matches the vault.** It specifies `Section A — Action Items` / `Section B — Context` / `Section C` (6 mentions of "Section A", **zero** of "Priorities"). The real notes moved to `# Priorities` with 🔴/🟡/🟢 triage around 0622. So the skill's own file spec contradicts CLAUDE.md house rule 4 (*match the latest note's actual format, not any template*) — and would regress the note if followed literally.

3. `dt-leadership-monitor` (not moved) is built around Former-Sponsor as the meeting organizer. Same departure.

Fixing 1 and 2 is the real work of this migration. The copy is the easy part.
