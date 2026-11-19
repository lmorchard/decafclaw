"""Turn provenance surfaces in WebSocket payloads (#1563).

Covers the live ``turn_provenance`` frame, the live ``message_complete``
frame, and the ``conv_history`` replay. Passive PRE_COMPOSE hydration rows
(``<recalled_facts>``, journal, notes, vault retrieval) are prompt plumbing and
stay out of the web transcript; the assistant row's capsule payload carries
their disclosure instead.
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
    index = {"conv-provenance": MagicMock(user_id="alice", conv_id="conv-provenance")}
    return state, index, sent, manager, config


PROVENANCE = {
    "facts": [{"fact": "User prefers espresso."}],
    "journals": [{"timestamp": "2026-11-19 09:00", "text": "Thinking about provenance."}],
    "vault": [{"page": "agent/pages/decafclaw.md", "score": 0.82, "excerpt": "Agent testbed."}],
    "scratchpad": [{"timestamp": "2026-11-19T10:00:00Z", "text": "Ship capsules."}],
}


class TestTurnProvenanceWebSocket:
    @pytest.mark.asyncio
    async def test_message_complete_carries_provenance(self, ws_context, monkeypatch):
        """The live completion frame includes the turn's provenance payload."""
        state, _index, sent, manager, _config = ws_context
        conv_id = "conv-provenance"
        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock
        _subscribe_to_conv(state, conv_id)

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            ctx.composer.last_provenance = dict(PROVENANCE)
            append_message(ctx.config, ctx.conv_id, {"role": "user", "content": user_message})
            append_message(
                ctx.config,
                ctx.conv_id,
                {"role": "assistant", "content": "Here is your answer.", "provenance": dict(PROVENANCE)},
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
        assert completes[-1]["provenance"] == PROVENANCE

    @pytest.mark.asyncio
    async def test_provenance_emitted_during_turn(self, ws_context, monkeypatch):
        """Composition emits the capsule payload on its own frame."""
        state, _index, sent, manager, _config = ws_context
        conv_id = "conv-provenance"
        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock
        _subscribe_to_conv(state, conv_id)

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            await ctx.publish("turn_provenance", provenance=dict(PROVENANCE))
            append_message(ctx.config, ctx.conv_id, {"role": "user", "content": user_message})
            append_message(
                ctx.config,
                ctx.conv_id,
                {"role": "assistant", "content": "Here is your answer.", "provenance": dict(PROVENANCE)},
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

        provenance_frames = [m for m in sent if m.get("type") == WSMessageType.TURN_PROVENANCE]
        assert provenance_frames
        assert provenance_frames[0]["provenance"] == PROVENANCE
        assert provenance_frames[0]["conv_id"] == conv_id

    @pytest.mark.asyncio
    async def test_message_complete_omits_empty_provenance(self, ws_context, monkeypatch):
        """A turn with no passive hydration keeps the field absent, not empty."""
        state, _index, sent, manager, _config = ws_context
        conv_id = "conv-provenance"
        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock
        _subscribe_to_conv(state, conv_id)

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            append_message(ctx.config, ctx.conv_id, {"role": "user", "content": user_message})
            return ToolResult(text="No context needed.")

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
        assert "provenance" not in completes[-1]
        assert not [m for m in sent if m.get("type") == WSMessageType.TURN_PROVENANCE]

    @pytest.mark.asyncio
    async def test_conv_history_replays_provenance_and_hides_prompt_plumbing(self, ws_context):
        """Reloaded history carries the assistant row's capsules and hides the rows."""
        state, index, sent, _manager, config = ws_context
        conv_id = "conv-provenance"
        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock

        append_message(config, conv_id, {"role": "user", "content": "Earlier question"})
        for role, content in (
            ("recalled_facts", "<recalled_facts>\nUser prefers espresso.\n</recalled_facts>"),
            ("recent_journal", "Recent journal entries"),
            ("conversation_notes", "[Conversation notes]"),
            ("vault_retrieval", "--- page (score: 0.82) ---"),
            ("vault_references", "[Referenced wiki page: Foo]"),
        ):
            append_message(config, conv_id, {"role": role, "content": content})
        append_message(
            config,
            conv_id,
            {"role": "assistant", "content": "Answer", "provenance": dict(PROVENANCE)},
        )

        await _handle_load_history(ws_mock, index, "alice", {"conv_id": conv_id}, state)

        history_frames = [m for m in sent if m.get("type") == WSMessageType.CONV_HISTORY]
        assert len(history_frames) == 1
        messages = history_frames[0]["messages"]

        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[-1]["provenance"] == PROVENANCE

    @pytest.mark.asyncio
    async def test_passive_vault_retrieval_does_not_emit_tool_status(self, ws_context, monkeypatch):
        """Passive vault hydration events must not emit fake TOOL_STATUS frames (#1571)."""
        state, _index, sent, manager, _config = ws_context
        conv_id = "conv-provenance"
        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock
        _subscribe_to_conv(state, conv_id)

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            await ctx.publish("vault_retrieval", text="[Automatically retrieved context]", results=[{"page": "p"}])
            await ctx.publish("vault_references", text="[Referenced wiki page: p]")
            append_message(ctx.config, ctx.conv_id, {"role": "user", "content": user_message})
            append_message(ctx.config, ctx.conv_id, {"role": "assistant", "content": "Done"})
            return ToolResult(text="Done")

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        future = await manager.enqueue_turn(
            conv_id=conv_id,
            kind=TurnKind.USER,
            prompt="Hello",
            user_id="alice",
        )
        await asyncio.wait_for(future, timeout=2.0)

        tool_status_frames = [
            m for m in sent
            if m.get("type") == WSMessageType.TOOL_STATUS
            and m.get("tool") in ("vault_retrieval", "vault_references")
        ]
        assert not tool_status_frames
