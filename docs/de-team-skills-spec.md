# Data Engineering skills - spec

Six skills that make the "How we work" doc cheap to follow, plus where a DE
skill belongs and what shape it has to be in to get merged.

Written from the "Data Engineering - How we work" doc and a read of
`cmg-sdp-transform` (300 merged PRs, the hooks, workflows, PR template,
CODEOWNERS, orchestration and docs). Nothing here is new policy - each skill
does one thing the doc already asks a person to do by hand.

The doc says two things that shape all of this:

> *"Not to police anyone - so that gaps are visible before they bite."*

> *"No human writes your tickets for you. Use the tools."*

So these six are **helpers**, not trackers. They run at the moment an engineer
is doing the work, they write a draft, and a person decides. The three tracked
metrics in the doc (coverage, review distribution, ownership) are measured
separately and are not in this spec.

---

## 1. Where a DE skill lives

Three homes exist today and the split between them is already consistent. Use
it rather than inventing a fourth.

| If the skill... | It goes in | Which today holds |
|---|---|---|
| reads or changes **one repo's** code, pipelines or docs | `<that repo>/.claude/skills/` | `cmg-sdp-transform`: `pr-review` · `createos-ai-platform`: `openspec-*`, `eval-driven-agent-iteration` |
| is **not tied to a codebase** and more than one team would run it | `createos-coding-agents/skills/` (the plugin) | `team-*`, `mute`, `pr-explain` |
| **reports on** the team rather than helping do the work | with whoever runs it, not in a shared repo | the coverage / review / ownership trackers |

The test is not who wrote the skill, it is **what breaks when the skill is
wrong.** A skill that runs `scripts/run.py impact` is wrong when
`cmg-sdp-transform` changes, so it versions with that repo and its PR gets
reviewed by people who know that code. A skill that drafts a Jira ticket is
wrong when the ticket convention changes, which is a team fact, not a repo
fact - that one belongs in the plugin where every repo picks it up at once.

All five of T1-T5 below are the first kind, so they go in
`cmg-sdp-transform/.claude/skills/` next to `pr-review`. T1 `ticket` is the
one worth revisiting: if a second team adopts the same ticket convention, move
it to the plugin and delete the copy.

> **Note:** there is no `createos-agents` repo in the org. The plugin repo is
> `createos-coding-agents`, and its skills sit at `skills/` at the root, not
> under `.claude/skills/`. Worth confirming which was meant before a PR is
> opened against a name that does not exist.

---

## 2. What every one of these must do

Same conventions as the existing `pr-review` skill, so review is about the
content rather than the shape.

1. **`SKILL.md` with `name` and `description`.** The description is what
   decides whether the skill fires at the right moment - write it as the
   trigger, not as a summary. Say what it is NOT for; that is what stops two
   skills fighting over the same request.
2. **Report to stdout. Post only when told.** No skill here sends a Slack
   message, transitions a ticket, or pushes a branch on its own. It writes the
   draft; the engineer sends it.
3. **Rules and pointers in `SKILL.md`, values in a config file next to it**
   (their ADR 0003, `docs/writing-docs.md` rule 5). Path lists, thresholds and
   rotas go in `<skill>/config.yml`, so changing a threshold is not a prose
   edit and two skills can read one list.
4. **Every claim carries its link** - PR number and URL, run id and URL, ticket
   key. A line with no link is a line nobody can check.
5. **Degrade per source.** Jira unreachable means the skill still produces what
   it can and says one line about what it could not read. It never stalls and
   never guesses past a dead source.
6. **One line in `AGENTS.md`** under "Open the doc that matches the task", which
   is how the repo's docs stay one hop away.

---

## 3. The six skills

### T0 - ownership data (a data change, not a skill)

**Why first.** §3 of the doc asks for a named human per production pipeline.
Today `pipeline_permissions.yml` grants **groups**, the runbook says "the owner
is whoever deployed it", and no file anywhere holds a name. Every ownership
question downstream is unanswerable until this exists.

**What.** `resources/ownership.yml`: one entry per orchestration group and per
standalone job, with `owner` and `escalate_to` as GitHub logins. Hand-
maintained, because owners are not derivable from anything.

**Then generate from it.** Extend `scripts/generate_pipeline_resources.py` to
stamp `tags: {owner: …}` and `email_notifications.on_failure` onto every
generated job, plus the Slack webhook for the alerts channel. Add a static
guard in the style of the existing pre-push guards that **fails when a group
has no entry** - "a pipeline with nobody against it is a bug" stops being a
sentence in a doc and becomes a test.

**Done when.** Every group in `orchestration/run_all.yml` resolves to a person,
and a new group cannot merge without one.

---

### T1 `ticket` - draft a TDE ticket

**Trigger.** "write a ticket for this", or a branch that has no key yet.

**In.** The current branch, a PR number, or a pasted thread.

**Out.** Title, description in the team's Jira shape, the suggested branch name
`<type>/TDE-NNN-<slug>`, and the `git branch -m` command to rename onto it.

**Does not.** Create the ticket. The doc says the human writes it; the skill
removes the blank page, not the judgement. When a Jira MCP with write scope is
available in the engineer's own session, the skill may offer the create call as
an explicit last step.

