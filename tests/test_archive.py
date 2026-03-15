"""Tests for conversation archive."""

from decafclaw.archive import append_message, archive_path, read_archive


def test_archive_path(config):
    path = archive_path(config, "conv-123")
    assert "test-agent" in str(path)
    assert "conv-123.jsonl" in str(path)


def test_append_and_read(config):
    conv_id = "test-roundtrip"
    append_message(config, conv_id, {"role": "user", "content": "hello"})
    append_message(config, conv_id, {"role": "assistant", "content": "hi"})

    msgs = read_archive(config, conv_id)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hello"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "hi"


def test_read_empty_archive(config):
    msgs = read_archive(config, "nonexistent")
    assert msgs == []


def test_append_preserves_tool_calls(config):
    conv_id = "test-tools"
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "tc1", "function": {"name": "shell", "arguments": '{"command":"ls"}'}}],
    }
    append_message(config, conv_id, msg)

    msgs = read_archive(config, conv_id)
    assert len(msgs) == 1
    assert msgs[0]["tool_calls"][0]["function"]["name"] == "shell"


def test_multiple_conversations(config):
    append_message(config, "conv-a", {"role": "user", "content": "a"})
    append_message(config, "conv-b", {"role": "user", "content": "b"})

    assert len(read_archive(config, "conv-a")) == 1
    assert len(read_archive(config, "conv-b")) == 1
    assert read_archive(config, "conv-a")[0]["content"] == "a"
    assert read_archive(config, "conv-b")[0]["content"] == "b"
