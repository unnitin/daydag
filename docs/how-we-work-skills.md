# Skills for "Data Engineering - How we work" - design and execution plans

Written 2026-09-20 from the doc Nitin posted to #team_data_engineering
(`${SLACK_CH_TEAM_DATA_ENGINEERING}`, thread ts `1789784388.620269`) on
2026-09-18, the doc itself (`${GDOC_HOW_WE_WORK}`), its three replies, and a read of `CreateMusicGroup/cmg-sdp-transform` today: 300
merged PRs back to 2026-06-10, 20 open PRs, the repo's hooks, workflows,
template, CODEOWNERS, orchestration and docs. Every number below comes from that
pull and can be re-run with the commands in §2. The doc is the contract; this
file is how the agent side helps the team keep it.

Two things the doc says that shape everything here: *"Not to police anyone - so
that gaps are visible before they bite"*, and *"No human writes your tickets for
you. Use the tools."* So the skills split into two homes. **Observers** live in
this repo and report to Nitin. **Helpers** live in the team's repo and do the
tedious part at the moment an engineer is doing the work.

## 1. What the doc asks, and what enforces it today

| Doc section | Expectation | What exists in `cmg-sdp-transform` now | Gap |
|---|---|---|---|
| §1 tickets | TDE key in every branch, ticket before branch, agent branches not exempt | `.githooks/pre-commit` checks generated resources; `pre-push` runs static guards. Neither reads the branch name. PR template says "Link the issue" (GitHub wording). | Nothing observes or enforces the key. `claude/*` branches merge as-is (29 of the last 175). |
| §2 review | One PR, one ticket, **one named reviewer**; description shape; risk-tiered review; drafts ≤ 1 week | CODEOWNERS is one line granting the same three people (VP-Data, DataEng-1, Revenue-Lead) every path. The review bot (`pr-review` skill, depth 5) reviews content, never process fields. | Every PR requests "the team" (three people), which the doc forbids. No risk-tier check. No draft-age check. |
| §2 description | What changed · Why · How I verified · Risk / blast radius | `.github/pull_request_template.md` has Why · Impact · Restates history? · Verified · Risk and rollback | Richer than the doc but not the same words; "What changed" is absent. Bodies rarely fill it (§2 below). |
| §3 pipelines | Named human per production pipeline; daily run check; same-day ack; merged ≠ delivered | `orchestration/run_all.yml` ships `PAUSED`; hand-built `run_all` / `run_all_prod` still run. `pipeline_permissions.yml` grants **groups**, not people. Runbook: "the owner is whoever deployed it". `drift_daily` posts to Slack. | No owner field anywhere. No failure-ack loop. DataEng-2's alerts-channel proposal is unanswered. |
| §4 environments | Validate selectively | `scripts/run.py impact` / `lineage` / `dev --targets` exist | Nothing turns a diff into the selective command. |
| §6 docs | Decisions in the repo; column comments; explained twice → write once | `docs/decisions/` (21 ADRs, format in its README), `docs/plans/`, `docs/writing-docs.md`, `docs_check.py` advisory | ADR drafting is by hand. |
| §7 done | Five-point "verified is done" | Scattered across runbook, run-discipline, local-verification | No single check. |
| Tracking | Ticket coverage (monthly), review distribution (weekly), production ownership (per sprint) | **None computed anywhere.** | All three. |

## 2. Baselines measured today

Reproduce with `gh pr list -R CreateMusicGroup/cmg-sdp-transform --state merged --limit 300 --json number,title,body,headRefName,mergedAt,author,reviews` and the classifier in §4 D1.

**Ticket coverage.** A PR counts as TDE-keyed when `TDE-\d+` appears in branch, title or body; GitHub-keyed when the branch carries a leading issue number (`fix/519-…`, `docs/302-…`) or the text says `#NNN` / closes / fixes.

| Window | Merged | TDE-keyed | GitHub-keyed | Neither | `claude/*` branches |
|---|---|---|---|---|---|
| last 30 days | 175 | 14 (8%) | 117 | 44 | 29 |
| last 90 days | 289 | 49 (17%) | 127 | 113 | 42 |

**Discrepancy to settle before Monday:** the doc says *"roughly 42% in the
transform repo"*. Neither definition reproduces it: TDE-only gives 8-17%, any
reference gives 61-75%. The doc also says *"Jira is the system of record. Not
GitHub issues, not both"*, which argues for the TDE-only number - the honest
one, and the one that makes 90% a real target. The skill computes both and
labels them; the target needs one.

