"""Tests for vault tools."""

import os
import re
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.frontmatter import parse_frontmatter
from decafclaw.skills.vault._grants import add_grant
from decafclaw.skills.vault.tools import (
    GateOutcome,
    _check_user_write_allowed,
    format_recent_journal_for_context,
    read_recent_journal_entries,
    resolve_page,
    tool_vault_backlinks,
    tool_vault_delete,
    tool_vault_grant_folder,
    tool_vault_journal_append,
    tool_vault_list,
    tool_vault_read,
    tool_vault_recent,
    tool_vault_rename,
    tool_vault_search,
    tool_vault_tags,
    tool_vault_update_frontmatter,
    tool_vault_write,
)


@pytest.fixture
def vault_dir(config):
    """Create the vault directory."""
    d = config.vault_root
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def agent_pages(config):
    """Create the agent pages directory."""
    d = config.vault_agent_pages_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def agent_journal(config):
    """Create the agent journal directory."""
    d = config.vault_agent_journal_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dummy_request_confirmation(*args, **kwargs):
    """Module-level stand-in callable so the gate sees a non-None
    ctx.request_confirmation. Tests that exercise the gate patch
    `request_confirmation` (the module-level helper) directly, so this
    sentinel should never actually be invoked.
    """
    raise AssertionError("ctx.request_confirmation should not be invoked directly")


class TestResolvePage:
    def test_explicit_folder_path(self, config, vault_dir):
        sub = vault_dir / "folder1"
        sub.mkdir()
        (sub / "Page.md").write_text("# Page in folder1")
        result = resolve_page(config, "folder1/Page")
        assert result is not None
        assert result.name == "Page.md"
        assert "folder1" in str(result)

    def test_ambiguous_stem_returns_sorted_first(self, config, vault_dir):
        for name in ["folder2", "folder1"]:
            d = vault_dir / name
            d.mkdir(exist_ok=True)
            (d / "Page.md").write_text(f"# Page in {name}")
        result = resolve_page(config, "Page")
        assert result is not None
        # Sorted alphabetically — folder1 comes first
        assert "folder1" in str(result)

    def test_from_page_proximity(self, config, vault_dir):
        for name in ["folder1", "folder2"]:
            d = vault_dir / name
            d.mkdir(exist_ok=True)
            (d / "Page.md").write_text(f"# Page in {name}")
        (vault_dir / "folder2" / "Other.md").write_text("# Other")
        result = resolve_page(config, "Page", from_page="folder2/Other")
        assert result is not None
        assert "folder2" in str(result)

    def test_nonexistent_returns_none(self, config, vault_dir):
        assert resolve_page(config, "DoesNotExist") is None

    def test_strips_md_suffix(self, config, vault_dir):
        """Passing 'Page.md' should resolve to Page.md, not Page.md.md."""
        (vault_dir / "Hello.md").write_text("# Hello")
        result = resolve_page(config, "Hello.md")
        assert result is not None
        assert result.name == "Hello.md"
        assert not (vault_dir / "Hello.md.md").exists()


class TestVaultRead:
    @pytest.mark.asyncio
    async def test_read_existing_page(self, ctx, vault_dir):
        (vault_dir / "Test Page.md").write_text("# Test Page\n\nContent here.")
        result = await tool_vault_read(ctx, "Test Page")
        assert "Content here." in result.text

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, ctx, vault_dir):
        result = await tool_vault_read(ctx, "Nope")
        assert "not found" in result.text

    @pytest.mark.asyncio
    async def test_read_rejects_path_traversal(self, ctx, vault_dir, config):
        outside = config.workspace_path / "secrets"
        outside.mkdir(parents=True)
        (outside / "secret.md").write_text("super secret")
        result = await tool_vault_read(ctx, "../secrets/secret")
        assert "not found" in result.text

    @pytest.mark.asyncio
    async def test_read_rejects_absolute_path(self, ctx, vault_dir):
        result = await tool_vault_read(ctx, "/etc/passwd")
        assert "not found" in result.text

    @pytest.mark.asyncio
    async def test_read_date_frontmatter_survives_json_serialization(
        self, ctx, vault_dir,
    ):
        """`data` is json.dumps'd into a fenced block by tool_execution.

        A `date:` line parses to a datetime.date, which used to make that dump
        raise — tool_execution catches it, so the agent silently lost the whole
        structured block instead of crashing. Assert it serializes for real.
        """
        import json

        (vault_dir / "Dated.md").write_text(
            "---\ndate: 2026-06-22\ntags:\n- blog\n---\n# Dated\n\nBody.\n"
        )
        result = await tool_vault_read(ctx, "Dated")
        assert result.data is not None
        assert json.loads(json.dumps(result.data))["frontmatter"] == {
            "date": "2026-06-22",
            "tags": ["blog"],
        }

    @pytest.mark.asyncio
    async def test_read_in_subdirectory(self, ctx, agent_pages):
        sub = agent_pages / "people"
        sub.mkdir()
        (sub / "Alice.md").write_text("# Alice\n\nA person.")
        result = await tool_vault_read(ctx, "Alice")
        assert "A person." in result.text


class TestVaultWrite:
    @pytest.mark.asyncio
    async def test_create_new_page(self, ctx, vault_dir):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_write(ctx, "agent/pages/New Page",
                                            "# New Page\n\nFresh.")
        assert "saved" in result.text.lower()
        path = vault_dir / "agent" / "pages" / "New Page.md"
        assert path.exists()
        assert "Fresh." in path.read_text()

    @pytest.mark.asyncio
    async def test_overwrite_existing(self, ctx, agent_pages):
        (agent_pages / "Existing.md").write_text("Old content.")
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_write(ctx, "agent/pages/Existing", "New content.")
        assert "New content." in (agent_pages / "Existing.md").read_text()

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, ctx, vault_dir):
        result = await tool_vault_write(ctx, "../../../etc/passwd", "hack")
        assert "error" in result.text.lower()

    @pytest.mark.asyncio
    async def test_rejects_dotdot_in_name(self, ctx, vault_dir):
        result = await tool_vault_write(ctx, "foo/../bar", "hack")
        assert "error" in result.text.lower()

    @pytest.mark.asyncio
    async def test_rejects_write_outside_agent_folder(self, ctx, vault_dir):
        """In a non-interactive context (no request_confirmation), vault_write
        to a path outside the agent folder must error rather than write.
        With Phase 2, the message names interactive confirmation as the
        missing prerequisite."""
        ctx.request_confirmation = None
        result = await tool_vault_write(ctx, "StrayRootPage", "# Stray")
        assert "error" in result.text.lower()
        assert "interactive confirmation" in result.text.lower()
        assert not (vault_dir / "StrayRootPage.md").exists()

    @pytest.mark.asyncio
    async def test_creates_subdirectory(self, ctx, vault_dir):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_write(ctx, "agent/pages/people/Alice", "# Alice")
        assert (vault_dir / "agent" / "pages" / "people" / "Alice.md").exists()

    @pytest.mark.asyncio
    async def test_rejects_empty_content(self, ctx, vault_dir):
        result = await tool_vault_write(ctx, "agent/pages/Empty", "")
        assert "error" in result.text.lower()

    @pytest.mark.asyncio
    async def test_strips_md_suffix(self, ctx, vault_dir):
        """Writing 'page.md' should create page.md, not page.md.md."""
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_write(ctx, "agent/pages/Test.md", "# Test")
        assert "saved" in result.text.lower()
        assert (vault_dir / "agent" / "pages" / "Test.md").exists()
        assert not (vault_dir / "agent" / "pages" / "Test.md.md").exists()

    @pytest.mark.asyncio
    async def test_publishes_vault_changed_on_create(self, ctx, agent_pages):
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_write(ctx, "agent/pages/Foo", "# Foo content")
        matching = [e for e in captured if e.get("type") == "vault_changed"]
        assert len(matching) == 1
        assert matching[0]["kind"] == "create"
        assert matching[0]["path"].endswith("Foo.md")

    @pytest.mark.asyncio
    async def test_publishes_vault_changed_on_update(self, ctx, agent_pages):
        (agent_pages / "Existing.md").write_text("Old content.")
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_write(ctx, "agent/pages/Existing", "New content.")
        matching = [e for e in captured if e.get("type") == "vault_changed"]
        assert len(matching) == 1
        assert matching[0]["kind"] == "update"

    @pytest.mark.asyncio
    async def test_does_not_publish_on_invalid_page_name(self, ctx, vault_dir):
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        await tool_vault_write(ctx, "..", "content")
        assert not [e for e in captured if e.get("type") == "vault_changed"]

    @pytest.mark.asyncio
    async def test_does_not_publish_on_empty_content(self, ctx, vault_dir):
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        await tool_vault_write(ctx, "agent/pages/Empty", "")
        assert not [e for e in captured if e.get("type") == "vault_changed"]

    @pytest.mark.asyncio
    async def test_does_not_publish_on_non_interactive_outside_agent(
        self, ctx, vault_dir,
    ):
        ctx.request_confirmation = None
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        await tool_vault_write(ctx, "StrayRootPage", "# Stray")
        assert not [e for e in captured if e.get("type") == "vault_changed"]


