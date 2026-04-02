"""Tests for proactive memory context retrieval."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.config_types import MemoryContextConfig
from decafclaw.memory_context import (
    _WIKI_LINK_RE,
    _enrich_results,
    _expand_graph_links,
    _trim_to_token_budget,
    format_memory_context,
    retrieve_memory_context,
)


def _make_result(text="entry", source_type="memory", similarity=0.5):
    return {"entry_text": text, "source_type": source_type, "similarity": similarity}


class TestTrimToTokenBudget:
    def test_empty(self):
        assert _trim_to_token_budget([], 500) == []

    def test_within_budget(self):
        results = [_make_result(text="a" * 100)]  # 25 tokens
        assert len(_trim_to_token_budget(results, 500)) == 1

    def test_exceeds_budget(self):
        results = [
            _make_result(text="a" * 400),  # 100 tokens
            _make_result(text="b" * 400),  # 100 tokens
            _make_result(text="c" * 400),  # 100 tokens
        ]
        trimmed = _trim_to_token_budget(results, 200)
        assert len(trimmed) == 2

    def test_first_entry_always_included(self):
        """Even if the first entry exceeds the budget, include it."""
        results = [_make_result(text="a" * 4000)]  # 1000 tokens
        trimmed = _trim_to_token_budget(results, 500)
        assert len(trimmed) == 1


class TestFormatMemoryContext:
    def test_basic_format(self):
        results = [
            _make_result(text="Les likes Boulevardiers", source_type="wiki", similarity=0.85),
            _make_result(text="Discussed project timeline", source_type="memory", similarity=0.6),
        ]
        text = format_memory_context(results)
        assert "[Automatically retrieved context" in text
        assert "Wiki (score: 0.85)" in text
        assert "Memory (score: 0.60)" in text
        assert "Les likes Boulevardiers" in text

    def test_empty_results(self):
        text = format_memory_context([])
        assert "[Automatically retrieved context" in text


class TestRetrieveMemoryContext:
    @pytest.mark.asyncio
    async def test_disabled(self, config):
        from dataclasses import replace
        cfg = replace(config, memory_context=MemoryContextConfig(enabled=False))
        results = await retrieve_memory_context(cfg, "hello")
        assert results == []

    @pytest.mark.asyncio
    async def test_no_embedding_model(self, config):
        from dataclasses import replace

        from decafclaw.config_types import EmbeddingConfig
        cfg = replace(config, embedding=EmbeddingConfig(model=""))
        results = await retrieve_memory_context(cfg, "hello")
        assert results == []

    @pytest.mark.asyncio
    async def test_embed_failure_returns_empty(self, config):
        with patch("decafclaw.memory_context.embed_text", new_callable=AsyncMock, return_value=None):
            results = await retrieve_memory_context(config, "hello")
            assert results == []

    @pytest.mark.asyncio
    async def test_filters_by_threshold(self, config):
        fake_embedding = [1.0] * 768
        search_results = [
            _make_result(text="high", similarity=0.8),
            _make_result(text="low", similarity=0.1),
        ]
        with patch("decafclaw.memory_context.embed_text", new_callable=AsyncMock, return_value=fake_embedding), \
             patch("decafclaw.memory_context.search_similar_sync", return_value=search_results):
            results = await retrieve_memory_context(config, "hello")
            assert len(results) == 1
            assert results[0]["entry_text"] == "high"

    @pytest.mark.asyncio
    async def test_respects_max_results(self, config):
        from dataclasses import replace
        cfg = replace(config, memory_context=MemoryContextConfig(max_results=2))
        fake_embedding = [1.0] * 768
        search_results = [_make_result(similarity=0.8) for _ in range(5)]
        with patch("decafclaw.memory_context.embed_text", new_callable=AsyncMock, return_value=fake_embedding), \
             patch("decafclaw.memory_context.search_similar_sync", return_value=search_results):
            results = await retrieve_memory_context(cfg, "hello")
            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_fail_open_on_exception(self, config):
        with patch("decafclaw.memory_context.embed_text", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            results = await retrieve_memory_context(config, "hello")
            assert results == []

    @pytest.mark.asyncio
    async def test_returns_all_candidates_for_composer(self, config):
        """retrieve_memory_context returns all candidates — the composer handles budget trimming."""
        from dataclasses import replace
        cfg = replace(config, memory_context=MemoryContextConfig(max_tokens=100))
        fake_embedding = [1.0] * 768
        # Each entry is 400 chars = 100 tokens; all 3 should be returned
        search_results = [
            _make_result(text="a" * 400, similarity=0.8),
            _make_result(text="b" * 400, similarity=0.7),
            _make_result(text="c" * 400, similarity=0.6),
        ]
        with patch("decafclaw.memory_context.embed_text", new_callable=AsyncMock, return_value=fake_embedding), \
             patch("decafclaw.memory_context.search_similar_sync", return_value=search_results):
            results = await retrieve_memory_context(cfg, "hello")
            assert len(results) == 3


# -- Wiki-link regex -----------------------------------------------------------


class TestWikiLinkRegex:
    def test_simple_link(self):
        assert _WIKI_LINK_RE.findall("See [[PageName]] for details") == ["PageName"]

    def test_display_text(self):
        assert _WIKI_LINK_RE.findall("See [[Target|display text]]") == ["Target"]

    def test_multiple_links(self):
        text = "Links: [[Alpha]], [[Beta|b]], and [[Gamma]]"
        assert _WIKI_LINK_RE.findall(text) == ["Alpha", "Beta", "Gamma"]

    def test_no_links(self):
        assert _WIKI_LINK_RE.findall("No links here") == []

    def test_nested_brackets(self):
        # Should not match malformed syntax
        assert _WIKI_LINK_RE.findall("Not a [[link") == []


# -- Enrich results ------------------------------------------------------------


class TestEnrichResults:
    def test_adds_defaults(self, config):
        results = [_make_result(text="entry", source_type="journal")]
        enriched = _enrich_results(config, results)
        assert enriched[0]["importance"] == 0.5
        assert "modified_at" in enriched[0]

    def test_reads_importance_from_frontmatter(self, config, tmp_path):
        # Create a vault page with frontmatter
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        page = vault_dir / "test.md"
        page.write_text("---\nimportance: 0.9\n---\n# Test")
        config.vault.vault_path = str(vault_dir)
        results = [{"entry_text": "test", "source_type": "page", "similarity": 0.8,
                     "file_path": "test.md"}]
        enriched = _enrich_results(config, results)
        assert enriched[0]["importance"] == 0.9

    def test_fail_open_on_missing_file(self, config):
        results = [{"entry_text": "entry", "source_type": "page", "similarity": 0.5,
                     "file_path": "nonexistent.md"}]
        enriched = _enrich_results(config, results)
        assert enriched[0]["importance"] == 0.5  # default


# -- Graph expansion -----------------------------------------------------------


class TestExpandGraphLinks:
    def test_expands_wiki_links(self, config, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        # Source page with a link
        source = vault_dir / "source.md"
        source.write_text("# Source\nSee [[linked]] for more.")
        # Linked page
        linked = vault_dir / "linked.md"
        linked.write_text("# Linked\nContent here.")
        config.vault.vault_path = str(vault_dir)
        results = [{"entry_text": "source content", "file_path": "source.md",
                     "similarity": 0.8, "source_type": "page"}]
        expanded = _expand_graph_links(config, results, similarity_discount=0.7)
        assert len(expanded) == 2  # original + linked
        linked_result = [r for r in expanded if r.get("source_type") == "graph_expansion"]
        assert len(linked_result) == 1
        assert linked_result[0]["linked_from"] == "source.md"
        assert linked_result[0]["similarity"] == pytest.approx(0.56)  # 0.8 * 0.7

    def test_resolves_with_unresolved_vault_root(self, config, tmp_path):
        """Regression: vault_root must be resolved before relative_to comparison.

        resolve_page returns resolved (absolute) paths. If vault_root is
        relative, relative_to fails and all links appear "outside vault root".
        """
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        source = vault_dir / "source.md"
        source.write_text("See [[linked]]")
        linked = vault_dir / "linked.md"
        linked.write_text("# Linked page")
        # Set vault_path as a relative-looking string (not pre-resolved)
        config.vault.vault_path = str(vault_dir)
        results = [{"entry_text": "source", "file_path": "source.md",
                     "similarity": 0.8, "source_type": "page"}]
        expanded = _expand_graph_links(config, results)
        graph_results = [r for r in expanded if r.get("source_type") == "graph_expansion"]
        assert len(graph_results) == 1, "linked page should be found despite unresolved vault_root"

    def test_deduplicates(self, config, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        # Two pages that link to the same target
        a = vault_dir / "a.md"
        a.write_text("See [[target]]")
        b = vault_dir / "b.md"
        b.write_text("Also [[target]]")
        target = vault_dir / "target.md"
        target.write_text("# Target")
        config.vault.vault_path = str(vault_dir)
        results = [
            {"entry_text": "a", "file_path": "a.md", "similarity": 0.8, "source_type": "page"},
            {"entry_text": "b", "file_path": "b.md", "similarity": 0.7, "source_type": "page"},
        ]
        expanded = _expand_graph_links(config, results)
        # target.md should appear only once
        target_results = [r for r in expanded if "target.md" in r.get("file_path", "")]
        assert len(target_results) == 1

    def test_skips_dangling_links(self, config, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        source = vault_dir / "source.md"
        source.write_text("See [[nonexistent]]")
        config.vault.vault_path = str(vault_dir)
        results = [{"entry_text": "source", "file_path": "source.md",
                     "similarity": 0.8, "source_type": "page"}]
        expanded = _expand_graph_links(config, results)
        assert len(expanded) == 1  # only the original, no expansion

    def test_empty_results(self, config):
        expanded = _expand_graph_links(config, [])
        assert expanded == []
