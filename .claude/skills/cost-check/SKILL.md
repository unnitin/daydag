---
name: cost-check
description: "Check the cloud bill and the cost-saving levers for Nitin: what GCP and Databricks are running at now against a base week, whether each lever's saving shows on the bill, what drove a spike, and what moved in Slack since the last check. Billing tables first (Databricks system.billing, the GCP billing export), Slack second (the lever register canvas and the cost threads). Read only: never edits the canvas, a ticket or a channel. Use for \"check cost\", \"what is the bill running at\", \"are the savings landing\", \"has the stable cost moved\", \"what drove yesterday's Databricks spend\", \"cost update\", \"is lever X on the bill\". Do NOT use for building the cost deck (consulting-slides) or for expenses (expenses)."
daydag:
  # Invoked by hand. Reads the two billing tables through `daydag.cost` and the
  # cost threads through Slack; writes nothing. Findings come back in chat, a
  # correction for the lever register is a draft to its owner, and a change to
  # the cost deck goes through consulting-slides.
  reads: [databricks, bigquery, slack, gmail, jira, github]
  sensitivity: shared
---

# cost-check - the bill first, then what people say about it

The billing tables are where a number comes from. Slack is where the levers,
their owners and their claims live. A lever is **on the bill** only when the
billing table for its own resource shows the drop over whole days after the
change. "Live", "merged", "deployed" and "first run looked good" are claims, not
savings (house rule 8: nothing auto-closes).

Answer first, every time: one line on what the bill runs at now against the base
week, and whether the estimate moved. Evidence after.

## 1. Read the bill - `daydag.cost`

Run from the repo root. Each command prints its basis on the first line; keep it
in the answer.

```sh
# the whole GCP bill by service, base week against the last whole days
python -m daydag.cost gcp --base 2026-10-29:2026-11-04 --recent 2026-11-07:2026-11-10

# inside Databricks: by product, pipeline, warehouse, job, cluster, sku or workspace
python -m daydag.cost databricks --by product --base <A:B> --recent <C:D>

# what drove one day: every line against its own previous seven days
python -m daydag.cost movers --by pipeline --day 2026-11-10

# one lever: its resource before and after the change date
python -m daydag.cost lever --by pipeline --id example_pipeline-prod --change 2026-11-06
```

| Table | What it is for | Day | Basis |
|---|---|---|---|
| Databricks `system.billing.usage` x `list_prices`, profile `${DATABRICKS_WORKSPACE_ID}`, run on warehouse `${DATABRICKS_BILLING_WAREHOUSE_ID}` | anything inside Databricks: a pipeline, a warehouse, a job, interactive notebooks | UTC | list price |
| GCP billing export `${GCP_BILLING_EXPORT_TABLE}`, queried in `${GCP_BILLING_QUERY_PROJECT}` with application-default credentials | the whole bill by service or project; Databricks is one line in it | `TIMEZONE` | export `cost`, before credits and any reseller discount; subscription SKUs out |

Rules the arithmetic does not enforce for you:

