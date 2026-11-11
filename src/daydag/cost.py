"""Cost: what the bill says, read from the billing tables, in the terms levers are argued in.

USING IT
    python -m daydag.cost gcp --base 2026-10-29:2026-11-04 --recent 2026-11-07:2026-11-10
    #   -> the GCP billing export by service, base window against recent window,
    #      as a monthly pace, with weekdays and weekends apart. `--by project`
    #      or `--by sku` splits it differently.
    python -m daydag.cost databricks --by pipeline --recent 2026-11-07:2026-11-10
    #   -> Databricks `system.billing.usage` at list price, split by product,
    #      sku, workspace, pipeline, warehouse, job or cluster (names joined in).
    python -m daydag.cost lever --by pipeline --id <id or name> --change 2026-11-10
    #   -> one lever's resource, the days before its change against the days
    #      after: $/day each side and the billed saving per month.
    python -m daydag.cost movers --source databricks --by pipeline --day 2026-11-10
    #   -> what drove one day: each line against its own previous seven days.
    #
    # Every command takes `--exclude DAY:USD:WHY` (repeatable) to take a named
    # one-off out of the pace, `--sql` to print the statement instead of
    # running it, and `--env PATH` (default `.env`).

    pace(rows, window, exclude=...)          # -> Pace, rows are DayCost
    render_pace(recent, base=..., basis=...) # -> text
    before_after(rows, change=..., days=...) # -> (Pace, Pace)
    movers(rows, day=..., n=...)             # -> [(line, usd, baseline)]

CONTRACTS
    1. Read only. Every statement is a SELECT assembled here from validated
       dates, ids and table names; nothing writes to Databricks, BigQuery,
       Slack or the vault.
    2. The basis is always named. Databricks rows are list price
       (`usage_quantity` x `list_prices.pricing.default`) on UTC days. Export
       rows are the export's `cost` (before credits and before any reseller
       discount) on days in TIMEZONE. The two never meet in one figure.
    3. A prepaid commit is not usage. The export carries one as a
       subscription SKU; the GCP query leaves every subscription SKU out, so
       a commit month does not read as a usage spike.
    4. A one-off leaves the pace only by name. `--exclude` takes a day, an
       amount and a reason, and the render lists each one under the total.
       One that matched no day with data is listed as NOT applied. Nothing is
       dropped silently.
    5. A failed read says why. The Databricks connector reports a missing
       grant as an "outputSchema" error; the statement API returns the real
       message, and `CostReadError` carries it. No figure is ever filled in.
    6. Whole days come from the table, not the clock. A day counts once the
       table holds usage LANDING_MARGIN past its end; a window that reaches
       further is cut, and the render says where. A lever's days without
       spend count as $0 only inside that line.

WHY IT EXISTS
    A lever marked "live" in a channel is a claim; the saving is what the
    billing table shows for that pipeline or warehouse after the change date.
    This module turns both tables into the same shape (a day, a line, an
    amount) so the run rate, a lever's before and after, and a spike's cause
    all come from one arithmetic, and the skill `cost-check` can set that
    against what Slack says.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from daydag.config import ConfigError, Identities, timezone_for

#: A month is thirty days, everywhere: the convention the cost estimates use.
DAYS_PER_MONTH = 30

#: The fewest days after a change before a lever's saving can be read.
MIN_DAYS_TO_READ = 3

#: Databricks splits: the usage column, and where the name comes from
#: (table, id column, name column), or None when the value is its own name.
DATABRICKS_BY: dict[str, tuple[str, tuple[str, str, str] | None]] = {
    "product": ("u.billing_origin_product", None),
    "sku": ("u.sku_name", None),
    "workspace": ("CAST(u.workspace_id AS STRING)", None),
    "pipeline": (
        "u.usage_metadata.dlt_pipeline_id",
        ("system.lakeflow.pipelines", "pipeline_id", "name"),
    ),
    "warehouse": (
        "u.usage_metadata.warehouse_id",
        ("system.compute.warehouses", "warehouse_id", "warehouse_name"),
    ),
    "job": ("u.usage_metadata.job_id", ("system.lakeflow.jobs", "job_id", "name")),
    "cluster": (
        "u.usage_metadata.cluster_id",
        ("system.compute.clusters", "cluster_id", "cluster_name"),
    ),
}

#: GCP export splits.
GCP_BY: dict[str, str] = {
    "service": "service.description",
    "project": "project.id",
    "sku": "CONCAT(service.description, ' / ', sku.description)",
}

_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
#: An id or a display name to match: no quote, backslash or semicolon can reach the SQL.
_MATCH = re.compile(r"^[^'\"\\;\n]{1,200}$")
_TABLE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){1,2}$")


class CostReadError(RuntimeError):
    """A billing table could not be read; the message is the source's own."""


