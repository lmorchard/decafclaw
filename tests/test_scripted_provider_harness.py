"""Agent-runtime integration tests on the scripted provider harness (#923).

Drives the real ConversationManager + TurnRunner stack end to end through
``AgentHarness`` (tests/harness.py): the LLM path is the normal
``call_llm`` / ``call_llm_streaming`` → provider registry →
``decafclaw.testing.ScriptedProvider`` route. The ``scripted_provider``
marker opens the agent entry seams of the #851 guard while the real
network provider seams stay guarded (verified in
``test_real_provider_seams_stay_guarded``).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest
from decafclaw.llm.providers.openai_compat import OpenAICompatProvider
from decafclaw.llm.registry import register_provider, unregister_provider
from decafclaw.media import ToolResult
from decafclaw.memory_context import embed_text as _real_embed_text
from decafclaw.testing import ScriptedResponse
from tests.harness import AgentHarness


async def _echo_tool(ctx, text: str = "") -> ToolResult:
    return ToolResult(text=f"echo: {text}")


def _tool_roundtrip_script() -> tuple[ScriptedResponse, ScriptedResponse]:
    return (
        ScriptedResponse.tool_call("echo", {"text": "hi"}, call_id="call_1", content="Echoing now."),
        ScriptedResponse.text("Echoed: hi", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    )


@pytest.mark.scripted_provider
async def test_tool_roundtrip_streaming(tmp_path):
    """Streaming tool round-trip: script → tool → final text, with
    persisted conversation and event order verified."""
    async with AgentHarness(tmp_path) as h:
        h.tool("echo", _echo_tool, description="Echoes its input.")
        h.script(*_tool_roundtrip_script())
        result = await (await h.enqueue("Please echo hi."))
        assert result.text == "Echoed: hi"

        # Persisted conversation, in archive order.
        msgs = h.archive()
        assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
        assert msgs[0]["content"] == "Please echo hi."
        tc = msgs[1]["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["function"]["name"] == "echo"
        assert tc["function"]["arguments"] == '{"text": "hi"}'
        assert msgs[2]["tool_call_id"] == "call_1"
        assert msgs[2]["content"] == "echo: hi"
        assert msgs[3]["content"] == "Echoed: hi"

        # Event stream order: user → turn start → LLM call (streamed,
        # with the tool call announced mid-stream) → tool execution →
        # final LLM call → completion.
        types = [ev["type"] for ev in h.events]
        i_llm_end_1 = types.index("llm_end")
        i = {
            "user_message": types.index("user_message"),
            "turn_start": types.index("turn_start"),
            "llm_start": types.index("llm_start"),
            "tool_call_start": types.index("tool_call_start"),
            "stream_done": types.index("stream_done"),
            "llm_end": i_llm_end_1,
            "tool_start": types.index("tool_start"),
            "tool_end": types.index("tool_end"),
            "message_complete": types.index("message_complete"),
            "turn_complete": types.index("turn_complete"),
        }
        assert (
            i["user_message"]
            < i["turn_start"]
            < i["llm_start"]
            < i["tool_call_start"]
            < i["stream_done"]
            < i["llm_end"]
            < i["tool_start"]
            < i["tool_end"]
            < i["message_complete"]
            < i["turn_complete"]
        )
        assert types[-1] == "turn_complete"
        # Both LLM calls streamed.
        assert types.count("stream_done") == 2
        assert types.count("llm_start") == 2

        msg_complete = [ev for ev in h.events if ev["type"] == "message_complete"][0]
        assert msg_complete["text"] == "Echoed: hi"
        assert msg_complete["final"] is True
        tool_end = [ev for ev in h.events if ev["type"] == "tool_end"][0]
        assert tool_end["tool"] == "echo"
        assert tool_end["result_text"] == "echo: hi"

        # The captured requests prove the normal path was exercised: the
        # first call saw the harness tool definition, the second saw the
        # tool result.
        reqs = h.provider.requests
        assert len(reqs) == 2
        assert "echo" in [t["function"]["name"] for t in (reqs[0].tools or [])]
        assert any(m.get("role") == "tool" for m in reqs[1].messages)
        assert all(r.streaming for r in reqs)


@pytest.mark.scripted_provider
async def test_tool_roundtrip_non_streaming_same_script(tmp_path):
    """The same script drives the non-streaming path and persists the
    identical conversation."""
    async with AgentHarness(tmp_path, streaming=False) as h:
        h.tool("echo", _echo_tool, description="Echoes its input.")
        h.script(*_tool_roundtrip_script())
        result = await (await h.enqueue("Please echo hi."))
        assert result.text == "Echoed: hi"

        msgs = h.archive()
        assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
        assert msgs[2]["content"] == "echo: hi"
        assert msgs[3]["content"] == "Echoed: hi"

        types = [ev["type"] for ev in h.events]
        assert "chunk" not in types
        assert "stream_done" not in types
        assert types.count("llm_start") == 2
        assert types[-1] == "turn_complete"

        assert [r.streaming for r in h.provider.requests] == [False, False]


async def _gated_tool(ctx, note: str = "") -> ToolResult:
    response = await ctx.request_confirmation(
        ConfirmationRequest(
            action_type=ConfirmationAction.CONTINUE_TURN,
            message="Allow the gated step?",
            tool_call_id=ctx.tools.current_call_id,
        )
    )
    if not response.approved:
        return ToolResult(text="denied by user")
    return ToolResult(text=f"done: {note}")


@pytest.mark.scripted_provider
async def test_confirmation_pause_and_resume(tmp_path):
    """A tool that needs user input pauses the turn; responding resumes
    it to completion."""
    async with AgentHarness(tmp_path) as h:
        h.tool("gated", _gated_tool, description="Runs after user approval.")
        h.script(
            ScriptedResponse.tool_call("gated", {"note": "step"}, call_id="call_g"),
            ScriptedResponse.text("All done."),
        )
        future = await h.enqueue("Please run the gated step.")

        req_event = await h.wait_for_event("confirmation_request")
        assert req_event["action_type"] == "continue_turn"
        assert req_event["message"] == "Allow the gated step?"
        assert h.state.pending_confirmation is not None
        await h.respond_confirmation(req_event["confirmation_id"], approved=True)

        result = await future
        assert result.text == "All done."

        msgs = h.archive()
        assert [m["role"] for m in msgs] == [
            "user",
            "assistant",
            "confirmation_request",
            "confirmation_response",
            "tool",
            "assistant",
        ]
        assert msgs[2]["confirmation_id"] == req_event["confirmation_id"]
        assert msgs[4]["content"] == "done: step"
        assert msgs[5]["content"] == "All done."

        types = [ev["type"] for ev in h.events]
        assert (
            types.index("confirmation_request")
            < types.index("confirmation_response")
            < types.index("tool_end")
            < types.index("turn_complete")
        )


@pytest.mark.scripted_provider
async def test_cancellation_of_held_turn(tmp_path):
    """Cancelling a turn while the provider is holding mid-stream ends it
    as cancelled (no hang, no crash)."""

    hold = asyncio.Event()  # never released — the turn hangs until cancelled
    async with AgentHarness(tmp_path) as h:
        h.script(ScriptedResponse.text("never delivered", hold=hold))
        future = await h.enqueue("Please do the slow thing.")
        await h.wait_for_event("llm_start")
        await h.cancel()
        result = await future
        assert result.text == "[cancelled]"
        assert result.termination_reason == "cancelled"

        types = [ev["type"] for ev in h.events]
        assert "turn_complete" in types
        msg_complete = [ev for ev in h.events if ev["type"] == "message_complete"][0]
        assert msg_complete["text"] == "[cancelled]"


@pytest.mark.scripted_provider
async def test_script_exhaustion_fails_turn_loudly(tmp_path):
    """An exhausted script fails the turn with an actionable error — it
    must not hang."""
    async with AgentHarness(tmp_path) as h:
        h.tool("echo", _echo_tool, description="Echoes its input.")
        # One response for a turn that needs two → the second LLM call
        # raises ScriptExhaustedError.
        h.script(ScriptedResponse.tool_call("echo", {"text": "hi"}, call_id="call_1"))
        result = await (await h.enqueue("Please echo hi."))
        assert result.termination_reason == "error"
        assert "ScriptedProvider exhausted" in result.text
        assert "call #1" in result.text

        err = [ev for ev in h.events if ev["type"] == "error"][0]
        assert "ScriptedProvider exhausted" in err["message"]
        # The turn terminated (no hang).
        assert "turn_complete" in [ev["type"] for ev in h.events]


@pytest.mark.scripted_provider
async def test_turn_emits_no_warning_logs(tmp_path, monkeypatch):
    """A harness turn stays warning-free: with vault retrieval disabled
    in the harness config, the fail-open memory-context path (which
    would hit the scripted provider's missing embedder and log a
    WARNING traceback) never runs.

    The root autouse ``mock_embed_text`` fixture patches
    ``decafclaw.memory_context.embed_text`` with a zero vector, so this
    test restores the real function — bound at module import, before
    fixture setup — to actually guard the harness config: if retrieval
    were re-enabled, the real embed path would reach the scripted
    provider's ``embed()`` and log the warning.
    """
    monkeypatch.setattr("decafclaw.memory_context.embed_text", _real_embed_text)
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger("decafclaw.memory_context")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        async with AgentHarness(tmp_path) as h:
            assert h.config.vault_retrieval.enabled is False
            h.script(ScriptedResponse.text("hi"))
            result = await (await h.enqueue("hi"))
            assert result.text == "hi"
            assert "turn_complete" in [ev["type"] for ev in h.events]
    finally:
        logger.removeHandler(handler)

    assert [r for r in captured if r.levelno >= logging.WARNING] == []


@pytest.mark.scripted_provider
async def test_real_provider_seams_stay_guarded(tmp_path):
    """Even with the scripted_provider marker (agent entry seams open),
    a real network provider reached through the normal path is still
    intercepted by the #851 guard — the turn fails loudly instead of
    making a network call."""
    async with AgentHarness(tmp_path) as h:
        register_provider("openai-compat", OpenAICompatProvider(url="http://127.0.0.1:1/v1", api_key="test"))
        try:
            h.config.model_configs["harness-model"] = ModelConfig(
                provider="openai-compat",
                model="some-model",
                streaming=False,
                context_window_size=200_000,
            )
            h.config.providers["openai-compat"] = ProviderConfig(type="openai-compat")
            h.script(ScriptedResponse.text("never used"))
            result = await (await h.enqueue("hi"))
            assert result.termination_reason == "error"
            assert "Unstubbed LLM call" in result.text
        finally:
            unregister_provider("openai-compat")