- **Never mix the two tables in one figure.** They disagree by day (the export
  lands Databricks charges on its own schedule) and by level (the Databricks
  tables have run several percent above the export's Databricks line). Use the
  export for the total and the Databricks tables for anything inside Databricks,
  and say which is which.
- **Whole days only.** Each command asks the table how far its usage has
  landed and counts a day only once it is six hours past its end; a window that
  reaches further is cut, and the render says so. A UTC day and a local day are
  different days: say which a figure is on.
- **Weekdays and weekends apart.** The render prints both and a 5+2 week shape.
  A window that is half weekend flatters the pace.
- **One-offs leave by name.** `--exclude DAY:USD:WHY`, each with its source (a
  Slack quote or a `movers` row). Show the pace with and without them. One
  that matched no day with data prints as NOT applied: fix the date.
- **A name can match two resources** (a dev and a prod copy). `lever` warns and
  sums them; pass the id from the `[id]` it prints.
- **A commit is not usage.** The export query leaves subscription SKUs out.
  Finance sees invoices, which carry a prepaid commit in the month it was billed;
  when Finance asks why a month jumped, split commit from usage before anything
  else.
- **Idle is not saved.** A dev warehouse nobody is querying reads near zero
  whatever its settings. Check the traffic before crediting a lever on it.
- **Overlapping levers cannot be read apart.** Two changes on the same warehouse
  or pipeline read as one before and after. Say so and read them together.
- **A failed read is one line with the real error** (rule 6). The Databricks
  connector reports a missing grant as an "outputSchema" error; `daydag.cost`
  goes through the statement API and prints the source's own message. Never fill
  in a number.

## 2. Read what people say - Slack

1. **The lever register:** canvas `${SLACK_CANVAS_COST_LEVERS}`, with
   `slack_read_canvas`, and its comment threads. It gives every lever's id,
   owner, sized saving, status, ticket and resource. **It belongs to its owner.**
   Never edit it, even to fix a number; a correction is a draft to the owner.
2. **The cost threads**, from the last check (default: two business days):
   `${SLACK_CH_SDP_DISCOVERY_POD}`, `${SLACK_CH_POD_DISCOVERY}`,
   `${SLACK_CH_TEAM_DATA_ENGINEERING}`, and the principal's DMs and group DMs with
   Finance and with each lever owner (from the people directory). Search with
   `in:` and `after:` filters and keywords such as `cost`, `DBU`, `/day`,
   `saving`, `billing`, `budget`, `warehouse`; then `from:<@ID>` for each lever
   owner. Follow every thread with replies (`slack_read_thread`): the number is
   usually in a reply.
3. **Gmail** for Finance and vendor mail on invoices, commits and credits.
4. **Jira and GitHub only to date a change**: the release or merge that gives a
   lever its `--change` date. A merged PR is not a saving.

Quote verbatim with the permalink (rule 1). Put each claim beside what the bill
shows for it.

## 3. Put them together

For each lever with a status of live or done:

1. Find its resource (pipeline, warehouse, job or cluster, by id or name) from
   the register row, its ticket, or `movers` on the day it changed.
2. Find the change date from the release, the merge or the owner's message.
3. Run `lever`. Then classify it:
   - **on the bill**: at least three whole days after, and the billed saving is
     within or above its sized range;
   - **partly**: on the bill, but below the sized range;
   - **not readable yet**: fewer than three days after, the resource idle, or an
     overlapping lever in the same window;
   - **not showing**: three days or more after, and no drop.
4. Show the claimed saving beside the billed one, with both sources.

Then run `movers` on the last whole day for each table. Anything new near the
top, in either direction, is a finding: a spike nobody has named, or a saving
nobody has claimed.

If the principal keeps a stable-cost estimate (the run rate once the levers in
flight land), say three things about it:

- whether the bill supports its "after the live fixes" step;
- what moved it since the last check;
- which new lever, sized in Slack, would move it next.

Show your arithmetic and mark it as yours, not the owner's. Propose any change
to the estimate; do not make it.

## 4. The answer

1. **One line:** the run rate now against the base week, on the export, and
   whether the estimate moved.
2. **By line:** the `gcp` table, with the one-offs named under it.
3. **Levers:** each lever's claimed saving, billed saving, verdict and evidence
   link.
4. **What moved since the last check:** verbatim quotes with permalinks.
5. **Discrepancies:** a Slack figure the bill does not match, the two tables
   disagreeing, a lever someone has called done that does not show (rule 5:
   surface, don't resolve).
6. **Caveats, short:** days read, weekend share, idle resources.

The answer goes to the principal, in chat or his DM. Anything for anyone else
(the register's owner, Finance, a channel) is a draft and waits for his go
(rule 2).

## Setup

`.env` keys (schema in `.env.example`): `DATABRICKS_WORKSPACE_ID` (the CLI
profile), `DATABRICKS_BILLING_WAREHOUSE_ID`, `GCP_BILLING_EXPORT_TABLE`,
`GCP_BILLING_QUERY_PROJECT`, `SLACK_CANVAS_COST_LEVERS`, `TIMEZONE`.

Access:

- **Databricks:** `USE SCHEMA` on `system.billing`, `system.lakeflow` and
  `system.compute`, plus `SELECT` on the tables read. A metastore or account
  admin grants these.
- **GCP:** BigQuery Data Viewer on the export dataset and Job User on the query
  project, through `gcloud auth application-default login`.

Check access with `--sql`, which prints the statement without running it, then
run it once by hand.
