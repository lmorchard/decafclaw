"""Unit tests for decafclaw.scheduled_activity."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from decafclaw.archive import append_message
from decafclaw.scheduled_activity import (
    collect_scheduled_activity,
    extract_activity,
    is_status_token,
    parse_scheduled_conv_id,
)


def test_parse_scheduled_conv_id():
    parsed = parse_scheduled_conv_id("schedule-dream-20261010-030000")
    assert parsed is not None
    name, dt = parsed
    assert name == "dream"
    assert dt.tzinfo is not None

    # Non-scheduled
    assert parse_scheduled_conv_id("conv-12345") is None
    assert parse_scheduled_conv_id("schedule-invalid") is None
    assert parse_scheduled_conv_id("schedule-task-badtimestamp") is None


def test_is_status_token():
    assert is_status_token("HEARTBEAT_OK") is True
    assert is_status_token("OK") is True
    assert is_status_token("DONE") is True
    assert is_status_token("") is True
    assert is_status_token("   ") is True

    assert is_status_token("HEARTBEAT_OK\nConsolidated 2 pages today.") is False
    assert is_status_token("Consolidated 2 pages today.") is False


def test_extract_activity(tmp_path):
    archive_file = tmp_path / "archive.jsonl"
    messages = [
        {"role": "user", "content": "Run task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "vault_write",
                        "arguments": json.dumps({"page": "agent/pages/Topic A", "content": "..."}),
                    }
                },
                {
                    "function": {
                        "name": "vault_write",
                        "arguments": json.dumps({"page": "agent/pages/Topic B", "content": "..."}),
                    }
                },
                {
                    "function": {
                        "name": "vault_write",
                        "arguments": json.dumps({"page": "agent/pages/Topic A", "content": "updated"}),
                    }
                },
            ],
        },
        {"role": "assistant", "content": "Consolidated findings into Topic A and Topic B."},
        {"role": "assistant", "content": "HEARTBEAT_OK"},
    ]
    with archive_file.open("w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")

    final, touched = extract_activity(archive_file)
    # Status token HEARTBEAT_OK should be skipped in favor of the narrative message
    assert final == "Consolidated findings into Topic A and Topic B."
    # Pages should be unique and preserve order of first appearance
    assert touched == ["agent/pages/Topic A", "agent/pages/Topic B"]


def test_collect_scheduled_activity(config, monkeypatch):
    # Set up fixed reference time
    ref_time = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    # 1. Recent dream run (2 hours ago)
    t_dream_recent = ref_time - timedelta(hours=2)
    conv_dream_recent = f"schedule-dream-{t_dream_recent.strftime('%Y%m%d-%H%M%S')}"
    append_message(config, conv_dream_recent, {"role": "user", "content": "Dream pass"})
    append_message(
        config,
        conv_dream_recent,
        {
            "role": "assistant",
            "content": "Dreamed of electric sheep",
            "tool_calls": [
                {
                    "function": {
                        "name": "vault_write",
                        "arguments": json.dumps({"page": "agent/pages/Sheep", "content": "..."}),
                    }
                }
            ],
        },
    )

    # 2. Older dream run (10 days ago = 240 hours ago)
    t_dream_old = ref_time - timedelta(days=10)
    conv_dream_old = f"schedule-dream-{t_dream_old.strftime('%Y%m%d-%H%M%S')}"
    append_message(config, conv_dream_old, {"role": "user", "content": "Old dream"})
    append_message(
        config,
        conv_dream_old,
        {
            "role": "assistant",
            "content": "Old dream summary",
            "tool_calls": [
                {
                    "function": {
                        "name": "vault_write",
                        "arguments": json.dumps({"page": "agent/pages/OldPage", "content": "..."}),
                    }
                }
            ],
        },
    )

    # 3. Recent garden run (3 hours ago)
    t_garden = ref_time - timedelta(hours=3)
    conv_garden = f"schedule-garden-{t_garden.strftime('%Y%m%d-%H%M%S')}"
    append_message(config, conv_garden, {"role": "user", "content": "Garden pass"})
    append_message(config, conv_garden, {"role": "assistant", "content": "Tended garden"})

    # 4. Non-scheduled conversation
    append_message(config, "regular-chat", {"role": "user", "content": "Hello"})
    append_message(config, "regular-chat", {"role": "assistant", "content": "Hi"})

    # Test filtering by skill_name="dream" with hours=168 (7 days)
    # Monkeypatch datetime.now in scheduled_activity
    class MockDatetime:
        @classmethod
        def now(cls, tz=None):
            return ref_time

        @classmethod
        def strptime(cls, date_string, format):
            return datetime.strptime(date_string, format)

        @classmethod
        def fromtimestamp(cls, ts, tz=None):
            return datetime.fromtimestamp(ts, tz)

    monkeypatch.setattr("decafclaw.scheduled_activity.datetime", MockDatetime)

    dream_runs = collect_scheduled_activity(config, hours=7 * 24, skill_name="dream")
    assert len(dream_runs) == 1
    run = dream_runs[0]
    assert run["skill_name"] == "dream"
    assert run["conv_id"] == conv_dream_recent
    assert run["final_message"] == "Dreamed of electric sheep"
    assert run["vault_pages_touched"] == ["agent/pages/Sheep"]

    # Test dream with wide window (20 days)
    all_dream_runs = collect_scheduled_activity(config, hours=20 * 24, skill_name="dream")
    assert len(all_dream_runs) == 2
    conv_ids = [r["conv_id"] for r in all_dream_runs]
    assert conv_dream_recent in conv_ids
    assert conv_dream_old in conv_ids

    # Test exclude_skill_name="garden"
    no_garden = collect_scheduled_activity(config, hours=24, exclude_skill_name="garden")
    assert len(no_garden) == 1
    assert no_garden[0]["skill_name"] == "dream"

    # Test all scheduled skills within 24 hours
    all_recent = collect_scheduled_activity(config, hours=24)
    assert len(all_recent) == 2
    skill_names = {r["skill_name"] for r in all_recent}
    assert skill_names == {"dream", "garden"}
