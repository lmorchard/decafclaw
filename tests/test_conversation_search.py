"""Tests for conversation archive with timestamps."""

from decafclaw.archive import append_message, read_archive


def test_archive_includes_timestamp(config):
    """Messages should get a timestamp added."""
    append_message(config, "test-ts", {"role": "user", "content": "hello"})

    msgs = read_archive(config, "test-ts")
    assert len(msgs) == 1
    assert "timestamp" in msgs[0]
    assert "2026" in msgs[0]["timestamp"]


def test_archive_preserves_existing_timestamp(config):
    """If message already has a timestamp, don't overwrite it."""
    append_message(config, "test-ts", {
        "role": "user",
        "content": "hello",
        "timestamp": "2025-01-01T00:00:00",
    })

    msgs = read_archive(config, "test-ts")
    assert msgs[0]["timestamp"] == "2025-01-01T00:00:00"
