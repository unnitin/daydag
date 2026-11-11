"""The cost arithmetic and the statements it sends: the numbers a lever is judged on."""

from __future__ import annotations

import json
from datetime import date

import pytest

from daydag.cost import (
    CostReadError,
    DayCost,
    OneOff,
    Window,
    before_after,
    databricks_rows,
    databricks_sql,
    gcp_sql,
    main,
    movers,
    pace,
    render_lever,
    render_pace,
    run_bigquery,
    run_databricks,
)

D = date.fromisoformat


def rows(spec: dict[str, dict[str, float]]) -> list[DayCost]:
    return [
        DayCost(D(day), line, usd) for day, lines in spec.items() for line, usd in lines.items()
    ]


WEEK = rows(
    {
        "2026-11-02": {"pipelines": 600, "warehouses": 400},  # Mon
        "2026-11-03": {"pipelines": 600, "warehouses": 400},
        "2026-11-07": {"pipelines": 300, "warehouses": 200},  # Sat
    }
)


def test_pace_averages_the_days_present_and_splits_the_weekend():
    p = pace(WEEK, Window.parse("2026-11-01:2026-11-07"))
    assert p.days_seen == (D("2026-11-02"), D("2026-11-03"), D("2026-11-07"))
    assert p.per_day == pytest.approx(2500 / 3)
    assert p.weekday == 1000 and p.weekend == 500
    assert p.week_shaped == pytest.approx((5 * 1000 + 2 * 500) / 7)
    assert p.missing == 4


def test_a_one_off_leaves_the_total_by_name_only():
    spike = OneOff(D("2026-11-03"), 300, "one notebook run")
    p = pace(WEEK, Window.parse("2026-11-02:2026-11-03"), [spike])
    assert p.per_day == 850
    assert p.excluded == (spike,)
    text = render_pace(p, basis="test basis")
    assert "one notebook run" in text and "one-offs out" in text


def test_one_off_needs_a_reason():
    with pytest.raises(ValueError, match="reason"):
        OneOff.parse("2026-11-03:300:")
    assert OneOff.parse("2026-11-03:1,250:reruns").usd == 1250


def test_window_rejects_a_backwards_range():
    with pytest.raises(ValueError):
        Window.parse("2026-11-04:2026-11-01")


def test_before_after_leaves_the_change_day_out():
    lever = rows(
        {
            "2026-11-08": {"p": 3000},
            "2026-11-09": {"p": 1700},
            "2026-11-10": {"p": 900},  # change day: half old, half new
            "2026-11-11": {"p": 200},
            "2026-11-12": {"p": 150},
        }
    )
    before, after = before_after(lever, change=D("2026-11-10"), days=2, through=D("2026-11-12"))
    assert before.per_day == 2350 and after.per_day == 175
    text = render_lever(before, after, what="pipeline: example", basis="b")
    assert "billed saving: $65.2k" in text
    assert "too early" in text  # two days after, three needed


def test_a_lever_counts_a_day_without_spend_as_zero():
    lever = rows({"2026-11-08": {"p": 300}, "2026-11-12": {"p": 100}})  # 9 and 11 Nov: no run
    before, after = before_after(lever, change=D("2026-11-10"), days=2, through=D("2026-11-12"))
    assert before.per_day == 150 and after.per_day == 50


def test_no_whole_day_after_the_change_reads_nothing():
    lever = rows({"2026-11-08": {"p": 3000}, "2026-11-09": {"p": 1700}})
    before, after = before_after(lever, change=D("2026-11-10"), days=2, through=D("2026-11-10"))
    text = render_lever(before, after, what="pipeline: example", basis="b")
    assert "nothing to read" in text and "billed saving" not in text


def test_movers_ranks_by_the_size_of_the_change():
    days = {f"2026-11-0{i}": {"steady": 100, "spiky": 50} for i in range(3, 10)}
    days["2026-11-10"] = {"steady": 100, "spiky": 900, "new": 300}
    found = movers(rows(days), day=D("2026-11-10"))
    assert [f[0] for f in found[:2]] == ["spiky", "new"]
    assert found[0][2] == 50


def test_render_pace_reads_base_against_recent():
    base = pace(WEEK, Window.parse("2026-11-02:2026-11-03"))
    recent = pace(WEEK, Window.parse("2026-11-07:2026-11-07"))
    text = render_pace(recent, base=base, basis="b")
    total = next(line for line in text.splitlines() if line.startswith("TOTAL"))
    assert "$30.0k" in total and "$15.0k" in total and "-$15.0k" in total


