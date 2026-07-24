"""Unit tests for the deterministic eval-diagnostics helpers (#528, #531)."""

import pytest

from decafclaw.eval.diagnostics import CANONICAL_AXES, aggregate_by_axis, parse_axes


def test_parse_axes_absent_returns_empty():
    assert parse_axes({"name": "x"}) == []


def test_parse_axes_string_form():
    assert parse_axes({"tests": "retrieval"}) == ["retrieval"]


def test_parse_axes_list_form():
    assert parse_axes({"tests": ["routing", "answer_quality"]}) == ["routing", "answer_quality"]


def test_parse_axes_unknown_raises():
    with pytest.raises(ValueError, match="unknown axis"):
        parse_axes({"tests": "smartness"})


def test_parse_axes_unknown_in_list_raises():
    with pytest.raises(ValueError, match="unknown axis"):
        parse_axes({"tests": ["retrieval", "bogus"]})


def test_canonical_axes_exact_set():
    assert CANONICAL_AXES == frozenset(
        {"retrieval", "routing", "answer_quality", "workflow_discipline"}
    )


def test_aggregate_single_axis_and_untagged():
    cases = [
        {"name": "a", "tests": "retrieval"},
        {"name": "b", "tests": "retrieval"},
        {"name": "c"},  # untagged
    ]
    results = [
        {"name": "a", "status": "pass"},
        {"name": "b", "status": "fail"},
        {"name": "c", "status": "pass"},
    ]
    agg = aggregate_by_axis(results, cases)
    assert agg["retrieval"] == {"total": 2, "passed": 1, "failed": 1, "pass_rate": 0.5}
    assert agg["untagged"] == {"total": 1, "passed": 1, "failed": 0, "pass_rate": 1.0}


def test_aggregate_multi_axis_double_counts():
    cases = [{"name": "a", "tests": ["routing", "answer_quality"]}]
    results = [{"name": "a", "status": "pass"}]
    agg = aggregate_by_axis(results, cases)
    assert agg["routing"]["total"] == 1
    assert agg["answer_quality"]["total"] == 1


def test_aggregate_empty_axis_absent():
    agg = aggregate_by_axis([{"name": "a", "status": "pass"}], [{"name": "a"}])
    assert "retrieval" not in agg  # only axes actually present appear
    assert agg["untagged"]["passed"] == 1
