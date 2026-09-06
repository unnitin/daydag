# DayDAG — Spec v0.1

> Owner: Nitin Srivastava · Drafted: Sep 4, 2026
> Purpose: a persistent agent that plans Nitin's day, keeps itself current from meeting notes, tracks tasks and open loops, and pushes proactive updates through Slack — built on the sources and conventions he already uses (Obsidian weekly notes + Workstreams, Slack, Google Calendar, Gmail/Gemini notes, Notion, GitHub, Jira).

---

## 1. What the analysis showed (evidence base)

The spec is grounded in three observed behavior sets rather than a generic assistant template.

**How Nitin uses Slack.** His messages are dominated by five patterns: (1) status-verification questions to named people — "yea I thought there was a task to move everything to one compute engine on the board — did that not get done?", "please answer if we already merged all PRs and are ready to run end to end test for 10k samples"; (2) delegation with explicit owners and deadlines — "@DataEng-1 please take whatever rafael has implemented… then we should prioritize dev for it"; (3) pipeline-state coordination — "which bronze tables did you just refresh?", "Any chance you could trigger silver as well?"; (4) process probes — "team are you all colliding while testing on astro? is this worth self organizing and solving?"; and (5) scheduling/logistics — moving the Modeler-Owner conversation because CTO can't make Tuesday. The implication: the agent's core job is not a to-do list — it is tracking **open loops**: questions Nitin asked that are owed an answer, asks he assigned to named people, and running jobs he's watching.

**How Nitin tracks tasks in Obsidian.** The vault is already a working task system: `Weekly Notes/MMDD-MMDD.md` is the plan of record (Priorities triaged 🔴/🟡/🟢, every item a checkbox tagged `*(mine)*` / `*(tracking: name)*` and compound forms like `*(mine w/ Stakeholder-1 / VP-AI)*`; closed items ticked `- [x]` in place), `Fact Base/Workstreams.md` is the evergreen store (Status · Owner · Last moved · Open decision · Sources per block), `Meeting Prep/MMDD-MMDD.md` holds per-meeting talking points, and `Fact Base/Internal Links.md` holds canonical Slack channel IDs. The agent must **adopt this system, not replace it** — it reads and writes these files, using the existing `weekly-planning` skill's conventions (append/propose, never clobber; surface discrepancies, never silently resolve).

> **Verified against the real vault 2026-09-05** — see [reference/vault-recipes.md](reference/vault-recipes.md). Two conventions described above are *stated* in the notes but not *practised*: closed items are ticked in place rather than struck and moved to `# Done this week` (which sits empty), and `(x-team)` does not appear at all — cross-team work is a `**Cross-teaming (…)**` sub-header under 🟢 instead. Per guardrail 2 the agent follows the practice, not the stated rule. The weekly note is also currently ~3 weeks stale (latest `0817-0821.md`), which is the state the first run will actually meet.

**What Nitin asks Claude for.** Recent chats cluster into: cross-source pending-item sweeps ("find everything I owe across Slack/Gmail/Notion/Obsidian" → then execute item by item); drafting comms in his voice (Slack notes to leads, external emails, notes to Sponsor) with iterative refinement; retrieval forensics ("when did VP-AI first flag Vendor-Contact", "find Sponsor's net-label-share feedback", "Revenue-Lead's revenue timeline"); steering-prep synthesis (pod steering pre-reads, forecasting deep-dives before a Sponsor meeting); meeting-note-driven follow-ups (!K7 → follow-up email + intro todos); and weekly planning/reporting (already codified as two skills). The agent productizes the sweep, prep, chase, and ingest patterns he currently invokes ad hoc.

---

## 2. Mission and operating principles

**Mission:** keep Nitin's plan current, his commitments visible, and his open loops closed — with zero new tools for him to check. Slack DM in, Obsidian as system of record, everything else is plumbing.

**Principles**

