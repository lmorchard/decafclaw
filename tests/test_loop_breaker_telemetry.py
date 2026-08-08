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
    await handle({
        "type": "loop_breaker",
        "context_id": "c1",
        "action": "stop",
        "signal": "error_surge",
        "reason": "4 of the last 6 tool results were errors"
    })

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


@pytest.mark.asyncio
async def test_subscriber_ignores_other_events(tmp_path):
    cfg = _config(tmp_path)
    handle = loop_breaker_telemetry.make_loop_breaker_subscriber(cfg)
    await handle({"type": "reflection_result", "passed": True})
    path = cfg.workspace_path / cfg.telemetry.loop_breaker_path
    assert not path.exists()


@pytest.mark.asyncio
async def test_subscriber_fail_open(tmp_path):
    cfg = _config(tmp_path)
    workspace = tmp_path / "data" / "t" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    # The default path is telemetry/loop_breaker.jsonl
    # So we write a file named telemetry
    (workspace / "telemetry").write_text("file, not a dir")
    handle = loop_breaker_telemetry.make_loop_breaker_subscriber(cfg)
    # This must not raise
    await handle({"type": "loop_breaker", "action": "stop", "signal": "repeat"})


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
