"""Tests for bare mentions parsing, context injection, and truncation."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.context_composer import ContextComposer
from decafclaw.context_types import ComposerMode
from decafclaw.mcp_client import ConnState
from decafclaw.memory_context import parse_bare_mentions


async def _mention_refs(context, config, message, history, mode):
    """Run MentionsExtension and return its legacy (messages, entry) shape."""
    from decafclaw.extensions.mentions import MentionsExtension

    context.composer.mode = mode
    contribution = await MentionsExtension({}, config)._contribute(context, message, history)
    if contribution is None:
        return [], None
    return contribution.messages, contribution.source_entry


def test_parse_bare_mentions():
    """Verify that bare @ mentions are parsed correctly, ignoring @[[PageName]]."""
    text = "Please check @src/agent.py and @mcp/demo/resource, but NOT @[[TestPage]]."
    mentions = parse_bare_mentions(text)

    assert len(mentions) == 2

    # First match
    assert mentions[0]["type"] == "file"
    assert mentions[0]["path"] == "src/agent.py"

    # Second match
    assert mentions[1]["type"] == "mcp"
    assert mentions[1]["server"] == "demo"
    assert mentions[1]["resource"] == "resource"
    assert mentions[1]["raw"] == "mcp/demo/resource"


@pytest.mark.asyncio
async def test_compose_mentions_references_file(ctx, config, tmp_path):
    """Verify workspace files mentioned with @ are resolved and injected."""
    ContextComposer()
    config.agent.data_home = str(tmp_path / "data")
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)

    # 1. Create a small file
    small_file = config.workspace_path / "small.txt"
    small_file.write_text("Hello World", encoding="utf-8")

    # 2. Create a large file > 8KB (8192 chars)
    large_file = config.workspace_path / "large.txt"
    large_content = "A" * 9000
    large_file.write_text(large_content, encoding="utf-8")

    # 3. Create a secret file
    secret_file = config.workspace_path / "secret.key"
    secret_file.write_text("my-secret-key", encoding="utf-8")

    # Verify small file injection
    messages, entry = await _mention_refs(ctx, config, "Read @small.txt please", [], ComposerMode.INTERACTIVE)
    assert len(messages) == 1
    assert messages[0]["role"] == "workspace_references"
    assert "Hello World" in messages[0]["content"]
    assert "small.txt" == messages[0]["workspace_file"]
    assert entry.items_included == 1
    assert entry.items_truncated == 0

    # Verify large file injection and truncation
    messages_large, entry_large = await _mention_refs(
        ctx, config, "Read @large.txt please", [], ComposerMode.INTERACTIVE
    )
    assert len(messages_large) == 1
    assert messages_large[0]["role"] == "workspace_references"
    assert len(messages_large[0]["content"]) < 9000
    assert "[Truncated: only first 8KB of large.txt inlined]" in messages_large[0]["content"]

    # Verify secret file exclusion
    messages_secret, entry_secret = await _mention_refs(
        ctx, config, "Read @secret.key please", [], ComposerMode.INTERACTIVE
    )
    assert len(messages_secret) == 1
    assert "is a secret and cannot be inlined" in messages_secret[0]["content"]


@pytest.mark.asyncio
async def test_compose_mentions_references_mcp(ctx, config, monkeypatch):
    """Verify active MCP resources mentioned with @ are resolved and injected."""
    ContextComposer()

    # Mock MCP Registry
    mock_registry = MagicMock()
    mock_state = MagicMock()
    mock_state.status = ConnState.CONNECTED
    mock_state.config.timeout = 5000

    class MockResource:
        def __init__(self, name, uri):
            self.name = name
            self.uri = uri

    mock_res = MockResource("notes", "demo://notes")
    mock_state.resources = [mock_res]

    # Mock session and read_resource
    mock_session = AsyncMock()

    class MockResult:
        def __init__(self, contents):
            self.contents = contents

    class MockContent:
        def __init__(self, text):
            self.text = text
            self.blob = None
            self.uri = "demo://notes"
            self.mimeType = "text/plain"

    mock_result = MockResult([MockContent("MCP Notes Content")])
    mock_session.read_resource.return_value = mock_result
    mock_state.session = mock_session

    mock_registry.servers = {"demo_server": mock_state}
    monkeypatch.setattr("decafclaw.mcp_client.get_registry", lambda: mock_registry)

    # Verify MCP resource injection
    messages, entry = await _mention_refs(ctx, config, "Check @mcp/demo_server/notes", [], ComposerMode.INTERACTIVE)
    assert len(messages) == 1
    assert messages[0]["role"] == "mcp_references"
    assert "MCP Notes Content" in messages[0]["content"]
    assert messages[0]["mcp_resource"] == "mcp/demo_server/notes"


@pytest.mark.asyncio
async def test_compose_mentions_references_dedup(ctx, config, tmp_path):
    """Verify that already injected mentions are skipped."""
    ContextComposer()
    config.agent.data_home = str(tmp_path / "data")
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)

    small_file = config.workspace_path / "small.txt"
    small_file.write_text("Hello World", encoding="utf-8")

    # History already contains this workspace_references
    history = [
        {
            "role": "workspace_references",
            "content": "[Referenced workspace file: small.txt]\n\nHello World",
            "workspace_file": "small.txt",
        }
    ]

    messages, entry = await _mention_refs(ctx, config, "Read @small.txt please", history, ComposerMode.INTERACTIVE)

    # Should be skipped
    assert len(messages) == 0
    assert entry is not None
    assert entry.items_truncated == 1
    assert entry.items_included == 0


@pytest.mark.asyncio
async def test_mentions_charged_to_fixed_tokens_budget(ctx, config, tmp_path):
    """Mention content is a fixed cost, so it shrinks the dynamic budget (#1365)."""
    config.agent.tool_context_budget_pct = 1.0
    config.compaction.max_tokens = 100_000
    config.vault_retrieval.max_tokens = 100_000
    config.agent.data_home = str(tmp_path / "data")
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    (config.workspace_path / "big.txt").write_text("A" * 4000, encoding="utf-8")

    with (
        patch("decafclaw.context_tools.collect_all_tool_defs", return_value=[]),
        patch(
            "decafclaw.extensions.vault_retrieval.retrieve_memory_context",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        composed = await ContextComposer().compose(ctx, "Read @big.txt please", [], mode=ComposerMode.INTERACTIVE)

    entries = {entry.source: entry for entry in composed.sources}
    assert entries["mentions"].tokens_estimated > 0
    # Fixed cost is every entry except the dynamic pool ("memory"). The budget
    # must reserve the mention tokens, not only system/tools/user/history.
    fixed = sum(entry.tokens_estimated for source, entry in entries.items() if source != "memory")
    assert ctx.composer.dynamic_token_budget == 100_000 - fixed - 4096