# --------------------------------------------------------------------------- shapes


@dataclass(frozen=True)
class Window:
    """Inclusive day range, written `START:END` (or one day)."""

    start: date
    end: date

    @classmethod
    def parse(cls, text: str) -> Window:
        """`2026-10-29:2026-11-04`, or one day.

        >>> Window.parse("2026-10-29:2026-11-04").days()
        7
        """
        head, _, tail = text.partition(":")
        start = date.fromisoformat(head)
        end = date.fromisoformat(tail) if tail else start
        if end < start:
            raise ValueError(f"window {text!r} ends before it starts")
        return cls(start, end)

    @classmethod
    def ending(cls, end: date, days: int) -> Window:
        return cls(end - timedelta(days=days - 1), end)

    def days(self) -> int:
        return (self.end - self.start).days + 1

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day <= self.end

    def label(self) -> str:
        if self.start == self.end:
            return self.start.strftime("%-d %b")
        return f"{self.start.strftime('%-d %b')}-{self.end.strftime('%-d %b')}"


@dataclass(frozen=True)
class DayCost:
    day: date
    line: str
    usd: float


@dataclass(frozen=True)
class OneOff:
    """A named charge taken out of a pace: `DAY:USD:WHY`."""

    day: date
    usd: float
    why: str

    @classmethod
    def parse(cls, text: str) -> OneOff:
        """`DAY:USD:WHY`; the reason is required.

        >>> OneOff.parse("2026-11-10:670:one notebook").usd
        670.0
        """
        parts = text.split(":", 2)
        if len(parts) != 3 or not parts[2].strip():
            raise ValueError(f"one-off {text!r} must be DAY:USD:WHY, with a reason")
        return cls(date.fromisoformat(parts[0]), float(parts[1].replace(",", "")), parts[2].strip())


@dataclass(frozen=True)
class Pace:
    """A window's spend per day: total, by line, weekdays and weekends apart."""

    window: Window
    per_day: float
    by_line: dict[str, float]
    weekday: float | None
    weekend: float | None
    days_seen: tuple[date, ...]
    excluded: tuple[OneOff, ...] = field(default=())

    @property
    def monthly(self) -> float:
        return self.per_day * DAYS_PER_MONTH

    @property
    def week_shaped(self) -> float:
        """Five weekdays and two weekend days, so a window heavy on either does not flatter."""
        if self.weekday is None or self.weekend is None:
            return self.per_day
        return (5 * self.weekday + 2 * self.weekend) / 7

    @property
    def missing(self) -> int:
        return self.window.days() - len(self.days_seen)


# --------------------------------------------------------------------------- arithmetic


def pace(
    rows: Iterable[DayCost],
    window: Window,
    exclude: Sequence[OneOff] = (),
    *,
    zero_fill: bool = False,
) -> Pace:
    """Average spend per day over the days the window has data for, one-offs out.

    `zero_fill` counts every day in the window, a day with no rows as $0: right
    for one resource (a pipeline that did not run cost nothing that day), wrong
    for the whole bill (a day with no rows has not landed yet).

    >>> rows = [DayCost(date(2026, 11, 9), "a", 100), DayCost(date(2026, 11, 10), "a", 300)]
    >>> p = pace(rows, Window.parse("2026-11-09:2026-11-10"),
    ...          [OneOff(date(2026, 11, 10), 100, "rerun")])
    >>> p.per_day, p.by_line["a"]
    (150.0, 200.0)
    """
    daily: dict[date, float] = defaultdict(float)
    lines: dict[str, float] = defaultdict(float)
    if zero_fill:
        for i in range(window.days()):
            daily[window.start + timedelta(days=i)] += 0.0
    landed: set[date] = set()
    for r in rows:
        if r.day in window:
            daily[r.day] += r.usd
            lines[r.line] += r.usd
            landed.add(r.day)
    # Only a day with rows can lose a one-off: on a day with none it would
    # make a negative day. An unapplied one-off is the caller's to report.
    taken = tuple(o for o in exclude if o.day in landed)
    for o in taken:
        daily[o.day] -= o.usd
    seen = tuple(sorted(daily))
    n = len(seen) or 1
    week = [v for d, v in daily.items() if d.weekday() < 5]
    end = [v for d, v in daily.items() if d.weekday() >= 5]
    return Pace(
        window=window,
        per_day=sum(daily.values()) / n,
        by_line={k: v / n for k, v in lines.items()},
        weekday=sum(week) / len(week) if week else None,
        weekend=sum(end) / len(end) if end else None,
        days_seen=seen,
        excluded=taken,
    )


