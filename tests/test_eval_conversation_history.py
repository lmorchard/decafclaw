"""Unit tests for `_seed_conversation_history` — archive + in-memory seeding."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from decafclaw.eval.runner import _seed_conversation_history


def _config_pointing_at(tmp_path: Path):
    """Minimal config stub with the one attr the seeder reads."""
    class _Cfg:
        workspace_path = tmp_path
    return _Cfg()


def test_empty_or_missing_returns_empty(tmp_path: Path):
    cfg = _config_pointing_at(tmp_path)
    assert _seed_conversation_history(cfg, {}) == []
    assert _seed_conversation_history(cfg, {"setup": {}}) == []
    assert _seed_conversation_history(cfg, {"setup": {"conversation_history": []}}) == []


def test_writes_archive_jsonl_at_eval_conv_path(tmp_path: Path):
    cfg = _config_pointing_at(tmp_path)
    test_case = {
        "setup": {
            "conversation_history": [
                {"role": "user", "content": "What's the project name?"},
                {"role": "assistant", "content": "DecafClaw."},
            ]
        }
    }
    out = _seed_conversation_history(cfg, test_case)
    archive = tmp_path / "conversations" / "eval.jsonl"
    assert archive.exists()
    lines = archive.read_text().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["role"] == "user"
    assert parsed[0]["content"] == "What's the project name?"
    assert parsed[1]["role"] == "assistant"
    # Returned list matches what was written
    assert out == parsed


def test_stamps_timestamps_when_missing(tmp_path: Path):
    cfg = _config_pointing_at(tmp_path)
    test_case = {
        "setup": {"conversation_history": [{"role": "user", "content": "hi"}]}
    }
    out = _seed_conversation_history(cfg, test_case)
    assert "timestamp" in out[0]
    archived = json.loads(
        (tmp_path / "conversations" / "eval.jsonl").read_text().strip()
    )
    assert archived["timestamp"] == out[0]["timestamp"]


def test_preserves_explicit_timestamps(tmp_path: Path):
    cfg = _config_pointing_at(tmp_path)
    test_case = {
        "setup": {
            "conversation_history": [
                {"role": "user", "content": "old", "timestamp": "2024-01-01T00:00:00"}
            ]
        }
    }
    out = _seed_conversation_history(cfg, test_case)
    assert out[0]["timestamp"] == "2024-01-01T00:00:00"


def test_rejects_non_dict_entry(tmp_path: Path):
    cfg = _config_pointing_at(tmp_path)
    test_case = {"setup": {"conversation_history": ["not a dict"]}}
    with pytest.raises(ValueError, match="role"):
        _seed_conversation_history(cfg, test_case)


def test_rejects_missing_role(tmp_path: Path):
    cfg = _config_pointing_at(tmp_path)
    test_case = {
        "setup": {"conversation_history": [{"content": "no role here"}]}
    }
    with pytest.raises(ValueError, match="role"):
        _seed_conversation_history(cfg, test_case)


def test_passes_through_tool_call_messages(tmp_path: Path):
    """Tool messages (role: tool) and assistant tool_calls must round-trip."""
    cfg = _config_pointing_at(tmp_path)
    test_case = {
        "setup": {
            "conversation_history": [
                {"role": "user", "content": "search the vault"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"name": "vault_search", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "no results"},
            ]
        }
    }
    out = _seed_conversation_history(cfg, test_case)
    archive = tmp_path / "conversations" / "eval.jsonl"
    parsed = [json.loads(line) for line in archive.read_text().splitlines()]
    assert parsed[1]["tool_calls"][0]["function"]["name"] == "vault_search"
    assert parsed[2]["tool_call_id"] == "call_1"
    assert len(out) == 3


def test_round_trips_through_archive_reader(tmp_path: Path):
    """The seeded archive should be readable via the public archive API."""
    from decafclaw.config import Config

    cfg = replace(Config(), agent=replace(Config().agent, data_home=str(tmp_path)))
    # workspace_path on Config is a computed property; agent.data_home drives it
    test_case = {
        "setup": {
            "conversation_history": [
                {"role": "user", "content": "seeded by test"},
                {"role": "assistant", "content": "got it"},
            ]
        }
    }
    _seed_conversation_history(cfg, test_case)

    from decafclaw.archive import read_archive
    rows = read_archive(cfg, "eval")
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert rows[1]["content"] == "got it"
