# CLAUDE.md — DayDAG

Orientation for Claude Code sessions building Nitin's DayDAG agent. The full product spec lives in `SPEC.md` — read it before building. This file is the operational context: who's who, canonical IDs, source access patterns, and house rules. Everything here was verified from live sources on Sep 4, 2026; treat IDs as current but re-verify against `Fact Base/Internal Links.md` in the vault, which is the canonical registry.

---

## 1. What this is

A persistent agent for Nitin Srivastava (SVP Data & AI, Create Music Group) that: builds a morning brief, preps meetings, ingests meeting notes to update itself, tracks open loops (questions asked / asks assigned / jobs watched), pulls GitHub on a schedule for team-progress context, and delivers everything via Slack DM. Obsidian is the system of record; the agent adopts Nitin's existing weekly-note + Workstreams system rather than inventing a task store.

**Build order (Phase 1):** package as a skill; loops in priority order — (1) morning brief, (2) meeting-notes ingestion, (3) GitHub progress snapshot, (4) open-loop chaser, (5) meeting prep pings, (6) on-demand commands (sweep / prep / find / draft / status / progress / done / add / snooze).

## 2. People and org map

Ownership map (from the weekly notes, stable): **VP-AI (VP-AI)** → A&R Discovery + AI Platform · **Enablement-Lead** → Agentic Enablement (Kiwi / CreateOS Labs) · **VP-Data** → Data Platform, Ingestion, DevOps, Security. Everything else sits with Nitin directly. **Sponsor** is the executive sponsor/audience (Former-Sponsor departed). **CTO** is the newly named CTO (relocating to Vancouver as of early Sep). Cross-teaming partners: **Analytics-Partner** (analytics/data product), **CTO**, **Gov-Lead** (governance/roadmap).

Slack user IDs (search by ID, never display name):

| Person | Slack ID | Role context |
|---|---|---|
| Nitin (the principal) | `${SLACK_USER_PRINCIPAL}` | DM target for all agent output |
| VP-Data | `${SLACK_USER_VP_DATA}` | VP Data Engineering |
| VP-AI | `${SLACK_USER_VP_AI}` | A&R Discovery + AI Platform lead |
| CTO | `${SLACK_USER_CTO}` | CTO |
| Sponsor (Sponsor) | `${SLACK_USER_SPONSOR}` | Exec sponsor |
| Gov-Lead | `${SLACK_USER_GOV_LEAD}` | Governance/roadmap |
| Modeler-Owner | `${SLACK_USER_MODELER_OWNER}` | Deal Modeler owner |
| Eng-Sr | `${SLACK_USER_ENG_SR}` | Sr engineer (Toptal) |
| DataEng-1 | `${SLACK_USER_DATAENG_1}` | Data engineering |
| Eng-2 | `${SLACK_USER_ENG_2}` | Engineering |
| Revenue-Lead | `${SLACK_USER_REVENUE_LEAD}` | Revenue lead |

Other frequent names: ML-Lead (lead ML, first-party models), Stakeholder-1, Eng-4, Eng-5, Eng-6, Eng-7, Eng-8, Eng-3, Product-1 (product).

## 3. Canonical identifiers