def before_after(
    rows: Sequence[DayCost],
    *,
    change: date,
    days: int = 7,
    through: date | None = None,
    exclude: Sequence[OneOff] = (),
) -> tuple[Pace, Pace]:
    """The `days` before a change against the days after it, the change day left out.

    The change day is half old and half new, so it belongs to neither side.
    """
    before = Window(change - timedelta(days=days), change - timedelta(days=1))
    first, last = change + timedelta(days=1), change + timedelta(days=days)
    if through is not None:
        last = min(last, through)
    if last < first:  # no whole day after the change has landed yet
        return pace(rows, before, exclude, zero_fill=True), pace((), Window(first, first))
    after = Window(first, last)
    return pace(rows, before, exclude, zero_fill=True), pace(rows, after, exclude, zero_fill=True)


def movers(
    rows: Sequence[DayCost], *, day: date, baseline_days: int = 7, n: int = 10
) -> list[tuple[str, float, float]]:
    """Each line's spend on `day` against its own daily average over the days before.

    Sorted by the size of the change, largest first. Returns (line, usd, baseline).
    """
    base = Window(day - timedelta(days=baseline_days), day - timedelta(days=1))
    on_day: dict[str, float] = defaultdict(float)
    before: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.day == day:
            on_day[r.line] += r.usd
        elif r.day in base:
            before[r.line] += r.usd
    out = [
        (line, on_day.get(line, 0.0), before.get(line, 0.0) / baseline_days)
        for line in set(on_day) | set(before)
    ]
    out.sort(key=lambda t: abs(t[1] - t[2]), reverse=True)
    return out[:n]


# --------------------------------------------------------------------------- render


def money(usd: float) -> str:
    """Thousands, one decimal.

    >>> money(403_812), money(-2_400), money(0)
    ('$403.8k', '-$2.4k', '$0.0k')
    """
    sign = "-" if usd < 0 else ""
    return f"{sign}${abs(usd) / 1000:,.1f}k"


def render_pace(
    recent: Pace,
    *,
    base: Pace | None = None,
    basis: str,
    top: int = 8,
    notes: Sequence[str] = (),
) -> str:
    """Monthly pace by line, base against recent, the one-offs named under the total."""
    order = base.by_line if base else recent.by_line
    lines = sorted(set(order) | set(recent.by_line), key=lambda k: -order.get(k, 0.0))
    shown, rest = lines[:top], lines[top:]

    def month(p: Pace | None, key: str | None = None) -> float | None:
        if p is None:
            return None
        return (p.by_line.get(key, 0.0) if key else p.per_day) * DAYS_PER_MONTH

    head = f"recent {recent.window.label()}"
    cols = [f"base {base.window.label()}", head, "change"] if base else [head]
    out = [basis, "", f"{'$ a month':<42}" + "".join(f"{c:>20}" for c in cols)]

    def row(name: str, b: float | None, r: float) -> str:
        if base is None:
            cells = [money(r)]
        elif b is None:  # e.g. a base window with no weekend in it
            cells = ["-", money(r), "-"]
        else:
            delta = money(r - b)
            cells = [money(b), money(r), delta if delta.startswith("-") else "+" + delta]
        return f"{name[:42]:<42}" + "".join(f"{c:>20}" for c in cells)

    for k in shown:
        out.append(row(k, month(base, k), month(recent, k) or 0.0))
    if rest:
        b = sum(base.by_line.get(k, 0.0) for k in rest) * DAYS_PER_MONTH if base else None
        r = sum(recent.by_line.get(k, 0.0) for k in rest) * DAYS_PER_MONTH
        out.append(row(f"{len(rest)} more lines", b, r))

    def off(p: Pace | None) -> float | None:
        if p is None:
            return None
        return -sum(o.usd for o in p.excluded) / (len(p.days_seen) or 1) * DAYS_PER_MONTH

    if recent.excluded or (base and base.excluded):
        out.append(row("one-offs out (listed below)", off(base), off(recent) or 0.0))
    out.append(row("TOTAL", month(base), recent.monthly))
    for name, attr in (
        ("  weekdays", "weekday"),
        ("  weekends", "weekend"),
        ("  week-shaped (5+2)", None),
    ):
        r = getattr(recent, attr) if attr else recent.week_shaped
        b = (getattr(base, attr) if attr else base.week_shaped) if base else None
        if r is None:
            continue
        out.append(row(name, b * DAYS_PER_MONTH if b is not None else None, r * DAYS_PER_MONTH))
    out.append("")
    for p, tag in ((base, "base"), (recent, "recent")):
        if p is None:
            continue
        note = f"{tag}: {len(p.days_seen)} of {p.window.days()} days with data"
        if p.missing:
            note += f" ({p.missing} missing - the pace averages the days present)"
        out.append(note)
        out.extend(f"  one-off out: {o.day} {money(o.usd)} - {o.why}" for o in p.excluded)
    out.extend(notes)
    out.append(f"Monthly = {DAYS_PER_MONTH} x the average day.")
    return "\n".join(out)


