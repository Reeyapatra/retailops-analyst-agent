"""The gold set is internally valid and reproducible from the seeded generator.

Builds the full default dataset (seed 42, 180 days, 800 orders/day) in a temp dir, so it is the
slowest test in the suite. It does not involve any language model.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from retailops.data.generate import GenConfig, generate
from retailops.data.load import build_warehouse
from retailops.guards.sql_guard import check_sql
from retailops.tools.sql import SqlRunner

GOLD = Path(__file__).parents[2] / "evals" / "datasets" / "sql_gold.jsonl"


@pytest.fixture(scope="module")
def gold() -> list[dict[str, Any]]:
    return [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory) -> SqlRunner:
    path = tmp_path_factory.mktemp("gold") / "w.duckdb"
    build_warehouse(generate(GenConfig(seed=42, days=180, orders_per_day=800)), path)
    return SqlRunner(path)


def test_gold_set_shape(gold: list[dict[str, Any]]) -> None:
    assert len(gold) >= 30
    assert len({g["id"] for g in gold}) == len(gold)
    assert len({g["question"] for g in gold}) == len(gold)
    by_level = Counter(g["difficulty"] for g in gold)
    assert set(by_level) == {"easy", "medium", "hard"} and min(by_level.values()) >= 8
    held = sum(g["held_out"] for g in gold)
    assert 0.15 <= held / len(gold) <= 0.25


def test_every_gold_query_passes_the_guard(gold: list[dict[str, Any]], runner: SqlRunner) -> None:
    for g in gold:
        r = check_sql(g["gold_sql"], allowed_tables=runner.allowed_tables)
        assert r.ok, (g["id"], r.violations)


def test_gold_results_reproduce_from_seed(gold: list[dict[str, Any]], runner: SqlRunner) -> None:
    for g in gold:
        res = runner.run(g["gold_sql"])
        assert res.ok and res.columns == g["gold_columns"], g["id"]
        got = [[round(v, 4) if isinstance(v, float) else v for v in r] for r in res.rows]
        want = g["gold_rows"]
        if not g["order_matters"]:
            # without ORDER BY, SQL row order is not defined; compare as multisets
            got, want = sorted(got, key=repr), sorted(want, key=repr)
        assert got == want, g["id"]
