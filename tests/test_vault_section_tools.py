"""Tests for vault_show_sections and vault_move_lines tools."""

import pytest

from decafclaw.skills.vault.tools import (
    tool_vault_move_lines,
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


@pytest.mark.asyncio
async def test_move_lines_basic(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True, exist_ok=True)
    (agent_pages / "src.md").write_text(
        "# Top\n\n- [ ] task1\n- [ ] task2\n- [ ] task3\n"
    )
    (agent_pages / "dst.md").write_text("# Today\n\n## inbox\n")
    result = await tool_vault_move_lines(
        vault_ctx,
        from_page="agent/pages/src",
        to_page="agent/pages/dst",
        lines="3,4",
        to_section="today/inbox",
    )
    assert "[error" not in result.text.lower()
    src_after = (agent_pages / "src.md").read_text()
    dst_after = (agent_pages / "dst.md").read_text()
    assert "task1" not in src_after
    assert "task2" not in src_after
    assert "task3" in src_after
    assert "task1" in dst_after
    assert "task2" in dst_after


@pytest.mark.asyncio
async def test_move_lines_refuses_write_outside_agent(vault_ctx):
    vault = vault_ctx.config.vault_root
    (vault / "agent" / "pages").mkdir(parents=True, exist_ok=True)
    (vault / "user_notes").mkdir()
    (vault / "agent" / "pages" / "src.md").write_text(
        "# Top\n\n- [ ] x\n"
    )
    (vault / "user_notes" / "dst.md").write_text("# User\n")
    # Writing into a user page must be refused
    result = await tool_vault_move_lines(
        vault_ctx,
        from_page="agent/pages/src",
        to_page="user_notes/dst",
        lines="3",
    )
    assert "[error" in result.text.lower()
    # user_notes/dst.md must be unchanged
    assert (vault / "user_notes" / "dst.md").read_text() == "# User\n"
