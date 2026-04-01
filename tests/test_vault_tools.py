"""Tests for vault tools."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.skills.vault.tools import (
    resolve_page,
    tool_vault_backlinks,
    tool_vault_journal_append,
    tool_vault_list,
    tool_vault_read,
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
    async def test_creates_subdirectory(self, ctx, vault_dir):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_vault_write(ctx, "agent/pages/people/Alice", "# Alice")
        assert (vault_dir / "agent" / "pages" / "people" / "Alice.md").exists()

    @pytest.mark.asyncio
    async def test_rejects_empty_content(self, ctx, vault_dir):
        result = await tool_vault_write(ctx, "agent/pages/Empty", "")
        assert "error" in result.text.lower()


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
