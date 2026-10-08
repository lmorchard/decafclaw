"""Replay metadata survives runtime/persistence paths, without reviving content."""

import copy
import dataclasses
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from decafclaw.agent import run_agent_turn
from decafclaw.archive import append_message, restore_history
from decafclaw.compaction import compact_history, flatten_messages
from decafclaw.config_types import ModelConfig, ProviderConfig, ReflectionConfig
from decafclaw.context_cleanup import clear_old_tool_results
from decafclaw.llm import init_providers
from decafclaw.llm.history import assistant_message
from decafclaw.llm.providers.openai_responses import OpenAIResponsesProvider, _ResponsesState
from decafclaw.media import EndTurnConfirm, ToolResult
from decafclaw.reflection import ReflectionResult


def wire(text="Done", calls=False, status="completed"):
    items = [{"type": "reasoning", "id": "rs", "summary": [], "encrypted_content": "opaque" * 500}]
    if text:
        items.append(
            {
                "type": "message",
                "id": "msg",
                "role": "assistant",
                "status": "completed",
                "phase": "commentary" if calls else "final_answer",
                "content": [{"type": "output_text", "text": text}],
            }
        )
    if calls:
        items.append(
            {
                "type": "function_call",
                "id": "fc",
                "call_id": "call",
                "name": "current_time",
                "arguments": "{}",
                "status": "completed",
            }
        )
    return {"status": status, "output": items, "usage": None}


async def normalized(text="Done", calls=False):
    state = _ResponsesState(OpenAIResponsesProvider()._provenance("gpt-6-luna"), None)
    state.accept_response(wire(text, calls))
    return await state.finalize()


async def test_agent_metadata_roundtrip_archive_and_end_turn(ctx):
    ctx.config.llm.streaming = False
    first, final = await normalized("Checking", True), await normalized()
    with (
        patch("decafclaw.agent.call_llm", new_callable=AsyncMock, side_effect=[first, final]),
        patch(
            "decafclaw.tool_execution.execute_tool",
            new_callable=AsyncMock,
            return_value=ToolResult(text="ok", end_turn=True),
        ),
    ):
        history = []
        await run_agent_turn(ctx, "Check time", history)
    restored = restore_history(ctx.config, ctx.conv_id)
    assert history[1]["provider_data"] == first["provider_data"]
    assert history[-1]["provider_data"] == final["provider_data"]
    assert restored[1]["provider_data"] == history[1]["provider_data"]
    assert restored[-1]["provider_data"] == history[-1]["provider_data"]


@pytest.mark.parametrize("approve", [True, False])
async def test_confirmation_presentation_keeps_metadata(ctx, approve):
    ctx.config.llm.streaming = False
    first = await normalized("Checking", True)
    presentation = await normalized("Review this")
    final = await normalized()
    gate = EndTurnConfirm(message="Review")
    with (
        patch("decafclaw.agent.call_llm", new_callable=AsyncMock, side_effect=[first, presentation, final]),
        patch(
            "decafclaw.tool_execution.execute_tool",
            new_callable=AsyncMock,
            return_value=ToolResult(text="ready", end_turn=gate),
        ),
        patch("decafclaw.agent._handle_end_turn_confirm", new_callable=AsyncMock, return_value=approve),
    ):
        history = []
        await run_agent_turn(ctx, "Check time", history)
    assert history[3]["provider_data"] == presentation["provider_data"]
    assert history[-1]["provider_data"] == final["provider_data"]


async def test_reflection_retry_keeps_state_but_escalation_drops_stale_text(ctx):
    ctx.config.llm.streaming = False
    ctx.config.reflection = ReflectionConfig(enabled=True, max_retries=1)
    first, second = await normalized("First"), await normalized("Second")
    with (
        patch("decafclaw.agent.call_llm", new_callable=AsyncMock, side_effect=[first, second]),
        patch(
            "decafclaw.reflection.evaluate_response",
            new_callable=AsyncMock,
            return_value=ReflectionResult(passed=False, critique="Try again"),
        ),
    ):
        history = []
        await run_agent_turn(ctx, "Answer", history)
    assert history[1]["provider_data"] == first["provider_data"]
    assert "Second" in history[-1]["content"]
    assert "Try switching" in history[-1]["content"]
    assert "provider_data" not in history[-1]


