"""Token-budget tests: context-window sizing and fixed-token accounting.

Split from the former monolithic ``test_context_composer.py`` by concern (#1420)."""

import pytest

from decafclaw.context_composer import ContextComposer
from decafclaw.context_types import SourceEntry


class TestGetContextWindowSize:
    def test_prefers_context_window_size(self, config):
        config.llm.context_window_size = 200000
        config.compaction.max_tokens = 100000
        composer = ContextComposer()
        assert composer._get_context_window_size(config) == 200000

    def test_falls_back_to_compaction_max_tokens(self, config):
        config.llm.context_window_size = 0
        config.compaction.max_tokens = 100000
        composer = ContextComposer()
        assert composer._get_context_window_size(config) == 100000

    @pytest.mark.asyncio
    async def test_auto_detects_context_window_size(self, config):
        class MockProvider:
            async def complete(self, *args, **kwargs):
                return {}

            async def embed(self, *args, **kwargs):
                return []

            async def get_model_info(self, model: str, **kwargs):
                return {"inputTokenLimit": 2097152}

        from decafclaw.config_types import ModelConfig
        from decafclaw.llm.registry import clear_providers, register_provider

        try:
            register_provider("mock", MockProvider())
            config.model_configs["test-model"] = ModelConfig(
                provider="mock", model="gemini-test", context_window_size=0
            )
            config.default_model = "test-model"

            from decafclaw.llm import ensure_model_context_window

            size = await ensure_model_context_window(config, "test-model")
            assert size == 2097152
            assert config.model_configs["test-model"].context_window_size == 2097152
        finally:
            clear_providers()

    @pytest.mark.asyncio
    async def test_default_table_survives_model_info_failure(self, config):
        """A raise from get_model_info must not hide the provider defaults table (#1830)."""

        class MockProvider:
            async def get_model_info(self, model: str, **kwargs):
                raise RuntimeError("discovery down")

            def default_context_window(self, model: str):
                return 111111

        from decafclaw.config_types import ModelConfig
        from decafclaw.llm import _failed_model_info, ensure_model_context_window
        from decafclaw.llm.registry import clear_providers, register_provider

        clear_providers()
        register_provider("mock", MockProvider())
        try:
            config.model_configs["test-model"] = ModelConfig(provider="mock", model="m", context_window_size=0)
            _failed_model_info.discard(("mock", "m"))
            size = await ensure_model_context_window(config, "test-model")
            assert size == 111111
            assert config.model_configs["test-model"].context_window_size == 111111
        finally:
            clear_providers()


class TestComputeFixedTokens:
    def test_fixed_total_charges_mention_entries(self):
        """Mention entries are fixed cost, not part of the dynamic pool (#1365)."""
        composer = ContextComposer()
        sources = [
            SourceEntry(source="system_prompt", tokens_estimated=100, items_included=1),
            SourceEntry(source="tools", tokens_estimated=50, items_included=2),
            SourceEntry(source="user_message", tokens_estimated=10, items_included=1),
            SourceEntry(source="mentions", tokens_estimated=700, items_included=1),
        ]
        assert composer._compute_fixed_tokens(sources, history_tokens=25) == 885
