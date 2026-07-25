"""Unit tests for the deterministic eval-diagnostics helpers (#528, #531)."""

import json

import pytest

from decafclaw.config import Config
from decafclaw.eval import runner as eval_runner
from decafclaw.eval.diagnostics import (
    CANONICAL_AXES,
    aggregate_by_axis,
    build_turn_diagnostics,
    detect_files_cited,
    detect_files_read,
    parse_axes,
    validate_axes,
)
from decafclaw.eval.runner import _build_test_config, run_test


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


def test_parse_axes_non_iterable_raises_valueerror():
    with pytest.raises(ValueError):
        parse_axes({"tests": 123})


def test_parse_axes_unhashable_element_raises_valueerror():
    with pytest.raises(ValueError):
        parse_axes({"tests": [{"nested": "dict"}]})


def test_validate_axes_passes_for_valid_cases():
    validate_axes([{"name": "a", "tests": "retrieval"},
                   {"name": "b", "tests": ["routing", "answer_quality"]},
                   {"name": "c"}])  # untagged is fine


def test_validate_axes_raises_on_unknown_axis_naming_the_case():
    with pytest.raises(ValueError, match="bad-case"):
        validate_axes([{"name": "ok", "tests": "retrieval"},
                       {"name": "bad-case", "tests": "smartness"}])


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


def _sidecar():
    return {
        "total_tokens_estimated": 12000,
        "total_tokens_actual": 11800,
        "context_window_size": 1000000,
        "compaction_threshold": 150000,
        "sources": [
            {"source": "system", "tokens_estimated": 3000, "items_included": 1,
             "items_truncated": 0, "details": {}},
            {"source": "tools", "tokens_estimated": 2000, "items_included": 8,
             "items_truncated": 40, "details": {}},
            {"source": "retrieved_context", "tokens_estimated": 1500,
             "items_included": 3, "items_truncated": 0, "details": {}},
        ],
        "memory_candidates": [
            {"file_path": "agent/pages/escalation-runbook.md",
             "composite_score": 0.82, "similarity": 0.79, "recency": 0.5,
             "importance": 0.9},
        ],
    }


def test_build_turn_diagnostics_full():
    calls = [("vault_read", {"page": "agent/pages/escalation-runbook.md"})]
    resp = "Per the escalation-runbook, Priya is paged first."
    d = build_turn_diagnostics(_sidecar(), calls, resp)
    assert d["tokens_by_section"] == {"system": 3000, "tools": 2000,
                                      "retrieved_context": 1500}
    assert d["active_tools"] == 8
    assert d["deferred_tools"] == 40
    assert d["total_tokens_estimated"] == 12000
    assert d["files_read"] == ["agent/pages/escalation-runbook.md"]
    assert "agent/pages/escalation-runbook.md" in d["files_cited"]
    assert d["tool_calls"] == {"names": ["vault_read"], "count": 1}
    assert d["retrieved_candidates"][0]["file_path"] == "agent/pages/escalation-runbook.md"


def test_build_turn_diagnostics_none_sidecar_degrades():
    calls = [("workspace_read", {"path": "notes/x.md"})]
    d = build_turn_diagnostics(None, calls, "read notes/x.md")
    assert d["tokens_by_section"] == {}
    assert d["active_tools"] is None
    assert d["deferred_tools"] is None
    assert d["total_tokens_estimated"] is None
    assert d["retrieved_candidates"] == []
    assert d["files_read"] == ["notes/x.md"]
    assert d["tool_calls"] == {"names": ["workspace_read"], "count": 1}


class _FakeResult:
    def __init__(self, text):
        self.text = text


@pytest.mark.asyncio
async def test_run_test_attaches_diagnostics(tmp_path, monkeypatch):
    # Fake the LLM turn: append a synthetic vault_read call to history, no model.
    async def _fake_turn(ctx, turn_input, history):
        history.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c0", "function": {
                "name": "vault_read",
                "arguments": json.dumps({"page": "agent/pages/foo"})}}],
        })
        history.append({"role": "tool", "tool_call_id": "c0", "content": "ok"})
        return _FakeResult("I read agent/pages/foo and here is the answer.")

    monkeypatch.setattr(eval_runner, "run_agent_turn", _fake_turn)
    monkeypatch.setattr(
        eval_runner, "read_context_sidecar",
        lambda config, conv_id: {
            "total_tokens_estimated": 100, "sources": [
                {"source": "tools", "tokens_estimated": 10,
                 "items_included": 5, "items_truncated": 20, "details": {}}],
            "memory_candidates": []},
    )

    cfg = _build_test_config(Config(), {"setup": {}}, str(tmp_path))
    result = await run_test(cfg, {"name": "t", "input": "hi", "expect": {}})

    diag = result["diagnostics"]
    assert diag["files_read"] == ["agent/pages/foo"]
    assert "agent/pages/foo" in diag["files_cited"]
    assert diag["active_tools"] == 5
    assert diag["deferred_tools"] == 20
    assert diag["tool_calls"]["count"] == 1
