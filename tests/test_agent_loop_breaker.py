"""Tests for LoopBreaker wiring into TurnRunner (#598)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.agent import _extract_call_signatures, run_agent_turn


def _mock_llm_response(content="Hello!", tool_calls=None, usage=None):
    """Build a mock LLM response dict (mirrors test_agent_turn.py's helper)."""
    return {
        "content": content,
        "tool_calls": tool_calls,
        "role": "assistant",
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50},
    }


# -- _extract_call_signatures ---------------------------------------------------


def test_extract_signatures_flags_errors():
    tool_calls = [
        {"id": "1", "function": {"name": "edit", "arguments": '{"path": "x"}'}},
        {"id": "2", "function": {"name": "read", "arguments": '{"path": "y"}'}},
    ]
    messages = [
        {"role": "tool", "tool_call_id": "1", "content": "[error: bad edit]"},
        {"role": "tool", "tool_call_id": "2", "content": "ok"},
    ]
    sigs = _extract_call_signatures(tool_calls, messages)
    assert sigs[0][0] == "edit" and sigs[0][2] is True
    assert sigs[1][0] == "read" and sigs[1][2] is False
    assert sigs[0][1] != sigs[1][1]


def test_extract_signatures_handles_missing_tool_result():
    """A tool_call with no matching tool-result message (shouldn't happen in
    practice, but defend against it) is treated as non-error."""
    tool_calls = [
        {"id": "missing", "function": {"name": "edit", "arguments": "{}"}},
    ]
    sigs = _extract_call_signatures(tool_calls, [])
    assert sigs[0] == ("edit", sigs[0][1], False)


def test_extract_signatures_handles_malformed_json_args():
    """Malformed JSON args fall back to the raw string rather than raising."""
    tool_calls = [
        {"id": "1", "function": {"name": "edit", "arguments": "not json"}},
    ]
    messages = [{"role": "tool", "tool_call_id": "1", "content": "ok"}]
    sigs = _extract_call_signatures(tool_calls, messages)
    assert sigs[0][0] == "edit"
    assert sigs[0][2] is False


# -- Integration: TurnRunner nudges then hard-stops on repeated tool errors ----


@pytest.mark.asyncio
async def test_turn_runner_nudges_then_stops_on_repeated_tool_errors(ctx):
    """A repeated failing tool call trips the loop-breaker: a [loop-breaker]
    system message is injected after repeat_threshold iterations, and the
    turn ends via _finalize_loop_break rather than running to
    max_tool_iterations."""
    ctx.config.llm.streaming = False
    ctx.config.agent.max_tool_iterations = 50
    ctx.config.loop_breaker.repeat_threshold = 3
    ctx.config.loop_breaker.error_threshold = 99  # isolate the repeat signal
    ctx.config.loop_breaker.error_window = 50

    # Unknown tool name -> execute_tool naturally returns a "[error: ...]"
    # ToolResult without any stubbing of execute_tool_calls.
    repeated_call = _mock_llm_response(
        content=None,
        tool_calls=[{
            "id": "tc-repeat",
            "function": {
                "name": "definitely_not_a_real_tool",
                "arguments": "{}",
            },
        }],
    )

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        # Repeat the same failing tool call indefinitely; the loop-breaker
        # should stop the turn well before max_tool_iterations (50).
        mock_llm.side_effect = [repeated_call] * 10
        history = []
        result = await run_agent_turn(ctx, "loop forever", history)

    assert "[loop-breaker] Stopped" in result.text
    assert "max tool iterations" not in result.text

    # The nudge is injected into the LLM-facing message list (not the
    # canonical turn `history`, mirroring the grace-turn nudge pattern) but
    # is archived for a durable record — check the archive.
    from decafclaw.archive import read_archive
    archived = read_archive(ctx.config, ctx.conv_id)
    system_msgs = [m for m in archived if m.get("role") == "system"]
    assert any("[loop-breaker]" in m.get("content", "") for m in system_msgs)

    # Tripped at repeat_threshold=3, and the LLM was called once per
    # iteration up to and including the stop iteration (NUDGE at 3rd call's
    # iteration, STOP at the 4th) — well short of the 10 stubbed responses
    # or the 50-iteration budget.
    assert mock_llm.call_count < 10
