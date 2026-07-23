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


# --- #535: token-aware matching (plurality / inflection) ---

def test_search_matches_across_plural_inflection(ctx):
    """The issue's headline repro: singular seed, plural query."""
    _write_dir(ctx.config, "conv-embed", [
        {"role": "user",
         "content": "I'm planning to switch our embedding provider "
                    "from OpenAI to Vertex"},
    ])
    out = tool_conversation_search(ctx, "embedding providers")
    assert "conv-embed" in out


def test_search_matches_ignoring_stopwords_and_plurals(ctx):
    """The acceptance-criteria repro: 'colors I like' -> 'color ... blue'."""
    _write_dir(ctx.config, "conv-color", [
        {"role": "user", "content": "My favorite color is blue"},
    ])
    out = tool_conversation_search(ctx, "colors I like")
    assert "conv-color" in out
    assert "favorite color is blue" in out


def test_search_preserves_midword_substring_match(ctx):
    """Zero regression: a mid-word substring query still matches. Token
    stemming alone would miss 'config' -> 'configuration'; the substring
    branch must keep it working."""
    _write_dir(ctx.config, "conv-cfg", [
        {"role": "user", "content": "the configuration was wrong"},
    ])
    out = tool_conversation_search(ctx, "config")
    assert "conv-cfg" in out


def test_search_ranks_higher_overlap_first(ctx):
    """A message overlapping more query tokens should surface before one
    overlapping fewer."""
    _write_dir(ctx.config, "conv-weak", [
        {"role": "user", "content": "we discussed the embedding format"},
    ])
    _write_dir(ctx.config, "conv-strong", [
        {"role": "user", "content": "we should switch embedding providers soon"},
    ])
    out = tool_conversation_search(ctx, "switch embedding providers")
    assert "conv-strong" in out
    assert "conv-weak" in out
    assert out.index("conv-strong") < out.index("conv-weak")


def test_search_no_match_when_no_tokens_overlap(ctx):
    _write_dir(ctx.config, "conv-x", [
        {"role": "user", "content": "the weather is sunny today"},
    ])
    out = tool_conversation_search(ctx, "quantum chromodynamics")
    assert "No conversation history found" in out