def render_lever(
    before: Pace, after: Pace, *, what: str, basis: str, notes: Sequence[str] = ()
) -> str:
    saving = (before.per_day - after.per_day) * DAYS_PER_MONTH

    def side(tag: str, p: Pace) -> str:
        n = len(p.days_seen)
        return f"  {tag} {p.window.label()}: ${p.per_day:,.0f} a day over {n} days"

    out = [basis, f"{what}", side("before", before)]
    if not after.days_seen:
        out.append("  after: no whole day since the change has landed yet - nothing to read")
        return "\n".join([*out, *notes])
    said = f"  billed saving: {money(saving)} a month"
    if saving < 0:
        said = f"  billed change: cost UP {money(-saving)} a month"
    out += [side("after ", after), said]
    if len(after.days_seen) < MIN_DAYS_TO_READ:
        out.append(
            f"  too early: {len(after.days_seen)} day(s) after the change; "
            f"read again once {MIN_DAYS_TO_READ} are in"
        )
    if after.per_day == 0 and before.per_day > 0:
        out.append(
            "  nothing billed after: retired, renamed, or idle - check before calling it a saving"
        )
    for p in (before, after):
        out.extend(f"  one-off out: {o.day} {money(o.usd)} - {o.why}" for o in p.excluded)
    return "\n".join([*out, *notes])


def render_movers(
    found: list[tuple[str, float, float]], *, day: date, basis: str, notes: Sequence[str] = ()
) -> str:
    out = [basis, f"{day} against each line's own previous seven days", *notes, ""]
    out.append(f"{'line':<64}{'that day':>12}{'usual day':>12}{'change':>12}")
    for line, usd, usual in found:
        out.append(f"{line[:64]:<64}{usd:>12,.0f}{usual:>12,.0f}{usd - usual:>+12,.0f}")
    return "\n".join(out)


# --------------------------------------------------------------------------- SQL


def _check_match(value: str) -> str:
    if not _MATCH.match(value):
        raise ValueError(
            f"{value!r} cannot be matched safely: no quotes, backslashes or semicolons"
        )
    return value


def _check_id(value: str) -> str:
    if not _ID.match(value):
        raise ValueError(f"id {value!r} is not a plain identifier")
    return value


def databricks_sql(
    start: date, end: date, *, by: str, only: str | None = None, with_id: bool = False
) -> str:
    """Daily list cost from `system.billing.usage`, split `by`, names joined where they exist.

    Ids are unique per workspace, not per account, so names join on both.
    `with_id` labels a line `name [id]`, so two resources sharing a name stay
    two lines - `lever` needs that to notice a name that matched more than one.
    """
    column, names = DATABRICKS_BY[by]
    none = f"'(no {by})'"
    join, label = "", f"COALESCE({column}, {none})"
    ctes = ""
    if names:
        table, id_col, name_col = names
        ctes = (
            # table and columns are constants from DATABRICKS_BY, never input
            f"WITH names AS (SELECT workspace_id, {id_col} AS id, {name_col} AS name "  # noqa: S608
            f"FROM {table} QUALIFY ROW_NUMBER() OVER "
            f"(PARTITION BY workspace_id, {id_col} ORDER BY change_time DESC) = 1)\n"
        )
        join = f"LEFT JOIN names n ON n.workspace_id = u.workspace_id AND n.id = {column}\n"
        label = f"COALESCE(n.name, {column}, {none})"
        if with_id:
            label = f"CONCAT(COALESCE(n.name, '?'), ' [', COALESCE({column}, '-'), ']')"
    where = f"u.usage_date BETWEEN DATE'{start}' AND DATE'{end}'"
    if only is not None:
        value = _check_match(only)
        match = f"{column} = '{value}'"
        where += f" AND ({match} OR n.name = '{value}')" if names else f" AND {match}"
    return (
        f"{ctes}SELECT CAST(u.usage_date AS STRING) AS day, {label} AS line,\n"
        "  ROUND(SUM(u.usage_quantity * p.pricing.default), 2) AS usd\n"
        "FROM system.billing.usage u\n"
        "JOIN system.billing.list_prices p ON u.sku_name = p.sku_name AND u.cloud = p.cloud\n"
        "  AND u.usage_start_time >= p.price_start_time\n"
        "  AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)\n"
        f"{join}WHERE {where}\n"
        "GROUP BY 1, 2"
    )