def test_databricks_sql_prices_at_list_and_joins_the_latest_name():
    sql = databricks_sql(D("2026-11-01"), D("2026-11-07"), by="pipeline", only="abc-123")
    assert "pricing.default" in sql and "system.billing.list_prices" in sql
    assert "ORDER BY change_time DESC" in sql
    assert "(u.usage_metadata.dlt_pipeline_id = 'abc-123' OR n.name = 'abc-123')" in sql
    named = databricks_sql(D("2026-11-01"), D("2026-11-07"), by="pipeline", only="[dev] gold_x-dev")
    assert "n.name = '[dev] gold_x-dev'" in named


def test_ids_cannot_carry_sql():
    with pytest.raises(ValueError):
        databricks_sql(D("2026-11-01"), D("2026-11-07"), by="job", only="1' OR '1'='1")
    with pytest.raises(ValueError):
        gcp_sql("p.d.t; DROP", D("2026-11-01"), D("2026-11-07"), by="service", tz="UTC")


def test_gcp_sql_leaves_commits_out_and_uses_local_days():
    sql = gcp_sql(
        "proj.ds.export", D("2026-11-01"), D("2026-11-07"), by="service", tz="America/Los_Angeles"
    )
    assert "NOT LOWER(sku.description) LIKE '%subscription%'" in sql
    assert "DATE(usage_start_time, 'America/Los_Angeles')" in sql
    assert "_PARTITIONTIME" in sql  # partition pruning, or the scan bills the whole export


def test_a_failed_statement_carries_the_sources_own_reason():
    payload = {"status": {"state": "FAILED", "error": {"message": "does not have USE SCHEMA"}}}
    with pytest.raises(CostReadError, match="USE SCHEMA"):
        databricks_rows(payload)


def test_run_databricks_waits_then_pages():
    calls = []
    answers = iter(
        [
            {"statement_id": "s1", "status": {"state": "PENDING"}},
            {
                "statement_id": "s1",
                "status": {"state": "SUCCEEDED"},
                "result": {
                    "data_array": [["2026-11-10", "a", "1"]],
                    "next_chunk_internal_link": "/next",
                },
            },
            {"data_array": [["2026-11-10", "b", "2"]]},
        ]
    )

    def cli(args):
        calls.append(args)
        return next(answers)

    got = run_databricks("SELECT 1", profile="p", warehouse_id="w", cli=cli, sleep=lambda s: None)
    assert got == [["2026-11-10", "a", "1"], ["2026-11-10", "b", "2"]]
    assert json.loads(calls[0][-1])["warehouse_id"] == "w"
    assert calls[1][2:4] == ["get", "/api/2.0/sql/statements/s1"]


def test_run_bigquery_waits_for_the_job_then_pages():
    answers = iter(
        [
            {"jobComplete": False, "jobReference": {"jobId": "j", "location": "US"}},
            {
                "jobComplete": True,
                "rows": [{"f": [{"v": "2026-11-10"}, {"v": "x"}, {"v": "5"}]}],
                "pageToken": "t",
            },
            {"jobComplete": True, "rows": [{"f": [{"v": "2026-11-10"}, {"v": "y"}, {"v": "6"}]}]},
        ]
    )
    got = run_bigquery(
        "SELECT 1",
        project="proj",
        http=lambda *a: next(answers),
        token=lambda: "tok",
        sleep=lambda s: None,
    )
    assert [r[1] for r in got] == ["x", "y"]