async def test_archive_compaction_keeps_only_live_tail_metadata(ctx):
    ctx.config.compaction.preserve_turns = 1
    ctx.config.compaction.memory_sweep_enabled = False
    history = []
    for i in range(3):
        history.extend(
            [
                {"role": "user", "content": str(i)},
                assistant_message(await normalized("Checking", True)),
                {"role": "tool", "tool_call_id": "call", "content": "result"},
                assistant_message(await normalized()),
            ]
        )
    for message in history:
        append_message(ctx.config, ctx.conv_id, message)
    with patch("decafclaw.context.call_llm", new_callable=AsyncMock, return_value={"content": "Summary"}):
        assert await compact_history(ctx, history)
    assert "provider_data" not in history[0]
    assert len([message for message in history if "provider_data" in message]) == 2
    assert "opaque" not in flatten_messages(history)
    restored = restore_history(ctx.config, ctx.conv_id)
    assert restored[-1]["provider_data"] == history[-1]["provider_data"]
    assert OpenAIResponsesProvider()._input("gpt-6-luna", restored)[-1]["phase"] == "final_answer"


async def test_cleared_tool_result_and_fork_use_current_history(config):
    config.cleanup.min_turn_age = 1
    config.cleanup.min_size_bytes = 10
    history = [
        {"role": "user", "content": "Old"},
        assistant_message(await normalized("Checking", True)),
        {"role": "tool", "tool_call_id": "call", "content": "old result " * 500},
        {"role": "user", "content": "New"},
    ]
    assert clear_old_tool_results(history, config).cleared_count == 1
    fork = copy.deepcopy(history)
    items = OpenAIResponsesProvider()._input("gpt-6-luna", fork)
    output = next(item for item in items if item.get("type") == "function_call_output")
    assert output["output"].startswith("[tool output cleared:")
    assert "old result" not in json.dumps(items)
    fork[1]["provider_data"]["openai_responses"]["output"][0]["encrypted_content"] = "fork"
    assert history[1]["provider_data"] != fork[1]["provider_data"]


async def test_browser_history_strips_opaque_state(config):
    from decafclaw.web.conversations import ConversationIndex
    from decafclaw.web.websocket import _annotate_widget_responses

    message = assistant_message(await normalized())
    append_message(config, "conv", message)
    messages, _ = ConversationIndex(config).load_history("conv")
    assert "provider_data" not in messages[0]
    assert "provider_data" not in _annotate_widget_responses([message], set())[0]
    assert "provider_data" in restore_history(config, "conv")[0]


@pytest.mark.live_llm
@pytest.mark.parametrize("status,executed", [("completed", True), ("incomplete", False), ("failed", False)])
async def test_actual_provider_through_agent_dispatch(ctx, status, executed):
    ctx.config = dataclasses.replace(
        ctx.config,
        providers={"oai": ProviderConfig(type="openai-responses")},
        model_configs={
            "luna": ModelConfig(
                provider="oai",
                model="gpt-6-luna",
                reasoning_effort="medium",
                streaming=False,
                context_window_size=10000,
            )
        },
        default_model="luna",
    )
    init_providers(ctx.config)
    client = AsyncMock()
    client.__aenter__.return_value = client
    payload = wire("Checking", calls=True, status=status)
    payload["incomplete_details"] = {"reason": "max_output_tokens"}
    client.post.side_effect = [httpx.Response(200, json=payload), httpx.Response(200, json=wire())]
    with (
        patch("httpx.AsyncClient", return_value=client),
        patch(
            "decafclaw.tool_execution.execute_tool", new_callable=AsyncMock, return_value=ToolResult(text="ok")
        ) as execute,
    ):
        history = []
        result = await run_agent_turn(ctx, "Check time", history)
    assert execute.called == executed
    assert result.text.endswith("Done")
    assert len(history[1]["tool_calls"]) == 1
    assert "provider_data" in history[-1]
    if executed:
        request = client.post.call_args.kwargs["json"]
        assert request["reasoning"] == {"effort": "medium"}
        assert len([item for item in request["input"] if item.get("type") == "function_call"]) == 1
