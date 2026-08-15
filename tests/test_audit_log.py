import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest

from decafclaw.events import EventBus
from decafclaw.audit_log import make_audit_log_subscriber, AuditLogSubscriber

pytestmark = pytest.mark.asyncio


async def test_audit_log_records_llm_call(tmp_path):
    config = type("Config", (), {
        "workspace_path": tmp_path,
        "audit_log": type("AuditLogConfig", (), {
            "enabled": True,
            "path": "audit.jsonl",
            "max_size_bytes": 1024 * 1024,
            "max_backups": 3,
        })()
    })()

    bus = EventBus()
    subscriber = make_audit_log_subscriber(config)
    bus.subscribe(subscriber)

    await bus.publish({
        "type": "llm_end",
        "iteration": 1,
        "model": "gemini-test",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
        },
        "duration_ms": 1234,
        "streaming": True,
    })

    await asyncio.sleep(0.01)

    log_path = tmp_path / "audit.jsonl"
    assert log_path.exists()
    content = log_path.read_text().strip()
    record = json.loads(content)
    assert record["event"] == "llm_call"
    assert record["model"] == "gemini-test"
    assert record["prompt_tokens"] == 100
    assert record["completion_tokens"] == 50
    assert record["duration_ms"] == 1234
    assert record["streaming"] is True


async def test_audit_log_records_tool_call(tmp_path):
    config = type("Config", (), {
        "workspace_path": tmp_path,
        "audit_log": type("AuditLogConfig", (), {
            "enabled": True,
            "path": "audit.jsonl",
            "max_size_bytes": 1024 * 1024,
            "max_backups": 3,
        })()
    })()

    bus = EventBus()
    subscriber = make_audit_log_subscriber(config)
    bus.subscribe(subscriber)

    await bus.publish({
        "type": "tool_end",
        "tool": "test_tool",
        "args": {"arg1": "val1"},
        "duration_ms": 42,
        "result_text": "success output",
        "input_bytes": 10,
    })

    await asyncio.sleep(0.01)

    log_path = tmp_path / "audit.jsonl"
    assert log_path.exists()
    content = log_path.read_text().strip()
    record = json.loads(content)
    assert record["event"] == "tool_call"
    assert record["tool_name"] == "test_tool"
    assert "args" in record
    assert record["args"] == '{"arg1": "val1"}'
    assert record["result_length"] == len("success output")
    assert record["duration_ms"] == 42
    assert record["outcome"] == "success"


async def test_audit_log_records_skill_and_mcp(tmp_path):
    config = type("Config", (), {
        "workspace_path": tmp_path,
        "audit_log": type("AuditLogConfig", (), {
            "enabled": True,
            "path": "audit.jsonl",
            "max_size_bytes": 1024 * 1024,
            "max_backups": 3,
        })()
    })()

    bus = EventBus()
    subscriber = make_audit_log_subscriber(config)
    bus.subscribe(subscriber)

    await bus.publish({
        "type": "skill_activated",
        "skill": "test_skill",
    })

    await bus.publish({
        "type": "mcp_server_connected",
        "server": "test_mcp_server",
    })

    await asyncio.sleep(0.01)

    log_path = tmp_path / "audit.jsonl"
    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 2
    rec1 = json.loads(lines[0])
    assert rec1["event"] == "skill_activated"
    assert rec1["identifier"] == "test_skill"
    
    rec2 = json.loads(lines[1])
    assert rec2["event"] == "mcp_server_connected"
    assert rec2["identifier"] == "test_mcp_server"


async def test_audit_log_rotation(tmp_path):
    config = type("Config", (), {
        "workspace_path": tmp_path,
        "audit_log": type("AuditLogConfig", (), {
            "enabled": True,
            "path": "audit.jsonl",
            "max_size_bytes": 100, # Very small to force rotation
            "max_backups": 2,
        })()
    })()

    subscriber = AuditLogSubscriber(config)
    
    # Write entries that exceed 100 bytes
    for i in range(5):
        subscriber.append_record({
            "event": "test_event",
            "index": i,
            "data": "x" * 50
        })
    
    log_path = tmp_path / "audit.jsonl"
    assert log_path.exists()
    
    # Check that rotation occurred
    assert (tmp_path / "audit.jsonl.1").exists()
    
    # Content of the latest log file should only be the last one or two events
    content = log_path.read_text().strip()
    assert "x" * 50 in content
