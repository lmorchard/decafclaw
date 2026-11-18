"""Tests for CompactionExtension and PRE_COMPACTION dispatch (#1490)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.archive import append_message
from decafclaw.compaction import compact_history
from decafclaw.events import TurnLifecycle
from decafclaw.extensions import ExtensionManager
from decafclaw.extensions.compaction import (
    CompactionExtension,
    find_compaction_extension,
)

LIFECYCLE = "decafclaw.context.call_llm"


@pytest.fixture
def populated_archive(config, ctx):
    ctx.config.compaction.memory_sweep_enabled = False
    for i in range(8):
        append_message(config, "test-conv", {"role": "user", "content": f"message {i}"})
        append_message(config, "test-conv", {"role": "assistant", "content": f"response {i}"})
    return "test-conv"


def _summary_response() -> dict:
    return {"content": "Summary of earlier conversation.", "tool_calls": None, "role": "assistant", "usage": None}


class TestPreCompactionDispatch:
    async def test_dispatched_immediately_before_summarization(self, ctx, populated_archive):
        order: list[str] = []

        def hook(context, history):
            assert history is not None
            order.append("hook")

        async def fake_summarize(*args, **kwargs):
            order.append("summarize")
            return _summary_response()

        ctx.add_interceptor(TurnLifecycle.PRE_COMPACTION, hook)
        ctx.config.compaction.preserve_turns = 5
        history = [{"role": "user", "content": f"message {i}"} for i in range(8)]

        with patch(LIFECYCLE, side_effect=fake_summarize):
            assert await compact_history(ctx, history) is True

        assert order == ["hook", "summarize"]

    async def test_raising_hook_is_isolated(self, ctx, populated_archive):
        called: list[str] = []

        def failing(context, history):
            raise RuntimeError("flush failed")

        def good(context, history):
            called.append("good")

        ctx.add_interceptor(TurnLifecycle.PRE_COMPACTION, failing)
        ctx.add_interceptor(TurnLifecycle.PRE_COMPACTION, good)
        ctx.config.compaction.preserve_turns = 5
        history = [{"role": "user", "content": f"message {i}"} for i in range(8)]

        with patch(LIFECYCLE, new_callable=AsyncMock, return_value=_summary_response()):
            assert await compact_history(ctx, history) is True

        assert called == ["good"]

    async def test_slow_hook_is_cancelled_without_failing_compaction(self, ctx, populated_archive):
        called: list[str] = []

        async def slow(context, history):
            await asyncio.sleep(0.5)
            called.append("slow")

        def fast(context, history):
            called.append("fast")

        ctx.config.lifecycle.pre_compaction_timeout = 0.05
        ctx.add_interceptor(TurnLifecycle.PRE_COMPACTION, slow)
        ctx.add_interceptor(TurnLifecycle.PRE_COMPACTION, fast)
        ctx.config.compaction.preserve_turns = 5
        history = [{"role": "user", "content": f"message {i}"} for i in range(8)]

        with patch(LIFECYCLE, new_callable=AsyncMock, return_value=_summary_response()):
            assert await compact_history(ctx, history) is True

        assert called == ["fast"]

    async def test_hook_declared_timeout_overrides_shared_default(self, ctx, populated_archive):
        called: list[str] = []

        class SlowCollector:
            pre_compaction_timeout = 0.5

            async def hook(self, context, history):
                await asyncio.sleep(0.1)
                called.append("slow-but-allowed")

        ctx.config.lifecycle.pre_compaction_timeout = 0.05
        ctx.add_interceptor(TurnLifecycle.PRE_COMPACTION, SlowCollector().hook)
        ctx.config.compaction.preserve_turns = 5
        history = [{"role": "user", "content": f"message {i}"} for i in range(8)]

        with patch(LIFECYCLE, new_callable=AsyncMock, return_value=_summary_response()):
            assert await compact_history(ctx, history) is True

        assert called == ["slow-but-allowed"]

    async def test_cancellation_propagates(self, ctx, populated_archive):
        async def cancelling_hook(context, history):
            raise asyncio.CancelledError()

        ctx.add_interceptor(TurnLifecycle.PRE_COMPACTION, cancelling_hook)
        ctx.config.compaction.preserve_turns = 5
        history = [{"role": "user", "content": f"message {i}"} for i in range(8)]

        with patch(LIFECYCLE, new_callable=AsyncMock, return_value=_summary_response()):
            with pytest.raises(asyncio.CancelledError):
                await compact_history(ctx, history)


class TestPolicy:
    async def test_compacts_when_prompt_exceeds_threshold(self, ctx):
        ctx.config.compaction.max_tokens = 1000
        ext = CompactionExtension({}, ctx.config)
        with patch("decafclaw.compaction.compact_history", new_callable=AsyncMock, return_value=True) as compact:
            assert await ext.maybe_compact(ctx, [], 1001) is True
            compact.assert_awaited_once()

    async def test_skips_when_prompt_under_threshold(self, ctx):
        ctx.config.compaction.max_tokens = 1000
        ext = CompactionExtension({}, ctx.config)
        with patch("decafclaw.compaction.compact_history", new_callable=AsyncMock, return_value=True) as compact:
            assert await ext.maybe_compact(ctx, [], 1000) is False
        compact.assert_not_awaited()

    async def test_lowered_threshold_wins(self, ctx):
        ctx.config.compaction.max_tokens = 1000
        ext = CompactionExtension({}, ctx.config)
        assert ext.threshold(ctx, ctx.config, lowered=500) == 500

    async def test_threshold_ratio_option_overrides_legacy(self, ctx):
        ctx.config.compaction.max_tokens = 100000
        ctx.config.compaction.threshold_ratio = None
        ctx.config.llm.context_window_size = 128000
        ext = CompactionExtension({"threshold_ratio": 0.1}, ctx.config)

        assert ext.threshold(ctx, ctx.config) == 12800
        with patch("decafclaw.compaction.compact_history", new_callable=AsyncMock, return_value=True) as compact:
            assert await ext.maybe_compact(ctx, [], 13000) is True
        compact.assert_awaited_once()

    async def test_max_tokens_option_overrides_legacy(self, ctx):
        ctx.config.compaction.max_tokens = 100000
        ext = CompactionExtension({"max_tokens": 2000}, ctx.config)
        assert ext.threshold(ctx, ctx.config) == 2000

    def test_overflow_threshold_uses_usage_then_estimate(self):
        assert CompactionExtension.overflow_threshold(10000, 0) == 8000
        assert CompactionExtension.overflow_threshold(0, 5000) == 4000
        assert CompactionExtension.overflow_threshold(0, 0) == 8000  # 10000 default
        assert CompactionExtension.overflow_threshold(100, 0) == 1000  # floor

    async def test_compact_is_fail_open(self, ctx):
        ext = CompactionExtension({}, ctx.config)
        with patch("decafclaw.compaction.compact_history", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            assert await ext.compact(ctx, []) is False

    async def test_disabled_extension_yields_no_policy(self, ctx):
        ctx.extension_manager = ExtensionManager(ctx.event_bus, ctx.config)
        assert find_compaction_extension(ctx) is None

    async def test_alias_binding_is_found(self, ctx):
        manager = ExtensionManager(ctx.event_bus, ctx.config)
        ext = CompactionExtension({}, ctx.config)
        manager.register("my_compact", ext)
        manager.enable("my_compact")
        ctx.extension_manager = manager

        assert find_compaction_extension(ctx) is ext

    async def test_maybe_compact_noops_without_manager(self, ctx):
        ctx.extension_manager = None
        with patch("decafclaw.compaction.compact_history", new_callable=AsyncMock, return_value=True) as compact:
            from decafclaw.agent import _maybe_compact

            await _maybe_compact(ctx, ctx.config, [], prompt_tokens=10_000_000)
        compact.assert_not_awaited()

    async def test_maybe_compact_uses_extension_policy(self, ctx):
        ctx.config.compaction.max_tokens = 1000
        with patch("decafclaw.compaction.compact_history", new_callable=AsyncMock, return_value=True) as compact:
            from decafclaw.agent import _maybe_compact

            await _maybe_compact(ctx, ctx.config, [], prompt_tokens=1001)
        compact.assert_awaited_once()

    def test_malformed_options_are_ignored(self, ctx):
        ctx.config.compaction.max_tokens = 100000
        ext = CompactionExtension({"max_tokens": "lots", "threshold_ratio": "half"}, ctx.config)
        assert ext.threshold(ctx, ctx.config) == 100000
