"""Unit tests for the deterministic eval-diagnostics helpers (#528, #531)."""

import pytest

from decafclaw.eval.diagnostics import CANONICAL_AXES, parse_axes


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
