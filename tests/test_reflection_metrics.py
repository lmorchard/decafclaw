"""Reflection cost/effectiveness telemetry (#409).

Records one metadata-only row per judge-eligible turn: outcome bucket, retry
count, judge token cost, first-vs-final response delta, and a critique
fingerprint. Fail-open. ``make reflection-stats`` aggregates.
"""

import json

import pytest

from decafclaw import reflection_metrics
from decafclaw.config import Config
from decafclaw.config_types import AgentConfig


def _config(tmp_path):
    return Config(agent=AgentConfig(data_home=str(tmp_path), id="t"))


# -- response delta ------------------------------------------------------------


def test_response_delta_identical():
    char_delta, overlap = reflection_metrics.response_delta("same text", "same text")
    assert char_delta == 0
    assert overlap == 1.0


def test_response_delta_disjoint():
    char_delta, overlap = reflection_metrics.response_delta("alpha beta", "gamma delta epsilon")
    assert overlap == 0.0
    assert char_delta == len("gamma delta epsilon") - len("alpha beta")


def test_response_delta_partial_overlap():
    _, overlap = reflection_metrics.response_delta("the quick fox", "the quick dog")
    # {the,quick,fox} vs {the,quick,dog}: intersection 2, union 4 → 0.5
    assert overlap == 0.5


def test_response_delta_empty_both():
    char_delta, overlap = reflection_metrics.response_delta("", "")
    assert char_delta == 0
    assert overlap == 1.0


# -- outcome classification ----------------------------------------------------


def test_classify_passed_first():
    assert reflection_metrics.classify_outcome(
        first_response="hi", last_error="", retry_count=0,
        exhausted=False, final_content="hi") == "passed_first"


def test_classify_passed_after_retry():
    assert reflection_metrics.classify_outcome(
        first_response="v0", last_error="", retry_count=1,
        exhausted=False, final_content="v1") == "passed_after_retry"


def test_classify_loop_exhausted():
    assert reflection_metrics.classify_outcome(
        first_response="v0", last_error="", retry_count=2,
        exhausted=True, final_content="v2") == "loop_exhausted"


def test_classify_errored():
    assert reflection_metrics.classify_outcome(
        first_response="v0", last_error="judge boom", retry_count=0,
        exhausted=False, final_content="v0") == "errored"


def test_classify_skipped_empty():
    assert reflection_metrics.classify_outcome(
        first_response=None, last_error="", retry_count=0,
        exhausted=False, final_content="") == "skipped_empty"


def test_classify_none_when_not_evaluated_but_has_content():
    # Eligible but judge never ran and content is non-empty (e.g. end_turn
    # path) → no row.
    assert reflection_metrics.classify_outcome(
        first_response=None, last_error="", retry_count=0,
        exhausted=False, final_content="a real answer") is None


# -- subscriber writes a record ------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_writes_record(tmp_path):
    cfg = _config(tmp_path)
    handle = reflection_metrics.make_reflection_metrics_subscriber(cfg)
    await handle({
        "type": "reflection_turn",
        "conv_id": "conv-1",
        "outcome": "passed_after_retry",
        "retry_count": 1,
        "judge_prompt_tokens": 300,
        "judge_completion_tokens": 20,
        "char_delta": 45,
        "overlap_ratio": 0.6,
        "critique_fingerprint": "the answer omits the error handling",
    })
    path = cfg.workspace_path / cfg.telemetry.reflection_metrics_path
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1
    rec = records[0]
    assert rec["outcome"] == "passed_after_retry"
    assert rec["retry_count"] == 1
    assert rec["judge_prompt_tokens"] == 300
    assert rec["conv_id"] == "conv-1"
    assert rec["critique_fingerprint"] == "the answer omits the error handling"
    assert "timestamp" in rec
    assert "type" not in rec


@pytest.mark.asyncio
async def test_subscriber_ignores_other_events(tmp_path):
    cfg = _config(tmp_path)
    handle = reflection_metrics.make_reflection_metrics_subscriber(cfg)
    await handle({"type": "reflection_result", "passed": True})
    path = cfg.workspace_path / cfg.telemetry.reflection_metrics_path
    assert not path.exists()


@pytest.mark.asyncio
async def test_subscriber_fail_open(tmp_path):
    cfg = _config(tmp_path)
    # Occupy the reflection/ dir path with a plain file so mkdir(parents) fails.
    workspace = tmp_path / "data" / "t" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "reflection").write_text("file, not a dir")
    handle = reflection_metrics.make_reflection_metrics_subscriber(cfg)
    await handle({"type": "reflection_turn", "outcome": "passed_first"})  # must not raise


# -- stats aggregation ---------------------------------------------------------


def test_aggregate_stats():
    records = [
        {"outcome": "passed_first", "retry_count": 0, "judge_prompt_tokens": 100,
         "judge_completion_tokens": 10},
        {"outcome": "passed_first", "retry_count": 0, "judge_prompt_tokens": 120,
         "judge_completion_tokens": 12},
        {"outcome": "passed_after_retry", "retry_count": 1, "judge_prompt_tokens": 300,
         "judge_completion_tokens": 30},
        {"outcome": "loop_exhausted", "retry_count": 2, "judge_prompt_tokens": 500,
         "judge_completion_tokens": 50},
    ]
    stats = reflection_metrics.aggregate(records)
    assert stats["total_turns"] == 4
    assert stats["buckets"]["passed_first"] == 2
    assert stats["buckets"]["loop_exhausted"] == 1
    assert stats["pass_first_rate"] == pytest.approx(0.5)
    assert stats["loop_exhausted_rate"] == pytest.approx(0.25)
    assert stats["mean_retries"] == pytest.approx((0 + 0 + 1 + 2) / 4)
    assert stats["total_judge_tokens"] == 100 + 10 + 120 + 12 + 300 + 30 + 500 + 50