def gcp_sql(
    table: str, start: date, end: date, *, by: str, tz: str, only: str | None = None
) -> str:
    """Daily `cost` from the GCP billing export, split `by`, subscription SKUs left out."""
    if not _TABLE.match(table):
        raise ValueError("the export table must be project.dataset.table")
    ZoneInfo(tz)  # raises on a bad zone
    column = GCP_BY[by]
    day = f"DATE(usage_start_time, '{tz}')"
    where = (
        f"DATE(_PARTITIONTIME) >= DATE_SUB(DATE '{start}', INTERVAL 2 DAY)\n"
        f"  AND {day} BETWEEN DATE '{start}' AND DATE '{end}'\n"
        "  AND NOT LOWER(sku.description) LIKE '%subscription%'"
    )
    if only is not None:
        where += f"\n  AND {column} = '{_check_match(only)}'"
    return (
        f"SELECT CAST({day} AS STRING) AS day, {column} AS line, ROUND(SUM(cost), 2) AS usd\n"
        f"FROM `{table}`\nWHERE {where}\nGROUP BY 1, 2"
    )


#: Charges keep landing after a day ends. A day counts as whole only once the
#: table holds usage this long past the day's end - asked of the table, not
#: the clock, because a day the clock calls over can still be half in.
LANDING_MARGIN = timedelta(hours=6)


def databricks_fresh_sql() -> str:
    """The newest usage the Databricks table holds, as epoch seconds."""
    return (
        "SELECT CAST(unix_timestamp(MAX(usage_end_time)) AS STRING) FROM system.billing.usage\n"
        "WHERE usage_date >= date_sub(current_date(), 4) AND usage_end_time <= current_timestamp()"
    )


def gcp_fresh_sql(table: str) -> str:
    """The newest usage the export holds, commits and future-dated rows left out."""
    if not _TABLE.match(table):
        raise ValueError("the export table must be project.dataset.table")
    return (
        f"SELECT UNIX_SECONDS(MAX(usage_end_time)) FROM `{table}`\n"  # noqa: S608
        "WHERE DATE(_PARTITIONTIME) >= DATE_SUB(CURRENT_DATE(), INTERVAL 4 DAY)\n"
        "  AND usage_end_time <= CURRENT_TIMESTAMP()\n"
        "  AND NOT LOWER(sku.description) LIKE '%subscription%'"
    )


def complete_through(newest: datetime, tz: ZoneInfo) -> date:
    """The last day, in `tz`, that ended at least LANDING_MARGIN before `newest`.

    >>> from zoneinfo import ZoneInfo
    >>> complete_through(datetime(2026, 11, 11, 16, tzinfo=UTC), ZoneInfo("America/Los_Angeles"))
    datetime.date(2026, 11, 10)
    >>> complete_through(datetime(2026, 11, 11, 5, tzinfo=UTC), ZoneInfo("UTC"))
    datetime.date(2026, 11, 9)
    """
    return (newest - LANDING_MARGIN).astimezone(tz).date() - timedelta(days=1)


# --------------------------------------------------------------------------- runners


def to_rows(records: Iterable[Sequence[object]]) -> list[DayCost]:
    """Rows from either table as DayCost; a null split reads `(none)`.

    >>> to_rows([["2026-11-10", "x", "12.5"], ["2026-11-10", None, None]])[1].line
    '(none)'
    """
    return [
        DayCost(
            date.fromisoformat(str(d)), str(line) if line is not None else "(none)", float(usd or 0)
        )
        for d, line, usd in records
    ]