def test_sql_flag_prints_without_running(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(
        "DATABRICKS_WORKSPACE_ID=p\nDATABRICKS_BILLING_WAREHOUSE_ID=w\n"
        "GCP_BILLING_EXPORT_TABLE=proj.ds.export\nGCP_BILLING_QUERY_PROJECT=proj\n"
    )
    assert main(["gcp", "--sql", "--env", str(env), "--recent", "2026-11-07:2026-11-10"]) == 0
    assert "FROM `proj.ds.export`" in capsys.readouterr().out
    assert (
        main(
            [
                "lever",
                "--by",
                "warehouse",
                "--id",
                "w1",
                "--change",
                "2026-11-10",
                "--sql",
                "--env",
                str(env),
            ]
        )
        == 0
    )
    assert "warehouse_id = 'w1'" in capsys.readouterr().out


def test_a_missing_key_is_one_line_not_a_traceback(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("TIMEZONE=UTC\n")
    assert main(["databricks", "--env", str(env)]) == 2
    assert "couldn't check the Databricks billing tables" in capsys.readouterr().err


def test_a_statement_that_never_finishes_gives_up():
    def cli(args):
        return {"statement_id": "s1", "status": {"state": "RUNNING"}}

    with pytest.raises(CostReadError, match="still running"):
        run_databricks(
            "SELECT 1", profile="p", warehouse_id="w", cli=cli, sleep=lambda s: None, limit_s=10
        )


# --- the review's findings, each pinned ------------------------------------------


def test_a_one_off_on_a_day_without_rows_takes_nothing_out():
    two_days = rows({"2026-11-02": {"a": 1000}, "2026-11-03": {"a": 1000}})
    stray = OneOff(D("2026-11-04"), 300, "typo'd date")
    p = pace(two_days, Window.parse("2026-11-02:2026-11-04"), [stray])
    assert p.per_day == 1000 and p.excluded == () and p.missing == 1


def test_an_unapplied_one_off_is_reported_not_dropped():
    from daydag.cost import _unapplied

    p = pace(WEEK, Window.parse("2026-11-02:2026-11-03"))
    lines = _unapplied([OneOff(D("2026-10-01"), 300, "wrong month")], [p, None])
    assert lines and "NOT applied" in lines[0] and "wrong month" in lines[0]


def test_a_base_without_a_weekend_prints_a_dash_not_the_recent_figure():
    base = pace(WEEK, Window.parse("2026-11-02:2026-11-03"))  # Mon-Tue
    recent = pace(WEEK, Window.parse("2026-11-07:2026-11-07"))  # Sat
    weekends = next(
        ln for ln in render_pace(recent, base=base, basis="b").splitlines() if "weekends" in ln
    )
    assert weekends.split()[1:] == ["-", "$15.0k", "-"]


def test_whole_days_come_from_the_table_not_the_clock():
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from daydag.cost import complete_through

    la = ZoneInfo("America/Los_Angeles")
    # 10 Nov ends 08:00 UTC 11 Nov in Los Angeles; whole once the table holds 14:00 UTC.
    assert complete_through(datetime(2026, 11, 11, 13, 59, tzinfo=UTC), la) == D("2026-11-09")
    assert complete_through(datetime(2026, 11, 11, 14, 0, tzinfo=UTC), la) == D("2026-11-10")


def test_a_window_past_the_last_whole_day_is_cut_and_says_so():
    from daydag.cost import _clip

    notes: list[str] = []
    assert _clip(Window.parse("2026-11-07:2026-11-11"), D("2026-11-10"), "recent", notes).end == D(
        "2026-11-10"
    )
    assert "cut to end 2026-11-10" in notes[0]


def test_names_join_per_workspace():
    sql = databricks_sql(D("2026-11-01"), D("2026-11-07"), by="job")
    assert "PARTITION BY workspace_id, job_id" in sql
    assert "n.workspace_id = u.workspace_id" in sql


def test_a_page_token_is_encoded():
    urls = []
    answers = iter(
        [
            {
                "jobComplete": True,
                "jobReference": {"jobId": "j", "location": "US"},
                "pageToken": "a+b/c=",
            },
            {"jobComplete": True},
        ]
    )

    def http(url, tok, body=None):
        urls.append(url)
        return next(answers)

    run_bigquery("SELECT 1", project="proj", http=http, token=lambda: "t", sleep=lambda s: None)
    assert "pageToken=a%2Bb%2Fc%3D" in urls[1]


def test_giving_up_cancels_the_statement():
    calls = []

    def cli(args):
        calls.append(args)
        return {"statement_id": "s1", "status": {"state": "RUNNING"}}

    with pytest.raises(CostReadError, match="cancelled"):
        run_databricks(
            "SELECT 1", profile="p", warehouse_id="w", cli=cli, sleep=lambda s: None, limit_s=5
        )
    assert calls[-1][2:4] == ["post", "/api/2.0/sql/statements/s1/cancel"]


def _env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "DATABRICKS_WORKSPACE_ID=p\nDATABRICKS_BILLING_WAREHOUSE_ID=w\n"
        "GCP_BILLING_EXPORT_TABLE=proj.ds.export\nGCP_BILLING_QUERY_PROJECT=proj\nTIMEZONE=UTC\n"
    )
    return str(env)


def test_an_unsafe_name_is_one_line_not_a_traceback(tmp_path, capsys):
    rc = main(
        [
            "lever",
            "--by",
            "pipeline",
            "--id",
            "O'Brien",
            "--change",
            "2026-11-10",
            "--env",
            _env(tmp_path),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 2 and out == "" and len(err.splitlines()) == 1 and "Traceback" not in err


def test_a_name_matching_two_resources_is_flagged(tmp_path, capsys, monkeypatch):
    from datetime import UTC, datetime

    from daydag import cost

    fresh = str(datetime(2026, 11, 14, 12, tzinfo=UTC).timestamp())
    answers = iter(
        [
            [[fresh]],
            [
                ["2026-11-08", "etl [id-1]", "100"],
                ["2026-11-12", "etl [id-2]", "40"],
            ],
        ]
    )
    monkeypatch.setattr(cost, "run_databricks", lambda q, **kw: next(answers))
    rc = main(
        [
            "lever",
            "--by",
            "pipeline",
            "--id",
            "etl",
            "--change",
            "2026-11-10",
            "--days",
            "3",
            "--env",
            _env(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "2 resources match 'etl'" in out and "SUM" in out