1. **Obsidian is the system of record.** The agent maintains the weekly note and Workstreams; it never invents a parallel task store. Its own operational state (chase list, watch items) lives in one dedicated vault file.
2. **Open loops over tasks.** A question asked in Slack, an ask assigned to a named person, a running pipeline job — each is a loop with an owner, a source permalink, and an expected-response clock.
3. **Quote, don't paraphrase.** Every surfaced item carries a verbatim quote + permalink (Slack ts, email threadId, note path). This is an existing house rule from the weekly-planning skill: Nitin corrects paraphrase-from-memory.
4. **Drafts, not sends.** The agent messages only Nitin autonomously. Anything addressed to anyone else is a draft in his voice, awaiting his yes.
5. **Surface discrepancies, don't resolve them.** Conflicting dates, duplicate meeting slots, defunct invites, "done in flight" claims — flagged, never silently fixed.
6. **Thin pushes.** Briefs fit one Slack message. Depth is one tap away (thread reply, linked note), never front-loaded.

---

## 3. Core loops

### 3.1 Morning brief — weekdays 6:45am PT, Slack DM to ${SLACK_USER_PRINCIPAL}

Assembled from: today's calendar (queried day-by-day — full-week pulls exceed output limits), the current weekly note's 🔴 High items, overnight deltas (Slack mentions/threads he's in since 6pm prior day; Gmail from key senders and gemini-notes@google.com), the chase list, and watch items.

Format (one message, his register — lowercase, hyphens, tight):

```
morning - Thu Sep 4

meetings (4)
- 9:00 pod steering w/ Sponsor - prep in thread 🧵
- 11:00 Modeler-Owner/CTO sync - ⚠️ CTO flying Tue; you said you'd move this to Monday, not moved yet
- 1:30 artifacts access call
- 3:00 VP-Data 1:1

top of the note (0831-0904)
- 🔴 note to Sponsor on data platform access - VP-Data/VP-AI feedback in, ready to send?
- 🔴 R1.5 staging validation - gated on Vendor-Contact sign-off

owed to you (2)
- Modeler-Owner/Eng-Sr: "are we ready to run e2e test for 10k samples" - asked Tue 6pm, no answer [link]
- VP-Data: bronze tables refreshed? - answered ✓, silver trigger still open [link]

shipping
- R1.5 staging: DATA-812 → done, 4 PRs merged since tue incl. compute-engine consolidation [links]
- DATA-790 (silver refresh) blocked 3d - blocker: waiting on ivan
- ⚠️ main red on createos-api since 11pm - nightly failed
- 2 PRs waiting on your review, oldest 3d

watch
- 10k e2e run - you said "as long as it doesn't die just let it run" - check status?
```

Meeting prep for the day's flagged meetings goes as threaded replies under the brief, not inline.

### 3.2 Meeting prep pings — 30 min before qualifying meetings

Qualifying: 1:1s, steering/pod meetings, anything with Sponsor/CTO/external parties. Skipped: standups, focus blocks. Per meeting: 3–5 talking points built from the last ~4 weeks of that meeting's notes (Gemini notes, Notion meeting notes, Granola) plus related Slack threads from the same window — the exact recipe of the weekly-planning skill's File 2, run just-in-time and delivered as a short DM. Points carry the "why now": not "sprint demo" but "sprint demo — frame the wins, force the Revenue-domain teaser decision."

### 3.3 Meeting-notes ingestion — the self-update loop

**Trigger:** arrival of a Gemini-notes email (from:gemini-notes@google.com), a new Granola/Notion meeting note, or a sweep at 12pm and 5pm PT.

**Pipeline per note:**
1. Parse Summary / Decisions / Next steps (treat as evidence to verify, not gospel — existing house rule).
2. Classify each item: **Nitin's commitment** ("I'll intro Daniel to CTO"), **ask he assigned** (owner + what + when), **decision made** (updates a Workstreams block's `Last moved` / `Open decision`), **new workstream**, or **noise**.
3. Write back: commitments → checkbox under the weekly note's Priorities (correct tier, `*(mine)*` tag, ticked in place when closed); assigned asks → chase list with owner + clock; decisions → propose a Workstreams block diff (dry-run style: show the diff, apply on confirm or via append if write tools are limited — the vault connector currently supports create/append only, so block edits are proposed as paste-ready diffs).
4. DM a one-line-per-item digest: "logged from the 2pm label call: 2 commitments (yours), 1 ask → Enablement-Lead, 1 decision → Workstreams#Artifacts. anything wrong, tell me and i'll fix."

This loop is what keeps the agent's model of the world current without Nitin feeding it.

### 3.4 Open-loop chaser — daily 12:15pm PT

Scans the chase list: any ask or question with no responsive activity for **2 business days** (configurable per owner/urgency) surfaces in a DM with the original quote + permalink and a pre-drafted nudge in his voice ("hey - circling back on the compute-engine consolidation, is that on the board or should we make a ticket? lmk"). One tap to approve → posted as him via draft flow; ignore → re-raised in 2 more days, then parked with a flag in the weekly note.

Detection sources: thread replies on the original message, mentions of the owner + topic keywords, and engineering-pulse evidence (§3.7) when the ask references a ticket key, PR, or repo — Jira status/assignee changes for the data team, GitHub PR/board activity elsewhere. A nudge for an ask that already has a ticket carries the key and current status rather than asking "is that on the board?" again.

### 3.5 EOD wrap — 4:30pm PT

Three lines: what closed today (struck items ready to move to Done), what moved (Workstreams deltas logged), tomorrow's first meeting + any prep gap ("9am w/ Sponsor, no notes from last week's session found - want me to build prep from the steering doc instead?"). Friday's wrap does **not** offer to run weekly-planning — that routine already ran at 1pm (§10), so asking at 4:30pm would be stale by three and a half hours. Instead it reports the outcome: the Workstreams changelog, which of the two planning files landed, and anything still waiting on a paste or a Gmail-draft approval.

### 3.6 Sunday week-ahead — Sundays 5:30pm PT, Slack DM to ${SLACK_USER_PRINCIPAL}

The only weekend push; Saturday and the rest of Sunday stay silent. Two jobs: pre-run Monday so 6:45am Monday holds no surprises, and show the shape of the week while there's still time to move something before it starts.

Assembled from: the next seven days of calendar (queried day-by-day, same as 3.1), the plan `weekly-planning` produced on Friday — **read, not re-derived** (§10); the week-ahead's job is the delta since Friday, not a second synthesis of the same week — carryover items still open in the closing week's note, chase-list loops whose clock expires Mon–Wed, live watch items, and OOO/travel detection for Nitin *and* the people he's waiting on — the "CTO flying Tue" class of problem is far cheaper to catch Sunday than Tuesday morning.

Format (one message, same register, wider than the morning brief since it spans five days):

```
week ahead - Mon Sep 7 → Fri Sep 11

monday
- 9:00 pod steering w/ Sponsor - prep drafted in thread 🧵
- 1:30 artifacts access call
- afternoon open - only real writing block this week

the week
- tue: ivan/ruwen sync - ⚠️ still on tue, ruwen flying. move it tonight?
- wed: back-to-backs 10-1, clear after
- thu: seth 1:1, sprint demo 2pm
- fri: light - weekly-planning slot 4:30

carrying in (3)
- 🔴 note to Sponsor on data platform access - seth/jon feedback in, unsent since wed
- 🔴 R1.5 staging validation - still gated on gui sign-off
- 🟡 compute-engine consolidation - asked jasmeet thu, clock hits tue

watch
- 10k e2e run - still going as of sun 5pm, no failures

heads up
- next week's note doesn't exist yet - run weekly-planning now instead of friday?
```

Rules specific to this loop:

1. **No chases go out on a weekend.** Loops due Mon–Wed are *listed* so Monday isn't a surprise; the nudge drafts themselves wait for the Monday 12:15pm chaser (§3.4). Nobody gets pinged in his voice on a Sunday.
2. **Monday prep is pre-built, not pre-sent.** Talking points for Monday's qualifying meetings go as threaded replies here, so the 30-min pings (§3.2) re-send something he's already seen rather than a cold draft.
3. **Missing weekly note is the lead, not a footnote.** If Friday's wrap didn't run `weekly-planning`, the message opens with that and an offer to run it now — the week-ahead never fabricates Priorities from the prior week's note.
4. **Thin, still.** One message. If the week is genuinely quiet, it says so in three lines rather than padding to the full shape.
5. **Opt-out.** Until two-way Slack exists (open decision 5), skipping a given Sunday is a request in the Claude chat, not a reply to the DM.

### 3.7 Engineering pulse — repo/PR state, computed pre-brief

Not a separate push. Three of Nitin's five observed Slack patterns are status-verification against engineering state ("did we merge all PRs and are ready to run e2e", "did that not get done?", "is that on the board"). This loop's job is to have the answer *before* he asks, and to fold it into pushes that already exist.

**Watch list.** `DayDAG/State.md` gains a `repos` block (repo · why watched · owner · linked workstream) and a `jira` block (project key · board/sprint · saved JQL · linked workstream). Editable by hand like the rest of the file; the agent proposes additions when a meeting note or Slack thread names a repo or ticket not on the list.

**Ticket keys are the spine.** A Jira key (`ABC-123`) appears in Slack messages, PR titles/branches, and meeting-note next-steps. The pulse extracts keys from all three and joins on them, which is what lets "jasmeet said he'd do the compute-engine consolidation" resolve to a ticket, a PR, and a status without Nitin holding the mapping in his head. Where the data team's Jira ticket is the unit of work, it — not the PR — is what the `shipping` block reports.

**Computed each run (pre-brief 6:40am, pre-wrap 4:25pm, pre-week-ahead Sunday 5:25pm):**
1. **Merges since last run** in watched repos — squashed to workstream level, not commit level ("R1.5 staging: 4 PRs merged incl. the compute-engine consolidation"), never a commit log.
2. **Open PRs that are stuck** — no review activity ≥2 business days, or awaiting *his* review (the one class where he's the blocker).
3. **Default-branch CI health** — red main, failed nightly, newly-broken checks. Red main leads; everything else is one line.
4. **Board deltas — Jira for the data team**, GitHub Projects where a team still uses it. Tickets moved column, opened, closed, reassigned, or newly blocked in tracked projects; sprint burn where a sprint is active. Specifically flags the two cases he hits by hand today: *"he believes there's a ticket, there isn't one"* (asked in Slack, never made) and *"ticket closed, nobody said so."*
5. **Job/pipeline runs** for watch items via the Databricks MCP where the watch item names one — degrade per guardrail 6, never block a brief on auth flakiness.

**Where it surfaces:** a `shipping` block in the morning brief (§3.1) *only when non-empty*; movement lines in the EOD wrap (§3.5); a five-day roll-up in the Sunday week-ahead (§3.6); and as Friday input to `weekly-progress-reporting`, which currently reconstructs this from memory and Slack.

**Where it feeds the chaser (§3.4):** repo evidence is the strongest signal available for whether an assigned ask actually moved. If a chase-list loop names a repo/PR/ticket and that thing merged or closed, the loop is marked **evidence of movement** and surfaced for confirmation — "jasmeet's compute-engine ask: DATA-812 moved to done fri, PR #412 merged tue. close the loop?" It is **never** auto-closed. Per principle 5 the agent surfaces, it doesn't resolve; and merged ≠ what he asked for.

**Rules:**
1. **State changes, not activity.** No commit counts, no lines-changed, nothing that reads as a productivity metric on named engineers. The unit is "did the thing he's tracking move."
2. **Quote + permalink** like everything else — PR number, title, merge time, link.
3. **Silence is fine.** A quiet day produces no `shipping` block at all rather than "no updates."
4. **Read Jira, don't drive it.** The data team's board is theirs. Comments, transitions, and ticket creation are drafted for Nitin's go like any other outbound (guardrail 1) — the agent never moves someone else's ticket or closes a loop by writing to their board.

Substrate note: the existing `morning-sync` skill already does the open-PR/CI half of this across services and can be invoked directly in Phase 1 rather than rebuilt.

### 3.8 On-demand commands (DM keywords)

- **sweep** — full pending-items pass across Slack/Gmail/Notion/Obsidian (the pattern from the Sep 3 session), returned as a triaged list with permalinks.
- **prep <meeting|person>** — ad-hoc talking points.
- **find <question>** — retrieval forensics ("when did VP-AI first flag Vendor-Contact" pattern): person-scoped Slack search (`from:<@ID>`, `sort:timestamp asc`), Gmail, thread reads; answer with quote + link.
- **draft <what>** — comms in his voice (voice profile already stored in preferences).
- **status <workstream>** — read Workstreams block + freshest Slack/notes evidence, report deltas.
- **ship <repo|workstream>** — engineering-pulse view on demand: what merged, what's stuck, board state, CI (§3.7).
- **sprint [project]** — data-team sprint snapshot from Jira: committed vs done, what's blocked and on whom, what slipped from last sprint.
- **done <item>** / **add <item>** / **snooze <loop>** — plan maintenance without opening Obsidian.

---

## 4. Sources and access

| Source | Access | Used for | Notes |
|---|---|---|---|
| Google Calendar | read; propose changes | day plan, prep triggers, OOO detection | day-by-day **confirmed by measurement** — a 5-day pull returned 156,681 chars and exceeded the output limit. Watch for OOO/flight events (a half-day "flight Thu" can hide a multi-week OOO) |
| Slack | read all; **send: DM to Nitin only**; drafts elsewhere | overnight deltas, open-loop detection, chase, delivery channel | channel IDs from `Fact Base/Internal Links.md`; person-scoped search via `from:<@USER_ID>` (display names unreliable); Nitin = ${SLACK_USER_PRINCIPAL} |
| Gmail | read; drafts only | Gemini meeting notes (from:gemini-notes@google.com — subject IS structured: `Notes: “<title>” <date>`; parse it, see audit), exec threads, external follow-ups | |
| Obsidian vault | read; create/append; block edits as proposed diffs | weekly note, Workstreams, Meeting Prep, Internal Links | vault-relative paths must include `Create Music Group/` prefix; connector has no delete/modify |
| Notion | read | meeting-notes DB (reliable for notes >~1 wk old; recent ones land in Gmail first), AI tool inventory, steering docs | |
| Granola | read | meeting transcripts/notes where Gemini notes absent | |
| GitHub | read | engineering pulse (§3.7), code side: merges, stuck PRs, CI health; evidence for loops that name a PR | watched repos listed in `DayDAG/State.md`; private wiki access needs browser session |
| Atlassian / Jira | read; comments + transitions as proposals only | **plan of record for the data team** — sprint/board state, ticket status, assignee, epic rollup; the "is that on the board?" answer; evidence for chase-list loops that name a ticket | projects + JQL saved in `DayDAG/State.md`; ticket key (`ABC-123`) is the join key to Slack/PR/meeting-note mentions; connector needs OAuth before first run |
| Goals & Progress doc (Drive) | read; drafts via `weekly-progress-reporting` only | the five SMART objectives and their dated key results — the sponsor-facing, Lattice-mapped record of what was committed | doc `${GDOC_GOALS_PRIORITIES}`; snapshot + KR ledger in [reference/goals-and-progress.md](reference/goals-and-progress.md). **Never written directly** |
| Databricks | read, on-demand only | job/pipeline run status for watch items (§3.7 item 5) and status answers that need a data check | auth flakiness is known; degrade gracefully, never block a brief on it |
| CreateOS platform | **out of scope** | — | the agent is not wired into any CreateOS service, as a source or as a host (§7). `createos-*` repos are still watched read-only via GitHub like any other repo |

**Agent state file:** `DayDAG/State.md` — chase list (owner · ask · quote · permalink · asked-on · last-activity · status), watch items, snoozes, watched repos (repo · why · owner · workstream), watched Jira projects, and a run log (timestamp · loop · sources reached · sources skipped). Human-readable so Nitin can edit it directly; agent re-reads before every loop.

---

## 5. Voice and formatting

All output in Nitin's stored voice profile: lowercase openers, short direct sentences, numbered lists only when there's substance to structure, "def / w/ / iirc / lmk / nw", hyphens never em dashes, warm but unpolished, "keep me honest" / "keep us posted", asks assigned to named people with direct @mentions. Briefs use the emoji set already sanctioned in his notes (🔴🟡🟢, ✓, ⭐, ⚠) and nothing else — note the plain `⚠` U+26A0 the notes actually use, not the emoji-presentation `⚠️`; the difference is visible in the vault. Drafts to Sponsor or external parties shift to his slightly-more-structured-but-still-conversational register (observed in the data-platform note and !K7 email sessions).

---

## 6. Guardrails

1. Autonomous sends limited to Nitin's own DM. Everything else — Slack drafts (`slack_send_message_draft`), Gmail drafts, calendar proposals — requires his explicit go, per action, in the conversation.
2. Vault writes are additive (create/append) or proposed diffs; the agent never rewrites an existing note wholesale. Weekly-note format follows whatever the **latest note actually uses** — the layout evolves; the note's own header wins over any template.
3. Every claim traceable: quote + permalink or file path. If the agent can't source it, it says so instead of asserting.
4. Discrepancies (conflicting dates, duplicate invites, defunct 1:1s with departed people, "done" claims unverified) are surfaced as flags, never auto-resolved.
5. Sensitive threads (personnel, comp, M&A) never appear in channel drafts — DM-to-Nitin only, minimal quoting.
6. Failure honesty: if a connector is down (Databricks auth, Obsidian index timeout — both observed), the brief ships anyway with a one-line "couldn't check X" rather than stalling or guessing.

---

## 7. Implementation phases

**Phase 1 — skill, manually triggered (1–2 days).** Package loops 3.1–3.8 as a `DayDAG` skill alongside `weekly-planning` / `weekly-progress-reporting`. Nitin triggers "run my morning" / "run ingest" / "sweep" from Claude; the skill encodes the source recipes, formats, chase-list schema, and write-back rules. Proves the formats and the ingestion classifier with zero infrastructure. Prereq: the Atlassian connector needs an OAuth pass before the pulse can read Jira; until then §3.7 runs code-side only and says so per guardrail 6.

**Phase 2 — scheduled (week 1–2).** Move the five timed loops (morning brief, noon chaser, ingest sweeps, EOD wrap, Sunday week-ahead) onto scheduled runs — the engineering pulse (§3.7) rides along as a pre-step of three of them rather than a sixth schedule — Claude scheduled tasks/Cowork if available on the plan, else a cron on the home-lab box calling the API with the same MCP connectors. Slack DM becomes the primary surface; the Claude chat becomes the escalation/refinement surface. Since there is no platform observability behind this (see below), each run appends a one-line log to `DayDAG/State.md`.

**Phase 2 is the end state for now.** The agent runs on Nitin's own account and connectors and is not wired into any CreateOS system.

**Out of scope — headless agent on the CreateOS AI platform.** Previously drafted as Phase 3 (register "DayDAG" as a fourth agent after Chatbot / Digest Scan / Artist Evaluation, headless execution on the existing Redis consumer-group workers, Langfuse observability, a Slack app for two-way commands). Parked deliberately: this is a personal tool, and putting it on the company platform turns it into a product with users, an on-call surface, and a data-access review — none of which the value here justifies yet. Consequences accepted while it stays out of scope:

- **No two-way Slack.** The MCP connector can only send. "done" / "snooze" / "send it" stay Claude-chat commands, not replies to the brief (§3.8). This is the biggest ergonomic cost and it is a real one — every approval means leaving Slack.
- **No platform observability or evals.** Failure mode is Nitin noticing a brief didn't arrive. Phase 2 should therefore write a one-line run log (timestamp · loop · sources reached · sources skipped) to `DayDAG/State.md` so a silent failure is at least diagnosable after the fact.
- **No shared state or multi-user story.** Nothing here generalizes to anyone else on the team without a rebuild.

Re-entry gate if that changes: two-way Slack becoming the blocking pain, or someone else wanting the same agent.

---

## 8. Success measures (4-week check)

- **Dropped balls:** asks/commitments that surfaced late or via a human reminder instead of the agent — target ~0/week by week 3.
- **Prep coverage:** % of qualifying meetings with a prep ping Nitin actually used (thread reaction as proxy).
- **Ingestion precision:** % of logged items from meeting notes that survive without correction ("anything wrong, tell me" replies as the signal).
- **Chase efficacy:** median days-to-answer on open loops, before vs after.
- **Brief fitness:** edits Nitin requests to format/content trend to zero; anything he asks twice gets folded into the skill.

---

## 9. Open decisions

1. **Scheduling substrate for Phase 2** — Claude-native scheduled tasks vs home-lab cron + API. Cron gives full control and uses existing infra; native keeps everything in one account.
2. **Chase-list clock defaults** — 2 business days globally, or per-owner (engineers vs execs vs external)?
3. ~~**Weekend behavior**~~ — **decided (Sep 4)**: Sunday 5:30pm PT week-ahead push (§3.6), silent otherwise. Open sub-question: whether 5:30pm is early enough to actually move a Tuesday meeting, or whether it wants to be Sunday morning.
4. **Engineering-pulse scope** — watch only repos/Jira projects tied to a live workstream, or every repo and board his teams touch? Narrow keeps the `shipping` block honest; wide catches the "nobody told me that shipped" case. Related: does the pulse cover team-wide activity or only work he's tracking, given rule 1 above.
5. **Two-way Slack without a platform** — with CreateOS hosting out of scope there is no Slack app, so the connector can only send and every approval means leaving Slack for the Claude chat. Options if that grates: a minimal standalone Slack app on the home-lab box (Slack's side is not the hard part; hosting an always-on listener is), tolerate it, or reopen the platform question. Worth deciding only after ~2 weeks of Phase 2 shows how often he actually wants to reply in place.

---

## 10. Existing automation — what this agent must not duplicate

The spec above was drafted as if the field were empty. It isn't: five Cowork routines predate this agent, three of them live. Planning is now orchestrated from this repo (`.claude/skills/`), and the DayDAG **reads their outputs rather than re-deriving them**.

| Routine | Schedule | Status | Relationship to the DayDAG loops |
|---|---|---|---|
| `weekly-planning-and-progress` (orchestrates `weekly-planning` + `weekly-progress-reporting`) | Fri 1pm PT | live · **moved here** | Owns the week-ahead plan, per-meeting talking points, and the `Workstreams.md` write-back with a `base_version` conflict-safe upsert. §3.6 reads its output; §3.5 reports its outcome; §3.3's write-back reuses its conflict mechanism rather than inventing one |
| `dt-leadership-monitor` | weekdays 7am | **to retire** | The D&T leadership call is cancelled — it was Former-Sponsor's meeting and it left with him. The routine chases a weekly update for a forum that no longer meets, so there is nothing to absorb into §3.4; disable it rather than porting it. Retiring it also removes the 6:45/7:00 competing-morning-push problem. `#dt-leadership` (`${SLACK_CH_DT_LEADERSHIP}`) stays a read-only source until it goes quiet |
| `pod-update-finalize` (+ a `pod-update-draft` not present locally) | Fri 8am | live | The draft scans #data-ai, #team_data_engineering, #createos-pod-leads for shipped work, decisions and blockers — the same sweep as §3.7 and §3.3. The pulse should **feed** the pod update, not run a parallel scan |
| `dt-leadership-prep` | Mon | disabled | superseded by the monitor |
| `weekly-pod-update-draft` | Thu | disabled | superseded by `pod-update-draft` |
| `weekly-feedback-scan` | weekly | live · **imported** | Private VP feedback log for VP-AI and VP-Data. Reads the same 7-day Slack/Gmail window as §3.3 and §3.7, so the pulse should feed it evidence rather than run a parallel scan. Its carry-forward items are open loops — but sensitive ones (§10.1) |

**Position:** DayDAG orchestrates around these; it does not absorb them yet. Consolidating three working routines into one agent matches the "zero new tools" mission better, but it is a migration and should wait until the DayDAG formats have stabilized (§7 gate). What DayDAG owns exclusively: open loops, chase, meeting-note ingestion, the daily briefs, and the engineering pulse.

### 10.1 `weekly-feedback-scan` — imported, and the strictest guardrail in the system

Found in the Cowork skills directory (it synced down after the first search came up empty) and imported to `.claude/skills/weekly-feedback-scan/`. It produces Nitin's private weekly feedback log for his two direct reports — **VP-AI** and **VP-Data** — calibrated against a written expectations baseline, and writes one dated Google Doc into a private Drive folder.

Its own rule, verbatim: *"read from Slack/Gmail/Drive, write one Google Doc, send nothing to anyone. No Slack messages, no emails, no sharing the doc. VP-AI and VP-Data must never receive anything from this workflow."*

**Reference material it depends on** (all Drive, all read every run because they evolve): VP Expectations `${GDOC_VP_EXPECTATIONS}`, feedback methodology `${GDOC_FEEDBACK_METHODOLOGY}`, and the log folder `${GDRIVE_FEEDBACK_LOG_FOLDER}` whose most recent entry supplies carry-forward items.

**Where it touches the DayDAG loops — and where it must not.**

1. **It is the sharpest test of guardrail 5.** Everything this routine handles is personnel content about named reports. It must never reach a brief, a channel draft, a shared artifact, or any surface with an audience of more than one.

2. **Its carry-forward items are open loops that cannot live in the chase list.** The methodology carries unresolved items across reviews and flags anything open past two reviews for re-scope — structurally identical to §3.4. But `DayDAG/State.md` is plaintext in an iCloud-synced vault that reaches every device Nitin owns, and chase entries are written to be surfaced in a morning brief. **Personnel carry-forward needs a separate, private partition** — which is an argument for the local state database (outside the vault) holding the sensitive slice, rather than folding these into the markdown file.

3. **The evidence sweep is duplicated work.** It scans the same 7-day Slack/Gmail window over the same channels as §3.3 and §3.7 — releases, incidents, missed dates, stakeholder friction. The pulse should hand it evidence with permalinks already attached rather than repeat the search. Note it names `#ar-tooling-dev-team`, which CLAUDE.md records as renamed to `#pod-discovery`.

4. **It shares the house evidence rule** — *"No link, no claim — if you can't cite it, cut it"* — which is principle 3 stated in the same terms, and worth reusing verbatim where the DayDAG loops need the same discipline.

5. **It is goal-linked.** Objective 5's Dec 31 key result is *"quarterly feedback / career-growth cadence + personnel-upgrade KPI instituted,"* and the four dimensions it tags against — Domain Expertise, Practicality, Problem Solving, Communication — are the Engineering Career Progression Framework from the goals doc. So this routine is the operating instrument for a tracked KR, not an incidental habit.

**Porting note:** unattended runs deliver their summary via `PushNotification` inside `<routine_summary>` tags. That is a Cowork mechanism; under §7 Phase 2 the equivalent is a Slack DM to Nitin — the one autonomous send the agent is allowed.

**Position: do not absorb this one.** Unlike the planning routines, its blast radius on failure is a person's career record, and its "send nothing" rule is easier to keep intact in a separate routine than inside an agent whose whole job is pushing messages. DayDAG feeds it evidence and stays out of its output.

**Superseded note:** the vault's `Feedback/<Person>/MMDD.md` files (Former-Report, Enablement-Lead, VP-AI, Former-Sponsor, VP-Data — three files, newest `Former-Sponsor/0604.md`) predate this routine and cover a wider set of people. They are hand-written notes, not its output; the routine writes to Drive, not the vault.
