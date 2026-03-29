"""Tests for memory tools — save and recent recall."""

import pytest

from decafclaw.memory import memory_dir
from decafclaw.tools.memory_tools import tool_memory_recent, tool_memory_save


@pytest.mark.asyncio
async def test_memory_save_creates_file(ctx):
    """Saving a memory creates a file in the workspace memories directory."""
    # Default search_strategy is "substring", so no embedding indexing occurs.
    result = await tool_memory_save(
        ctx,
        tags=["test", "unit"],
        content="This is a test memory.",
    )

    assert "Saved memory" in result
    assert "test, unit" in result

    # Verify the file was created on disk
    mem_base = memory_dir(ctx.config)
    md_files = list(mem_base.rglob("*.md"))
    assert len(md_files) == 1
    contents = md_files[0].read_text()
    assert "This is a test memory." in contents


@pytest.mark.asyncio
async def test_memory_recent_returns_entries(ctx):
    """After saving, tool_memory_recent returns the saved entry."""
    await tool_memory_save(
        ctx,
        tags=["recall"],
        content="Remember this fact.",
    )

    result = tool_memory_recent(ctx, n=5)
    assert "Remember this fact." in result
