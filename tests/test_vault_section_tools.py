"""Tests for vault_show_sections, vault_move_lines, and vault_section tools."""

import pytest

from decafclaw.skills.vault.tools import (
    tool_vault_move_lines,
    tool_vault_section,
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
    # Source must also be unchanged when target write is refused
    assert (vault / "agent" / "pages" / "src.md").read_text() == "# Top\n\n- [ ] x\n"


@pytest.mark.asyncio
async def test_move_lines_leaves_source_untouched_when_insert_fails(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    src_text = "# Top\n\n- [ ] task1\n- [ ] task2\n"
    dst_text = "# Target\n\n## known\n"
    (agent_pages / "src.md").write_text(src_text)
    (agent_pages / "dst.md").write_text(dst_text)
    result = await tool_vault_move_lines(
        vault_ctx,
        from_page="agent/pages/src",
        to_page="agent/pages/dst",
        lines="3,4",
        to_section="nonexistent/section/path",
    )
    assert "[error" in result.text.lower()
    # Both files must be unchanged
    assert (agent_pages / "src.md").read_text() == src_text
    assert (agent_pages / "dst.md").read_text() == dst_text


@pytest.mark.asyncio
async def test_section_add(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text("# Top\n\n## First\n")
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/note",
        action="add",
        title="Second",
        level=2,
        after="top/first",
    )
    assert "[error" not in result.text.lower()
    content = (agent_pages / "note.md").read_text()
    assert "## Second" in content


@pytest.mark.asyncio
async def test_section_rename(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text("# Top\n\n## Old\n")
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/note",
        action="rename",
        section="top/old",
        title="New",
    )
    assert "[error" not in result.text.lower()
    content = (agent_pages / "note.md").read_text()
    assert "## New" in content
    assert "## Old" not in content


@pytest.mark.asyncio
async def test_section_refuses_write_outside_agent(vault_ctx):
    vault = vault_ctx.config.vault_root
    (vault / "user_notes").mkdir()
    (vault / "user_notes" / "x.md").write_text("# U\n")
    result = await tool_vault_section(
        vault_ctx,
        page="user_notes/x",
        action="add",
        title="New",
        level=2,
    )
    assert "[error" in result.text.lower()
    # Unchanged
    assert (vault / "user_notes" / "x.md").read_text() == "# U\n"


@pytest.mark.asyncio
async def test_move_lines_refuses_same_file(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    original = "# Top\n\n- [ ] task1\n- [ ] task2\n"
    (agent_pages / "note.md").write_text(original)
    result = await tool_vault_move_lines(
        vault_ctx,
        from_page="agent/pages/note",
        to_page="agent/pages/note",
        lines="3",
    )
    assert "[error" in result.text.lower()
    # File must be byte-for-byte unchanged
    assert (agent_pages / "note.md").read_text() == original


@pytest.mark.asyncio
async def test_move_lines_multiline_prepend_into_section_preserved(vault_ctx):
    """Regression: moving 2+ lines must not collapse them into a single line."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True, exist_ok=True)
    (agent_pages / "src.md").write_text(
        "# Top\n\n- [ ] alpha\n- [ ] beta\n- [ ] gamma\n"
    )
    (agent_pages / "dst.md").write_text("# Today\n\n## inbox\n")
    result = await tool_vault_move_lines(
        vault_ctx,
        from_page="agent/pages/src",
        to_page="agent/pages/dst",
        lines="3,4",
        to_section="today/inbox",
        position="prepend",
    )
    assert "[error" not in result.text.lower()
    dst_lines = (agent_pages / "dst.md").read_text().splitlines()
    # Both moved lines must appear as distinct lines in the output
    assert any("alpha" in line for line in dst_lines), "alpha not found as distinct line"
    assert any("beta" in line for line in dst_lines), "beta not found as distinct line"
    # They must NOT be merged on a single line
    merged = [line for line in dst_lines if "alpha" in line and "beta" in line]
    assert not merged, f"alpha and beta were merged onto the same line: {merged}"


@pytest.mark.asyncio
async def test_move_lines_multiline_prepend_sectionless_preserved(vault_ctx):
    """Regression: moving 2+ lines without to_section must not collapse them."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True, exist_ok=True)
    (agent_pages / "src.md").write_text(
        "# Top\n\n- [ ] alpha\n- [ ] beta\n- [ ] gamma\n"
    )
    (agent_pages / "dst.md").write_text("# Target\n\n")
    result = await tool_vault_move_lines(
        vault_ctx,
        from_page="agent/pages/src",
        to_page="agent/pages/dst",
        lines="3,4",
        # No to_section — sectionless code path
        position="prepend",
    )
    assert "[error" not in result.text.lower()
    dst_lines = (agent_pages / "dst.md").read_text().splitlines()
    assert any("alpha" in line for line in dst_lines), "alpha not found as distinct line"
    assert any("beta" in line for line in dst_lines), "beta not found as distinct line"
    merged = [line for line in dst_lines if "alpha" in line and "beta" in line]
    assert not merged, f"alpha and beta were merged onto the same line: {merged}"


@pytest.mark.asyncio
async def test_section_add_level_out_of_range(vault_ctx):
    """vault_section add must reject level values outside 1-6."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True, exist_ok=True)
    original = "# Top\n\n## First\n"
    (agent_pages / "note.md").write_text(original)
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/note",
        action="add",
        title="Bad",
        level=7,
    )
    assert "[error" in result.text.lower()
    # File must be unchanged
    assert (agent_pages / "note.md").read_text() == original


@pytest.mark.asyncio
async def test_move_lines_invalid_position(vault_ctx):
    """vault_move_lines must reject unknown position values."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True, exist_ok=True)
    src_text = "# Top\n\n- [ ] task1\n"
    dst_text = "# Target\n"
    (agent_pages / "src.md").write_text(src_text)
    (agent_pages / "dst.md").write_text(dst_text)
    result = await tool_vault_move_lines(
        vault_ctx,
        from_page="agent/pages/src",
        to_page="agent/pages/dst",
        lines="3",
        position="invalid",
    )
    assert "[error" in result.text.lower()
    # Both files must be unchanged
    assert (agent_pages / "src.md").read_text() == src_text
    assert (agent_pages / "dst.md").read_text() == dst_text
