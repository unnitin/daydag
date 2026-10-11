---
name: team-practices
description: "Track the Data Engineering \"How we work\" commitments for Nitin. Today: ticket coverage (monthly) and review distribution (weekly) - who is reviewing, PRs stuck without a human review, drafts past a week, and human-required changes merged on bot review alone. Use for \"ticket coverage\", \"who is reviewing\", \"how we work metrics\", \"are we writing tickets\", \"stale PRs in the transform repo\". Observes and drafts; never writes to Jira or GitHub. Do NOT use for Nitin's own day (that's daily-loops) or the Friday progress report (weekly-progress-reporting)."
daydag:
  writes:
    # Distinct ids, not a claim on daily-loops' `local:event-log` or its
    # `vault:DayDAG/Proposals/` folder - the registry matches EXACTLY, so a
    # sub-id is its own artifact with its own single writer.
    - vault:DayDAG/TeamPractices.md
    - vault:DayDAG/Proposals/team-practices/
    - local:event-log/team-practices
  reads: [github, jira]
  emits: [evidence.team-practices]
  schedule: "mon 08:00 reviews; first weekday of the month 08:00 coverage"
  sensitivity: shared
---

# team-practices - the team's own commitments, measured

The Data Engineering "How we work" doc asks for three tracked metrics. This skill
computes them and reports to Nitin. **`coverage` and `reviews` exist today**;
`owners` and `pack` are planned, and the plan is the file below.

**Read `docs/how-we-work-skills.md` before changing anything here.** It holds the
measured baselines, the skill map, the execution plan for each command and the
five decisions that are still open. Do not re-derive them; do not restate them
here.

The doc's own framing governs the tone of every render: *"not to police anyone -
so that gaps are visible before they bite"*. A number, the PRs behind it, and a
link on every line. No adjectives about people.

## coverage

```sh
gh pr list -R CreateMusicGroup/cmg-sdp-transform --state merged \
  --search "merged:>=2026-08-20" --limit 300 \
  --json number,title,body,headRefName,mergedAt,url
```

Hand the decoded list to `daydag.team_practices`:

```python
from daydag.team_practices import PullRequest, coverage

prs = [PullRequest.from_record(r, repo=REPO) for r in records]
report = coverage(prs, repo=REPO, window=(start, end))
print(report.render())
```

Then append `report.rows()` to the event log under `team-practices.pr` - that is
the history the trend line is drawn from, and it lives outside the vault.

### What the render may and may not carry

- **Both shares, both labelled.** TDE-keyed against the 90% target, and the
  looser any-reference count beside it. The doc's "roughly 42%" reproduces
  under neither definition; §2 of the plan has the numbers. Until Nitin pins
  one, report both and say which the target applies to.
- **No author, ever.** `PullRequest` does not parse one. The doc asks who is
  *reviewing* (that is `reviews`, by role token), never who is writing.
- **Every uncovered PR carries its link.** A bare list of numbers is a list
  nobody can act on.
- **Jira confirms, it never gates.** Pass `confirm_keys` to mark keys no ticket
  answers to. If Atlassian is down the coverage number is unchanged and one
  line says so.

### Where it lands

Nitin's DM, like every other push. A channel version for
#team\_data\_engineering is a **draft** - it goes nowhere until he says go, per
house rule 2, and it carries the two shares and the uncovered numbers only.

## reviews

```sh
gh pr list -R CreateMusicGroup/cmg-sdp-transform --state merged \
  --search "merged:>=<monday>" --limit 100 \
  --json number,title,body,headRefName,mergedAt,url,author,reviews,files,isDraft,createdAt,updatedAt
gh pr list -R CreateMusicGroup/cmg-sdp-transform --state open --limit 100 --json <the same>
```

```python
from daydag.team_practices import PullRequest, reviews

report = reviews(merged, open_prs, repo=REPO, week_of=monday, roles=ROLES, now=now)
```

`roles` maps `github login -> people-directory role token`. **Build it by hand
until #162 lands** - the directory has no login field yet, so a login missing
from the map is counted and deliberately not named. The report says how many
that was; it never guesses, and it never prints a login.

Four checks, all from §2 of the doc:

- **who reviewed**, by role token, bot in its own count, self-reviews excluded,
  with the top reviewer's share called out - the concentration the weekly
  summary exists to notice.
- **human-required merged on bot review alone.** Risk tier comes from
  `reference/team-practices-risk-paths.yml`, never from prose here. The doc's
  tie-break holds: unsure goes in the first bucket, so an unlisted path is
  human-required.
- **stuck ≥ 2 business days** without a human review. A bot approval does not
  reset that clock.
- **drafts past a week**, with their age.

## Not this skill

Writing a ticket, filling a PR description, or choosing a reviewer are the
engineer's own moment, and those skills belong in `cmg-sdp-transform/.claude/skills/`
next to their `pr-review`. Plan §5 has all six. Nothing here writes to Jira,
GitHub or Databricks: the Jira token is read-only by construction, a change to
the team's repo is a diff in `DayDAG/Proposals/team-practices/`, and jobs are
listed, never triggered.
