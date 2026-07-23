"""Tool-usage telemetry (#310).

Subscriber consumes ``tool_end`` events (enriched with conv_id / duration_ms /
input_bytes at the publish site) and appends one metadata-only JSONL record per
tool call. Fail-open. A report ranks tools and flags never-called ones.
"""

import json

import pytest

from decafclaw import tool_telemetry
from decafclaw.config import Config
from decafclaw.config_types import AgentConfig


def _config(tmp_path):
    cfg = Config(agent=AgentConfig(data_home=str(tmp_path), id="t"))
    cfg.skill_tool_owners = {"vault_write": "vault", "dream_now": "dream"}
    return cfg


# -- source classification ----------------------------------------------------


def test_classify_source_core():
    cfg = Config()
    cfg.skill_tool_owners = {}
    assert tool_telemetry.classify_source("notes_append", cfg) == ("core", "")


def test_classify_source_skill():
    cfg = Config()
    cfg.skill_tool_owners = {"vault_write": "vault"}
    assert tool_telemetry.classify_source("vault_write", cfg) == ("skill", "vault")


def test_classify_source_mcp():
    cfg = Config()
    cfg.skill_tool_owners = {}
    assert tool_telemetry.classify_source(
        "mcp__fastmail__send_email", cfg) == ("mcp", "fastmail")


# -- outcome inference ---------------------------------------------------------


def test_infer_outcome():
    assert tool_telemetry.infer_outcome("all good") == "success"
    assert tool_telemetry.infer_outcome("[error executing foo: boom]") == "error"
    assert tool_telemetry.infer_outcome("[error: unknown tool 'x']") == "error"
    assert tool_telemetry.infer_outcome("[cancelled: foo]") == "cancelled"


# -- subscriber writes a record ------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_writes_record(tmp_path):
    cfg = _config(tmp_path)
    handle = tool_telemetry.make_tool_telemetry_subscriber(cfg)
    await handle({
        "type": "tool_end",
        "tool": "vault_write",
        "conv_id": "conv-1",
        "result_text": "wrote page",
        "duration_ms": 12.5,
        "input_bytes": 40,
    })
    path = cfg.workspace_path / cfg.telemetry.tool_usage_path
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1
    rec = records[0]
    assert rec["tool"] == "vault_write"
    assert rec["source"] == "skill"
    assert rec["source_detail"] == "vault"
    assert rec["outcome"] == "success"
    assert rec["conv_id"] == "conv-1"
    assert rec["duration_ms"] == 12.5
    assert rec["input_bytes"] == 40
    assert rec["output_bytes"] == len("wrote page".encode("utf-8"))
    assert "timestamp" in rec


@pytest.mark.asyncio
async def test_subscriber_ignores_other_events(tmp_path):
    cfg = _config(tmp_path)
    handle = tool_telemetry.make_tool_telemetry_subscriber(cfg)
    await handle({"type": "tool_start", "tool": "vault_write"})
    path = cfg.workspace_path / cfg.telemetry.tool_usage_path
    assert not path.exists()


@pytest.mark.asyncio
async def test_subscriber_fail_open_on_bad_path(tmp_path, caplog):
    cfg = _config(tmp_path)
    # Point the path at a location that can't be created (a file as a dir parent).
    blocker = tmp_path / "data" / "t" / "workspace"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("i am a file, not a dir")
    handle = tool_telemetry.make_tool_telemetry_subscriber(cfg)
    # Must not raise.
    await handle({"type": "tool_end", "tool": "x", "result_text": "ok"})


# -- report aggregation --------------------------------------------------------


def test_aggregate_counts_and_error_rate():
    records = [
        {"tool": "a", "conv_id": "c1", "outcome": "success", "timestamp": "2026-07-23T10:00:00Z"},
        {"tool": "a", "conv_id": "c1", "outcome": "error", "timestamp": "2026-07-23T11:00:00Z"},
        {"tool": "a", "conv_id": "c2", "outcome": "success", "timestamp": "2026-07-23T12:00:00Z"},
        {"tool": "b", "conv_id": "c1", "outcome": "success", "timestamp": "2026-07-23T09:00:00Z"},
    ]
    stats = tool_telemetry.aggregate(records)
    a = stats["a"]
    assert a["calls"] == 3
    assert a["unique_convs"] == 2
    assert a["errors"] == 1
    assert a["error_rate"] == pytest.approx(1 / 3)
    assert a["last_called"] == "2026-07-23T12:00:00Z"


def test_build_report_flags_unused(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    # One record for a known skill tool; the other known tools are unused.
    path = cfg.workspace_path / cfg.telemetry.tool_usage_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "tool": "vault_write", "conv_id": "c1", "outcome": "success",
        "timestamp": "2026-07-23T10:00:00Z"}) + "\n")
    # Pretend the full known-tool set is exactly these two skill tools.
    monkeypatch.setattr(tool_telemetry, "known_tool_names",
                        lambda c: {"vault_write", "dream_now"})
    report = tool_telemetry.build_report(cfg)
    assert "vault_write" in report
    assert "dream_now" in report  # unused → listed
    assert "unused" in report.lower()
