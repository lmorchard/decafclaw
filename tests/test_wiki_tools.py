"""Tests for wiki tools."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.skills.wiki.tools import (
    tool_wiki_backlinks,
    tool_wiki_list,
    tool_wiki_read,
    tool_wiki_search,
    tool_wiki_write,
)


@pytest.fixture
def wiki_dir(config):
    """Create the wiki directory."""
    d = config.workspace_path / "wiki"
    d.mkdir(parents=True)
    return d


class TestWikiRead:
    @pytest.mark.asyncio
    async def test_read_existing_page(self, ctx, wiki_dir):
        (wiki_dir / "Test Page.md").write_text("# Test Page\n\nContent here.")
        result = await tool_wiki_read(ctx, "Test Page")
        assert "Content here." in result

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, ctx, wiki_dir):
        result = await tool_wiki_read(ctx, "Nope")
        assert "not found" in result.text

    @pytest.mark.asyncio
    async def test_read_rejects_path_traversal(self, ctx, wiki_dir, config):
        memories = config.workspace_path / "memories"
        memories.mkdir(parents=True)
        (memories / "secret.md").write_text("super secret")
        result = await tool_wiki_read(ctx, "../memories/secret")
        assert "not found" in result.text
        assert "super secret" not in getattr(result, "text", "")

    @pytest.mark.asyncio
    async def test_read_rejects_absolute_path(self, ctx, wiki_dir):
        result = await tool_wiki_read(ctx, "/etc/passwd")
        assert "not found" in result.text

    @pytest.mark.asyncio
    async def test_read_in_subdirectory(self, ctx, wiki_dir):
        sub = wiki_dir / "people"
        sub.mkdir()
        (sub / "Alice.md").write_text("# Alice\n\nA person.")
        result = await tool_wiki_read(ctx, "Alice")
        assert "A person." in result


class TestWikiWrite:
    @pytest.mark.asyncio
    async def test_create_new_page(self, ctx, wiki_dir):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            result = await tool_wiki_write(ctx, "New Page", "# New Page\n\nFresh.")
        assert "saved" in result.lower()
        assert (wiki_dir / "New Page.md").exists()
        assert "Fresh." in (wiki_dir / "New Page.md").read_text()

    @pytest.mark.asyncio
    async def test_overwrite_existing(self, ctx, wiki_dir):
        (wiki_dir / "Existing.md").write_text("Old content.")
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_wiki_write(ctx, "Existing", "New content.")
        assert "New content." in (wiki_dir / "Existing.md").read_text()

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, ctx, wiki_dir):
        result = await tool_wiki_write(ctx, "../../../etc/passwd", "hack")
        assert "error" in result.text.lower()

    @pytest.mark.asyncio
    async def test_rejects_dotdot_in_name(self, ctx, wiki_dir):
        result = await tool_wiki_write(ctx, "foo/../bar", "hack")
        assert "error" in result.text.lower()

    @pytest.mark.asyncio
    async def test_creates_subdirectory(self, ctx, wiki_dir):
        with patch("decafclaw.embeddings.index_entry", new_callable=AsyncMock):
            await tool_wiki_write(ctx, "people/Alice", "# Alice")
        assert (wiki_dir / "people" / "Alice.md").exists()


class TestWikiSearch:
    @pytest.mark.asyncio
    async def test_search_by_content(self, ctx, wiki_dir):
        (wiki_dir / "Drinks.md").write_text("# Drinks\n\nBoulevardier, Old Fashioned")
        (wiki_dir / "Food.md").write_text("# Food\n\nPizza, Tacos")
        result = await tool_wiki_search(ctx, "Boulevardier")
        assert "Drinks" in result
        assert "Food" not in result

    @pytest.mark.asyncio
    async def test_search_by_title(self, ctx, wiki_dir):
        (wiki_dir / "DecafClaw.md").write_text("# DecafClaw\n\nAn agent.")
        result = await tool_wiki_search(ctx, "DecafClaw")
        assert "DecafClaw" in result

    @pytest.mark.asyncio
    async def test_search_no_results(self, ctx, wiki_dir):
        result = await tool_wiki_search(ctx, "nonexistent")
        assert "no" in result.lower()

    @pytest.mark.asyncio
    async def test_search_empty_wiki(self, ctx, config):
        result = await tool_wiki_search(ctx, "anything")
        assert "no" in result.lower()


class TestWikiList:
    @pytest.mark.asyncio
    async def test_list_pages(self, ctx, wiki_dir):
        (wiki_dir / "Alpha.md").write_text("# Alpha")
        (wiki_dir / "Beta.md").write_text("# Beta")
        result = await tool_wiki_list(ctx)
        assert "Alpha" in result
        assert "Beta" in result
        assert "2 page" in result

    @pytest.mark.asyncio
    async def test_list_with_pattern(self, ctx, wiki_dir):
        (wiki_dir / "Alpha.md").write_text("# Alpha")
        (wiki_dir / "Beta.md").write_text("# Beta")
        result = await tool_wiki_list(ctx, pattern="Alpha")
        assert "Alpha" in result
        assert "Beta" not in result

    @pytest.mark.asyncio
    async def test_empty_wiki(self, ctx, config):
        result = await tool_wiki_list(ctx)
        assert "no" in result.lower()


class TestWikiBacklinks:
    @pytest.mark.asyncio
    async def test_finds_backlinks(self, ctx, wiki_dir):
        (wiki_dir / "DecafClaw.md").write_text("# DecafClaw\n\nAn agent.")
        (wiki_dir / "Les Orchard.md").write_text("# Les\n\nWorks on [[DecafClaw]].")
        (wiki_dir / "Blog.md").write_text("# Blog\n\nNo links here.")
        result = await tool_wiki_backlinks(ctx, "DecafClaw")
        assert "Les Orchard" in result
        assert "Blog" not in result

    @pytest.mark.asyncio
    async def test_no_backlinks(self, ctx, wiki_dir):
        (wiki_dir / "Orphan.md").write_text("# Orphan\n\nNobody links here.")
        result = await tool_wiki_backlinks(ctx, "Orphan")
        assert "No pages" in result

    @pytest.mark.asyncio
    async def test_case_insensitive(self, ctx, wiki_dir):
        (wiki_dir / "Target.md").write_text("# Target")
        (wiki_dir / "Linker.md").write_text("See [[target]] for details.")
        result = await tool_wiki_backlinks(ctx, "Target")
        assert "Linker" in result
