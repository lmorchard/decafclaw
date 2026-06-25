"""Tests for tool_conversation_search over the dir sidecar layout."""

import json

from decafclaw.conversation_paths import conversations_root
from decafclaw.tools.conversation_tools import tool_conversation_search


def _write_dir(config, conv_id: str, messages: list[dict]) -> None:
    d = conversations_root(config) / conv_id
    d.mkdir(parents=True, exist_ok=True)
    with (d / "archive.jsonl").open("w") as fh:
        for m in messages:
            fh.write(json.dumps(m) + "\n")


def test_search_no_history(ctx):
    out = tool_conversation_search(ctx, "anything")
    assert "No conversation history found" in out


def test_search_finds_match_in_dir_layout(ctx):
    _write_dir(ctx.config, "conv-dir", [
        {"role": "user", "content": "tell me about pelican migration"},
    ])
    out = tool_conversation_search(ctx, "pelican")
    assert "conv-dir" in out
    assert "pelican migration" in out


def test_search_finds_across_multiple_conversations(ctx):
    _write_dir(ctx.config, "conv-one", [
        {"role": "user", "content": "osprey sighting"},
    ])
    _write_dir(ctx.config, "conv-two", [
        {"role": "user", "content": "osprey nesting"},
    ])
    out = tool_conversation_search(ctx, "osprey")
    assert "conv-one" in out
    assert "conv-two" in out
