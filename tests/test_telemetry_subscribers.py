"""Shared subscriber contract for the JSONL telemetry writers (#1909).

Each module keeps its own record-shape test; these cover the common contract:
writes one record on its trigger event, ignores other events, and fails open
when the output path cannot be created.
"""

import json
from dataclasses import dataclass
from typing import Callable

import pytest

from decafclaw import loop_breaker_telemetry, reflection_metrics, retrieval_telemetry, tool_telemetry
from decafclaw.config import Config
from decafclaw.config_types import AgentConfig


def _config(tmp_path):
    return Config(agent=AgentConfig(data_home=str(tmp_path), id="t"))


def _tool_config(tmp_path):
    cfg = _config(tmp_path)
    cfg.skill_tool_owners = {"vault_write": "vault"}
    return cfg


@dataclass(frozen=True)
class SubscriberCase:
    name: str
    factory: Callable
    config: Callable
    trigger: dict
    ignored: dict
    path: Callable
    check: Callable


CASES = [
    SubscriberCase(
        name="tool",
        factory=tool_telemetry.make_tool_telemetry_subscriber,
        config=_tool_config,
        trigger={
            "type": "tool_end",
            "tool": "vault_write",
            "conv_id": "conv-1",
            "result_text": "wrote page",
            "duration_ms": 12.5,
            "input_bytes": 40,
        },
        ignored={"type": "tool_start", "tool": "vault_write"},
        path=lambda cfg: cfg.workspace_path / cfg.telemetry.tool_usage_path,
        check=lambda rec: rec["tool"] == "vault_write",
    ),
    SubscriberCase(
        name="loop_breaker",
        factory=loop_breaker_telemetry.make_loop_breaker_subscriber,
        config=_config,
        trigger={
            "type": "loop_breaker",
            "context_id": "c1",
            "action": "stop",
            "signal": "error_surge",
            "reason": "4 of the last 6 tool results were errors",
        },
        ignored={"type": "reflection_result", "passed": True},
        path=lambda cfg: cfg.workspace_path / cfg.telemetry.loop_breaker_path,
        check=lambda rec: rec["action"] == "stop",
    ),
    SubscriberCase(
        name="reflection",
        factory=reflection_metrics.make_reflection_metrics_subscriber,
        config=_config,
        trigger={
            "type": "reflection_turn",
            "conv_id": "conv-1",
            "outcome": "passed_after_retry",
            "retry_count": 1,
            "judge_prompt_tokens": 300,
            "judge_completion_tokens": 20,
            "char_delta": 45,
            "overlap_ratio": 0.6,
            "critique_fingerprint": "the answer omits the error handling",
        },
        ignored={"type": "reflection_result", "passed": True},
        path=lambda cfg: cfg.workspace_path / cfg.telemetry.reflection_metrics_path,
        check=lambda rec: rec["outcome"] == "passed_after_retry",
    ),
    SubscriberCase(
        name="retrieval",
        factory=retrieval_telemetry.make_retrieval_telemetry_subscriber,
        config=_config,
        trigger={
            "type": "retrieval_event",
            "conv_id": "c1",
            "candidates": [
                {
                    "file_path": "pages/a.md",
                    "source_type": "page",
                    "similarity": 0.9,
                    "recency": 0.8,
                    "importance": 0.5,
                    "composite_score": 0.77,
                    "included": True,
                    "drop_reason": None,
                }
            ],
        },
        ignored={"type": "tool_end"},
        path=lambda cfg: cfg.workspace_path / cfg.telemetry.retrieval_path,
        check=lambda rec: rec["conv_id"] == "c1",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_subscriber_writes_one_record(case, tmp_path):
    cfg = case.config(tmp_path)
    await case.factory(cfg)(case.trigger)

    records = [json.loads(line) for line in case.path(cfg).read_text().splitlines()]
    assert len(records) == 1
    case.check(records[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_subscriber_ignores_other_events(case, tmp_path):
    cfg = case.config(tmp_path)
    await case.factory(cfg)(case.ignored)
    assert not case.path(cfg).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_subscriber_fails_open_on_bad_path(case, tmp_path):
    cfg = case.config(tmp_path)
    # Make the workspace itself a file so the telemetry path cannot be created.
    workspace = cfg.workspace_path
    workspace.parent.mkdir(parents=True, exist_ok=True)
    workspace.write_text("i am a file, not a dir")

    await case.factory(cfg)(case.trigger)  # must not raise