**Done when.** A branch with no key can get one in under a minute, which is the
only honest way to ask for 90% coverage.

---

### T2 `pr-describe` - fill the template, pick one reviewer

**Trigger.** Opening a PR.

**In.** `git diff main...HEAD` and `python3 scripts/run.py impact origin/main`.

**Out.** The PR body filled in: What changed · Why (with the TDE key from the
branch, or a blocker line if there is none) · Impact (from `impact`) · Verified
(the tests the session actually ran) · Risk and rollback.

**And one reviewer, named.** From a rota file, excluding the author, least
loaded by review count over the last two weeks. This is the mechanical answer
to review concentration: one person currently reviews the clear majority of
what merges, and "one named reviewer" cannot happen while CODEOWNERS requests
the same three people on every PR.

**Also flags.** An agent branch (`claude/*`, `codex/*`), and a diff that will
not be "reviewable in one sitting" - it prints `git diff --stat` and says so.

**Config.** `risk-paths.yml` - which paths need a human review. The doc's
tie-break is the file's rule: schema or published contract, orchestration or
production pipeline, and royalty / revenue / financial data need a human;
docs, tests and config bumps do not; **anything unsure goes in the first
bucket.** An unlisted path is not a safe path.

---

### T3 `validate-selectively` - diff to commands

**Trigger.** "what do I need to run for this change?"

**In.** The diff.

**Out.** The changed tables from `run.py impact`, then the dry-run command, the
selective refresh (`run.py dev --targets …` / `refresh --pipeline <group>`),
and the V2-catalog step first.

**One exception, printed loudly.** When a changed node's `inputs:` or a
schema-bearing key changed, selective validation is not enough - the skill
prints the full-path command and the reason instead.

**Nothing new.** A wrapper over commands that already exist, which is the
point: the commands are not the problem, remembering which one applies is.

---

### T4 `decision-record` - ADR draft

**Trigger.** A decision reached in a thread or a PR discussion.

**In.** That thread or discussion.

**Out.** `docs/decisions/NNNN-<slug>.md` in the existing README format -
Context (with the numbers), Decision, Rejected alternatives, Consequences -
numbered from the next free number in the directory.

**Writes the file, never commits.** `docs_check.py` already catches duplicate
numbers.

**Why.** There are 21 ADRs and the format is good; the cost is starting one.
§6 of the doc says a thing explained twice should be written once, and this is
what makes that a two-minute job.

---

### T5 `done-check` - the five-point check, with evidence

**Trigger.** "is this actually done?", or before closing a ticket.

**In.** A PR number.

**Out.** Five lines, each ✓ or ✗ with a link:

1. **Ticket state** - Jira when reachable, else the key exists and the PR says
   what shipped.
2. **Review matched the risk tier** - T2's classifier against the actual
   reviews.
3. **It ran in the target environment after merge** - job runs for the pipelines
   owning the touched tables since `mergedAt`, and the output was looked at (a
   Verified section naming a query or a run).
4. **Quality checks pass** - the quality-metrics and drift tables since merge.
5. **Definitions written** - column comments touched, or an ADR in the diff.

**Never closes the ticket or the PR.** "Verified is done" is a judgement; this
makes the evidence for it one command instead of five tabs.

---

## 4. Build order

Each step is useful before the next one exists.

1. **T2 `pr-describe`** and **T3 `validate-selectively`** - two small PRs.
   Engineers feel these on the next PR they open, which is what earns the rest
   a hearing.
2. **T1 `ticket`** - pairs with the short session on writing tickets the doc
   promises.
3. **T0 ownership data** - the biggest and the one everything about §3 waits on.
4. **T5 `done-check`**, then **T4 `decision-record`**.

T0 is third rather than first only because T2 and T3 are days of work and T0 is
a week of chasing down who owns what. If that chase is already happening, move
it up.

---

## 5. Open questions

These are decisions, not missing work. Each changes what a skill does.

1. **Ticket coverage definition.** The doc says coverage is "roughly 42%".
   Measured over the last 30 days it is 8% counting TDE keys and 75% counting
   any work-item reference, and over 90 days 17% and 61%. Neither reproduces
   42%. The doc also says *"Jira is the system of record. Not GitHub issues,
   not both"*, which argues for the TDE number - it is the honest one and the
   one that makes 90% a real target. **Pick one before the target is quoted
   again.**
2. **CODEOWNERS.** One line grants the same three people every path, so every
   PR requests "the team", which the doc forbids. "One named reviewer" is not
   possible until this becomes a rota or path owners. T2 can pick the name, but
   only if CODEOWNERS stops overriding it.
3. **PR template.** It has Why · Impact · Restates history? · Verified · Risk
   and rollback - richer than the doc, but missing **What changed**. One-line
   PR, and then T2 fills a template that matches the doc's own words.
4. **Alerts channel.** The failure-ack loop in §3 needs one channel that
   production failures land in, with people in it. The proposal in the thread
   is unanswered.
5. **Skill home for T1.** Repo-local now, plugin later if a second team adopts
   the ticket convention. Cheap to move, expensive to fork.
