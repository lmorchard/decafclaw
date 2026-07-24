"""Unit tests for the deterministic eval-diagnostics helpers (#528, #531)."""

import pytest

from decafclaw.eval.diagnostics import (
    CANONICAL_AXES,
    aggregate_by_axis,
    detect_files_cited,
    detect_files_read,
    parse_axes,
)


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


def test_detect_files_read_maps_args():
    calls = [
        ("vault_read", {"page": "agent/pages/DecafClaw"}),
        ("workspace_read", {"path": "notes/todo.md"}),
        ("conversation_search", {"query": "x"}),  # not a read tool
        ("vault_read", {"page": "agent/pages/DecafClaw"}),  # dup
    ]
    assert detect_files_read(calls) == ["agent/pages/DecafClaw", "notes/todo.md"]


def test_detect_files_read_drops_empty():
    assert detect_files_read([("vault_read", {})]) == []


def test_detect_files_cited_path_and_wiki():
    resp = "See [[Escalation Runbook]]. I read the escalation-runbook page."
    known = ["agent/pages/escalation-runbook.md", "agent/pages/oncall-rotation.md"]
    cited = detect_files_cited(resp, known)
    assert "agent/pages/escalation-runbook.md" in cited  # stem 'escalation-runbook' matched
    assert "Escalation Runbook" in cited                 # wiki-mention
    assert "agent/pages/oncall-rotation.md" not in cited  # never mentioned


def test_detect_files_cited_no_false_positive_on_unrelated():
    resp = "I don't have anything on that topic."
    assert detect_files_cited(resp, ["agent/pages/escalation-runbook.md"]) == []
