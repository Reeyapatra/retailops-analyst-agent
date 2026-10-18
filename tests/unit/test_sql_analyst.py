"""SQL Analyst subgraph behaviour, driven by a scripted model (no network, no API key).

These tests verify the control flow (guard, retry with feedback, give-up, untrusted-data
handling). They do NOT measure how well a real model writes SQL; that is the gold-set run.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from fakes import ScriptedLLM

from retailops.agents.sql_analyst import SQLDraft, SQLSummary, ask_sql, build_sql_analyst
from retailops.data.generate import GenConfig, generate
from retailops.data.load import build_warehouse
from retailops.observability import run_config
from retailops.tools.sql import SqlRunner

SUMMARY = SQLSummary(answer="There were N orders.", confidence_note="Direct count.")


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("analyst") / "w.duckdb"
    build_warehouse(generate(GenConfig(orders_per_day=20)), path)
    return path


def run(db: Path, drafts: list[str], question: str = "How many orders?", max_retries: int = 2):  # noqa: ANN201
    llm = ScriptedLLM(
        {SQLDraft: [SQLDraft(sql=s) for s in drafts], SQLSummary: [SUMMARY]},
    )
    graph = build_sql_analyst(llm, SqlRunner(db), max_retries=max_retries)
    return ask_sql(graph, question), llm


def drafts_seen(llm: ScriptedLLM) -> list[str]:
    return [m[1][1].content for m in llm.calls if m[0] == "SQLDraft"]


def test_happy_path_returns_evidence(db: Path) -> None:
    out, llm = run(db, ["SELECT count(*) AS n FROM fct_orders"])
    assert out.status == "ok" and len(out.attempts) == 1 and out.attempts[0].stage == "ok"
    assert out.evidence is not None and out.evidence.row_count == 1
    assert out.evidence.columns == ["n"] and "LIMIT" in out.evidence.sql.upper()
    assert out.answer == SUMMARY.answer


def test_hallucinated_column_is_fixed_by_retry_with_error_fed_back(db: Path) -> None:
    out, llm = run(
        db, ["SELECT sum(revenue) FROM fct_orders", "SELECT sum(net_revenue) FROM fct_orders"]
    )
    assert out.status == "ok" and [a.stage for a in out.attempts] == ["execute", "ok"]
    second_prompt = drafts_seen(llm)[1]
    assert "previous query failed" in second_prompt and "revenue" in second_prompt


def test_destructive_sql_is_rejected_never_run_and_retried(db: Path) -> None:
    out, llm = run(db, ["DROP TABLE fct_orders", "SELECT count(*) FROM fct_orders"])
    assert out.status == "ok" and out.attempts[0].stage == "guard"
    assert "NOT_SELECT" in drafts_seen(llm)[1] or "Only SELECT" in drafts_seen(llm)[1]
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT count(*) FROM fct_orders").fetchone()[0] > 0  # type: ignore[index]
    con.close()


def test_gives_up_after_bounded_retries(db: Path) -> None:
    out, llm = run(db, ["SELECT bad1 FROM fct_orders"] * 3, max_retries=2)
    assert out.status == "failed" and len(out.attempts) == 3 and out.evidence is None
    assert [m[0] for m in llm.calls].count("SQLSummary") == 0  # no summary of a failure


def test_zero_retries_means_single_attempt(db: Path) -> None:
    out, _ = run(db, ["SELECT bad FROM fct_orders"], max_retries=0)
    assert out.status == "failed" and len(out.attempts) == 1


def test_empty_result_triggers_retry(db: Path) -> None:
    out, _ = run(
        db,
        [
            "SELECT count(*) FROM fct_orders WHERE region = 'Nowhere' GROUP BY region",
            "SELECT region, count(*) FROM fct_orders GROUP BY region",
        ],
    )
    assert out.status == "ok" and [a.stage for a in out.attempts] == ["check", "ok"]


def test_persistent_empty_result_is_reported_not_hidden(db: Path) -> None:
    empty = "SELECT order_id FROM fct_orders WHERE region = 'Nowhere'"
    out, _ = run(db, [empty] * 3)
    assert out.status == "empty_result" and out.evidence is not None
    assert out.evidence.row_count == 0


def test_all_null_single_row_is_suspicious(db: Path) -> None:
    out, _ = run(
        db,
        [
            "SELECT max(delivered_ts) FROM fct_orders WHERE region = 'Nowhere'",
            "SELECT count(*) FROM fct_orders",
        ],
    )
    assert out.attempts[0].stage == "check" and out.status == "ok"


def test_follow_up_context_reaches_the_generator(db: Path) -> None:
    llm = ScriptedLLM({SQLDraft: [SQLDraft(sql="SELECT 1 AS x")], SQLSummary: [SUMMARY]})
    graph = build_sql_analyst(llm, SqlRunner(db))
    ask_sql(graph, "and for the West region?", context="Previous: revenue by region in March")
    assert "revenue by region in March" in drafts_seen(llm)[0]


def test_data_borne_injection_stays_data(db: Path) -> None:
    llm = ScriptedLLM(
        {
            SQLDraft: [
                SQLDraft(
                    sql="SELECT notes FROM raw_events WHERE notes LIKE '%INJECTION_FIXTURE%' "
                    "AND event_type = 'ORDER_CREATED' ORDER BY notes"
                )
            ],
            SQLSummary: [SUMMARY],
        }
    )
    out = ask_sql(build_sql_analyst(llm, SqlRunner(db)), "Show the unusual notes")
    summary_prompt = [m for m in llm.calls if m[0] == "SQLSummary"][0][1][1].content
    assert out.status == "ok" and out.evidence is not None and out.evidence.row_count >= 1
    assert "<untrusted_data" in summary_prompt and "ignore" in summary_prompt.lower()
    # the system prompt tells the model to treat the block as data
    assert "never instructions" in llm.calls[-1][1][0].content
    con = duckdb.connect(str(db), read_only=True)  # fixture text did not alter anything
    assert con.execute("SELECT count(*) FROM raw_events").fetchone()[0] > 0  # type: ignore[index]
    con.close()


def test_run_config_carries_trace_metadata() -> None:
    cfg = run_config("sql_analyst", thread_id="t1", scenario_id="gold-01")
    md = cfg["metadata"]
    assert md["prompt_versions"]["sql_generate"] == "v1"
    assert md["thread_id"] == "t1" and md["scenario_id"] == "gold-01"
    assert {"git_sha", "model_ids", "app_env"} <= set(md)
    assert "sql_analyst" in cfg["tags"]
