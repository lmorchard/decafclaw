"""Tests for vault_show_sections, vault_move_lines, and vault_section tools."""

from unittest.mock import AsyncMock, patch

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


@pytest.mark.asyncio
async def test_move_lines_reindexes_both_pages(vault_ctx):
    """vault_move_lines must reindex both source and target after writing."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True, exist_ok=True)
    (agent_pages / "src.md").write_text("# Top\n\n- [ ] task1\n- [ ] task2\n")
    (agent_pages / "dst.md").write_text("# Today\n\n## inbox\n")
    with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock) as mock_index, \
         patch("decafclaw.embeddings.delete_entries"):
        result = await tool_vault_move_lines(
            vault_ctx,
            from_page="agent/pages/src",
            to_page="agent/pages/dst",
            lines="3",
            to_section="today/inbox",
        )
    assert "[error" not in result.text.lower()
    # index_entry must be called for both affected pages
    assert mock_index.call_count == 2
    called_paths = {call.args[1] for call in mock_index.call_args_list}
    assert any("src" in p for p in called_paths), f"src not reindexed: {called_paths}"
    assert any("dst" in p for p in called_paths), f"dst not reindexed: {called_paths}"


@pytest.mark.asyncio
async def test_vault_section_reindexes_after_add(vault_ctx):
    """vault_section must reindex the page after a successful add."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True, exist_ok=True)
    (agent_pages / "note.md").write_text("# Top\n\n## First\n")
    with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock) as mock_index, \
         patch("decafclaw.embeddings.delete_entries"):
        result = await tool_vault_section(
            vault_ctx,
            page="agent/pages/note",
            action="add",
            title="Second",
            level=2,
            after="top/first",
        )
    assert "[error" not in result.text.lower()
    assert mock_index.call_count == 1
    # The indexed path must correspond to note.md
    indexed_path = mock_index.call_args.args[1]
    assert "note" in indexed_path


@pytest.mark.asyncio
async def test_vault_section_publishes_vault_changed(vault_ctx):
    """vault_section publishes a single vault_changed event after a successful
    write. Exercising the 'add' branch is enough — every action branch shares
    the same publish pattern."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True, exist_ok=True)
    (agent_pages / "note.md").write_text("# Top\n\n## First\n")
    captured: list[dict] = []

    async def capture(event):
        captured.append(event)

    vault_ctx.event_bus.publish = capture
    with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock), \
         patch("decafclaw.embeddings.delete_entries"):
        result = await tool_vault_section(
            vault_ctx,
            page="agent/pages/note",
            action="add",
            title="Second",
            level=2,
            after="top/first",
        )
    assert "[error" not in result.text.lower()
    matching = [e for e in captured if e.get("type") == "vault_changed"]
    assert len(matching) == 1
    assert matching[0]["kind"] == "section"
    assert matching[0]["path"].endswith("note.md")


@pytest.mark.asyncio
async def test_vault_move_lines_publishes_vault_changed_for_both_pages(vault_ctx):
    """vault_move_lines publishes one vault_changed event per affected page —
    one for the target write, one for the source write."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True, exist_ok=True)
    (agent_pages / "src.md").write_text(
        "# Top\n\n- [ ] task1\n- [ ] task2\n- [ ] task3\n"
    )
    (agent_pages / "dst.md").write_text("# Today\n\n## inbox\n")
    captured: list[dict] = []

    async def capture(event):
        captured.append(event)

    vault_ctx.event_bus.publish = capture
    with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock), \
         patch("decafclaw.embeddings.delete_entries"):
        result = await tool_vault_move_lines(
            vault_ctx,
            from_page="agent/pages/src",
            to_page="agent/pages/dst",
            lines="3,4",
            to_section="today/inbox",
        )
    assert "[error" not in result.text.lower()
    matching = [e for e in captured if e.get("type") == "vault_changed"]
    assert len(matching) == 2
    assert all(e["kind"] == "move" for e in matching)
    paths = {e["path"] for e in matching}
    assert any(p.endswith("src.md") for p in paths)
    assert any(p.endswith("dst.md") for p in paths)


# --- Section path resolution and miss diagnostics (#671) --------------------


@pytest.mark.asyncio
async def test_section_add_after_bare_title(vault_ctx):
    """#671: 'First' previously had to be spelled 'top/first'."""
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
        after="First",
    )
    assert "[error" not in result.text.lower()
    assert "## Second" in (agent_pages / "note.md").read_text()


@pytest.mark.asyncio
async def test_section_miss_lists_known_paths(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text("# Top\n\n## First\n")
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/note",
        action="rename",
        section="Nonexistent",
        title="X",
    )
    assert "not found" in result.text
    assert "Top/First" in result.text


@pytest.mark.asyncio
async def test_section_add_miss_echoes_the_failed_path(vault_ctx):
    """The add branch used to say 'target section not found' with no path."""
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
        after="Nowhere",
    )
    assert "Nowhere" in result.text
    assert "Top/First" in result.text


@pytest.mark.asyncio
async def test_section_ambiguous_path_errors_with_candidates(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "amb.md").write_text(
        "# Top\n\n## Notes\n\na\n\n## Archive\n\n### Notes\n\nb\n"
    )
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/amb",
        action="rename",
        section="Notes",
        title="X",
    )
    assert "ambiguous" in result.text
    assert "Top/Notes" in result.text
    assert "Top/Archive/Notes" in result.text
    # Nothing was renamed.
    assert "## Notes" in (agent_pages / "amb.md").read_text()


@pytest.mark.asyncio
async def test_section_add_with_content(vault_ctx):
    """One call should both create the section and fill it (#671 Phase 5).

    Document.add_section always supported `content`; the tool didn't expose
    it, so the agent had to add an empty section and then rewrite the page.
    """
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text(
        "# Project Notes\n\n## Background\n\nkeep me\n\n## TODO\n\n- Old item\n"
    )
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/note",
        action="add",
        title="Status",
        level=2,
        content="Working on it.",
        after="Background",
    )
    assert "[error" not in result.text.lower()
    text = (agent_pages / "note.md").read_text()
    assert "## Status" in text
    assert "Working on it." in text
    # Ordering holds and the untouched sections survive.
    assert text.index("## Background") < text.index("## Status") < text.index("## TODO")
    assert "keep me" in text
    assert "- Old item" in text


@pytest.mark.asyncio
async def test_section_add_without_content_still_empty(vault_ctx):
    """content is optional — omitting it keeps the old behavior."""
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
        after="First",
    )
    assert "[error" not in result.text.lower()
    assert "## Second" in (agent_pages / "note.md").read_text()


@pytest.mark.asyncio
async def test_section_add_infers_level_from_anchor(vault_ctx):
    """Omitting level must reach add_section's inference, not trip validation.

    The tool guarded `not isinstance(level, int)` before `level` became
    optional, so an omitted level failed with "level must be between 1 and 6,
    got None" — caught only at the tool layer, since the Document-level tests
    call add_section directly.
    """
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text(
        "# Project Notes\n\n## Background\n\nkeep me\n\n## TODO\n\n- Old item\n"
    )
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/note",
        action="add",
        title="Status",
        content="Working on it.",
        after="Background",
    )
    assert "[error" not in result.text.lower()
    text = (agent_pages / "note.md").read_text()
    assert "## Status" in text
    # A sibling, not a second H1 that would reparent TODO.
    assert "\n# Status" not in text


@pytest.mark.asyncio
async def test_section_add_still_rejects_out_of_range_level(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text("# Top\n\n## First\n")
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/note",
        action="add",
        title="Nope",
        level=9,
        after="First",
    )
    assert "level must be between 1 and 6" in result.text