class TestVaultWriteUserFolders:
    """Phase 2: vault_write to user folders via the three-tier gate.

    Allowlist and grants short-circuit to direct write; otherwise the tool
    requests user confirmation; non-interactive contexts error out.
    """

    @pytest.mark.asyncio
    async def test_allowlist_bypass_writes_directly(self, ctx, vault_dir):
        ctx.config.vault.user_writable_paths = ["creative/"]
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_write(ctx, "creative/foo", "content body")
        assert "saved" in str(result).lower()
        assert (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_grant_bypass_writes_directly(self, ctx, vault_dir):
        ctx.conv_id = "conv-123"
        add_grant(ctx.config, "conv-123", "creative/")
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_write(ctx, "creative/foo", "content body")
        assert "saved" in str(result).lower()
        assert (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_no_allowlist_or_grant_confirms_then_writes(self, ctx, vault_dir):
        ctx.request_confirmation = _dummy_request_confirmation
        with (
            patch("decafclaw.skills.vault.tools.request_confirmation",
                  AsyncMock(return_value={"approved": True})) as mock_conf,
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            result = await tool_vault_write(ctx, "creative/foo", "content body")
        assert "saved" in str(result).lower()
        assert mock_conf.called
        assert (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_denied_returns_error_no_write(self, ctx, vault_dir):
        ctx.request_confirmation = _dummy_request_confirmation
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock(return_value={"approved": False})):
            result = await tool_vault_write(ctx, "creative/foo", "content body")
        text = result.text if hasattr(result, "text") else str(result)
        assert "denied by user" in text
        assert not (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_non_interactive_context_errors(self, ctx, vault_dir):
        ctx.request_confirmation = None
        result = await tool_vault_write(ctx, "creative/foo", "content body")
        text = result.text if hasattr(result, "text") else str(result)
        assert "interactive confirmation" in text
        assert not (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_confirmation_preview_includes_path_and_content(self, ctx, vault_dir):
        ctx.request_confirmation = _dummy_request_confirmation
        captured: dict = {}

        async def capture_request(ctx_, **kwargs):
            captured.update(kwargs)
            return {"approved": True}

        with (
            patch("decafclaw.skills.vault.tools.request_confirmation",
                  side_effect=capture_request),
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            await tool_vault_write(ctx, "creative/foo", "hello world content")
        assert "creative/foo.md" in captured["message"]
        assert "hello world" in captured["message"]
        assert "(new page)" in captured["message"]


class TestVaultDelete:
    @pytest.mark.asyncio
    async def test_deletes_agent_page(self, ctx, agent_pages):
        (agent_pages / "Stale.md").write_text("old")
        with patch("decafclaw.embeddings.delete_entries") as mock_del:
            result = await tool_vault_delete(ctx, "agent/pages/Stale")
        assert "deleted" in result.text.lower()
        assert not (agent_pages / "Stale.md").exists()
        mock_del.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleans_up_empty_parent_dirs(self, ctx, vault_dir, config):
        # Nested page with its own folder; folder should go away after delete.
        pages = config.vault_agent_pages_dir
        nested = pages / "people" / "obsolete"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "Alice.md").write_text("old")
        with patch("decafclaw.embeddings.delete_entries"):
            await tool_vault_delete(ctx, "agent/pages/people/obsolete/Alice")
        assert not nested.exists()
        # But the vault root itself must still exist
        assert vault_dir.exists()

    @pytest.mark.asyncio
    async def test_rejects_page_outside_agent_dir(self, ctx, vault_dir):
        """In a non-interactive context (no request_confirmation), vault_delete
        of a path outside the agent folder must error rather than delete.
        With Phase 3, the message names interactive confirmation as the
        missing prerequisite."""
        ctx.request_confirmation = None
        (vault_dir / "User Notes.md").write_text("mine")
        result = await tool_vault_delete(ctx, "User Notes")
        assert "error" in result.text.lower()
        assert "interactive confirmation" in result.text.lower()
        assert (vault_dir / "User Notes.md").exists()

    @pytest.mark.asyncio
    async def test_nonexistent_page(self, ctx, vault_dir):
        result = await tool_vault_delete(ctx, "agent/pages/Never Existed")
        assert "error" in result.text.lower()
        assert "not found" in result.text.lower()

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, ctx, vault_dir):
        result = await tool_vault_delete(ctx, "../../../etc/passwd")
        assert "error" in result.text.lower()

    @pytest.mark.asyncio
    async def test_strips_md_suffix(self, ctx, agent_pages):
        (agent_pages / "Test.md").write_text("x")
        with patch("decafclaw.embeddings.delete_entries"):
            result = await tool_vault_delete(ctx, "agent/pages/Test.md")
        assert "deleted" in result.text.lower()
        assert not (agent_pages / "Test.md").exists()

    @pytest.mark.asyncio
    async def test_rejects_empty_page_name(self, ctx, vault_dir):
        # A bare ".md" file at the vault root must NOT be matched by empty
        # or trailing-slash inputs.
        (vault_dir / ".md").write_text("hidden")
        for bad in ["", "   ", "agent/pages/", ".md", "/"]:
            result = await tool_vault_delete(ctx, bad)
            assert "error" in result.text.lower(), f"bad input {bad!r} was accepted"
        assert (vault_dir / ".md").exists()

    @pytest.mark.asyncio
    async def test_publishes_vault_changed_on_delete(self, ctx, agent_pages):
        (agent_pages / "Stale.md").write_text("old")
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        with patch("decafclaw.embeddings.delete_entries"):
            await tool_vault_delete(ctx, "agent/pages/Stale")
        matching = [e for e in captured if e.get("type") == "vault_changed"]
        assert len(matching) == 1
        assert matching[0]["kind"] == "delete"
        assert matching[0]["path"].endswith("Stale.md")


class TestVaultDeleteUserFolders:
    """Phase 3: vault_delete on user folders via the three-tier gate.

    Allowlist and grants short-circuit to direct delete; otherwise the tool
    requests user confirmation; non-interactive contexts error out.
    """

    @pytest.mark.asyncio
    async def test_allowlist_bypass_deletes_directly(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "foo.md").write_text("body")
        ctx.config.vault.user_writable_paths = ["creative/"]
        with patch("decafclaw.embeddings.delete_entries"):
            result = await tool_vault_delete(ctx, "creative/foo")
        assert "deleted" in result.text.lower()
        assert not (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_grant_bypass_deletes_directly(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "foo.md").write_text("body")
        ctx.conv_id = "conv-123"
        add_grant(ctx.config, "conv-123", "creative/")
        with patch("decafclaw.embeddings.delete_entries"):
            result = await tool_vault_delete(ctx, "creative/foo")
        assert "deleted" in result.text.lower()
        assert not (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_no_allowlist_or_grant_confirms_then_deletes(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "foo.md").write_text("body")
        ctx.request_confirmation = _dummy_request_confirmation
        with (
            patch("decafclaw.skills.vault.tools.request_confirmation",
                  AsyncMock(return_value={"approved": True})) as mock_conf,
            patch("decafclaw.embeddings.delete_entries"),
        ):
            result = await tool_vault_delete(ctx, "creative/foo")
        assert "deleted" in result.text.lower()
        assert mock_conf.called
        assert not (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_denied_returns_error_no_delete(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "foo.md").write_text("body")
        ctx.request_confirmation = _dummy_request_confirmation
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock(return_value={"approved": False})):
            result = await tool_vault_delete(ctx, "creative/foo")
        text = result.text if hasattr(result, "text") else str(result)
        assert "denied by user" in text
        assert (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_non_interactive_context_errors(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "foo.md").write_text("body")
        ctx.request_confirmation = None
        result = await tool_vault_delete(ctx, "creative/foo")
        text = result.text if hasattr(result, "text") else str(result)
        assert "interactive confirmation" in text
        assert (vault_dir / "creative" / "foo.md").exists()

    @pytest.mark.asyncio
    async def test_confirmation_preview_includes_path(self, ctx, vault_dir):
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "foo.md").write_text("body")
        ctx.request_confirmation = _dummy_request_confirmation
        captured: dict = {}

        async def capture_request(ctx_, **kwargs):
            captured.update(kwargs)
            return {"approved": True}

        with (
            patch("decafclaw.skills.vault.tools.request_confirmation",
                  side_effect=capture_request),
            patch("decafclaw.embeddings.delete_entries"),
        ):
            await tool_vault_delete(ctx, "creative/foo")
        assert "creative/foo.md" in captured["message"]
        assert "(cannot be undone" in captured["message"]


class TestVaultRename:
    @pytest.mark.asyncio
    async def test_renames_agent_page(self, ctx, agent_pages):
        (agent_pages / "Old Name.md").write_text("body")
        with patch("decafclaw.embeddings.delete_entries"), \
             patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_vault_rename(
                ctx, "agent/pages/Old Name", "agent/pages/New Name"
            )
        assert "renamed" in result.text.lower()
        assert not (agent_pages / "Old Name.md").exists()
        assert (agent_pages / "New Name.md").exists()
        assert (agent_pages / "New Name.md").read_text() == "body"

    @pytest.mark.asyncio
    async def test_can_move_to_subfolder(self, ctx, agent_pages, config):
        (agent_pages / "Alice.md").write_text("about alice")
        with patch("decafclaw.embeddings.delete_entries"), \
             patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_rename(
                ctx, "agent/pages/Alice", "agent/pages/people/Alice"
            )
        assert not (agent_pages / "Alice.md").exists()
        assert (agent_pages / "people" / "Alice.md").exists()

    @pytest.mark.asyncio
    async def test_cleans_up_empty_parent_dirs(self, ctx, config):
        pages = config.vault_agent_pages_dir
        old_dir = pages / "stale-folder"
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / "Doc.md").write_text("x")
        with patch("decafclaw.embeddings.delete_entries"), \
             patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_rename(
                ctx, "agent/pages/stale-folder/Doc", "agent/pages/Doc"
            )
        assert not old_dir.exists()
        assert (pages / "Doc.md").exists()

    @pytest.mark.asyncio
    async def test_refuses_to_clobber_existing(self, ctx, agent_pages):
        (agent_pages / "A.md").write_text("a")
        (agent_pages / "B.md").write_text("b")
        result = await tool_vault_rename(
            ctx, "agent/pages/A", "agent/pages/B"
        )
        assert "error" in result.text.lower()
        assert "already exists" in result.text.lower()
        # Neither file should be touched
        assert (agent_pages / "A.md").read_text() == "a"
        assert (agent_pages / "B.md").read_text() == "b"

    @pytest.mark.asyncio
    async def test_rejects_rename_outside_agent_dir(self, ctx, vault_dir, agent_pages):
        """Non-interactive context + new path outside agent → error, no rename."""
        (agent_pages / "Inside.md").write_text("x")
        ctx.request_confirmation = None
        result = await tool_vault_rename(
            ctx, "agent/pages/Inside", "Escaped"
        )
        assert "error" in result.text.lower()
        assert "interactive confirmation" in result.text.lower()
        assert (agent_pages / "Inside.md").exists()
        assert not (vault_dir / "Escaped.md").exists()

    @pytest.mark.asyncio
    async def test_rejects_source_outside_agent_dir(self, ctx, vault_dir, agent_pages):
        (vault_dir / "User Notes.md").write_text("mine")
        result = await tool_vault_rename(
            ctx, "User Notes", "agent/pages/Stolen"
        )
        assert "error" in result.text.lower()
        assert (vault_dir / "User Notes.md").exists()
        assert not (agent_pages / "Stolen.md").exists()

    @pytest.mark.asyncio
    async def test_nonexistent_source(self, ctx, vault_dir):
        result = await tool_vault_rename(
            ctx, "agent/pages/Never Existed", "agent/pages/New"
        )
        assert "error" in result.text.lower()
        assert "not found" in result.text.lower()

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, ctx, agent_pages):
        (agent_pages / "Doc.md").write_text("x")
        result = await tool_vault_rename(
            ctx, "agent/pages/Doc", "../../../etc/passwd"
        )
        assert "error" in result.text.lower()

    @pytest.mark.asyncio
    async def test_rejects_empty_source_or_target(self, ctx, agent_pages):
        (agent_pages / "Doc.md").write_text("x")
        # Empty / trailing-slash / bare-".md" inputs on either side must be
        # rejected so a rename can't land at a hidden ".md" file.
        bad_values = ["", "   ", "agent/pages/", ".md"]
        for bad in bad_values:
            result = await tool_vault_rename(ctx, bad, "agent/pages/New")
            assert "error" in result.text.lower(), f"bad source {bad!r} accepted"
        for bad in bad_values:
            result = await tool_vault_rename(ctx, "agent/pages/Doc", bad)
            assert "error" in result.text.lower(), f"bad target {bad!r} accepted"
        assert (agent_pages / "Doc.md").exists()

    @pytest.mark.asyncio
    async def test_publishes_vault_changed_on_rename(self, ctx, agent_pages):
        (agent_pages / "Old Name.md").write_text("body")
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        with (
            patch("decafclaw.embeddings.delete_entries"),
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            await tool_vault_rename(
                ctx, "agent/pages/Old Name", "agent/pages/New Name"
            )
        matching = [e for e in captured if e.get("type") == "vault_changed"]
        assert len(matching) == 1
        assert matching[0]["kind"] == "rename"
        assert matching[0]["path"].endswith("New Name.md")


class TestVaultRenameUserFolders:
    """Phase 4: vault_rename across user folders via the three-tier gate.

    A single confirmation must cover BOTH source and target paths. If either
    needs confirmation, the whole op confirms once. If either is non-interactive
    error, the whole op fails.
    """

    @pytest.mark.asyncio
    async def test_old_in_agent_new_in_user_confirms(self, ctx, agent_pages, vault_dir):
        """Rename agent/pages/X to creative/X — confirms (new path is outside)."""
        (agent_pages / "X.md").write_text("body")
        ctx.request_confirmation = _dummy_request_confirmation
        with (
            patch("decafclaw.skills.vault.tools.request_confirmation",
                  AsyncMock(return_value={"approved": True})) as mock_conf,
            patch("decafclaw.embeddings.delete_entries"),
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            result = await tool_vault_rename(ctx, "agent/pages/X", "creative/X")
        assert "renamed" in result.text.lower()
        assert mock_conf.called
        assert not (agent_pages / "X.md").exists()
        assert (vault_dir / "creative" / "X.md").exists()

    @pytest.mark.asyncio
    async def test_both_in_user_confirms_once(self, ctx, vault_dir):
        """Rename creative/A to creative/B — single confirmation covers both."""
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "A.md").write_text("body")
        ctx.request_confirmation = _dummy_request_confirmation
        with (
            patch("decafclaw.skills.vault.tools.request_confirmation",
                  AsyncMock(return_value={"approved": True})) as mock_conf,
            patch("decafclaw.embeddings.delete_entries"),
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            result = await tool_vault_rename(ctx, "creative/A", "creative/B")
        assert "renamed" in result.text.lower()
        assert mock_conf.call_count == 1
        assert not (vault_dir / "creative" / "A.md").exists()
        assert (vault_dir / "creative" / "B.md").exists()

    @pytest.mark.asyncio
    async def test_both_in_user_with_grant_no_confirm(self, ctx, vault_dir):
        """Pre-grant 'creative/' — rename within creative/ requires no confirm."""
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "A.md").write_text("body")
        ctx.conv_id = "conv-rename-grant"
        add_grant(ctx.config, "conv-rename-grant", "creative/")
        # If request_confirmation gets called, this AsyncMock asserts loudly.
        sentinel = AsyncMock(side_effect=AssertionError(
            "request_confirmation must not be invoked when grant covers both paths"))
        with (
            patch("decafclaw.skills.vault.tools.request_confirmation", sentinel),
            patch("decafclaw.embeddings.delete_entries"),
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            result = await tool_vault_rename(ctx, "creative/A", "creative/B")
        assert "renamed" in result.text.lower()
        assert not sentinel.called
        assert not (vault_dir / "creative" / "A.md").exists()
        assert (vault_dir / "creative" / "B.md").exists()

    @pytest.mark.asyncio
    async def test_old_in_grant_new_outside_grant_confirms(self, ctx, vault_dir):
        """Grant covers creative/ but target is notes/ — needs confirmation."""
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "A.md").write_text("body")
        ctx.conv_id = "conv-rename-partial"
        add_grant(ctx.config, "conv-rename-partial", "creative/")
        ctx.request_confirmation = _dummy_request_confirmation
        with (
            patch("decafclaw.skills.vault.tools.request_confirmation",
                  AsyncMock(return_value={"approved": True})) as mock_conf,
            patch("decafclaw.embeddings.delete_entries"),
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            result = await tool_vault_rename(ctx, "creative/A", "notes/B")
        assert "renamed" in result.text.lower()
        assert mock_conf.called
        assert not (vault_dir / "creative" / "A.md").exists()
        assert (vault_dir / "notes" / "B.md").exists()

    @pytest.mark.asyncio
    async def test_denied_no_rename(self, ctx, vault_dir):
        """Deny → original file remains, target doesn't exist."""
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "A.md").write_text("body")
        ctx.request_confirmation = _dummy_request_confirmation
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock(return_value={"approved": False})):
            result = await tool_vault_rename(ctx, "creative/A", "creative/B")
        assert "denied by user" in result.text
        assert (vault_dir / "creative" / "A.md").exists()
        assert not (vault_dir / "creative" / "B.md").exists()

    @pytest.mark.asyncio
    async def test_non_interactive_errors(self, ctx, vault_dir):
        """ctx.request_confirmation = None → error, no rename."""
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "A.md").write_text("body")
        ctx.request_confirmation = None
        result = await tool_vault_rename(ctx, "creative/A", "creative/B")
        assert "interactive confirmation" in result.text
        assert (vault_dir / "creative" / "A.md").exists()
        assert not (vault_dir / "creative" / "B.md").exists()

    @pytest.mark.asyncio
    async def test_confirmation_preview_includes_both_paths(self, ctx, vault_dir):
        """Capture confirmation kwargs; assert message has both old and new paths."""
        (vault_dir / "creative").mkdir(parents=True, exist_ok=True)
        (vault_dir / "creative" / "A.md").write_text("body")
        ctx.request_confirmation = _dummy_request_confirmation
        captured: dict = {}

        async def capture_request(ctx_, **kwargs):
            captured.update(kwargs)
            return {"approved": True}

        with (
            patch("decafclaw.skills.vault.tools.request_confirmation",
                  side_effect=capture_request),
            patch("decafclaw.embeddings.delete_entries"),
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            await tool_vault_rename(ctx, "creative/A", "notes/B")
        assert "creative/A.md" in captured["message"]
        assert "notes/B.md" in captured["message"]


def _write_journal(config, day: datetime, entries: list[tuple[str, str]]):
    """Write a daily journal file mirroring vault_journal_append's format.

    ``entries`` is a list of ``(HH:MM, content)`` for the given ``day``.
    """
    d = config.vault_agent_journal_dir / str(day.year)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{day:%Y-%m-%d}.md"
    text = ""
    for hhmm, content in entries:
        text += f"\n## {day:%Y-%m-%d} {hhmm}\n\n- **tags:** test\n\n{content}\n"
    path.write_text(text, encoding="utf-8")
    return path


class TestReadRecentJournalEntries:
    def test_reads_only_entries_within_window(self, config):
        now = datetime(2026, 7, 23, 15, 0)
        _write_journal(config, now, [
            ("02:00", "old, outside 6h window"),
            ("14:30", "recent, inside window"),
        ])
        got = read_recent_journal_entries(
            config, now=now, max_hours=6, max_entries=5)
        assert len(got) == 1
        assert "recent" in got[0].text
        assert got[0].timestamp == datetime(2026, 7, 23, 14, 30)

    def test_caps_at_max_entries_keeping_most_recent(self, config):
        now = datetime(2026, 7, 23, 15, 0)
        _write_journal(config, now, [
            ("11:00", "one"), ("12:00", "two"), ("13:00", "three"),
            ("14:00", "four"), ("14:45", "five"),
        ])
        got = read_recent_journal_entries(
            config, now=now, max_hours=24, max_entries=3)
        # Most recent 3, returned chronological (oldest first).
        assert [e.timestamp.strftime("%H:%M") for e in got] == \
            ["13:00", "14:00", "14:45"]

    def test_crosses_midnight_reads_both_files(self, config):
        now = datetime(2026, 7, 23, 2, 0)
        yesterday = now - timedelta(days=1)
        _write_journal(config, yesterday, [("23:30", "late last night")])
        _write_journal(config, now, [("01:15", "early this morning")])
        got = read_recent_journal_entries(
            config, now=now, max_hours=6, max_entries=5)
        assert [e.text.split("\n")[-1] for e in got][-2:] == \
            ["late last night", "early this morning"]

    def test_missing_files_return_empty(self, config):
        now = datetime(2026, 7, 23, 15, 0)
        assert read_recent_journal_entries(
            config, now=now, max_hours=24, max_entries=5) == []

    def test_zero_bounds_return_empty(self, config):
        now = datetime(2026, 7, 23, 15, 0)
        _write_journal(config, now, [("14:30", "recent")])
        assert read_recent_journal_entries(
            config, now=now, max_hours=0, max_entries=5) == []
        assert read_recent_journal_entries(
            config, now=now, max_hours=24, max_entries=0) == []

    def test_format_includes_header_and_bodies(self, config):
        now = datetime(2026, 7, 23, 15, 0)
        _write_journal(config, now, [
            ("13:00", "first thought"), ("14:00", "second thought"),
        ])
        got = read_recent_journal_entries(
            config, now=now, max_hours=24, max_entries=5)
        text = format_recent_journal_for_context(got, max_hours=24)
        assert "last 24h" in text
        assert "first thought" in text
        assert text.index("first thought") < text.index("second thought")


class TestVaultJournalAppend:
    @pytest.mark.asyncio
    async def test_appends_entry(self, ctx, agent_journal):
        result = await tool_vault_journal_append(
            ctx, tags=["test", "foo"], content="Something happened.")
        assert "saved" in result.text.lower()
        # Find the journal file
        files = list(agent_journal.rglob("*.md"))
        assert len(files) == 1
        text = files[0].read_text()
        assert "Something happened." in text
        assert "**tags:** test, foo" in text

    @pytest.mark.asyncio
    async def test_appends_multiple_entries(self, ctx, agent_journal):
        await tool_vault_journal_append(ctx, tags=["a"], content="First")
        await tool_vault_journal_append(ctx, tags=["b"], content="Second")
        files = list(agent_journal.rglob("*.md"))
        assert len(files) == 1  # same day = same file
        text = files[0].read_text()
        assert "First" in text
        assert "Second" in text

    @pytest.mark.asyncio
    async def test_publishes_vault_changed_on_journal_append(
        self, ctx, agent_journal,
    ):
        captured: list[dict] = []

        async def capture(event):
            captured.append(event)

        ctx.event_bus.publish = capture
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_journal_append(
                ctx, tags=["test"], content="Something happened.",
            )
        matching = [e for e in captured if e.get("type") == "vault_changed"]
        assert len(matching) == 1
        assert matching[0]["kind"] == "journal"
        assert matching[0]["path"].endswith(".md")

    @pytest.mark.asyncio
    async def test_emits_inline_tags_alongside_bullet(self, ctx, agent_journal):
        """Phase 3 (#318): tags also surface as inline #tags in the body,
        in addition to the existing back-compat bullet."""
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_journal_append(
                ctx, tags=["rust", "async"], content="hi")
        files = list(agent_journal.rglob("*.md"))
        assert len(files) == 1
        text = files[0].read_text()
        assert "- **tags:** rust, async" in text
        assert "#rust #async" in text

    @pytest.mark.asyncio
    async def test_inline_tags_do_not_break_recent_journal_reader(
        self, ctx, agent_journal,
    ):
        """Guard against #306's `read_recent_journal_entries` regressing:
        the inline #tags line must not split entries or break parsing."""
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_journal_append(
                ctx, tags=["rust", "async"], content="hi")
        files = list(agent_journal.rglob("*.md"))
        text = files[0].read_text()

        entries = read_recent_journal_entries(
            ctx.config, now=datetime.now(), max_hours=24, max_entries=10)
        assert len(entries) == 1
        header_match = re.search(
            r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$", text, re.MULTILINE)
        assert header_match is not None
        expected_ts = datetime.strptime(header_match.group(1), "%Y-%m-%d %H:%M")
        assert entries[0].timestamp == expected_ts
        assert "hi" in entries[0].text
        assert "#rust #async" in entries[0].text

    @pytest.mark.asyncio
    async def test_inline_tags_match_extract_tags_no_duplication(
        self, ctx, agent_journal,
    ):
        from decafclaw.tags import extract_tags

        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_journal_append(
                ctx, tags=["rust", "async"], content="hi")
        files = list(agent_journal.rglob("*.md"))
        text = files[0].read_text()
        assert extract_tags(text, "journal") == {"rust", "async"}

    @pytest.mark.asyncio
    async def test_inline_tag_normalizes_leading_hash(self, ctx, agent_journal):
        """A caller-supplied tag that already starts with '#' must not
        double up to '##' in the inline line, and must still round-trip
        through extract_tags."""
        from decafclaw.tags import extract_tags

        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_journal_append(
                ctx, tags=["#Rust", "async"], content="x")
        files = list(agent_journal.rglob("*.md"))
        text = files[0].read_text()

        assert "##Rust" not in text
        assert "#Rust" in text
        assert "#async" in text
        assert extract_tags(text, "journal") == {"rust", "async"}

    @pytest.mark.asyncio
    async def test_empty_tags_no_inline_line(self, ctx, agent_journal):
        from decafclaw.tags import extract_tags

        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_journal_append(ctx, tags=[], content="x")
        files = list(agent_journal.rglob("*.md"))
        text = files[0].read_text()
        assert "- **tags:** untagged" in text

        lines = text.splitlines()
        tags_line_idx = next(
            i for i, line in enumerate(lines)
            if line.startswith("- **tags:**")
        )
        # No inline tag line between the bullet and the blank line/content.
        assert lines[tags_line_idx + 1] == ""
        assert extract_tags(text, "journal") == set()


class TestVaultSearch:
    @pytest.mark.asyncio
    async def test_substring_search_by_content(self, ctx, vault_dir):
        (vault_dir / "Drinks.md").write_text("# Drinks\n\nBoulevardier")
        (vault_dir / "Food.md").write_text("# Food\n\nPizza")
        result = await tool_vault_search(ctx, "Boulevardier")
        assert "Drinks" in result.text
        assert "Food" not in result.text

    @pytest.mark.asyncio
    async def test_search_no_results(self, ctx, vault_dir):
        result = await tool_vault_search(ctx, "nonexistent")
        assert "no" in result.text.lower()

    @pytest.mark.asyncio
    async def test_search_with_folder_filter(self, ctx, agent_pages):
        (agent_pages / "Topic.md").write_text("# Topic\n\nImportant")
        result = await tool_vault_search(ctx, "Important",
                                         folder="agent/pages")
        assert "Topic" in result.text

    @pytest.mark.asyncio
    async def test_search_empty_vault(self, ctx, config):
        # vault dir doesn't exist
        result = await tool_vault_search(ctx, "anything")
        assert "does not exist" in result.text.lower()


class TestVaultSearchTags:
    """Tag filtering on vault_search (#318 phase 4)."""

    @pytest.mark.asyncio
    async def test_query_plus_tags_drops_non_matching_results(
        self, ctx, agent_pages
    ):
        (agent_pages / "AsyncPage.md").write_text(
            "# AsyncPage\n\nWidget notes about async work. #async")
        (agent_pages / "RustPage.md").write_text(
            "# RustPage\n\nWidget notes about rust work. #rust")

        result = await tool_vault_search(ctx, "widget", tags=["async"])

        assert "AsyncPage" in result.text
        assert "RustPage" not in result.text

    @pytest.mark.asyncio
    async def test_empty_query_pure_tag_filter_is_and_by_default(
        self, ctx, agent_pages
    ):
        (agent_pages / "Both.md").write_text(
            "---\ntags: [rust, async]\n---\nbody")
        (agent_pages / "OnlyRust.md").write_text(
            "---\ntags: [rust]\n---\nbody")

        result = await tool_vault_search(ctx, "", tags=["rust", "async"])

        assert "Both" in result.text
        assert "OnlyRust" not in result.text

    @pytest.mark.asyncio
    async def test_any_tag_true_is_or(self, ctx, agent_pages):
        (agent_pages / "Both.md").write_text(
            "---\ntags: [rust, async]\n---\nbody")
        (agent_pages / "OnlyRust.md").write_text(
            "---\ntags: [rust]\n---\nbody")
        (agent_pages / "OnlyAsync.md").write_text(
            "---\ntags: [async]\n---\nbody")
        (agent_pages / "Neither.md").write_text("no tags here")

        result = await tool_vault_search(
            ctx, "", tags=["rust", "async"], any_tag=True)

        assert "Both" in result.text
        assert "OnlyRust" in result.text
        assert "OnlyAsync" in result.text
        assert "Neither" not in result.text

    @pytest.mark.asyncio
    async def test_tag_filter_spans_page_and_journal_source_types(
        self, ctx, agent_pages, agent_journal
    ):
        (agent_pages / "PageTag.md").write_text(
            "---\ntags: [shared]\n---\nbody")
        (agent_journal / "2026-07-24.md").write_text(
            "## 2026-07-24 10:00\n\n- **tags:** shared\n\nJournal content")

        result = await tool_vault_search(ctx, "", tags=["shared"])

        assert "agent/pages/PageTag" in result.text
        assert "agent/journal/2026-07-24" in result.text

    @pytest.mark.asyncio
    async def test_empty_tags_leaves_behavior_unchanged(
        self, ctx, vault_dir
    ):
        """Omitted/empty `tags` must NOT enter the pure-filter path — that
        path calls `pages_with_tags(config, [])`, which vacuously matches
        every file (empty AND). Guard against that footgun regressing.

        This used to also assert `"Foo" in result_*.text`, i.e. that an
        unconstrained search returned every page. Those two lines were
        dropped in #673, which makes exactly that call refuse and point at
        `vault_list` instead — they pinned the behavior being removed and
        were incidental to what this test is for. The `assert_not_called()`
        below is the real content: it guards the empty-AND footgun
        independently of what the unconstrained call chooses to return.

        The third call is constrained (`days=1`) but tagless, and it is NOT
        redundant — do not delete it. #673 makes the first two calls refuse
        before reaching the `if not query and req_tags:` branch this test
        exists to guard, so post-#673 they satisfy `assert_not_called()` by
        never getting there. Only a call that survives the refusal still
        exercises the `and req_tags` conjunct; without it, mutating that
        branch to `if not query:` — reintroducing the exact empty-AND footgun
        named above — would leave this test green. `Foo.md` is written
        immediately, so a 1-day window contains it.
        """
        (vault_dir / "Foo.md").write_text("hello")

        with patch(
            "decafclaw.skills.vault.tools.pages_with_tags"
        ) as mock_pages_with_tags:
            await tool_vault_search(ctx, "")
            await tool_vault_search(ctx, "", tags=[])
            await tool_vault_search(ctx, "", tags=[], days=1)

        mock_pages_with_tags.assert_not_called()

    @pytest.mark.asyncio
    async def test_source_type_and_tags_compose_in_pure_filter(
        self, ctx, agent_pages, agent_journal
    ):
        """Pure tag-filter path (empty query) must apply source_type as an
        additional filter on top of the tag match, not just tags alone
        (P4-M1)."""
        (agent_pages / "PageTag.md").write_text(
            "---\ntags: [async]\n---\nbody")
        (agent_journal / "2026-07-24.md").write_text(
            "## 2026-07-24 10:00\n\n- **tags:** async\n\nJournal content")

        result = await tool_vault_search(
            ctx, "", tags=["async"], source_type="page")

        assert "agent/pages/PageTag" in result.text
        assert "agent/journal/2026-07-24" not in result.text

    @pytest.mark.asyncio
    async def test_days_filters_pure_tag_filter(self, ctx, agent_pages):
        """Pure tag-filter path (empty query) must honor `days` recency,
        matching the semantic/substring branches — a page whose tag matches
        but is older than `days` is excluded (#318)."""
        recent = agent_pages / "RecentRust.md"
        recent.write_text("---\ntags: [rust]\n---\nbody")
        stale = agent_pages / "StaleRust.md"
        stale.write_text("---\ntags: [rust]\n---\nbody")
        _set_mtime(stale, 30)

        result = await tool_vault_search(ctx, "", tags=["rust"], days=7)

        assert "RecentRust" in result.text
        assert "StaleRust" not in result.text


class TestVaultSearchTagsSemantic:
    """Tag filtering on the SEMANTIC branch of vault_search (#318 phase 4
    review fix). `TestVaultSearchTags` above only exercises the default
    test config's `search_strategy = "substring"`; the semantic branch —
    the path the deployed agent actually uses — otherwise has zero
    coverage, including `_semantic_result_matches_tags`'s fail-open
    behavior.
    """

    @pytest.mark.asyncio
    async def test_and_filter_drops_non_matching_candidate(
        self, ctx, agent_pages, monkeypatch
    ):
        ctx.config.embedding.search_strategy = "semantic"
        (agent_pages / "AsyncPage.md").write_text(
            "# AsyncPage\n\nWidget notes about async work. #async")
        (agent_pages / "RustPage.md").write_text(
            "# RustPage\n\nWidget notes about rust work. #rust")

        async def fake_search_similar(*args, **kwargs):
            return [
                {"file_path": "agent/pages/AsyncPage.md", "similarity": 0.9,
                 "source_type": "page", "entry_text": "Widget notes async"},
                {"file_path": "agent/pages/RustPage.md", "similarity": 0.8,
                 "source_type": "page", "entry_text": "Widget notes rust"},
            ]

        monkeypatch.setattr(
            "decafclaw.embeddings.search_similar", fake_search_similar)

        result = await tool_vault_search(ctx, "widget", tags=["async"])

        assert result.data["mode"] == "semantic"
        paths = [r["path"] for r in result.data["results"]]
        assert paths == ["agent/pages/AsyncPage"]

    @pytest.mark.asyncio
    async def test_any_tag_or_filter(self, ctx, agent_pages, monkeypatch):
        ctx.config.embedding.search_strategy = "semantic"
        (agent_pages / "Both.md").write_text(
            "---\ntags: [rust, async]\n---\nbody about widgets")
        (agent_pages / "OnlyRust.md").write_text(
            "---\ntags: [rust]\n---\nbody about widgets")
        (agent_pages / "OnlyAsync.md").write_text(
            "---\ntags: [async]\n---\nbody about widgets")
        (agent_pages / "Neither.md").write_text(
            "body about widgets, no tags")

        async def fake_search_similar(*args, **kwargs):
            return [
                {"file_path": "agent/pages/Both.md", "similarity": 0.9,
                 "source_type": "page", "entry_text": "Both"},
                {"file_path": "agent/pages/OnlyRust.md", "similarity": 0.8,
                 "source_type": "page", "entry_text": "OnlyRust"},
                {"file_path": "agent/pages/OnlyAsync.md", "similarity": 0.7,
                 "source_type": "page", "entry_text": "OnlyAsync"},
                {"file_path": "agent/pages/Neither.md", "similarity": 0.6,
                 "source_type": "page", "entry_text": "Neither"},
            ]

        monkeypatch.setattr(
            "decafclaw.embeddings.search_similar", fake_search_similar)

        result = await tool_vault_search(
            ctx, "widgets", tags=["rust", "async"], any_tag=True)

        assert "Both" in result.text
        assert "OnlyRust" in result.text
        assert "OnlyAsync" in result.text
        assert "Neither" not in result.text

    @pytest.mark.asyncio
    async def test_fail_open_excludes_unreadable_candidate(
        self, ctx, agent_pages, monkeypatch
    ):
        """A candidate row whose file is missing/unreadable must be
        excluded, not raise — matching `pages_with_tags`'s per-file
        fail-open behavior."""
        ctx.config.embedding.search_strategy = "semantic"
        (agent_pages / "AsyncPage.md").write_text(
            "# AsyncPage\n\nWidget notes about async work. #async")

        async def fake_search_similar(*args, **kwargs):
            return [
                {"file_path": "agent/pages/AsyncPage.md", "similarity": 0.9,
                 "source_type": "page", "entry_text": "Widget notes async"},
                # Points at a file that was never written to disk.
                {"file_path": "agent/pages/Ghost.md", "similarity": 0.85,
                 "source_type": "page", "entry_text": "Ghost entry"},
            ]

        monkeypatch.setattr(
            "decafclaw.embeddings.search_similar", fake_search_similar)

        result = await tool_vault_search(ctx, "widget", tags=["async"])

        paths = [r["path"] for r in result.data["results"]]
        assert paths == ["agent/pages/AsyncPage"]

    @pytest.mark.asyncio
    async def test_fail_open_excludes_candidate_when_extract_tags_raises(
        self, ctx, agent_pages, monkeypatch
    ):
        """A candidate whose extract_tags call raises must be excluded
        from the results, not blow up the whole semantic search branch
        (which would fall back to the global un-filtered path instead of
        just dropping that one candidate)."""
        from decafclaw.tags import extract_tags

        ctx.config.embedding.search_strategy = "semantic"
        (agent_pages / "AsyncPage.md").write_text(
            "# AsyncPage\n\nWidget notes about async work. #async")
        (agent_pages / "BadPage.md").write_text(
            "# BadPage\n\nWidget notes. #async")

        async def fake_search_similar(*args, **kwargs):
            return [
                {"file_path": "agent/pages/AsyncPage.md", "similarity": 0.9,
                 "source_type": "page", "entry_text": "Widget notes async"},
                {"file_path": "agent/pages/BadPage.md", "similarity": 0.85,
                 "source_type": "page", "entry_text": "Widget notes bad"},
            ]

        monkeypatch.setattr(
            "decafclaw.embeddings.search_similar", fake_search_similar)

        real_extract_tags = extract_tags

        def fake_extract_tags(content, source_type):
            if "BadPage" in content:
                raise ValueError("boom")
            return real_extract_tags(content, source_type)

        monkeypatch.setattr(
            "decafclaw.skills.vault.tools.extract_tags", fake_extract_tags)

        result = await tool_vault_search(ctx, "widget", tags=["async"])

        paths = [r["path"] for r in result.data["results"]]
        assert paths == ["agent/pages/AsyncPage"]


class TestVaultList:
    @pytest.mark.asyncio
    async def test_list_pages(self, ctx, vault_dir):
        (vault_dir / "Alpha.md").write_text("# Alpha")
        (vault_dir / "Beta.md").write_text("# Beta")
        result = await tool_vault_list(ctx)
        assert "Alpha" in result
        assert "Beta" in result
        assert "2 page" in result

    @pytest.mark.asyncio
    async def test_list_with_folder(self, ctx, agent_pages):
        (agent_pages / "Topic.md").write_text("# Topic")
        result = await tool_vault_list(ctx, folder="agent/pages")
        assert "Topic" in result
        assert "1 page" in result

    @pytest.mark.asyncio
    async def test_list_with_pattern(self, ctx, vault_dir):
        (vault_dir / "Alpha.md").write_text("# Alpha")
        (vault_dir / "Beta.md").write_text("# Beta")
        result = await tool_vault_list(ctx, pattern="Alpha")
        assert "Alpha" in result
        assert "Beta" not in result

    @pytest.mark.asyncio
    async def test_empty_vault(self, ctx, config):
        result = await tool_vault_list(ctx)
        assert "does not exist" in result.lower()


class TestWikiMentionRegex:
    """Verify @[[folder/Page]] mentions work with folder paths."""

    def test_folder_path_mention(self):
        from decafclaw.memory_context import _WIKI_MENTION_RE
        text = "Check @[[agent/pages/Foo]] for details"
        matches = _WIKI_MENTION_RE.findall(text)
        assert matches == ["agent/pages/Foo"]

    def test_pipe_display_with_folder(self):
        from decafclaw.memory_context import _WIKI_MENTION_RE
        text = "See @[[agent/pages/Foo|my display text]]"
        matches = _WIKI_MENTION_RE.findall(text)
        assert matches == ["agent/pages/Foo|my display text"]

    def test_multiple_folder_mentions(self):
        from decafclaw.memory_context import parse_wiki_references
        text = "Check @[[projects/Alpha]] and @[[people/Bob]]"
        results = parse_wiki_references(text)
        pages = [r["page"] for r in results]
        assert "projects/Alpha" in pages
        assert "people/Bob" in pages


class TestVaultBacklinks:
    @pytest.mark.asyncio
    async def test_finds_backlinks(self, ctx, vault_dir):
        (vault_dir / "DecafClaw.md").write_text("# DecafClaw\n\nAn agent.")
        (vault_dir / "Les Orchard.md").write_text("# Les\n\nWorks on [[DecafClaw]].")
        (vault_dir / "Blog.md").write_text("# Blog\n\nNo links here.")
        result = await tool_vault_backlinks(ctx, "DecafClaw")
        assert "Les Orchard" in result
        assert "Blog" not in result

    @pytest.mark.asyncio
    async def test_no_backlinks(self, ctx, vault_dir):
        (vault_dir / "Orphan.md").write_text("# Orphan\n\nNobody links here.")
        result = await tool_vault_backlinks(ctx, "Orphan")
        assert "No pages" in result

    @pytest.mark.asyncio
    async def test_case_insensitive(self, ctx, vault_dir):
        (vault_dir / "Target.md").write_text("# Target")
        (vault_dir / "Linker.md").write_text("See [[target]] for details.")
        result = await tool_vault_backlinks(ctx, "Target")
        assert "Linker" in result

    @pytest.mark.asyncio
    async def test_pipe_display_syntax(self, ctx, vault_dir):
        """[[target|display]] links are matched by target, not display text."""
        (vault_dir / "Tempest (arcade game).md").write_text("# Tempest")
        (vault_dir / "Arcade Games.md").write_text(
            "Classic: [[Tempest (arcade game)|Tempest arcade game]].")
        result = await tool_vault_backlinks(ctx, "Tempest (arcade game)")
        assert "Arcade Games" in result


class TestCheckUserWriteAllowed:
    """Unit tests for `_check_user_write_allowed` — the three-tier gate.

    Phase 1 plumbing only: this verifies the helper's classification.
    Phases 2-4 wire the helper into the actual write/delete/rename tools.
    """

    def test_agent_dir_allows(self, ctx, agent_pages):
        """Paths inside agent/ skip every other check."""
        ctx.request_confirmation = _dummy_request_confirmation
        path = agent_pages / "Note.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.ALLOW

    def test_agent_journal_allows(self, ctx, agent_journal):
        """Paths inside agent/journal/ are also under agent/, so ALLOW."""
        ctx.request_confirmation = _dummy_request_confirmation
        path = agent_journal / "2026" / "2026-05-07.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.ALLOW

    def test_allowlist_allows(self, ctx, vault_dir):
        """Paths under a configured allowlist entry skip confirmation."""
        ctx.request_confirmation = _dummy_request_confirmation
        ctx.config.vault.user_writable_paths = ["creative/"]
        path = vault_dir / "creative" / "story.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.ALLOW

    def test_allowlist_normalizes_entry(self, ctx, vault_dir):
        """An entry without a trailing slash still matches its subtree."""
        ctx.request_confirmation = _dummy_request_confirmation
        ctx.config.vault.user_writable_paths = ["creative"]  # no trailing slash
        path = vault_dir / "creative" / "story.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.ALLOW

    def test_allowlist_partial_prefix_does_not_match(self, ctx, vault_dir):
        """`creative/` should NOT match `creativewriting/...` — trailing-slash anchors."""
        ctx.request_confirmation = _dummy_request_confirmation
        ctx.config.vault.user_writable_paths = ["creative/"]
        path = vault_dir / "creativewriting" / "story.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.NEEDS_CONFIRMATION

    def test_allowlist_skips_invalid_entries(self, ctx, vault_dir):
        """Bad allowlist entries are skipped (logged) without breaking the gate."""
        ctx.request_confirmation = _dummy_request_confirmation
        ctx.config.vault.user_writable_paths = ["..", "creative/"]
        path = vault_dir / "creative" / "story.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.ALLOW

    def test_grants_allow(self, ctx, vault_dir):
        """A folder grant on this conv_id allows writes under it."""
        ctx.request_confirmation = _dummy_request_confirmation
        ctx.conv_id = "conv-X"
        add_grant(ctx.config, "conv-X", "creative/")
        path = vault_dir / "creative" / "foo" / "bar.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.ALLOW

    def test_grants_scoped_to_conversation(self, ctx, vault_dir):
        """A grant for one conv_id does NOT carry over to another."""
        ctx.request_confirmation = _dummy_request_confirmation
        add_grant(ctx.config, "other-conv", "creative/")
        ctx.conv_id = "conv-X"
        path = vault_dir / "creative" / "foo.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.NEEDS_CONFIRMATION

    def test_outside_yields_needs_confirmation(self, ctx, vault_dir):
        """Outside agent/, no allowlist, no grant — confirmation required."""
        ctx.request_confirmation = _dummy_request_confirmation
        path = vault_dir / "random" / "page.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.NEEDS_CONFIRMATION

    def test_no_request_conf_yields_non_interactive_error(self, ctx, vault_dir):
        """When no UI is available, the gate signals non-interactive error."""
        ctx.request_confirmation = None
        path = vault_dir / "random" / "page.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.NON_INTERACTIVE_ERROR

    def test_agent_dir_allows_even_when_non_interactive(self, ctx, agent_pages):
        """Agent-dir writes never require confirmation, even from heartbeat ctx."""
        ctx.request_confirmation = None
        path = agent_pages / "Note.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.ALLOW

    def test_allowlist_allows_even_when_non_interactive(self, ctx, vault_dir):
        """Allowlist short-circuit beats the non-interactive check."""
        ctx.request_confirmation = None
        ctx.config.vault.user_writable_paths = ["creative/"]
        path = vault_dir / "creative" / "story.md"
        assert _check_user_write_allowed(ctx, path) == GateOutcome.ALLOW


class TestVaultGrantFolder:
    """Phase 5: vault_grant_folder requests per-conversation folder trust."""

    @pytest.mark.asyncio
    async def test_approves_and_persists(self, ctx, vault_dir):
        ctx.conv_id = "conv-X"
        ctx.request_confirmation = _dummy_request_confirmation
        with patch(
            "decafclaw.skills.vault.tools.request_confirmation",
            AsyncMock(return_value={"approved": True}),
        ):
            result = await tool_vault_grant_folder(ctx, "creative", "batch rename")
        assert "trusted" in result.text
        from decafclaw.skills.vault._grants import read_grants
        assert "creative/" in read_grants(ctx.config, "conv-X")

    @pytest.mark.asyncio
    async def test_denied_returns_error_no_persist(self, ctx, vault_dir):
        ctx.conv_id = "conv-X"
        ctx.request_confirmation = _dummy_request_confirmation
        with patch(
            "decafclaw.skills.vault.tools.request_confirmation",
            AsyncMock(return_value={"approved": False}),
        ):
            result = await tool_vault_grant_folder(ctx, "creative", "...")
        assert "denied by user" in result.text
        from decafclaw.skills.vault._grants import read_grants
        assert read_grants(ctx.config, "conv-X") == set()

    @pytest.mark.asyncio
    async def test_rejects_dotdot(self, ctx, vault_dir):
        ctx.request_confirmation = _dummy_request_confirmation
        result = await tool_vault_grant_folder(ctx, "../etc", "escape")
        assert "invalid folder" in result.text

    @pytest.mark.asyncio
    async def test_strips_leading_slash(self, ctx, vault_dir):
        """Leading `/` is stripped (consistent with vault.user_writable_paths).

        `/creative` normalizes to `creative/` and is accepted; the grant key
        in the sidecar omits the leading slash.
        """
        ctx.conv_id = "conv-X"
        ctx.request_confirmation = _dummy_request_confirmation
        with patch(
            "decafclaw.skills.vault.tools.request_confirmation",
            AsyncMock(return_value={"approved": True}),
        ):
            result = await tool_vault_grant_folder(ctx, "/creative", "batch rename")
        assert "trusted" in result.text
        from decafclaw.skills.vault._grants import read_grants
        grants = read_grants(ctx.config, "conv-X")
        assert "creative/" in grants
        assert "/creative/" not in grants
        assert "/creative" not in grants

    @pytest.mark.asyncio
    async def test_rejects_inside_agent(self, ctx, vault_dir):
        ctx.request_confirmation = _dummy_request_confirmation
        result = await tool_vault_grant_folder(ctx, "agent/pages", "...")
        assert "no grant needed" in result.text

    @pytest.mark.asyncio
    async def test_rejects_no_conv_id(self, ctx, vault_dir):
        ctx.request_confirmation = _dummy_request_confirmation
        # Empty string and None should both be rejected — the impl uses
        # `if not ctx.conv_id:` which catches both, but the contract is explicit.
        for missing in ("", None):
            ctx.conv_id = missing
            result = await tool_vault_grant_folder(ctx, "creative", "...")
            assert "conversation context" in result.text, (
                f"conv_id={missing!r} was not rejected"
            )

    @pytest.mark.asyncio
    async def test_rejects_non_interactive(self, ctx, vault_dir):
        ctx.request_confirmation = None
        result = await tool_vault_grant_folder(ctx, "creative", "...")
        assert "interactive confirmation" in result.text

    @pytest.mark.asyncio
    async def test_rejects_empty_folder(self, ctx, vault_dir):
        ctx.request_confirmation = _dummy_request_confirmation
        result = await tool_vault_grant_folder(ctx, "", "...")
        assert "folder is required" in result.text

    @pytest.mark.asyncio
    async def test_rejects_empty_reason(self, ctx, vault_dir):
        ctx.request_confirmation = _dummy_request_confirmation
        result = await tool_vault_grant_folder(ctx, "creative", "")
        assert "reason is required" in result.text

    @pytest.mark.asyncio
    async def test_grant_then_write_skips_confirmation(self, ctx, vault_dir):
        """Integration: grant a folder, then write — no second confirmation."""
        ctx.conv_id = "conv-X"
        ctx.request_confirmation = _dummy_request_confirmation
        with patch(
            "decafclaw.skills.vault.tools.request_confirmation",
            AsyncMock(return_value={"approved": True}),
        ):
            await tool_vault_grant_folder(ctx, "creative", "batch")
        # Now write — should NOT call request_confirmation again
        sentinel = AsyncMock(side_effect=AssertionError("should not have been called"))
        with (
            patch("decafclaw.skills.vault.tools.request_confirmation", sentinel),
            patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock),
        ):
            result = await tool_vault_write(ctx, "creative/foo", "content body")
        assert sentinel.call_count == 0
        assert "saved" in str(result)


def _set_mtime(path, days_ago: float):
    """Backdate a file's atime and mtime by `days_ago` days."""
    when = time.time() - days_ago * 86400
    os.utime(path, (when, when))


class TestVaultRecent:
    async def test_lists_only_recent_pages(self, ctx, agent_pages):
        (agent_pages / "fresh.md").write_text("# Fresh")
        old = agent_pages / "stale.md"
        old.write_text("# Stale")
        _set_mtime(old, 30)
        result = await tool_vault_recent(ctx, days=7)
        text = str(result)
        assert "agent/pages/fresh" in text
        assert "stale" not in text

    async def test_source_type_filter_user_only(self, ctx, vault_dir, agent_pages):
        # Human content lives outside agent/ → source_type "user".
        journals = vault_dir / "journals" / "2026"
        journals.mkdir(parents=True)
        (journals / "2026-06-24.md").write_text("# Daily note")
        (agent_pages / "ingested.md").write_text("# Ingested page")
        result = await tool_vault_recent(ctx, days=7, source_type="user")
        text = str(result)
        assert "journals/2026/2026-06-24" in text
        assert "agent/pages/ingested" not in text

    async def test_folder_scope(self, ctx, vault_dir, agent_pages):
        journals = vault_dir / "journals"
        journals.mkdir(parents=True)
        (journals / "note.md").write_text("# Note")
        (agent_pages / "page.md").write_text("# Page")
        result = await tool_vault_recent(ctx, days=7, folder="journals")
        text = str(result)
        assert "journals/note" in text
        assert "agent/pages/page" not in text

    async def test_no_recent_pages_message(self, ctx, agent_pages):
        old = agent_pages / "ancient.md"
        old.write_text("# Ancient")
        _set_mtime(old, 90)
        result = await tool_vault_recent(ctx, days=7)
        assert "No pages modified" in str(result)

    async def test_rejects_non_positive_days(self, ctx, agent_pages):
        for d in (0, -1):
            result = await tool_vault_recent(ctx, days=d)
            assert "error" in str(result).lower()

    async def test_excludes_symlinked_escape(self, ctx, vault_dir, agent_pages,
                                              tmp_path):
        # A symlinked dir inside the vault pointing outside must not leak its
        # files into the listing (resolved path escapes the vault root).
        (agent_pages / "real.md").write_text("# Real")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "leak.md").write_text("# Leak")
        os.symlink(outside, vault_dir / "sneaky")
        result = await tool_vault_recent(ctx, days=7)
        text = str(result)
        assert "agent/pages/real" in text
        assert "leak" not in text

    async def test_missing_folder_errors(self, ctx, vault_dir):
        result = await tool_vault_recent(ctx, days=7, folder="nope/missing")
        assert "does not exist" in str(result).lower()


class TestVaultTags:
    async def test_sorted_by_count_desc_with_tie_break(
        self, ctx, agent_pages, agent_journal,
    ):
        (agent_pages / "Rust.md").write_text("---\ntags: [Rust]\n---\nabout rust")
        (agent_pages / "Async.md").write_text("body mentions #Rust and #async")
        journal_day_dir = agent_journal / "2026"
        journal_day_dir.mkdir(parents=True, exist_ok=True)
        (journal_day_dir / "2026-07-24.md").write_text(
            "## 2026-07-24 10:00\n\n- **tags:** rust, zeta\n\nnotes"
        )

        result = await tool_vault_tags(ctx)

        assert result.data["tags"] == [
            {"tag": "Rust", "count": 3},
            {"tag": "async", "count": 1},
            {"tag": "zeta", "count": 1},
        ]
        assert "Rust (3)" in result.text
        assert "3 tag(s)" in result.text

    async def test_all_untagged_journal_yields_no_tags(self, ctx, agent_journal):
        """Regression guard for the shared collect_all_tags primitive: an
        all-untagged journal entry must not surface a spurious "untagged"
        tag via this tool."""
        journal_day_dir = agent_journal / "2026"
        journal_day_dir.mkdir(parents=True, exist_ok=True)
        (journal_day_dir / "2026-07-24.md").write_text(
            "## 2026-07-24 10:00\n\n- **tags:** untagged\n\nsome note"
        )

        result = await tool_vault_tags(ctx)

        assert result.data["tags"] == []
        assert "No tags found" in result.text

    async def test_empty_vault_returns_no_tags(self, ctx, vault_dir):
        result = await tool_vault_tags(ctx)
        assert result.data["tags"] == []


class TestVaultUpdateFrontmatter:
    @pytest.mark.asyncio
    async def test_fills_absent_fields(self, ctx, agent_pages):
        p = agent_pages / "topic.md"
        p.write_text("Body text.\n")
        await tool_vault_update_frontmatter(
            ctx, page="agent/pages/topic.md",
            fields={"summary": "s", "importance": 0.8})
        meta, body = parse_frontmatter(p.read_text())
        assert meta["summary"] == "s" and meta["importance"] == 0.8
        assert body.strip() == "Body text."

    @pytest.mark.asyncio
    async def test_respects_existing_value_when_not_overwrite(self, ctx, agent_pages):
        p = agent_pages / "topic.md"
        p.write_text("---\nsummary: manual\n---\nBody.\n")
        await tool_vault_update_frontmatter(
            ctx, page="agent/pages/topic.md",
            fields={"summary": "auto"}, overwrite=False)
        meta, _ = parse_frontmatter(p.read_text())
        assert meta["summary"] == "manual"

    @pytest.mark.asyncio
    async def test_overwrite_replaces(self, ctx, agent_pages):
        p = agent_pages / "topic.md"
        p.write_text("---\nimportance: 0.5\n---\nBody.\n")
        await tool_vault_update_frontmatter(
            ctx, page="agent/pages/topic.md",
            fields={"importance": 0.9}, overwrite=True)
        meta, _ = parse_frontmatter(p.read_text())
        assert meta["importance"] == 0.9

    @pytest.mark.asyncio
    async def test_clamps_importance_and_coerces_lists(self, ctx, agent_pages):
        p = agent_pages / "t.md"
        p.write_text("Body.\n")
        await tool_vault_update_frontmatter(
            ctx, page="agent/pages/t.md",
            fields={"importance": 5.0, "tags": "a"})
        meta, _ = parse_frontmatter(p.read_text())
        assert meta["importance"] == 1.0 and meta["tags"] == ["a"]

    @pytest.mark.asyncio
    async def test_reindexes_after_write(self, ctx, agent_pages):
        p = agent_pages / "t.md"
        p.write_text("Body.\n")
        with patch("decafclaw.skills.vault.tools._reindex_page",
                   new_callable=AsyncMock) as rx:
            await tool_vault_update_frontmatter(
                ctx, page="agent/pages/t.md", fields={"summary": "s"})
        rx.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_interactive_user_page_confirms_then_updates(self, ctx, vault_dir):
        """Interactive caller updating a user-owned page outside agent/ must
        hit the same gate as vault_write: confirmation requested, then the
        write proceeds on approval."""
        p = vault_dir / "creative" / "foo.md"
        p.parent.mkdir(parents=True)
        p.write_text("Body.\n")
        ctx.request_confirmation = _dummy_request_confirmation
        with (
            patch("decafclaw.skills.vault.tools.request_confirmation",
                  AsyncMock(return_value={"approved": True})) as mock_conf,
            patch("decafclaw.skills.vault.tools._reindex_page",
                  new_callable=AsyncMock),
        ):
            await tool_vault_update_frontmatter(
                ctx, page="creative/foo", fields={"summary": "s"})
        assert mock_conf.called
        meta, _ = parse_frontmatter(p.read_text())
        assert meta["summary"] == "s"

    @pytest.mark.asyncio
    async def test_interactive_user_page_denied_returns_error_no_update(
        self, ctx, vault_dir,
    ):
        p = vault_dir / "creative" / "foo.md"
        p.parent.mkdir(parents=True)
        p.write_text("Body.\n")
        ctx.request_confirmation = _dummy_request_confirmation
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock(return_value={"approved": False})):
            result = await tool_vault_update_frontmatter(
                ctx, page="creative/foo", fields={"summary": "s"})
        assert "denied by user" in result.text
        meta, _ = parse_frontmatter(p.read_text())
        assert "summary" not in meta

    @pytest.mark.asyncio
    async def test_noop_merge_skips_write_reindex_publish(self, ctx, agent_pages):
        """When the merge produces no change (e.g. overwrite=False on an
        already-populated field), the tool must not rewrite the file, reindex,
        or publish vault_changed."""
        p = agent_pages / "topic.md"
        original = "---\nsummary: existing\n---\nBody.\n"
        p.write_text(original)
        with (
            patch("decafclaw.skills.vault.tools._reindex_page",
                  new_callable=AsyncMock) as rx,
            patch("decafclaw.skills.vault.tools.publish_vault_changed",
                  new_callable=AsyncMock) as pub,
        ):
            result = await tool_vault_update_frontmatter(
                ctx, page="agent/pages/topic.md",
                fields={"summary": "different"}, overwrite=False)
        assert p.read_text() == original
        rx.assert_not_awaited()
        pub.assert_not_awaited()
        assert result.data["changed"] == []

    @pytest.mark.asyncio
    async def test_non_interactive_user_page_updates_without_gate(
        self, ctx, vault_dir,
    ):
        """Non-interactive callers (scheduled dream/garden) can't prompt, so
        vault-wide reach proceeds ungated — this is the tool's intended
        maintenance purpose."""
        p = vault_dir / "creative" / "foo.md"
        p.parent.mkdir(parents=True)
        p.write_text("Body.\n")
        ctx.request_confirmation = None
        with patch("decafclaw.skills.vault.tools._reindex_page",
                   new_callable=AsyncMock):
            result = await tool_vault_update_frontmatter(
                ctx, page="creative/foo", fields={"summary": "s"})
        assert "updated" in result.text.lower()
        meta, _ = parse_frontmatter(p.read_text())
        assert meta["summary"] == "s"
