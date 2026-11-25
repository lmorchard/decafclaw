"""Tests for vault_edit, vault_append, vault_replace_lines, and vault_read slicing (#1771)."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.skills.vault.tools import (
    TOOL_DEFINITIONS,
    TOOLS,
    tool_vault_append,
    tool_vault_edit,
    tool_vault_read,
    tool_vault_replace_lines,
)

PAGE_TEXT = "# Note\n\n## Background\n\nOriginal background.\n\n## TODO\n\n- item\n"


@pytest.fixture
def vault_dir(config):
    d = config.vault_root
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def agent_pages(config):
    d = config.vault_agent_pages_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def note(agent_pages):
    path = agent_pages / "note.md"
    path.write_text(PAGE_TEXT)
    return path


def _dummy_request_confirmation(*args, **kwargs):
    raise AssertionError("ctx.request_confirmation should not be invoked directly")


async def _edit(ctx, *args, **kwargs):
    with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
        return await tool_vault_edit(ctx, *args, **kwargs)


class TestVaultReadSlicing:
    @pytest.mark.asyncio
    async def test_full_read_returns_content_and_version(self, ctx, note):
        result = await tool_vault_read(ctx, "agent/pages/note")
        assert result.text == PAGE_TEXT
        assert isinstance(result.data["version"], str) and result.data["version"]

    @pytest.mark.asyncio
    async def test_line_slice_is_numbered_and_has_same_version(self, ctx, note):
        full = await tool_vault_read(ctx, "agent/pages/note")
        sliced = await tool_vault_read(ctx, "agent/pages/note", start_line=3, end_line=5)
        assert sliced.text.startswith("Lines 3-5 of 9:\n")
        assert "3| ## Background" in sliced.text
        assert "5| Original background." in sliced.text
        assert "TODO" not in sliced.text
        assert sliced.data["version"] == full.data["version"]
        assert sliced.data["range"] == [3, 5]


class TestVaultEdit:
    @pytest.mark.asyncio
    async def test_replaces_text_writes_file_and_returns_diff(self, ctx, note):
        result = await _edit(ctx, "agent/pages/note", "Original background.", "Updated background.")
        assert "replaced 1 occurrence" in result.text
        assert "+Updated background." in result.text
        assert "Version:" in result.text
        assert note.read_text() == PAGE_TEXT.replace("Original background.", "Updated background.")
        assert result.data["replaced"] == 1

    @pytest.mark.asyncio
    async def test_publishes_vault_changed_update(self, ctx, note):
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        await _edit(ctx, "agent/pages/note", "Original background.", "Updated.")
        matching = [e for e in captured if e.get("type") == "vault_changed"]
        assert len(matching) == 1
        assert matching[0]["kind"] == "update"
        assert matching[0]["path"].endswith("note.md")

    @pytest.mark.asyncio
    async def test_reindexes_page_after_edit(self, ctx, note):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock) as mock_index:
            await tool_vault_edit(ctx, "agent/pages/note", "Original background.", "Updated.")
        assert mock_index.called

    @pytest.mark.asyncio
    async def test_missing_text_returns_error_without_write(self, ctx, note):
        result = await _edit(ctx, "agent/pages/note", "not in the page", "x")
        assert result.text.startswith("[error: text not found")
        assert note.read_text() == PAGE_TEXT

    @pytest.mark.asyncio
    async def test_ambiguous_text_returns_count(self, ctx, note):
        result = await _edit(ctx, "agent/pages/note", "##", "###")
        assert "found 2 matches" in result.text
        assert note.read_text() == PAGE_TEXT

    @pytest.mark.asyncio
    async def test_replace_all_replaces_every_match(self, ctx, note):
        result = await _edit(ctx, "agent/pages/note", "##", "###", replace_all=True)
        assert "replaced 2 occurrence" in result.text
        assert note.read_text() == PAGE_TEXT.replace("##", "###")

    @pytest.mark.asyncio
    async def test_stale_expected_version_rejects_without_write(self, ctx, note):
        result = await _edit(
            ctx,
            "agent/pages/note",
            "Original background.",
            "Updated.",
            expected_version="stale-token",
        )
        assert "stale version" in result.text
        assert note.read_text() == PAGE_TEXT

    @pytest.mark.asyncio
    async def test_fresh_expected_version_succeeds(self, ctx, note):
        read = await tool_vault_read(ctx, "agent/pages/note")
        result = await _edit(
            ctx,
            "agent/pages/note",
            "Original background.",
            "Updated.",
            expected_version=read.data["version"],
        )
        assert "replaced 1 occurrence" in result.text
        assert "Updated." in note.read_text()

    @pytest.mark.asyncio
    async def test_missing_page_returns_error(self, ctx, vault_dir):
        result = await _edit(ctx, "agent/pages/missing", "a", "b")
        assert "not found" in result.text

    @pytest.mark.asyncio
    async def test_crlf_page_keeps_crlf_after_edit(self, ctx, agent_pages):
        path = agent_pages / "crlf.md"
        path.write_bytes(b"one\r\ntwo\r\n")
        await _edit(ctx, "agent/pages/crlf", "one\ntwo", "ONE\nTWO")
        assert path.read_bytes() == b"ONE\r\nTWO\r\n"


class TestVaultEditGate:
    @pytest.mark.asyncio
    async def test_outside_agent_folder_confirms_then_writes(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir()
        (vault_dir / "creative" / "foo.md").write_text("draft text\n")
        ctx.request_confirmation = _dummy_request_confirmation
        with (
            patch(
                "decafclaw.skills.vault.tools.request_confirmation",
                AsyncMock(return_value={"approved": True}),
            ) as mock_conf,
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            result = await tool_vault_edit(ctx, "creative/foo", "draft text", "final text")
        assert "replaced 1 occurrence" in result.text
        assert mock_conf.called
        assert (vault_dir / "creative" / "foo.md").read_text() == "final text\n"

    @pytest.mark.asyncio
    async def test_denied_returns_error_without_write(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir()
        (vault_dir / "creative" / "foo.md").write_text("draft text\n")
        ctx.request_confirmation = _dummy_request_confirmation
        with patch("decafclaw.skills.vault.tools.request_confirmation", AsyncMock(return_value={"approved": False})):
            result = await tool_vault_edit(ctx, "creative/foo", "draft text", "final text")
        assert "denied by user" in result.text
        assert (vault_dir / "creative" / "foo.md").read_text() == "draft text\n"

    @pytest.mark.asyncio
    async def test_non_interactive_outside_agent_folder_errors(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir()
        (vault_dir / "creative" / "foo.md").write_text("draft text\n")
        ctx.request_confirmation = None
        result = await tool_vault_edit(ctx, "creative/foo", "draft text", "final text")
        assert "interactive confirmation" in result.text
        assert (vault_dir / "creative" / "foo.md").read_text() == "draft text\n"


class TestVaultAppend:
    @pytest.mark.asyncio
    async def test_appends_with_newline_separator(self, ctx, note):
        result = await _append(ctx, "agent/pages/note", "- extra item")
        assert "Appended" in result.text
        assert note.read_text() == PAGE_TEXT + "- extra item"

    @pytest.mark.asyncio
    async def test_adds_separator_when_page_lacks_trailing_newline(self, ctx, agent_pages):
        path = agent_pages / "tight.md"
        path.write_text("no newline")
        await _append(ctx, "agent/pages/tight", "next line")
        assert path.read_text() == "no newline\nnext line"

    @pytest.mark.asyncio
    async def test_creates_missing_page_and_publishes_create(self, ctx, agent_pages):
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        result = await _append(ctx, "agent/pages/new-page", "# New\n\nBody\n")
        assert result.data["created"] is True
        assert (agent_pages / "new-page.md").read_text() == "# New\n\nBody\n"
        matching = [e for e in captured if e.get("type") == "vault_changed"]
        assert len(matching) == 1
        assert matching[0]["kind"] == "create"

    @pytest.mark.asyncio
    async def test_refuses_empty_content(self, ctx, note):
        result = await _append(ctx, "agent/pages/note", "   ")
        assert "refusing to append empty content" in result.text
        assert note.read_text() == PAGE_TEXT

    @pytest.mark.asyncio
    async def test_outside_agent_folder_requires_confirmation(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir()
        (vault_dir / "creative" / "foo.md").write_text("draft\n")
        ctx.request_confirmation = None
        result = await tool_vault_append(ctx, "creative/foo", "more")
        assert "interactive confirmation" in result.text
        assert (vault_dir / "creative" / "foo.md").read_text() == "draft\n"


async def _append(ctx, *args, **kwargs):
    with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
        return await tool_vault_append(ctx, *args, **kwargs)


class TestVaultReplaceLines:
    @pytest.mark.asyncio
    async def test_replaces_range(self, ctx, note):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_replace_lines(ctx, "agent/pages/note", 3, 5, "## Updated\n\nNew body.\n")
        assert "Replaced lines 3-5 with 3 line(s)" in result.text
        assert note.read_text() == PAGE_TEXT.replace(
            "## Background\n\nOriginal background.\n", "## Updated\n\nNew body.\n"
        )

    @pytest.mark.asyncio
    async def test_empty_content_deletes_lines(self, ctx, note):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_replace_lines(ctx, "agent/pages/note", 8, 9)
        assert "Deleted lines 8-9" in result.text
        assert "- item" not in note.read_text()

    @pytest.mark.asyncio
    async def test_invalid_range_returns_error(self, ctx, note):
        result = await tool_vault_replace_lines(ctx, "agent/pages/note", 3, 99, "x")
        assert "invalid line range" in result.text
        assert note.read_text() == PAGE_TEXT


class TestSectionToolsExcised:
    def test_tools_registry_has_no_section_tools(self):
        assert "vault_section" not in TOOLS
        assert "vault_move_lines" not in TOOLS
        assert "vault_show_sections" not in TOOLS
        assert {"vault_edit", "vault_append", "vault_replace_lines"} <= set(TOOLS)

    def test_definitions_have_no_section_tools(self):
        names = {td["function"]["name"] for td in TOOL_DEFINITIONS}
        assert not {"vault_section", "vault_move_lines", "vault_show_sections"} & names
        assert {"vault_edit", "vault_append", "vault_replace_lines"} <= names


class TestVaultEditIdenticalText:
    @pytest.mark.asyncio
    async def test_identical_text_is_a_noop_error(self, ctx, note):
        before = note.read_text()
        before_mtime = note.stat().st_mtime_ns

        result = await _edit(ctx, "agent/pages/note", "Original background.", "Original background.")

        assert "[error: old_text and new_text are identical" in result.text
        assert note.read_text() == before
        assert note.stat().st_mtime_ns == before_mtime
