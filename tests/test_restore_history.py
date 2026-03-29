"""Tests for archive.restore_history and its helpers."""

import json

from decafclaw.archive import restore_history

CONV_ID = "test-conv-123"


def _write_jsonl(path, messages):
    """Write a list of dicts as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


def _conv_dir(config):
    return config.workspace_path / "conversations"


def test_restore_history_no_archive(config):
    """Returns None when no archive or compacted files exist."""
    result = restore_history(config, CONV_ID)
    assert result is None


def test_restore_history_archive_only(config):
    """Returns full archive messages when no compacted sidecar exists."""
    messages = [
        {"role": "user", "content": "hello", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "hi", "timestamp": "2026-01-01T00:00:01"},
    ]
    _write_jsonl(_conv_dir(config) / f"{CONV_ID}.jsonl", messages)

    result = restore_history(config, CONV_ID)
    assert result == messages


def test_restore_history_compacted_only(config):
    """Returns compacted history when sidecar exists, even if archive also exists."""
    archive_msgs = [
        {"role": "user", "content": "old msg", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "old reply", "timestamp": "2026-01-01T00:00:01"},
    ]
    compacted_msgs = [
        {"role": "assistant", "content": "summary of conversation", "timestamp": "2026-01-01T00:00:01"},
    ]
    _write_jsonl(_conv_dir(config) / f"{CONV_ID}.jsonl", archive_msgs)
    _write_jsonl(_conv_dir(config) / f"{CONV_ID}.compacted.jsonl", compacted_msgs)

    result = restore_history(config, CONV_ID)
    # Should return only the compacted messages (no newer archive entries)
    assert result == compacted_msgs


def test_restore_history_compacted_plus_newer(config):
    """Returns compacted messages plus archive entries newer than the last compacted timestamp."""
    compacted_msgs = [
        {"role": "assistant", "content": "summary", "timestamp": "2026-01-01T00:00:05"},
    ]
    archive_msgs = [
        {"role": "user", "content": "old msg", "timestamp": "2026-01-01T00:00:00"},
        {"role": "assistant", "content": "old reply", "timestamp": "2026-01-01T00:00:03"},
        {"role": "user", "content": "new msg", "timestamp": "2026-01-01T00:00:06"},
        {"role": "assistant", "content": "new reply", "timestamp": "2026-01-01T00:00:07"},
    ]
    _write_jsonl(_conv_dir(config) / f"{CONV_ID}.jsonl", archive_msgs)
    _write_jsonl(_conv_dir(config) / f"{CONV_ID}.compacted.jsonl", compacted_msgs)

    result = restore_history(config, CONV_ID)
    assert result == [
        {"role": "assistant", "content": "summary", "timestamp": "2026-01-01T00:00:05"},
        {"role": "user", "content": "new msg", "timestamp": "2026-01-01T00:00:06"},
        {"role": "assistant", "content": "new reply", "timestamp": "2026-01-01T00:00:07"},
    ]


def test_restore_history_empty_archive(config):
    """Returns None when archive file exists but is empty."""
    path = _conv_dir(config) / f"{CONV_ID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")

    result = restore_history(config, CONV_ID)
    assert result is None
