"""Tests for vault tools."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.skills.vault.tools import (
    resolve_page,
    tool_vault_backlinks,
    tool_vault_delete,
    tool_vault_journal_append,
    tool_vault_list,
    tool_vault_read,
    tool_vault_rename,
    tool_vault_search,
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
        assert "saved" in result.lower()
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
        """vault_write must refuse paths outside the agent folder, mirroring
        vault_delete / vault_rename behavior."""
        result = await tool_vault_write(ctx, "StrayRootPage", "# Stray")
        assert "error" in result.text.lower()
        assert "agent folder" in result.text.lower()
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
        assert "saved" in result.lower() or "saved" in str(result).lower()
        assert (vault_dir / "agent" / "pages" / "Test.md").exists()
        assert not (vault_dir / "agent" / "pages" / "Test.md.md").exists()


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
        # A page directly at the vault root is outside agent/
        (vault_dir / "User Notes.md").write_text("mine")
        result = await tool_vault_delete(ctx, "User Notes")
        assert "error" in result.text.lower()
        assert "agent folder" in result.text.lower()
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
        (agent_pages / "Inside.md").write_text("x")
        result = await tool_vault_rename(
            ctx, "agent/pages/Inside", "Escaped"
        )
        assert "error" in result.text.lower()
        assert "agent folder" in result.text.lower()
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


class TestVaultJournalAppend:
    @pytest.mark.asyncio
    async def test_appends_entry(self, ctx, agent_journal):
        result = await tool_vault_journal_append(
            ctx, tags=["test", "foo"], content="Something happened.")
        assert "saved" in result.lower()
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
        from decafclaw.agent import _WIKI_MENTION_RE
        text = "Check @[[agent/pages/Foo]] for details"
        matches = _WIKI_MENTION_RE.findall(text)
        assert matches == ["agent/pages/Foo"]

    def test_pipe_display_with_folder(self):
        from decafclaw.agent import _WIKI_MENTION_RE
        text = "See @[[agent/pages/Foo|my display text]]"
        matches = _WIKI_MENTION_RE.findall(text)
        assert matches == ["agent/pages/Foo|my display text"]

    def test_multiple_folder_mentions(self):
        from decafclaw.agent import _parse_wiki_references
        text = "Check @[[projects/Alpha]] and @[[people/Bob]]"
        results = _parse_wiki_references(text)
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