**Review distribution, last 30 days** (reviews on merged PRs, excluding the author's own):

Reviewers are people-directory role tokens, not GitHub logins - this repo is
public (`python -m daydag.people show vp-data` resolves one). `DataEng-2` and
`DataEng-3` have no directory entry yet; §7 has the gap.

| Reviewer | PRs reviewed |
|---|---|
| the review bot | 169 |
| VP-Data | 111 |
| Eng-Sr | 68 |
| Modeler-Owner | 37 |
| DataEng-2 | 10 |
| DataEng-1 | 9 |
| DataEng-3 | 3 |
| others (5 people) | ≤ 2 each |

VP-Data reviewed 63% of everything merged. Two engineers are auto-requested on
every PR by CODEOWNERS and reviewed 9 and 1. 167 of 175 had a human review; 8
merged on the bot alone or unreviewed. This is exactly the concentration the doc
says the weekly summary exists to notice.

**Description shape, last 30 days:** "why" appears in 72 of 175 bodies, "risk"
in 42, "what changed" in 12. (Counted against the doc's wording, not the
template's headings.)

**Open PRs today:** 20. Four drafts older than a week (#429 11d, #361 18d, #179
57d, #101 102d). #100 has been open 106 days. Three of VP-Data's PRs (#490,
#491, #493) have sat four days with bot-only or self review.

## 3. The skill map

| Doc section | Skill | Home | Serves |
|---|---|---|---|
| Tracking 1, §1 | **D1 `coverage`** | DayDAG (`team-practices`) | metric 1 |
| Tracking 2, §2 | **D2 `reviews`** | DayDAG | metric 2, stale PRs, drafts, risk-tier misses |
| Tracking 3, §3 | **D3 `owners`** | DayDAG | metric 3, unowned pipelines, un-acked failures |
| §5 ceremonies | **D4 `pack`** | DayDAG | sprint planning / retro input, composes D1-D3 |
| §1 | **T1 `ticket`** | `cmg-sdp-transform/.claude/skills/` | draft a TDE ticket from branch / PR / thread |
| §2 | **T2 `pr-describe`** | team repo | fill the template, classify risk, name one reviewer |
| §4 | **T3 `validate-selectively`** | team repo | diff → dry-run + selective refresh commands |
| §6 | **T4 `decision-record`** | team repo | ADR draft in the README format |
| §7 | **T5 `done-check`** | team repo | the five-point checklist with evidence |
| §3 | **T0 ownership data** (not a skill) | team repo | `owner:` per group, stamped into job tags + failure notifications |

One DayDAG skill, four commands, mirrors how `daily-loops` is one skill with
seven loops: one manifest, one writer set, one schedule entry. Five team skills
sit beside their existing `pr-review` skill and follow the same shape - read
`gh`/`git`, write a report to stdout, post only when told.

### Rules that cut across all of them

- **Aggregates are shareable; people-lists are not tabulated except where the
  doc asks.** The doc asks for *who is reviewing* - so D2 names reviewers. It does
  not ask for coverage per author - so D1 reports a share and a list of PR
  numbers, never an author table. `pulse.FORBIDDEN_IN_OUTPUT` (commits, lines
  changed, +/-, contributions) applies to every render.
- **Read the team's systems, never write them.** Jira token is read-only by
  construction. GitHub: `gh` read; a proposed change to the team repo is a diff
  in `DayDAG/Proposals/team-practices/` or a draft PR text, never a push.
  Databricks: jobs and runs are listed, never triggered.
- **Every line carries its link** - PR number + URL, run id + URL, ticket key.
  No link, no claim.
- **Degrade per source.** Jira unreachable → coverage still computes from
  branch/title/body and says "couldn't confirm ticket state in jira". Alerts
  channel unreadable → failures listed without ack status and it says so.
- **Team skills carry rules and pointers, not values** (their ADR 0003,
  `docs/writing-docs.md` rule 5). Path lists, thresholds and reviewer rotas live
  in a config file next to the skill, not in `SKILL.md`.

## 4. DayDAG skill `team-practices` - execution plans

Manifest, to be validated by `python -m daydag.registry` before anything else:

```yaml
---
name: team-practices
description: "Track the Data Engineering 'How we work' commitments for Nitin - ticket coverage (monthly), review distribution (weekly), production ownership and un-acked failures (per sprint) - and assemble the sprint-planning pack. Commands: coverage / reviews / owners / pack. Observes and drafts; never writes to Jira, GitHub or Databricks."
daydag:
  writes:
    - vault:DayDAG/TeamPractices.md
    - vault:DayDAG/Proposals/team-practices/
    - local:event-log/team-practices
  reads: [github, jira, databricks, slack, calendar]
  consumes: [evidence.pulse]
  emits: [evidence.team-practices]
  schedule: "mon 08:00 reviews; first weekday 08:00 coverage; owners + pack the working day before sprint planning"
  sensitivity: shared
---
```

Two registry notes. `local:event-log` is `daily-loops`' artifact; the exact-match
rule means `local:event-log/team-practices` (a table namespace inside the same
SQLite file) is a distinct id, the same way `Proposals/team-practices/` sits
inside `daily-loops`' `Proposals/`. That is the established sub-id pattern from
`.claude/skills/README.md`; confirm it is the intended reading rather than a
loophole before shipping. `evidence.team-practices` is emitted so
`weekly-progress-reporting` (the Friday highlights) and, via the bus, the private
`weekly-feedback-scan` can consume the review-load numbers without either skill
knowing about this one.

`TeamPractices.md` is the human-editable mirror of the three metrics and the
owners list, in the same spirit as `State.md`: Nitin's hand edits win, and the
skill appends or proposes, never rewrites.

### D1 `coverage` - metric 1

**Purpose.** The monthly ticket-coverage number, both definitions, with the
uncovered PRs listed so they can be back-filled - and a weekly trend line so the
month-end is not a surprise.

**Trigger.** First weekday of the month 08:00 PT (the doc says monthly); a
one-line trend inside D2's weekly run; on demand `coverage [repo] [--days N]`.

**Reads.** `gh pr list --state merged` for every repo in `Watchlist.md` tagged
`TDE` (today: `cmg-sdp-transform`, `createos-data-schemas`). Jira (when
reachable): `issuekey in (…)` for the keys found, to confirm they exist and are
not `Done` weeks before the PR - the *"he believes there's a ticket, there isn't
one"* case from SPEC §3.7.

**Steps.**
1. Pull merged PRs for the window. Classify each: `TDE` if `TDE-\d+` in branch,
   title or body; `GH` if branch starts `<type>/\d+-` or text has `#\d+` /
   closes / fixes; else `none`. Flag `claude/*` and `codex/*` branch prefixes
   separately (the doc's "agent branches are not exempt").
2. Bounded Jira check of the TDE keys found; mark keys that return nothing.
3. Write one row per PR to `team_practices_prs` in the event log (number, repo,
   merged_at, class, key, agent_branch, reviewers) - this is the history the
   trend draws from, and it lives outside the vault by the ARCHITECTURE rule.
4. Render.

**Output (DM, voice per CLAUDE.md §5):**

```
ticket coverage - cmg-sdp-transform, aug 20 → sep 19
🔴 TDE-keyed: 14 of 175 merged (8%) · target 90%
🟡 any work-item ref: 131 of 175 (75%)
29 merged from claude/* branches, 2 of them keyed
44 with no reference at all - list: #527 #526 #525 #520 … (full list in TeamPractices.md)
2 keys not found in jira: TDE-9xx (#nnn), TDE-9yy (#nnn) - couldn't confirm the rest, jira timed out
```

Plus a **channel draft** (never sent) with the two shares and the uncovered PR
numbers, no names, for #team_data_engineering.

**Guardrails.** No author column anywhere. The uncovered list is PR numbers with
links; the reader can click. Jira down is one line, not a stall.

**Acceptance.** `tests/test_team_practices.py`: classifier on the eleven branch
shapes seen today (`feat/TDE-591-…`, `feature/TDE-653_…`, `fix/519-…`,
`docs/302-…`, `claude/issue-510-…`, `chore/registry-cutover-end`, …); the render
contains no author login and none of `FORBIDDEN_IN_OUTPUT`; an empty Jira
response yields the "couldn't confirm" line and a non-empty report.

**Size.** S. The classifier ran today in 40 lines of Python; the rest is the
existing plan/fetch/render pattern from `daydag.run`.

### D2 `reviews` - metric 2, plus the §2 rules that are checkable

**Purpose.** The weekly "who is reviewing" summary the doc promises, and the
three §2 rules a machine can see: stale unreviewed PRs, drafts past a week, and
human-review-required PRs that merged on the bot alone.

**Trigger.** Monday 08:00 PT, before standup; on demand `reviews [--days 7]`.

**Reads.** `gh pr list` merged (7d) and open, with `reviews`, `reviewRequests`,
`isDraft`, `files` (for risk tier). Consumes `evidence.pulse` so the merge list
is not fetched twice.

**Steps.**
1. Reviewer table for the week: reviews on merged PRs, bot in its own row,
   author's self-review excluded. Concentration line: top reviewer's share.
2. Risk tier per merged PR from the paths it touched, using
   `reference/team-practices-risk-paths.yml` (mirrors the doc: schema or
   published contract, orchestration or production pipeline,
   royalty/revenue/financial data → **human required**; docs, tests, config
   bumps, generated refreshes → bot enough). Report human-required PRs whose
   only review was the review bot.
3. Open PRs: no human review activity ≥ 2 business days (SPEC §3.7 already
   defines this), and drafts open > 7 days.
4. CODEOWNERS check: PRs with more than one requested reviewer and no single
   assignee - the "one named reviewer, not the team" rule.
5. Append a `team_practices_reviews` row per reviewer per week; render.

**Output (DM):**

```
reviews - week of sep 14
vp-data 24 · eng-sr 9 · modeler-owner 6 · dataeng-2 3 · dataeng-1 1 · bot 31 of 33
🔴 vp-data reviewed 73% of what merged - the one-person-on-leave risk the doc names
🟡 2 human-required PRs merged on bot review only: #500 (orchestration) #486 (orchestration)
🟡 stuck ≥ 2 biz days: #493 #491 #490 (4d, bot or self review only) · #433 #417 (9d idle)
🔴 drafts past a week: #429 11d · #361 18d · #179 57d · #101 102d - finish or close
codeowners still requests 3 people on every PR - one named reviewer isn't real until that changes
```

Channel draft: the reviewer table and the stuck/draft lists. Reviewer names are
in the doc's own ask, so they stay.

**Guardrails.** Reviewer counts only - never author throughput. Risk tiers are a
config file, not prose. The "human-required merged on bot" line links the PR
and quotes the doc rule; it does not judge the reviewer.

**Acceptance.** Risk classifier fixture: `orchestration/run_all.yml` → human;
`docs/tables.md` → bot; a `nodes/**` file with a schema key change → human;
mixed → human ("if unsure, first bucket"). Stale rule uses business days in
PT. Render never contains a commit count.

**Size.** S-M. The only new logic is the path classifier.

### D3 `owners` - metric 3 and the failure-ack loop

**Purpose.** The live list of production pipelines and who to escalate to,
the unowned ones flagged as bugs, and failures in the last 24 hours that nobody
has acknowledged.

**Depends on T0** (the ownership data in the team repo). Until T0 lands, D3
runs in "inventory only" mode: lists production pipelines from the bundle YAML
and reports every one as unowned, which is the true state today.

**Trigger.** The working day before sprint planning, 08:00 PT (the doc: "reviewed
at sprint planning"; find the event by title via the calendar recipe rather than
hard-coding a weekday); failure-ack section daily inside the morning brief only
when non-empty; on demand `owners`.

**Reads.**
- `cmg-sdp-transform` via the pulse mirror: `orchestration/*.yml` groups,
  `resources/jobs/*.yml`, `resources/ops/*.yml`, and T0's ownership file.
- Databricks Jobs API, read-only: `manage_jobs list` for job tags `owner=` and
  `manage_job_runs list completed_only` for failed runs since the last run.
  **Prerequisite:** `~/.databrickscfg` has three profiles and no `DEFAULT`; the
  one hosting the data platform must be named in `.env` (a
  `DATABRICKS_DATA_PLATFORM_PROFILE` key) and switched to with
  `manage_workspace switch`. The known expired-OAuth trap (an "outputSchema /
  no structured output" error) means `SELECT 1` first, and one line on failure.
- Slack: the alerts channel DataEng-2 proposed (`${SLACK_CH_PIPELINE_ALERTS}`). **Today it is
  not readable from Nitin's Slack** (channel_not_found; not in a channel search
  either), so it is private and he is not a member. Until that changes the ack
  check is skipped and says so.

**Steps.**
1. Inventory: union of orchestration groups, standalone jobs and ops jobs =
   the production pipeline set. Join to owners (T0) and to Databricks job tags.
2. Flag: pipeline with no owner; owner who has left (not in the people
   directory); job in Databricks with no YAML (the hand-built `run_all` /
   `run_all_prod` the YAML comment says still run on their own clocks).
3. Failures: failed or timed-out runs since the last check, per production job.
   For each, look for a human reply in the alert's thread (when readable). No
   reply within the same working day → "un-acked".
4. Write the owners list into `TeamPractices.md` (additive; hand edits win) and
   a `team_practices_runs` row per failure; render.

**Output (DM, pre-sprint-planning):**

```
production ownership - for sprint planning tue
41 groups in run_all (23 bronze · 16 silver · 2 gold) + 3 other orchestrations + 6 standalone jobs
🔴 0 have a named owner - T0 not landed; the doc calls each one a bug
run_all (989282945893313) and run_all_prod (733795701981754) still run outside the bundle
failures last 24h: silver_luminate_daily 2 runs (run ids …) - couldn't check acks, not in ${SLACK_CH_PIPELINE_ALERTS}
```

**Guardrails.** Never triggers, cancels or repairs a run. The owners list is
proposed as a diff to the team repo (`Proposals/team-practices/ownership.diff`),
not pushed. An un-acked failure is reported once per run id.

**Acceptance.** Inventory test from a fixture copy of `orchestration/run_all.yml`
(the count changes with the repo; the test asserts the union logic, not 43).
Owner join with a missing file → every row unowned, no crash. Databricks
unreachable → inventory still renders with one degrade line.

**Size.** M. Two new sources (Databricks jobs, alerts channel) and a calendar
lookup.

### D4 `pack` - the sprint-planning and retro input

**Purpose.** One message before sprint planning with the three metrics, the
unowned pipelines, the stale drafts and two or three *specific* observations for
retro in the doc's own sense ("the standup ran 35 minutes on Tuesday", not
"communication could be better").

**Trigger.** Same as D3; also the Sunday week-ahead consumes it via
`evidence.team-practices` when sprint planning falls in the coming week.

**Steps.** Run D1 (trend), D2, D3; add retro candidates that are already facts
with links: red-main incidents from the pulse's CI read, PRs that sat > 5 days,
failures that took > 1 working day to ack. Render as one DM; draft the
channel version.

**Guardrails.** Composes, never re-fetches. Retro candidates are events with
links, never adjectives about people.

**Acceptance.** Pack with all three sub-reports degraded still renders (each
contributes its one degrade line). Size S once D1-D3 exist.

## 5. Team skills for `cmg-sdp-transform/.claude/skills/` - execution plans

These are PRs to the team's repo, opened by Nitin or VP-Data. Same conventions as
their `pr-review`: `SKILL.md` with `name` / `description`, a report to stdout,
post only when told, no values in prose (ADR 0003; `docs/writing-docs.md`).
Each lands with a one-line pointer in `AGENTS.md` under "Open the doc that
matches the task", which is how their docs stay one hop away.

### T0 - ownership data (a data change, not a skill)

Add `resources/ownership.yml`: one entry per orchestration group and standalone
job, `owner: <github login>` and `escalate_to: <github login>`. Extend
`scripts/generate_pipeline_resources.py` to stamp `tags: {owner: …}` and
`email_notifications.on_failure: [owner email]` (and the Slack webhook to the
alerts channel DataEng-2 proposed) onto every generated job. Add a static guard,
in the same style as the pre-push guards, that fails when a group has no entry -
"a pipeline with nobody against it is a bug" becomes a test. Owners are not
derivable, so the file is hand-maintained; everything downstream is generated
(writing-docs rule 6). Size M. This is the single change that makes §3 real
rather than documentary, and D3 reads it.

### T1 `ticket`

Draft a TDE ticket from the current branch, a PR number, or a pasted Slack
thread. Output: title, description in the team's Jira shape, suggested branch
rename `<type>/TDE-NNN-<slug>` (the org convention the doc adopts), and the
`git branch -m` command. Does not create the ticket - the read-only token and
the doc's "then edit" both say the human does. When the team has a Jira MCP with
write scope in their own sessions, the skill can offer the create call as the
last, explicit step. Size S.

### T2 `pr-describe`

From `git diff main...HEAD` and `python3 scripts/run.py impact origin/main`,
fill the PR template: What changed (added to the template - see §6), Why with
the TDE key from the branch (or a blocker line if there is none), Impact from
`impact`, Verified from the tests the session ran, Risk tier from
`.claude/skills/pr-describe/risk-paths.yml` (same rules as D2, one source of
truth would be better - keep the file in the team repo and have D2 read it via
the mirror). Names **one** reviewer: eligible reviewers from a rota file,
excluding the author, least-loaded by `gh` review count over the last two
weeks - the mechanical answer to the concentration in §2. Flags a `claude/*`
branch and a diff that will not be "reviewable in one sitting" by printing
`git diff --stat`. Size S.

### T3 `validate-selectively`

DataEng-2's reply, codified. From the diff: changed tables via `run.py impact`,
then print the dry-run command, the selective refresh
(`run.py dev --targets …` / `refresh --pipeline <group>`), and the V2-catalog
step first. When a changed node's `inputs:` or schema-bearing keys changed, print
the doc's caveat and the full-path command instead. Pure wrapper over existing
`run.py`; no new logic. Size S.

### T4 `decision-record`

From a thread or PR discussion, draft `docs/decisions/NNNN-<slug>.md` in the
README format (Context with the numbers, Decision, Rejected, Consequences), next
free number from a directory listing. Writes the file, never commits.
`docs_check.py` already catches duplicate numbers advisorily. Size S.

### T5 `done-check`

Given a PR number, walk the doc's five "done" items with evidence:

1. ticket state (Jira read when available; else the key exists and the PR says
   what shipped),
2. review level matched the risk tier (T2's classifier + `gh pr view` reviews),
3. it ran in the target environment after merge (Databricks runs for the jobs
   owning the touched tables, via the `databricks` CLI read-only, since
   `mergedAt`), and the output was looked at (a `Verified` section that names
   a query or run),
4. quality checks pass (`gold[_env].log.quality_metrics` and `drift_daily` rows
   since merge - observe-only tables the repo already documents),
5. definitions written (column comments touched, or an ADR in the diff).

Output: five lines with ✓ / ✗ and links. Never closes a ticket or the PR. Size M.

## 6. Build order - a stack, each step useful alone

0. **Decisions for Monday, no code** - the meeting Nitin already called:
   - Pin the coverage definition (recommend TDE-keyed) and re-state the baseline
     from §2 so the 42% is not repeated.
   - PR template: add `## What changed` above `## Why`; keep the rest. One-line PR.
   - CODEOWNERS: replace the three-person global with a rota or path owners so
     "one named reviewer" is possible. Otherwise every metric in D2 will keep
     saying the same thing.
   - Alerts channel: add Nitin (and the agent's Slack identity if separate) to
     `${SLACK_CH_PIPELINE_ALERTS}`; turn on Databricks failure notifications into it (T0 does
     this mechanically).
   - Name the Databricks profile that hosts the data platform in `.env`.
1. **D1 `coverage`** - the classifier already exists in this session; the first
   number can be in Monday's discussion. Ships the manifest and the event-log
   tables.
2. **D2 `reviews`** - same data, adds the risk-path config and the stale/draft
   checks. First Monday run the week after.
3. **T2 `pr-describe` + T3 `validate-selectively`** - two small PRs to the team
   repo; engineers feel these immediately.
4. **T1 `ticket`** - one PR; pairs with the "short session on writing tickets"
   the doc promises.
5. **T0 ownership data** (team PR), then **D3 `owners`** reads it.
6. **T5 `done-check`**, **T4 `decision-record`**.
7. **D4 `pack`** - wiring only, once D1-D3 exist; then `weekly-progress-reporting`
   consumes `evidence.team-practices` for the Friday highlights.

Steps 1-2 are DayDAG PRs: manifest, `src/daydag/team_practices.py` (classifiers
+ renders), `reference/team-practices-risk-paths.yml`, tests, and a
`.claude/skills/team-practices/SKILL.md` that points at this file rather than
restating it.

## 7. What could not be verified today

- **Jira.** The Atlassian MCP timed out this session. Coverage computes without
  it; ticket-state checks (D1 step 2, T5 item 1) are unverified until it is back.
- **Databricks.** Three profiles, no `DEFAULT`; which workspace hosts the data
  platform jobs was not confirmed, so the job inventory in D3 rests on the
  bundle YAML for now.
- **Alerts channel `${SLACK_CH_PIPELINE_ALERTS}`.** Not readable from Nitin's Slack; the
  failure-ack loop is designed but cannot be exercised.
- **The people directory has no GitHub login field.** D2's reviewer names and
  T2's rota need `github login → Slack id`; the fix is one hand-corrected fact
  per person in `daydag.people`, not a lookup.
- **The 42%.** See §2; the number the doc quotes does not reproduce under
  either definition, and the definition is a decision, not a measurement.
