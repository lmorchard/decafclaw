"""Tests for vault_show_sections tool."""

import pytest

from decafclaw.skills.vault.tools import (
    tool_vault_show_sections,
)

NOTE_TEXT = "# Top\n\n## Sub A\n\ncontent a\n\n## Sub B\n\ncontent b\n"


@pytest.fixture
def vault_ctx(ctx):
    """A Context with the vault root directory created."""
    ctx.config.vault_root.mkdir(parents=True, exist_ok=True)
    return ctx


def _write_note(vault_ctx):
    """Write the standard test note and return its path."""
    note_dir = vault_ctx.config.vault_root / "agent" / "pages"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / "note.md"
    note_path.write_text(NOTE_TEXT)
    return note_path


@pytest.mark.asyncio
async def test_show_sections_outline(vault_ctx):
    _write_note(vault_ctx)
    result = await tool_vault_show_sections(vault_ctx, page="agent/pages/note")
    assert "# Top" in result.text
    assert "## Sub A" in result.text
    assert "## Sub B" in result.text
    # Line numbers present (1-based)
    assert "1:" in result.text


@pytest.mark.asyncio
async def test_show_sections_specific(vault_ctx):
    _write_note(vault_ctx)
    result = await tool_vault_show_sections(
        vault_ctx, page="agent/pages/note", section="top/sub a"
    )
    assert "content a" in result.text
    assert "content b" not in result.text


@pytest.mark.asyncio
async def test_show_sections_missing_page(vault_ctx):
    result = await tool_vault_show_sections(vault_ctx, page="agent/pages/missing")
    assert "[error" in result.text.lower() or "not found" in result.text.lower()
