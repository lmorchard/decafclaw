"""Server-to-client regression tests for thinking / reasoning tokens (#154).

Covers the full path from LLM provider -> agent -> conversation manager -> WebSocket,
verifying that thinking tokens are forwarded during streaming, captured on completions,
preserved on intermediate tool-calling iterations, and reloaded from history.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.archive import append_message, read_archive
from decafclaw.config import Config
from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.events import EventBus
from decafclaw.llm.history import assistant_message, without_provider_data
from decafclaw.llm.providers.openai_compat import OpenAICompatProvider, _clean_messages
from decafclaw.media import ToolResult
from decafclaw.web.conversations import ConversationIndex
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _handle_load_history, _subscribe_to_conv


@pytest.fixture
def ws_context(tmp_path):
    config = Config()
    config.agent.data_home = str(tmp_path)
    config.agent.workspace_path = str(tmp_path / "workspace")
    bus = EventBus()
    manager = ConversationManager(config, bus)

    sent = []

    state = {
        "config": config,
        "manager": manager,
        "event_bus": bus,
        "conv_subscriptions": {},
    }
    index = {
        "conv-thinking": MagicMock(user_id="alice", conv_id="conv-thinking"),
    }
    return state, index, sent, manager, config


class TestThinkingWebSocketStreaming:
    @pytest.mark.asyncio
    async def test_streaming_thinking_deltas_and_completion_reach_websocket(self, ws_context, monkeypatch):
        """Streaming thinking chunks and text chunks flow from manager to WebSocket frames."""
        state, index, sent, manager, config = ws_context
        conv_id = "conv-thinking"

        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock

        # Subscribe WebSocket to conversation
        _subscribe_to_conv(state, conv_id)

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            append_message(ctx.config, ctx.conv_id, {"role": "user", "content": user_message})
            assert ctx.on_stream_chunk is not None
            # Stream thinking deltas
            await ctx.on_stream_chunk("thinking", "Let me ponder step 1. ")
            await ctx.on_stream_chunk("thinking", "Now step 2.")
            # Stream text deltas
            await ctx.on_stream_chunk("text", "The answer is ")
            await ctx.on_stream_chunk("text", "42.")
            return ToolResult(
                text="The answer is 42.",
                thinking="Let me ponder step 1. Now step 2.",
            )

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        future = await manager.enqueue_turn(
            conv_id=conv_id,
            kind=TurnKind.USER,
            prompt="What is the answer?",
            user_id="alice",
        )
        await asyncio.wait_for(future, timeout=2.0)

        # 1. Verify CHUNK frames
        chunk_msgs = [m for m in sent if m.get("type") == WSMessageType.CHUNK]
        assert len(chunk_msgs) == 4

        thinking_chunks = [m["thinking"] for m in chunk_msgs if "thinking" in m]
        assert thinking_chunks == ["Let me ponder step 1. ", "Now step 2."]

        text_chunks = [m["text"] for m in chunk_msgs if "text" in m]
        assert text_chunks == ["The answer is ", "42."]

        # 2. Verify MESSAGE_COMPLETE frame carries both text and thinking
        complete_msgs = [m for m in sent if m.get("type") == WSMessageType.MESSAGE_COMPLETE]
        assert len(complete_msgs) >= 1
        final_complete = complete_msgs[-1]
        assert final_complete["text"] == "The answer is 42."
        assert final_complete["thinking"] == "Let me ponder step 1. Now step 2."
        assert final_complete["final"] is True


class TestThinkingNonStreaming:
    @pytest.mark.asyncio
    async def test_nonstreaming_completion_forwards_thinking_to_websocket(self, ws_context, monkeypatch):
        """Non-streaming completions with reasoning propagate to WebSocket MESSAGE_COMPLETE."""
        state, index, sent, manager, config = ws_context
        conv_id = "conv-thinking"

        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock

        _subscribe_to_conv(state, conv_id)

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            append_message(ctx.config, ctx.conv_id, {"role": "user", "content": user_message})
            return ToolResult(
                text="Non-streaming response text",
                thinking="Non-streaming reasoning block",
            )

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        future = await manager.enqueue_turn(
            conv_id=conv_id,
            kind=TurnKind.USER,
            prompt="Hello non-streaming",
            user_id="alice",
        )
        await asyncio.wait_for(future, timeout=2.0)

        complete_msgs = [m for m in sent if m.get("type") == WSMessageType.MESSAGE_COMPLETE]
        assert len(complete_msgs) >= 1
        final_complete = complete_msgs[-1]
        assert final_complete["text"] == "Non-streaming response text"
        assert final_complete["thinking"] == "Non-streaming reasoning block"


class TestThinkingIntermediateToolCallsAndHistory:
    @pytest.mark.asyncio
    async def test_text_before_tools_forwards_thinking_and_history_retains_it(self, ws_context):
        """Assistant message with tool calls retains thinking in archive and forwards via text_before_tools."""
        state, index, sent, manager, config = ws_context
        conv_id = "conv-thinking"

        ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
        state["ws_send"] = ws_mock

        _subscribe_to_conv(state, conv_id)

        # Emulate assistant_message with tool_calls and thinking
        response = {
            "content": "Running search...",
            "thinking": "Need to search docs before deciding.",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{}"}}],
        }
        msg = assistant_message(response)
        assert msg["thinking"] == "Need to search docs before deciding."
        assert msg["content"] == "Running search..."
        assert len(msg["tool_calls"]) == 1

        # Archive it
        append_message(config, conv_id, msg)

        # Emulate text_before_tools event from agent
        sub_callback = list(manager._conversations[conv_id].subscribers.values())[0]
        await sub_callback(
            {
                "type": "text_before_tools",
                "conv_id": conv_id,
                "text": "Running search...",
                "thinking": "Need to search docs before deciding.",
            }
        )

        # Verify WebSocket received MESSAGE_COMPLETE with thinking
        complete_msgs = [m for m in sent if m.get("type") == WSMessageType.MESSAGE_COMPLETE]
        assert len(complete_msgs) == 1
        assert complete_msgs[0]["thinking"] == "Need to search docs before deciding."
        assert complete_msgs[0]["text"] == "Running search..."

        # Verify history loading preserves thinking for the tool-calling assistant message
        loaded = read_archive(config, conv_id)
        assert len(loaded) == 1
        assert loaded[0]["thinking"] == "Need to search docs before deciding."
        assert loaded[0]["content"] == "Running search..."
        assert loaded[0]["tool_calls"] == response["tool_calls"]

        # Verify load_history retains thinking
        conv_index = ConversationIndex(config)
        history_msgs, _ = conv_index.load_history(conv_id)
        assert history_msgs[0]["thinking"] == "Need to search docs before deciding."


class TestOpenAICompatProviderReasoning:
    @pytest.mark.asyncio
    async def test_openai_compat_cleans_thinking_from_api_messages(self):
        """Internal 'thinking' field is stripped from request messages so OpenAI endpoints don't reject it."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "thinking": "internal thought", "provider_data": {"x": 1}},
        ]
        cleaned = _clean_messages(messages)
        assert len(cleaned) == 2
        assert "thinking" not in cleaned[1]
        assert "provider_data" not in cleaned[1]
        assert cleaned[1]["content"] == "hello"
        assert cleaned[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_openai_compat_stream_state_captures_reasoning_deltas(self):
        """_StreamState emits thinking chunks and finalizes thinking attribute."""
        from decafclaw.llm.providers.openai_compat import _StreamState

        emitted = []

        async def callback(chunk_type, data):
            emitted.append((chunk_type, data))

        state = _StreamState(on_chunk=callback)

        # Chunk with reasoning_content
        await state.process_chunk(
            {
                "choices": [
                    {
                        "delta": {"reasoning_content": "Pondering problem... "},
                    }
                ]
            }
        )
        await state.process_chunk(
            {
                "choices": [
                    {
                        "delta": {"reasoning_content": "Found approach."},
                    }
                ]
            }
        )
        # Chunk with text content
        await state.process_chunk(
            {
                "choices": [
                    {
                        "delta": {"content": "Solution is X."},
                    }
                ]
            }
        )

        final = await state.finalize()

        assert emitted == [
            ("thinking", "Pondering problem... "),
            ("thinking", "Found approach."),
            ("text", "Solution is X."),
            ("done", {"usage": None}),
        ]
        assert final["thinking"] == "Pondering problem... Found approach."
        assert final["content"] == "Solution is X."
