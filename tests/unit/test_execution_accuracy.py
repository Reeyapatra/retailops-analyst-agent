from __future__ import annotations

from evals.evaluators.execution_accuracy import results_match


def test_exact_match() -> None:
    assert results_match([["a", 1], ["b", 2]], [["a", 1], ["b", 2]], order_matters=True)


def test_order_insensitive_when_order_does_not_matter() -> None:
    assert results_match([["a", 1], ["b", 2]], [["b", 2], ["a", 1]], order_matters=False)


def test_order_sensitive_when_order_matters() -> None:
    assert not results_match([["a", 1], ["b", 2]], [["b", 2], ["a", 1]], order_matters=True)


def test_float_tolerance() -> None:
    assert results_match([[31.2885]], [[31.29]], order_matters=False)
    assert not results_match([[31.2885]], [[31.5]], order_matters=False)


def test_int_and_float_equal() -> None:
    assert results_match([[5608]], [[5608.0]], order_matters=False)


def test_wrong_values_row_counts_and_shapes_fail() -> None:
    assert not results_match([[1]], [[2]], order_matters=False)
    assert not results_match([[1], [2]], [[1]], order_matters=False)
    assert not results_match([[1, 2]], [[1]], order_matters=False)
    assert not results_match([["a"]], [["b"]], order_matters=False)


def test_none_handling() -> None:
    assert results_match([[None]], [[None]], order_matters=False)
    assert not results_match([[None]], [[0]], order_matters=False)


def test_bool_is_not_a_number() -> None:
    assert not results_match([[1]], [[True]], order_matters=False)