def databricks_rows(payload: dict) -> list[list]:
    """The rows of a finished statement, or the source's own reason it did not finish."""
    status = payload.get("status") or {}
    state = status.get("state")
    if state != "SUCCEEDED":
        message = (status.get("error") or {}).get("message") or f"statement {state or 'unknown'}"
        raise CostReadError(f"Databricks: {message}")
    return list((payload.get("result") or {}).get("data_array") or [])


Runner = Callable[[list[str]], dict]


#: How long one CLI call or HTTP request may take before the read gives up.
CALL_TIMEOUT_S = 180


def _cli(args: list[str]) -> dict:
    """One CLI call; every way it can fail becomes a CostReadError, never a hang."""
    try:
        done = subprocess.run(  # noqa: S603
            args, capture_output=True, text=True, check=False, timeout=CALL_TIMEOUT_S
        )
    except FileNotFoundError as err:
        raise CostReadError(f"the `{args[0]}` CLI is not on PATH") from err
    except subprocess.TimeoutExpired as err:
        raise CostReadError(f"`{args[0]}` gave no answer in {CALL_TIMEOUT_S}s") from err
    if done.returncode != 0:
        raise CostReadError(f"{args[0]}: {(done.stderr or done.stdout).strip()[-400:]}")
    try:
        return json.loads(done.stdout or "{}")
    except ValueError as err:
        raise CostReadError(f"`{args[0]}` answered with something that is not JSON") from err


def run_databricks(
    sql: str,
    *,
    profile: str,
    warehouse_id: str,
    cli: Runner = _cli,
    sleep: Callable[[float], None] = time.sleep,
    limit_s: float = 300,
) -> list[list]:
    """Run one statement through the CLI's statement API, waiting and paging as needed."""
    body = {
        "warehouse_id": warehouse_id,
        "statement": sql,
        "wait_timeout": "50s",
        "on_wait_timeout": "CONTINUE",
        "format": "JSON_ARRAY",
        "disposition": "INLINE",
    }
    base = ["databricks", "api"]
    payload = cli(
        [*base, "post", "/api/2.0/sql/statements", "-p", profile, "--json", json.dumps(body)]
    )
    waited = 0.0
    while (payload.get("status") or {}).get("state") in ("PENDING", "RUNNING"):
        sid = payload["statement_id"]
        if waited >= limit_s:
            # A statement left running keeps the warehouse up: a cost tool adding cost.
            with contextlib.suppress(CostReadError):
                cli([*base, "post", f"/api/2.0/sql/statements/{sid}/cancel", "-p", profile])
            raise CostReadError(f"Databricks: still running after {limit_s:.0f}s, cancelled")
        sleep(5)
        waited += 5
        payload = cli([*base, "get", f"/api/2.0/sql/statements/{sid}", "-p", profile])
    rows = databricks_rows(payload)
    link = (payload.get("result") or {}).get("next_chunk_internal_link")
    while link:
        chunk = cli([*base, "get", link, "-p", profile])
        rows += chunk.get("data_array") or []
        link = chunk.get("next_chunk_internal_link")
    return rows


def bigquery_rows(payload: dict) -> list[list]:
    return [[cell.get("v") for cell in row["f"]] for row in payload.get("rows") or []]


def _http(url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - https only, built here
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=CALL_TIMEOUT_S) as resp:  # noqa: S310
            return json.load(resp)
    except ValueError as err:
        raise CostReadError("BigQuery answered with something that is not JSON") from err
    except TimeoutError as err:
        raise CostReadError(f"BigQuery gave no answer in {CALL_TIMEOUT_S}s") from err
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise CostReadError(f"BigQuery: HTTP {err.code}: {str(detail)[:400]}") from err
    except urllib.error.URLError as err:
        raise CostReadError(f"BigQuery unreachable: {err.reason}") from err


def _adc_token() -> str:
    try:
        done = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=CALL_TIMEOUT_S,
        )
    except FileNotFoundError as err:
        raise CostReadError("the `gcloud` CLI is not on PATH") from err
    except subprocess.TimeoutExpired as err:
        raise CostReadError(f"`gcloud` gave no token in {CALL_TIMEOUT_S}s") from err
    if done.returncode != 0:
        raise CostReadError(
            f"gcloud: {done.stderr.strip()[-300:]} (run: gcloud auth application-default login)"
        )
    return done.stdout.strip()