**Slack workspace:** `create-music.slack.com`. Channels (re-verify in `Fact Base/Internal Links.md` — IDs drift as channels get renamed; `${SLACK_CH_POD_DISCOVERY}` was #ar-tooling-dev-team, now #pod-discovery):

| Channel | ID |
|---|---|
| #pod-discovery | `${SLACK_CH_POD_DISCOVERY}` |
| #sdp-discovery-pod | `${SLACK_CH_SDP_DISCOVERY_POD}` |
| #createos-dream-analytics | `${SLACK_CH_CREATEOS_DREAM_ANALYTICS}` |
| #createos-discovery-staging-feedback | `${SLACK_CH_CREATEOS_DISCOVERY_STAGING_FEEDBACK}` |
| #createos-ai-platform | `${SLACK_CH_CREATEOS_AI_PLATFORM}` |
| #team_data_engineering | `${SLACK_CH_TEAM_DATA_ENGINEERING}` |
| #data-ai-leads | `${SLACK_CH_DATA_AI_LEADS}` |
| #dt-leadership | `${SLACK_CH_DT_LEADERSHIP}` |
| #dnt-leadership | `${SLACK_CH_DNT_LEADERSHIP}` |
| #project-dream | `${SLACK_CH_PROJECT_DREAM}` |
| Group DM CTO/VP-Data/VP-AI/Nitin | `${SLACK_GDM_LEADS_4}` |
| Group DM VP-Data/VP-AI/Nitin | `${SLACK_GDM_LEADS_3}` |
| Group DM Sponsor/VP-Data/Nitin | `${SLACK_GDM_SPONSOR_3}` |
| DM Sponsor | `${SLACK_DM_SPONSOR}` |
| DM VP-AI | `${SLACK_DM_VP_AI}` |

**Obsidian vault (direct filesystem access in Claude Code — no MCP needed):**
`${VAULT_ROOT}/`
- `DayDAG/` — **everything the agent writes into the vault lives here**, nowhere else: `State.md` (chase list, watch items, snoozes, notes-gaps), `Decisions.md` (pending decisions, answered in place), `Watchlist.md` (repos · Jira projects · channels), `Proposals/` (diffs awaiting a yes), `Archive/` (pre-cutover snapshots). See ARCHITECTURE.md.
- `Weekly Notes/MMDD-MMDD.md` — plan of record, Mon–Fri ranges (NOT Sun–Sat). Latest as of Sep 3: `0817-0821.md` — there is a gap; do not assume a current-week note exists. Find the latest by sorting filenames.
- `Fact Base/Workstreams.md` — evergreen store, ONE living file. Block schema: `Status (Active/Watching/Blocked/Parked/Done) · Owner · Last moved · Open decision · Sources`. Written by `weekly-planning` today; **custody transfers to DayDAG and the file moves to `DayDAG/Workstreams.md`** at the cut — grep this path before assuming it.
- `Meeting Prep/MMDD-MMDD.md` — per-meeting talking points (plain date name, no suffix).
- `Fact Base/Internal Links.md` + `Important Links.md` — canonical Slack channel IDs, repo URLs.
- iCloud caveat: files may be evicted placeholders; if a read returns stub content, `brctl download <path>` or open in Finder first.

**Gmail:** Gemini meeting notes come from `gemini-notes@google.com` — search by **sender + body keywords + newer_than:Nd**, never by subject (Gemini uses the meeting title as body, not a consistent subject). VP-AI's AIM updates: `from:${EMAIL_VP_AI} subject:"AIM Program Update"`.

**GitHub:** org `CreateMusicGroup`. Active repos for the progress loop: `createos-discovery-services` (prod Deal Modeler), `createos-lead-generation` (first-party ML, ML-Lead), `createos-analytics` (Luminate exploration, locked), `createos-ai-platform` (agent platform; has a wiki). Pods use GitHub issue boards with wave parent issues. Use `gh` CLI (assume authenticated on Nitin's machine). Private wiki pages don't fetch anonymously.

**Notion:** meeting-notes DB is reliable for notes older than ~1 week; the last few days land in Gmail (Gemini) first — check Gmail before declaring a meeting note missing. Finding a person's docs: filter `created_by_user_ids` (VP-AI: `${NOTION_USER_JONATHAN}`) + date range beats keyword search.

**Google Calendar:** query **day-by-day** — full-week pulls exceed output limits. Watch for OOO/flight events; a half-day "flight Thu" can hide a multi-week OOO (confirm real return dates via Gmail "Upcoming OOO" notices).

**Databricks (only for on-demand `find`/`status`, never blocking a brief):** AI Platform workspace profile `${DATABRICKS_WORKSPACE_ID}`, host `https://${DATABRICKS_WORKSPACE_ID}.7.gcp.databricks.com`. Known trap: expired OAuth surfaces as "outputSchema / no structured output" errors, not auth errors — fix is `manage_workspace login`, then `SELECT 1` smoke test. Discovery data: `gold_dev.discovery`; `track_dna` bridges `cmg_artist_id` → `mr_id` for `weekly_worldwide`.

**Environment note:** in claude.ai/Cowork, Slack/Gmail/Calendar/Notion exist as connectors already. In Claude Code, the vault and `gh` are direct, but Slack/Gmail/GCal need MCP servers configured (`claude mcp add ...`) — check what's configured before assuming.

## 4. Source access patterns (hard-won, don't rediscover)

- **Slack search:** `from:<@USERID>` + `in:<#CHANNELID>` + `after:/before:` filters beat keyword search. Display-name `from:@miko` silently fails — resolve to ID first. Chronology needs explicit `sort:timestamp`. "First time X said Y": `from:<@ID>` + broad keyword + `sort_dir:asc`, then read the thread via `slack_read_thread` with the parent `message_ts`.
- **Slack reads:** top-level channel reads miss threaded replies — always follow `message_ts` into threads for anything with a reply count.
- **Gmail reads:** `get_message` with PLAIN_TEXT format for full bodies; search results alone are metadata.
- **Obsidian semantic search is unreliable for recency** — it surfaces old notes. For "latest weekly note," list the directory and sort filenames; use search only for topical lookup.
- **GitHub docs:** `raw/main/` paths return clean text; org repo search at `orgs/CreateMusicGroup/repositories?q=...`.

## 5. Voice (all agent output and drafts)

Lowercase openers, casual capitalization. Short direct sentences. Numbered lists only when there's real structure. Shorthand: `def`, `w/`, `iirc`, `lmk`, `nw`, `tq`. **Hyphens, never em dashes.** Warm but unpolished, light humor. Signature phrases: "keep me honest", "keep us posted". Asks go to named people with direct @mentions. Emoji only from the sanctioned set: 🔴🟡🟢 ✓ ⭐ ⚠️. For Sponsor/external drafts: slightly more structured but still conversational.

## 6. House rules (non-negotiable)

1. **Quote verbatim with permalinks** (Slack ts / email threadId / note path). Nitin corrects paraphrase-from-memory; sourcing saves a round-trip.
2. **Drafts, not sends.** Autonomous messages go only to Nitin's DM (`${SLACK_USER_PRINCIPAL}`). Anything to anyone else = draft awaiting explicit go, per message.
3. **Vault writes are additive or proposed diffs.** Never rewrite a note wholesale. Workstreams edits: diff blocks, touch only what changed, leave the rest byte-for-byte, bump frontmatter `updated:`. On conflict, surface the diff — never clobber.
4. **Match the latest weekly note's actual format**, not any template — the layout evolves (Section A–D → Priorities happened ~0622). The note's own header documents its conventions.
5. **Surface discrepancies, don't resolve them:** conflicting dates, duplicate meeting slots, defunct invites from departed people, unverified "done" claims.
6. **Degrade gracefully:** a down connector gets one line ("couldn't check X") — never block or guess.
7. Personnel/comp/M&A content: Nitin's DM only, minimal quoting.

## 7. Reuse, don't rebuild

Two existing skills encode the weekly workflows and must be invoked (not duplicated) by the Friday flow: **`weekly-planning`** (week-ahead plan + per-meeting talking points + Workstreams write-back — its file formats, triage scheme 🔴🟡🟢 with `(mine)/(tracking)/(x-team)` tags, and write-back rules are the house standard) and **`weekly-progress-reporting`** (Priorities-doc progress update + highlights email to Sponsor). Read both before writing any note-generation code.

## 8. Agent state

The `DayDAG/` vault folder — human-editable markdown the agent re-reads before every loop. `State.md` holds:
- **Chase list:** `owner · ask · verbatim quote · permalink · asked-on · last-activity · status (open/answered/parked/snoozed-until)`
- **Watch items:** running jobs/PR sets Nitin flagged, with source link and check condition
- **GitHub snapshot cache:** per-repo last-pull JSON (PRs, review ages, board moves) + diff vs prior pull; refreshed 6am/3pm PT weekdays

Watched repos/projects live in `DayDAG/Watchlist.md`; pending decisions in `DayDAG/Decisions.md`. History, metrics, the meeting ledger and anything sensitive go to the SQLite event log at `~/.local/state/` — **outside the vault**, since iCloud corrupts a WAL touched from two devices.

Open-loop clock default: 2 business days without responsive activity → surface with a pre-drafted nudge. GitHub activity on a referenced PR/ticket counts as responsive; merge auto-closes the loop with a note.