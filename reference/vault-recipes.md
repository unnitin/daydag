# Source recipes — vault half

> Deliverable for #7 (M1-4). Covers the Obsidian vault and the GitHub repos it names.
> Method: read the real files, not the spec's description of them. Where the two disagree, this file records reality — SPEC §6.2 says the note's own format wins.
> Verified 2026-09-05.

## Literal paths

```
VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Create Music Group"

$VAULT/Weekly Notes/MMDD-MMDD.md      30 files
$VAULT/Fact Base/Workstreams.md       16 blocks, YAML frontmatter
$VAULT/Fact Base/Internal Links.md    repos, Jira boards, dashboards
$VAULT/Meeting Prep/                  per-week talking points
$VAULT/Daily Notes/  Feedback/  Strategic Memo/
```

POSIX-writable. **There is a decoy:** `Documents/Weekly Notes/` at the vault *root* (outside `Create Music Group/`) holds one file, `claude-write-test.md` — a previous write that missed the prefix. Always resolve under `$VAULT`.

## Weekly note anatomy

Real example: `Weekly Notes/0817-0821.md`, 193 lines.

```
# Week of August 17–21, 2026
> Vault destination: · Spine note: [[0727-0731]] · Gap note: · This week's frame: ·
> Ownership map: · How this file works:          ← blockquote preamble, multi-paragraph
---
# Priorities
## 🔴 High — needs my hand this week
## 🟡 Medium
## 🟢 Lower / ongoing
     **Mine** / **Tracking (VP-AI / Enablement-Lead / VP-Data own; my visibility)** / **Cross-teaming (Analytics-Partner / CTO / Gov-Lead)**
---
# Week-specific context      ### per-topic, each with a "Source:" line and verbatim quotes
---
# Meetings (this week)       ### Mon 8/17 … ### Fri 8/21
---
# Done this week             ← present, empty, HTML comment only
```

### Item grammar

```
- [ ] **Title** *(mine w/ Stakeholder-1 / VP-AI / VP-Data)* — body text → [[Workstreams#Anchor|Alias]]
	- [x] nested sub-item, free text, no tag
- [x] Work w/ Enablement-Lead to land messages for Friday w/ Sponsor      ← no bold, no tag, no link
```

Tolerant parse — roughly a third of items omit one or more parts:

| Part | Pattern | Always present? |
|---|---|---|
| checkbox | `^- \[([ xX])\] ` | yes |
| title | `\*\*(.+?)\*\*` | no |
| owner tag | `\*\((.+?)\)\*` | no |
| body | ` — (.*)` | no |
| workstream link | `\[\[Workstreams#([^\|\]]+)(?:\|([^\]]+))?\]\]` | no |
| sub-items | leading tab + `- [ ]` | sometimes |

## Deltas from SPEC §1 / §3.3 — five corrections

1. **Tags are italicised, and the vocabulary is different.** Spec says `(mine)` / `(tracking: name)` / `(x-team)`. Reality: `*(mine)*`, `*(tracking: VP-Data / Declan)*`, and compound forms the spec doesn't anticipate — `*(mine w/ Stakeholder-1 / VP-AI / VP-Data)*`, `*(mine; Gov-Lead HM)*`. **`(x-team)` does not appear anywhere.** Cross-team work is expressed structurally instead, as a `**Cross-teaming (…)**` bold sub-header under 🟢.

2. **Closed items are ticked in place, not struck and moved.** Both the spec *and this note's own preamble* say "close an item → strike it (~~…~~ ✓) and move it to **Done**." In practice every closed item is `- [x]` where it sits, and `# Done this week` is empty but for its HTML comment. **Write-back (#16) must follow the practice, not the stated rule** — moving items to Done would be the agent imposing a convention its owner abandoned.

3. **Warning glyph is `⚠` (U+26A0), not `⚠️`.** Spec §5 sanctions `⚠️`. Emitting the emoji-presentation variant would make agent-written lines visibly differ from hand-written ones.

4. **Priority headers carry trailing prose** — `## 🔴 High — needs my hand this week`. Match on the emoji, not the full string.

5. **`# Meetings` contains arbitrary free-form prose.** In 0817-0821 three sets of interview notes (Rhamses, Gabriel, Ethan) are pasted mid-section as deep nested numbered lists, with `###` and `##` headings that break the day sequence. Any parser walking headings under Meetings must tolerate unrelated structure rather than assuming `### <Day> M/D`.

## Meetings section format

```
### Mon 8/17
- standups (AI Platform, Pod 10, DE) · **10:00 Nitin / VP-Data / Eng-3 Weekly** · **3:00 VP-AI ↔ Nitin Connect** —
```

Meetings are bold, `·`-separated, often several per line. A trailing `—` is an empty takeaway slot. `⭐` marks the ones that matter; `⚠` marks a conflict. A `### ⚠ Discrepancies to resolve (not silently fixed)` subsection already exists by hand — SPEC principle 5 matches established practice, so the agent should append to that section rather than invent a surface.

## Workstreams.md anatomy

```yaml
---
type: evergreen
updated: 2026-08-14
---
```

Then 16 `## Block name` sections, each five bold-key bullets:

```
- **Status:** Active            ← Active | Watching | Blocked | Parked | Done
- **Owner:** VP-Data
- **Last moved:** week of 8/3–8/10 — …
- **Open decision:** … ; … ; …   ← semicolon-separated, often several
- **Sources:** #team_devops (8/11, CDI-420); [[0720-0724]]; Sprint 7 notes
```

Parse: `^- \*\*(Status|Owner|Last moved|Open decision|Sources):\*\* (.*)$`. The `updated:` frontmatter field must be bumped on any write.

Sources cite three id types the agent should learn to emit: Slack channel + id (`#migration-pod (${SLACK_CH_MIGRATION_POD})`), Gmail message id (`19ede39ddc10eecb`), and wiki-links to weekly notes.

## Harvested from Internal Links.md

Five repos, all under `CreateMusicGroup`, all readable with the current token:

| Repo | Default branch | Last push |
|---|---|---|
| cmg-sdp-transform (data platform) | `main` | 2026-09-05 |
| createos-dsp-ingestion (statement ingestion) | **`develop`** | 2026-07-15 |
| createos-discovery-services (A&R Discovery) | `main` | 2026-09-04 |
| createos-coding-agents | `main` | 2026-08-27 |
| createos-lead-generation (stream forecasting) | `main` | 2026-09-04 |

**Do not hardcode `main`** — `createos-dsp-ingestion` defaults to `develop`, so a branch-health check assuming `main` reports nothing and looks green. Resolve via `defaultBranchRef` per repo (#11).

Jira: `createmusic.atlassian.net`, one board recorded — **CING** (statement ingestion). The data team's other project keys are not in the vault; harvest them in #1.

## The finding that affects M2 most

**The weekly note is three weeks stale.** Latest is `0817-0821.md`; today is 2026-09-05. Nothing exists for `0824-0828` or `0831-0904`, and `Workstreams.md` says `updated: 2026-08-14`.

SPEC §3.1 makes the weekly note's 🔴 items the second block of the morning brief, and §3.6 has the week-ahead read "the incoming weekly note's Priorities." Right now both would read an empty or absent file. Two consequences:

- **#9 needs a defined behaviour for a missing or stale note**, not as an edge case but as the state on day one. The §3.6 rule — a missing note *leads* the message rather than being a footnote — should apply to the morning brief too.
- The first real run is closer to a **re-establish-the-spine job** than a normal week. `0817-0821.md` did exactly this once before and documents how (`> Spine note:` + `**Gap note:**` in the preamble); reuse that pattern rather than inventing one.