def run_bigquery(
    sql: str,
    *,
    project: str,
    http: Callable[..., dict] = _http,
    token: Callable[[], str] = _adc_token,
    sleep: Callable[[float], None] = time.sleep,
    limit_s: float = 300,
) -> list[list]:
    """Run one query with application-default credentials, waiting and paging as needed."""
    tok = token()
    api = f"https://bigquery.googleapis.com/bigquery/v2/projects/{_check_id(project)}"
    resp = http(f"{api}/queries", tok, {"query": sql, "useLegacySql": False, "timeoutMs": 60000})
    job = resp.get("jobReference") or {}
    jid = urllib.parse.quote(str(job.get("jobId", "")), safe="")
    params = {"location": job.get("location", ""), "timeoutMs": 60000}
    waited = 0.0
    while not resp.get("jobComplete"):
        if waited >= limit_s:
            with contextlib.suppress(CostReadError):
                http(f"{api}/jobs/{jid}/cancel?{urllib.parse.urlencode(params)}", tok, {})
            raise CostReadError(f"BigQuery: still running after {limit_s:.0f}s, cancelled")
        sleep(2)
        waited += 2
        resp = http(f"{api}/queries/{jid}?{urllib.parse.urlencode(params)}", tok)
    rows = bigquery_rows(resp)
    while resp.get("pageToken"):
        page = urllib.parse.urlencode({**params, "pageToken": resp["pageToken"]})
        resp = http(f"{api}/queries/{jid}?{page}", tok)
        rows += bigquery_rows(resp)
    return rows


# --------------------------------------------------------------------------- CLI

DATABRICKS_BASIS = "Databricks system.billing.usage at list price, UTC days"

#: How a failed read names its source, in the one line it prints.
SOURCE_NAMES = {"databricks": "the Databricks billing tables", "gcp": "the GCP billing export"}


def _gcp_basis(tz: str) -> str:
    return (
        "GCP billing export, `cost` before credits and reseller discount, "
        f"{tz} days, subscription SKUs out"
    )


@dataclass(frozen=True)
class _Source:
    """One billing table, wired from .env: how to ask it, and on which days."""

    name: str
    basis: str
    days_in: ZoneInfo
    sql: Callable[..., str]
    run: Callable[[str], list[list]]
    fresh_sql: Callable[[], str]


def _source(name: str, ids: Identities) -> _Source:
    local = timezone_for(ids)
    if name == "databricks":
        profile = ids["DATABRICKS_WORKSPACE_ID"]
        warehouse = ids["DATABRICKS_BILLING_WAREHOUSE_ID"]
        return _Source(
            name=name,
            basis=DATABRICKS_BASIS,
            days_in=ZoneInfo("UTC"),
            sql=databricks_sql,
            run=lambda q: run_databricks(q, profile=profile, warehouse_id=warehouse),
            fresh_sql=databricks_fresh_sql,
        )
    table, project = ids["GCP_BILLING_EXPORT_TABLE"], ids["GCP_BILLING_QUERY_PROJECT"]
    return _Source(
        name=name,
        basis=_gcp_basis(local.key),
        days_in=local,
        sql=lambda s, e, *, by, only=None, with_id=False: gcp_sql(
            table, s, e, by=by, tz=local.key, only=only
        ),
        run=lambda q: run_bigquery(q, project=project),
        fresh_sql=lambda: gcp_fresh_sql(table),
    )


def _through(src: _Source) -> date:
    """The last whole day the table holds, asked of the table itself."""
    got = src.run(src.fresh_sql())
    if not got or not got[0] or got[0][0] in (None, ""):
        raise CostReadError("no usage in the last four days")
    return complete_through(datetime.fromtimestamp(float(got[0][0]), UTC), src.days_in)


def _clip(window: Window, through: date, tag: str, notes: list[str]) -> Window:
    if window.start > through:
        raise ValueError(
            f"the {tag} window starts after {through}, the last whole day in the table"
        )
    if window.end > through:
        notes.append(f"{tag} window cut to end {through}: later days have not fully landed")
        return Window(window.start, through)
    return window


