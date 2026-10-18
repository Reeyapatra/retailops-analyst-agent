"""Groundedness and failure-classification evaluators (tests written before the code)."""

from __future__ import annotations

from evals.evaluators.groundedness import (
    citation_validity,
    extract_numbers,
    numeric_groundedness,
)


def grounded(answer: str, rows: list[list[object]], question: str = "", sql: str = "") -> float:
    return numeric_groundedness(answer, rows=rows, question=question, sql=sql)["score"]  # type: ignore[return-value]


def test_extracts_plain_formatted_percent_and_scaled_numbers() -> None:
    nums = {
        n.value
        for n in extract_numbers("Revenue was $39,920,619.68, up 5.4% (about 39.9 million).")
    }
    assert 39920619.68 in nums and 5.4 in nums
    assert any(abs(v - 39_900_000) < 1 for v in nums)


def test_grounded_when_every_number_is_in_the_rows() -> None:
    assert grounded("There were 146,475 orders.", [[146475]]) == 1.0
    assert grounded("Net revenue was $39,920,619.68.", [[39920619.68]]) == 1.0


def test_rounding_and_percent_conversions_are_accepted() -> None:
    assert grounded("The cancellation rate was 4.21%.", [[0.0421]]) == 1.0
    assert grounded("AOV was 293.00.", [[293.0015]]) == 1.0
    assert grounded("Revenue was about 39.9 million.", [[39920619.68]]) == 1.0


def test_ungrounded_number_is_flagged() -> None:
    result = numeric_groundedness("There were 146,475 orders and 9,999 refunds.", rows=[[146475]])
    assert result["score"] == 0.5 and result["ungrounded"] == [9999.0]


def test_numbers_from_the_question_and_sql_are_allowed() -> None:
    assert (
        grounded(
            "For March 2026, the top 5 regions by revenue were East (14,016,107.85).",
            [["East", 14016107.85]],
            question="top 5 regions by revenue in March 2026",
            sql="SELECT ... LIMIT 5",
        )
        == 1.0
    )


def test_answers_without_numbers_are_trivially_grounded_but_flagged_as_such() -> None:
    r = numeric_groundedness("The West region leads.", rows=[["West"]])
    assert r["score"] == 1.0 and r["n_numbers"] == 0


def test_empty_evidence_with_numbers_is_ungrounded() -> None:
    assert grounded("There were 42 orders.", []) == 0.0


def test_citation_validity() -> None:
    ok = citation_validity(cited=["a#x", "b#y"], retrieved=["a#x", "b#y", "c#z"])
    assert ok["score"] == 1.0
    bad = citation_validity(cited=["a#x", "fake#q"], retrieved=["a#x"])
    assert bad["score"] == 0.5 and bad["invalid"] == ["fake#q"]
    assert citation_validity(cited=[], retrieved=["a#x"])["score"] == 1.0
