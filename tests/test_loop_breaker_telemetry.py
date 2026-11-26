"""Loop breaker telemetry (#645)."""

import json

import pytest

from decafclaw import loop_breaker_telemetry
from decafclaw.config import Config
from decafclaw.config_types import AgentConfig


def _config(tmp_path):
    return Config(agent=AgentConfig(data_home=str(tmp_path), id="t"))


@pytest.mark.asyncio
async def test_subscriber_writes_record(tmp_path):
    cfg = _config(tmp_path)
    handle = loop_breaker_telemetry.make_loop_breaker_subscriber(cfg)
    await handle(
        {
            "type": "loop_breaker",
            "context_id": "c1",
            "action": "stop",
            "signal": "error_surge",
            "reason": "4 of the last 6 tool results were errors",
        }
    )

    path = cfg.workspace_path / cfg.telemetry.loop_breaker_path
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1
    rec = records[0]

    assert rec["action"] == "stop"
    assert rec["signal"] == "error_surge"
    assert rec["reason"] == "4 of the last 6 tool results were errors"
    assert rec["context_id"] == "c1"
    assert "timestamp" in rec
    assert "type" not in rec


def test_aggregate_counts_by_action_and_signal():
    records = [
        {"action": "nudge", "signal": "repeat"},
        {"action": "nudge", "signal": "repeat"},
        {"action": "nudge", "signal": "error_surge"},
        {"action": "redirect", "signal": "repeat"},
        {"action": "stop", "signal": "error_surge"},
        {"action": "stop", "signal": "repeat"},
    ]
    stats = loop_breaker_telemetry.aggregate(records)

    assert stats["total_events"] == 6
    assert stats["actions"]["nudge"] == 3
    assert stats["actions"]["redirect"] == 1
    assert stats["actions"]["stop"] == 2

    assert stats["signals"]["repeat"] == 4
    assert stats["signals"]["error_surge"] == 2