def _unapplied(exclude: Sequence[OneOff], paces: Iterable[Pace | None]) -> list[str]:
    """Contract 4: a one-off that took nothing out says so, rather than vanishing."""
    applied = {o for p in paces if p is not None for o in p.excluded}
    return [
        f"NOT applied: {o.day} {money(o.usd)} - {o.why} (no data that day in the windows read)"
        for o in exclude
        if o not in applied
    ]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m daydag.cost", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser, source: str | None) -> None:
        if source is None:
            p.add_argument("--source", choices=("gcp", "databricks"), default="databricks")
        else:
            p.set_defaults(source=source)
        p.add_argument(
            "--exclude", action="append", default=[], type=OneOff.parse, metavar="DAY:USD:WHY"
        )
        p.add_argument("--sql", action="store_true", help="print the statement, do not run it")
        p.add_argument("--env", default=".env")

    for name, by in (("gcp", GCP_BY), ("databricks", DATABRICKS_BY)):
        p = sub.add_parser(name, help=f"monthly pace from the {name} billing table")
        common(p, name)
        p.add_argument(
            "--by", choices=sorted(by), default="service" if name == "gcp" else "product"
        )
        p.add_argument(
            "--recent", type=Window.parse, help="START:END (default: the last 7 whole days)"
        )
        p.add_argument("--base", type=Window.parse, help="START:END to compare against")
        p.add_argument("--top", type=int, default=8)

    p = sub.add_parser("lever", help="one resource before and after its change date")
    common(p, None)
    p.add_argument("--by", required=True)
    p.add_argument("--id", required=True, help="the resource id, or its name as the table shows it")
    p.add_argument("--change", required=True, type=date.fromisoformat)
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("movers", help="what drove one day")
    common(p, None)
    p.add_argument("--by", required=True)
    p.add_argument("--day", required=True, type=date.fromisoformat)
    p.add_argument("--n", type=int, default=10)

    args = ap.parse_args(argv)
    if args.cmd in ("lever", "movers"):
        allowed = DATABRICKS_BY if args.source == "databricks" else GCP_BY
        if args.by not in allowed:
            ap.error(f"--by for {args.source} is one of {', '.join(sorted(allowed))}")

    try:
        ids = Identities.from_file(Path(args.env))
        src = _source(args.source, ids)
        # The clock's yesterday bounds what to ask for; the table's own
        # freshness (fetched below) decides which of those days are whole.
        guess = datetime.now(src.days_in).date() - timedelta(days=1)
        notes: list[str] = []
        if args.cmd in ("gcp", "databricks"):
            asked = args.recent or Window(guess - timedelta(days=9), guess)
            span = [asked, args.base] if args.base else [asked]
            sql = src.sql(min(w.start for w in span), max(w.end for w in span), by=args.by)
            if args.sql:
                print(sql)
                return 0
            through, rows = _through(src), to_rows(src.run(sql))
            recent = (
                _clip(args.recent, through, "recent", notes)
                if args.recent
                else Window.ending(through, 7)
            )
            base = _clip(args.base, through, "base", notes) if args.base else None
            now_p = pace(rows, recent, args.exclude)
            base_p = pace(rows, base, args.exclude) if base else None
            notes += _unapplied(args.exclude, [now_p, base_p])
            print(render_pace(now_p, base=base_p, basis=src.basis, top=args.top, notes=notes))
        elif args.cmd == "lever":
            span = timedelta(days=args.days)
            sql = src.sql(
                args.change - span, args.change + span, by=args.by, only=args.id, with_id=True
            )
            if args.sql:
                print(sql)
                return 0
            through, rows = _through(src), to_rows(src.run(sql))
            before, after = before_after(
                rows, change=args.change, days=args.days, through=through, exclude=args.exclude
            )
            notes += _unapplied(args.exclude, [before, after])
            found = sorted({r.line for r in rows})
            what = found[0] if len(found) == 1 else f"{args.id} (no spend in the window)"
            if len(found) > 1:
                what = f"{len(found)} resources match {args.id!r}: {', '.join(found)}"
                notes.insert(0, "  WARNING: the figures are their SUM - pass one id to read one")
            print(
                render_lever(before, after, what=f"{args.by}: {what}", basis=src.basis, notes=notes)
            )
        else:
            sql = src.sql(args.day - timedelta(days=7), args.day, by=args.by)
            if args.sql:
                print(sql)
                return 0
            through, rows = _through(src), to_rows(src.run(sql))
            if args.day > through:
                notes.append(f"{args.day} has not fully landed (whole days end {through})")
            found = movers(rows, day=args.day, n=args.n)
            print(render_movers(found, day=args.day, basis=src.basis, notes=notes))
    except (CostReadError, ConfigError, ValueError) as err:
        # One line, the source named, and nothing on stdout: a failed read
        # never leaves a partial table behind to be read as the bill. A value
        # refused as unsafe (a quote in a name) lands here too, not as a trace.
        reason = " ".join(str(err).split())
        print(f"couldn't check {SOURCE_NAMES[args.source]}: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
