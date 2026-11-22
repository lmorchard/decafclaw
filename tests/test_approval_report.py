"""Approval telemetry in the audit log and the report counter surface (#1641)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from decafclaw.approval_report import (
    audit_file_paths,
    format_text,
    load_records,
    summarize,
)
from decafclaw.audit_log import make_audit_log_subscriber
from decafclaw.events import EventBus

pytestmark = pytest.mark.asyncio


def _config(tmp_path: Path):
    return type(
        "Config",
        (),
        {
            "workspace_path": tmp_path,
            "audit_log": type(
                "AuditLogConfig",
                (),
                {
                    "enabled": True,
                    "path": "audit.jsonl",
                    "max_size_bytes": 1024 * 1024,
                    "max_backups": 3,
                },
            )(),
        },
    )()


def _decision(tier: str, decision: str = "allow", *, days_ago: float = 0, **extra) -> dict:
    record = {
        "event": "approval_decision",
        "timestamp": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
        "tier": tier,
        "decision": decision,
        "activity": "decafclaw",
        "activity_established": True,
    }
    record.update(extra)
    return record


def _human(approved: bool, *, days_ago: float = 0) -> dict:
    return {
        "event": "approval_human",
        "timestamp": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
        "approved": approved,
        "latency_ms": 1200,
    }


async def test_audit_log_records_approval_events(tmp_path):
    bus = EventBus()
    bus.subscribe(make_audit_log_subscriber(_config(tmp_path)))

    await bus.publish(
        {
            "type": "approval_decision",
            "conv_id": "conv-1",
            "command": "make deploy",
            "cwd": "/workspace/homelab",
            "decision": "prompt",
            "tier": "activity_new",
            "reason": "first `homelab` command in this conversation",
            "activity": "homelab",
            "activity_established": False,
        }
    )
    await bus.publish(
        {
            "type": "approval_human",
            "conv_id": "conv-1",
            "command": "make deploy",
            "approved": True,
            "latency_ms": 800,
            "tier": "activity_new",
            "activity": "homelab",
            "activity_established": False,
        }
    )

    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [record["event"] for record in records] == ["approval_decision", "approval_human"]
    decision, human = records
    assert decision["tier"] == "activity_new"
    assert decision["activity"] == "homelab"
    assert decision["activity_established"] is False
    assert decision["conv_id"] == "conv-1"
    assert human["approved"] is True
    assert human["latency_ms"] == 800


async def test_summarize_counts_prompts_rates_and_tiers():
    records = [
        _decision("aux_allow"),
        _decision("aux_allow"),
        _decision("aux_decline", decision="decline"),
        _decision("read_only"),
        _decision("activity_new", decision="prompt"),
        _human(True),
        _human(True),
        _human(False),
    ]

    summary = summarize(records, days=7)

    assert summary["human_prompts"] == 3
    assert summary["human_approved"] == 2
    assert summary["human_denied"] == 1
    assert summary["human_approval_rate"] == pytest.approx(0.6667)
    assert summary["judge_allowed"] == 2
    assert summary["judge_declined"] == 1
    assert summary["judge_decline_rate"] == pytest.approx(0.3333)
    assert summary["read_only_auto_approved"] == 1
    assert summary["activity_new_prompts"] == 1
    assert summary["by_tier"]["aux_allow"] == 2


async def test_summarize_windows_out_old_records():
    records = [_decision("aux_allow", days_ago=10), _decision("aux_allow", days_ago=1)]
    summary = summarize(records, days=7)
    assert summary["decisions"] == 1


async def test_summarize_ignores_unparseable_timestamps():
    records = [_decision("aux_allow"), {"event": "approval_decision", "timestamp": "not-a-time"}]
    assert summarize(records, days=7)["decisions"] == 1


async def test_load_records_skips_malformed_lines_and_other_events(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(_decision("aux_allow")),
                "{broken json",
                json.dumps({"event": "tool_call", "tool_name": "shell"}),
                json.dumps(_human(True)),
            ]
        )
    )

    records = load_records([path])
    assert [record["event"] for record in records] == ["approval_decision", "approval_human"]


async def test_audit_file_paths_returns_existing_rotation_backups(tmp_path):
    base = tmp_path / "audit.jsonl"
    base.write_text("")
    (tmp_path / "audit.jsonl.1").write_text("")
    paths = audit_file_paths(base, backups=3)
    assert paths == [base, tmp_path / "audit.jsonl.1"]


async def test_format_text_reports_rates():
    summary = summarize([_decision("aux_allow"), _decision("aux_decline"), _human(True), _human(False)], days=7)
    text = format_text(summary, [Path("/tmp/audit.jsonl")])
    assert "human prompts:  2" in text
    assert "50.0%" in text
    assert "aux_allow" in text
