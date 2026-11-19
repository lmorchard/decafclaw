"""Recalled memory facts surface in WebSocket payloads (#1514).

Covers both wire paths: the live ``message_complete`` frame and the
``conv_history`` replay. The raw ``<recalled_facts>`` prompt envelope is
plumbing and stays out of the web transcript; the assistant row carries the
atomic fact list instead.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.archive import append_message
from decafclaw.config import Config
from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.events import EventBus
from decafclaw.media import ToolResult
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _handle_load_history, _subscribe_to_conv


@pytest.fixture
def ws_context(tmp_path):
    config = Config()
    config.agent.data_home = str(tmp_path)
    bus = EventBus()
    manager = ConversationManager(config, bus)

    sent = []
    state = {
        "config": config,
        "manager": manager,
        "event_bus": bus,
        "conv_subscriptions": {},
    }
    index = {"conv-recall": MagicMock(user_id="alice", conv_id="conv-recall")}
    return state, index, sent, manager, config


class TestRecalledFactsWebSocket:
    @pytest.mark.asyncio
    async def test_message_complete_carries_recalled_facts(self, ws_context, monkeypatch):
        """The live completion frame includes the turn's atomic facts."""
        state, _index, sent, manager, _config = ws_context
        conv_id = "conv-recall"
        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock
        _subscribe_to_conv(state, conv_id)

        facts = ["User prefers espresso.", "User is in Pacific time."]

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            ctx.composer.last_recalled_facts = list(facts)
            append_message(ctx.config, ctx.conv_id, {"role": "user", "content": user_message})
            append_message(
                ctx.config,
                ctx.conv_id,
                {"role": "assistant", "content": "Here is your answer.", "recalled_facts": list(facts)},
            )
            return ToolResult(text="Here is your answer.")

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        future = await manager.enqueue_turn(
            conv_id=conv_id,
            kind=TurnKind.USER,
            prompt="What should I drink?",
            user_id="alice",
        )
        await asyncio.wait_for(future, timeout=2.0)

        completes = [m for m in sent if m.get("type") == WSMessageType.MESSAGE_COMPLETE]
        assert completes
        assert completes[-1]["recalled_facts"] == facts

    @pytest.mark.asyncio
    async def test_recalled_facts_emitted_during_turn(self, ws_context, monkeypatch):
        """Turn composition emits recalled_facts frame before generation completes."""
        state, _index, sent, manager, _config = ws_context
        conv_id = "conv-recall"
        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock
        _subscribe_to_conv(state, conv_id)

        facts = ["User prefers espresso.", "User is in Pacific time."]

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            await ctx.publish("recalled_facts", facts=list(facts))
            append_message(ctx.config, ctx.conv_id, {"role": "user", "content": user_message})
            append_message(
                ctx.config,
                ctx.conv_id,
                {"role": "assistant", "content": "Here is your answer.", "recalled_facts": list(facts)},
            )
            return ToolResult(text="Here is your answer.")

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        future = await manager.enqueue_turn(
            conv_id=conv_id,
            kind=TurnKind.USER,
            prompt="What should I drink?",
            user_id="alice",
        )
        await asyncio.wait_for(future, timeout=2.0)

        fact_frames = [m for m in sent if m.get("type") == WSMessageType.RECALLED_FACTS]
        assert fact_frames
        assert fact_frames[0]["facts"] == facts
        assert fact_frames[0]["conv_id"] == conv_id

    @pytest.mark.asyncio
    async def test_message_complete_omits_empty_recalled_facts(self, ws_context, monkeypatch):
        """A turn with no memory hit keeps the field absent, not an empty list."""
        state, _index, sent, manager, _config = ws_context
        conv_id = "conv-recall"
        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock
        _subscribe_to_conv(state, conv_id)

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            append_message(ctx.config, ctx.conv_id, {"role": "user", "content": user_message})
            return ToolResult(text="No memory needed.")

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        future = await manager.enqueue_turn(
            conv_id=conv_id,
            kind=TurnKind.USER,
            prompt="Hello",
            user_id="alice",
        )
        await asyncio.wait_for(future, timeout=2.0)

        completes = [m for m in sent if m.get("type") == WSMessageType.MESSAGE_COMPLETE]
        assert completes
        assert "recalled_facts" not in completes[-1]

    @pytest.mark.asyncio
    async def test_conv_history_replays_facts_and_hides_prompt_envelope(self, ws_context):
        """Reloaded history carries the assistant row's facts but hides the envelope."""
        state, index, sent, _manager, config = ws_context
        conv_id = "conv-recall"
        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock

        facts = ["User prefers espresso."]
        append_message(config, conv_id, {"role": "user", "content": "Earlier question"})
        append_message(
            config,
            conv_id,
            {
                "role": "recalled_facts",
                "content": "<recalled_facts>\nUser prefers espresso.\n</recalled_facts>",
            },
        )
        append_message(
            config,
            conv_id,
            {"role": "assistant", "content": "Answer", "recalled_facts": facts},
        )

        await _handle_load_history(ws_mock, index, "alice", {"conv_id": conv_id}, state)

        history_frames = [m for m in sent if m.get("type") == WSMessageType.CONV_HISTORY]
        assert len(history_frames) == 1
        messages = history_frames[0]["messages"]

        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[-1]["recalled_facts"] == facts
