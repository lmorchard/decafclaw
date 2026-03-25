"""Tests for proactive memory context retrieval."""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.config_types import MemoryContextConfig
from decafclaw.memory_context import (
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
        assert "Wiki (relevance: 0.85)" in text
        assert "Memory (relevance: 0.60)" in text
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
    async def test_respects_token_budget(self, config):
        from dataclasses import replace
        cfg = replace(config, memory_context=MemoryContextConfig(max_tokens=100))
        fake_embedding = [1.0] * 768
        # Each entry is 400 chars = 100 tokens
        search_results = [
            _make_result(text="a" * 400, similarity=0.8),
            _make_result(text="b" * 400, similarity=0.7),
            _make_result(text="c" * 400, similarity=0.6),
        ]
        with patch("decafclaw.memory_context.embed_text", new_callable=AsyncMock, return_value=fake_embedding), \
             patch("decafclaw.memory_context.search_similar_sync", return_value=search_results):
            results = await retrieve_memory_context(cfg, "hello")
            assert len(results) == 1
