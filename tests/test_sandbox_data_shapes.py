"""Assert `.data` shape on every allowlisted sandbox tool.

The code_execution sandbox exposes 11 tools to LLM-authored scripts via
the `dc.<tool>(...)` proxy. Each call returns a ToolResultProxy with
`.text` / `.data` / `.error`. The proxy's `.data` mirrors the underlying
tool's `ToolResult.data`. This file pins down the contract — what fields
appear on `.data` for each tool — so future tool edits don't silently
drop or rename fields that scripts depend on.

The 11 allowlisted tools (SANDBOX_ALLOWED_TOOLS in
src/decafclaw/skills/code_execution/tools.py):

  vault_read, vault_search, vault_journal_append, vault_write,
  workspace_read, workspace_list,
  notes_read, notes_append,
  tabstack_extract_markdown, tabstack_extract_json, tabstack_research.

tabstack_* are network-dependent and not exercised here; the other 8
have local-only happy paths we can drive with the standard `ctx`
fixture.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.media import ToolResult

# ---------------------------------------------------------------------------
# vault_*
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_dir(config):
    """Create vault root + agent pages dir; return the agent pages dir."""
    pages = config.vault_agent_pages_dir
    pages.mkdir(parents=True, exist_ok=True)
    return pages


@pytest.mark.asyncio
async def test_vault_read_data_shape(ctx, vault_dir):
    from decafclaw.skills.vault.tools import tool_vault_read
    page = vault_dir / "Sample.md"
    page.write_text(
        "---\ntags: [a, b]\nsummary: hi\n---\nbody line 1\nbody line 2\n"
    )
    result = await tool_vault_read(ctx, "agent/pages/Sample")
    assert isinstance(result, ToolResult)
    assert result.data is not None
    assert result.data["path"] == "agent/pages/Sample"
    assert result.data["frontmatter"]["summary"] == "hi"
    assert result.data["body_size"] > 0
    assert result.data["body_lines"] >= 2


@pytest.mark.asyncio
async def test_vault_write_data_shape(ctx, vault_dir):
    from decafclaw.skills.vault.tools import tool_vault_write
    with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
        result = await tool_vault_write(
            ctx, "agent/pages/New", "# Hello\n\nWorld."
        )
    assert isinstance(result, ToolResult)
    assert result.data is not None
    assert result.data["path"] == "agent/pages/New"
    assert result.data["created"] is True
    assert result.data["bytes_written"] > 0


@pytest.mark.asyncio
async def test_vault_write_data_shape_overwrite(ctx, vault_dir):
    """Second write to same page should report created=False."""
    from decafclaw.skills.vault.tools import tool_vault_write
    (vault_dir / "Existing.md").write_text("# Old")
    with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
        result = await tool_vault_write(
            ctx, "agent/pages/Existing", "# New"
        )
    assert result.data["created"] is False


@pytest.mark.asyncio
async def test_vault_journal_append_data_shape(ctx, config):
    """Drives the real append path without patching the file writer; only
    the embedding index call is mocked since it's network-adjacent."""
    from decafclaw.skills.vault.tools import tool_vault_journal_append
    config.vault_agent_journal_dir.mkdir(parents=True, exist_ok=True)
    with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
        result = await tool_vault_journal_append(
            ctx, tags=["alpha", "beta"], content="hello journal",
        )
    assert isinstance(result, ToolResult)
    assert result.data is not None
    assert result.data["tags"] == ["alpha", "beta"]
    assert result.data["entry_size"] > 0
    # Path is vault-relative; should end with today's date file.
    assert result.data["path"].endswith(".md")


# ---------------------------------------------------------------------------
# workspace_*
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_read_data_shape(ctx):
    from decafclaw.tools.workspace_tools import (
        tool_workspace_read,
        tool_workspace_write,
    )
    tool_workspace_write(ctx, "f.txt", "line1\nline2\nline3")
    result = tool_workspace_read(ctx, "f.txt")
    assert isinstance(result, ToolResult)
    assert result.data is not None
    assert result.data["path"] == "f.txt"
    assert result.data["lines"] == 3
    assert result.data["range"] == [1, 3]
    assert result.data["truncated"] is False
    assert result.data["size"] > 0


@pytest.mark.asyncio
async def test_workspace_read_partial_data_shape(ctx):
    from decafclaw.tools.workspace_tools import (
        tool_workspace_read,
        tool_workspace_write,
    )
    tool_workspace_write(ctx, "f.txt", "a\nb\nc\nd\ne")
    result = tool_workspace_read(ctx, "f.txt", start_line=2, end_line=4)
    assert result.data["range"] == [2, 4]
    assert result.data["truncated"] is False
    assert result.data["lines"] == 5  # total file lines, not selected


@pytest.mark.asyncio
async def test_workspace_list_data_shape(ctx):
    from decafclaw.tools.workspace_tools import (
        tool_workspace_list,
        tool_workspace_write,
    )
    tool_workspace_write(ctx, "a.txt", "aaa")
    tool_workspace_write(ctx, "sub/b.txt", "bbb")
    result = tool_workspace_list(ctx, ".")
    assert isinstance(result, ToolResult)
    assert result.data is not None
    assert result.data["path"] == "."
    entries_by_name = {e["name"]: e for e in result.data["entries"]}
    assert "a.txt" in entries_by_name
    assert entries_by_name["a.txt"]["is_dir"] is False
    assert entries_by_name["a.txt"]["size"] == 3
    assert "sub" in entries_by_name
    assert entries_by_name["sub"]["is_dir"] is True
    assert entries_by_name["sub"]["size"] is None


# ---------------------------------------------------------------------------
# notes_*
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_append_data_shape(ctx):
    from decafclaw.tools.notes_tools import tool_notes_append
    result = tool_notes_append(ctx, "first note")
    assert isinstance(result, ToolResult)
    assert result.data is not None
    assert result.data["chars"] == len("first note")
    assert "timestamp" in result.data


@pytest.mark.asyncio
async def test_notes_read_data_shape(ctx):
    from decafclaw.tools.notes_tools import (
        tool_notes_append,
        tool_notes_read,
    )
    tool_notes_append(ctx, "alpha")
    tool_notes_append(ctx, "beta")
    result = tool_notes_read(ctx)
    assert isinstance(result, ToolResult)
    assert result.data is not None
    assert result.data["count"] == 2
    assert len(result.data["notes"]) == 2
    texts = [n["text"] for n in result.data["notes"]]
    assert "alpha" in texts
    assert "beta" in texts
    for n in result.data["notes"]:
        assert "timestamp" in n


@pytest.mark.asyncio
async def test_notes_read_empty_data_shape(ctx):
    from decafclaw.tools.notes_tools import tool_notes_read
    result = tool_notes_read(ctx)
    assert result.data == {"count": 0, "notes": []}
