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

    published_events = []
    ctx.event_bus.subscribe(lambda event: published_events.append(event))

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        # Repeat the same failing tool call indefinitely; the loop-breaker
        # should stop the turn well before max_tool_iterations (50).
        mock_llm.side_effect = [repeated_call] * 10
        history = []
        result = await run_agent_turn(ctx, "loop forever", history)

    assert "[loop-breaker] Stopped" in result.text
    assert "max tool iterations" not in result.text

    # The nudge itself is ephemeral: appended to the LLM-facing message list
    # only (mirroring the grace-turn nudge pattern), never archived — an
    # archived "system"-role message would get read back into history by
    # restore_history on a restart/reload, permanently polluting context on
    # later turns. Confirm it's absent from the archive...
    from decafclaw.archive import read_archive
    archived = read_archive(ctx.config, ctx.conv_id)
    system_msgs = [m for m in archived if m.get("role") == "system"]
    assert not any("[loop-breaker]" in (m.get("content") or "") for m in system_msgs)

    # ...while the hard-stop's final assistant message IS archived (it's the
    # turn's actual output, correctly durable).
    assistant_msgs = [m for m in archived if m.get("role") == "assistant"]
    assert any("[loop-breaker] Stopped" in (m.get("content") or "") for m in assistant_msgs)

    # The nudge is still observable via the durable/observable event signal.
    nudge_events = [e for e in published_events
                     if e.get("type") == "loop_breaker" and e.get("action") == "nudge"]
    assert len(nudge_events) == 1

    stop_events = [e for e in published_events
                   if e.get("type") == "loop_breaker" and e.get("action") == "stop"]
    assert len(stop_events) == 1

    # Tripped at repeat_threshold=3, and the LLM was called once per
    # iteration up to and including the stop iteration (NUDGE at 3rd call's
    # iteration, STOP at the 4th) — well short of the 10 stubbed responses
    # or the 50-iteration budget.
    assert mock_llm.call_count < 10


# -- Integration: a genuine end-turn signal always wins over the breaker ------


@pytest.mark.asyncio
async def test_end_turn_signal_preempts_loop_breaker(ctx):
    """A genuine end-turn signal (e.g. end_turn=True from a tool result) must
    win over the loop-breaker even when the tool-call history would
    otherwise trip it on that very iteration.

    _handle_tool_calls checks end_turn_signal (widget pause / EndTurnConfirm
    / end_turn=True) BEFORE recording the iteration's calls into the
    LoopBreaker and computing a verdict — so a genuine end-turn returns
    early and never reaches loop_breaker.record()/verdict() for that
    iteration. This locks in that ordering: two prior iterations of
    identical failing tool calls prime the breaker to its NUDGE trip (and it
    fires), then the third iteration reaches repeat_threshold again but
    signals end_turn=True instead of a normal tool result — the turn must
    end via the end-turn path, not _finalize_loop_break."""
    ctx.config.llm.streaming = False
    ctx.config.agent.max_tool_iterations = 50
    ctx.config.loop_breaker.repeat_threshold = 2
    ctx.config.loop_breaker.error_threshold = 99  # isolate the repeat signal
    ctx.config.loop_breaker.error_window = 50

    repeated_call = _mock_llm_response(
        content=None,
        tool_calls=[{
            "id": "tc-repeat",
            "function": {
                "name": "some_tool",
                "arguments": "{}",
            },
        }],
    )
    final_response = _mock_llm_response(content="All done here.")

    # end_turn_signal sequence for the three tool-call iterations: the first
    # two look like normal (non-end-turn) tool completions so the breaker
    # accumulates the repeat count and fires its one-shot NUDGE; the third
    # would push the same repeated call past repeat_threshold again (which
    # would be a STOP if recorded) but instead signals a genuine end_turn.
    end_turn_signals = iter([False, False, True])

    async def fake_execute_tool_calls(fork_ctx, tool_calls, history, messages):
        for tc in tool_calls:
            tool_msg = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": "still not done",
            }
            history.append(tool_msg)
            messages.append(tool_msg)
        return None, next(end_turn_signals)

    published_events = []
    ctx.event_bus.subscribe(lambda event: published_events.append(event))

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm, \
         patch("decafclaw.agent.execute_tool_calls",
               side_effect=fake_execute_tool_calls) as mock_execute:
        mock_llm.side_effect = [repeated_call, repeated_call, repeated_call, final_response]
        history = []
        result = await run_agent_turn(ctx, "keep trying", history)

    # The real end-turn signal wins: the turn ends with the final no-tools
    # LLM call's content, not the loop-breaker's hard-stop text.
    assert result.text == "All done here."
    assert "[loop-breaker] Stopped" not in result.text

    # A NUDGE legitimately fired on the second iteration (breaker still
    # observes and escalates right up to the end-turn iteration)...
    nudge_events = [e for e in published_events
                    if e.get("type") == "loop_breaker" and e.get("action") == "nudge"]
    assert len(nudge_events) == 1

    # ...but STOP never fires, because the third iteration's end_turn=True
    # is handled before the breaker ever records/verdicts that iteration.
    stop_events = [e for e in published_events
                   if e.get("type") == "loop_breaker" and e.get("action") == "stop"]
    assert len(stop_events) == 0

    # 3 tool-call iterations + 1 final no-tools call after end_turn=True.
    assert mock_llm.call_count == 4
    assert mock_execute.call_count == 3
