"""Turn-settlement event ordering tests (issue #1000).

Pins the event order around the turn-settle point on the #923 harness
(``AgentHarness`` + ``ScriptedProvider``): the manager emits exactly one
``turn_complete`` per dispatched turn, from the turn runner's ``finally``
block, after state persistence and the busy-flag reset, before queued
messages are dispatched. The event carries a ``reason`` naming the settle
outcome: ``completed``, ``cancelled``, or ``failed``. A pause for
confirmation (or widget input, which rides the same confirmation infra)
is NOT an outcome — the turn is still in flight and no ``turn_complete``
fires until the pause resolves.

All waits are event-based (``wait_for_event`` / futures) — no fixed
sleeps. The documented trace lives in docs/conversations.md
("Turn lifecycle").
"""

from __future__ import annotations

import asyncio

import pytest

from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest
from decafclaw.media import ToolResult
from decafclaw.testing import ScriptedResponse
from tests.harness import AgentHarness


def _types(events: list[dict]) -> list[str]:
    return [ev["type"] for ev in events]


def _turn_completes(events: list[dict]) -> list[dict]:
    return [ev for ev in events if ev["type"] == "turn_complete"]


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
async def test_normal_completion_settle_order(tmp_path):
    """Completed turn: user_message → turn_start → message_complete →
    turn_complete(reason=completed); turn_complete is the last event and
    fires exactly once."""
    async with AgentHarness(tmp_path) as h:
        h.script(
            ScriptedResponse.text("All good.", usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6})
        )
        result = await (await h.enqueue("Say all good."))
        assert result.text == "All good."

        types = _types(h.events)
        i = {
            "user_message": types.index("user_message"),
            "turn_start": types.index("turn_start"),
            "message_complete": types.index("message_complete"),
            "turn_complete": types.index("turn_complete"),
        }
        assert i["user_message"] < i["turn_start"] < i["message_complete"] < i["turn_complete"]
        assert types.count("turn_complete") == 1
        assert types[-1] == "turn_complete"

        # The LLM call's bus events are present and land before the settle
        # (the turn runner's finally gathers in-flight event forwards before
        # emitting turn_complete).
        assert "llm_start" in types
        assert "llm_end" in types
        assert types.index("llm_start") < i["turn_complete"]
        assert types.index("llm_end") < i["turn_complete"]

        (turn_complete,) = _turn_completes(h.events)
        assert turn_complete["reason"] == "completed"


@pytest.mark.scripted_provider
async def test_reflection_retry_settle_order(tmp_path):
    """A reflection retry happens WITHIN the turn: the failing judge
    verdict and the retry LLM call all precede the single settle. The
    judge runs on the same scripted provider, so the script alternates
    agent responses with verdicts."""
    async with AgentHarness(tmp_path, reflection=True) as h:
        h.script(
            ScriptedResponse.text("The capital of France is Paris."),
            ScriptedResponse.text('{"pass": false, "critique": "Name the source."}'),
            ScriptedResponse.text("The capital of France is Paris, per the official gazette."),
            ScriptedResponse.text('{"pass": true}'),
        )
        result = await (await h.enqueue("What is the capital of France?"))
        assert "Paris" in result.text

        types = _types(h.events)
        i = {
            "turn_start": types.index("turn_start"),
            "message_complete": types.index("message_complete"),
            "turn_complete": types.index("turn_complete"),
        }
        assert i["turn_start"] < i["message_complete"] < i["turn_complete"]
        assert types.count("turn_complete") == 1
        assert types.count("reflection_result") == 2
        assert types[-1] == "turn_complete"

        # Both verdicts land before the settle (the turn runner's finally
        # gathers in-flight event forwards before emitting turn_complete).
        verdict_positions = [n for n, t in enumerate(types) if t == "reflection_result"]
        assert all(n < i["turn_complete"] for n in verdict_positions)

        verdicts = [ev for ev in h.events if ev["type"] == "reflection_result"]
        assert verdicts[0]["passed"] is False
        assert verdicts[1]["passed"] is True

        # The retry consumed the shared iteration budget within one turn:
        # agent (streaming) → judge (non-streaming) → agent (streaming)
        # → judge (non-streaming), in that call order.
        assert len(h.provider.requests) == 4
        assert [r.streaming for r in h.provider.requests] == [True, False, True, False]

        (turn_complete,) = _turn_completes(h.events)
        assert turn_complete["reason"] == "completed"


@pytest.mark.scripted_provider
async def test_pause_does_not_settle_until_resume(tmp_path):
    """A confirmation pause (widget input rides the same infra) is not a
    settle: while the turn is paused there is no turn_complete and the
    busy flag stays up; resuming runs the turn to a completed settle."""
    async with AgentHarness(tmp_path) as h:
        h.tool("gated", _gated_tool, description="Runs after user approval.")
        h.script(
            ScriptedResponse.tool_call("gated", {"note": "step"}, call_id="call_g"),
            ScriptedResponse.text("All done."),
        )
        future = await h.enqueue("Please run the gated step.")

        req = await h.wait_for_event("confirmation_request")
        # Paused: the turn is in flight, not settled.
        assert "turn_complete" not in _types(h.events)
        assert h.state.busy is True
        assert h.state.pending_confirmation is not None

        await h.respond_confirmation(req["confirmation_id"], approved=True)
        result = await future
        assert result.text == "All done."

        types = _types(h.events)
        assert (
            types.index("confirmation_request")
            < types.index("confirmation_response")
            < types.index("tool_end")
            < types.index("message_complete")
            < types.index("turn_complete")
        )
        assert types.count("turn_complete") == 1
        assert types[-1] == "turn_complete"

        (turn_complete,) = _turn_completes(h.events)
        assert turn_complete["reason"] == "completed"


@pytest.mark.scripted_provider
async def test_cancellation_settle_order(tmp_path):
    """Cancelling an in-flight turn settles it as cancelled: the
    [cancelled] message_complete precedes turn_complete(reason=cancelled)."""
    hold = asyncio.Event()  # never released — the turn hangs until cancelled
    async with AgentHarness(tmp_path) as h:
        h.script(ScriptedResponse.text("never delivered", hold=hold))
        future = await h.enqueue("Please do the slow thing.")
        await h.wait_for_event("llm_start")
        await h.cancel()
        result = await future
        assert result.text == "[cancelled]"
        assert result.termination_reason == "cancelled"

        types = _types(h.events)
        assert types.index("llm_start") < types.index("message_complete") < types.index("turn_complete")
        assert types.count("turn_complete") == 1
        assert types[-1] == "turn_complete"
        msg_complete = [ev for ev in h.events if ev["type"] == "message_complete"][0]
        assert msg_complete["text"] == "[cancelled]"

        (turn_complete,) = _turn_completes(h.events)
        assert turn_complete["reason"] == "cancelled"


@pytest.mark.scripted_provider
async def test_error_settle_order(tmp_path):
    """A provider error settles the turn as failed: the manager's error
    event precedes turn_complete(reason=failed); no message_complete."""
    async with AgentHarness(tmp_path, streaming=False) as h:
        h.script(ScriptedResponse(content="", error=RuntimeError("boom")))
        result = await (await h.enqueue("Do the failing thing."))
        assert result.termination_reason == "error"

        types = _types(h.events)
        errors = [ev for ev in h.events if ev["type"] == "error"]
        assert errors
        assert errors[0]["message"].startswith("Agent turn failed")
        assert "message_complete" not in types
        assert types.index("error") < types.index("turn_complete")
        assert types.count("turn_complete") == 1
        assert types[-1] == "turn_complete"

        (turn_complete,) = _turn_completes(h.events)
        assert turn_complete["reason"] == "failed"


@pytest.mark.scripted_provider
async def test_queued_followup_dispatches_after_settle(tmp_path):
    """A message arriving while the turn is busy is queued, not started:
    the follow-up's turn_start fires only after the first turn's
    turn_complete (the settle-before-drain contract)."""
    hold = asyncio.Event()
    async with AgentHarness(tmp_path) as h:
        h.script(
            ScriptedResponse.text("One.", hold=hold),
            ScriptedResponse.text("Two."),
        )
        future1 = await h.enqueue("First.")
        await h.wait_for_event("llm_start")

        # Turn 1 is in flight; the follow-up must queue, not start.
        future2 = await h.enqueue("Second.")
        assert getattr(future2, "queued", False) is True
        assert h.state.busy is True
        assert _types(h.events).count("turn_start") == 1
        assert "turn_complete" not in _types(h.events)

        # Release turn 1; it settles, then the queued turn dispatches.
        hold.set()
        result1 = await future1
        assert result1.text == "One."
        result2 = await future2
        assert result2.text == "Two."

        types = _types(h.events)
        first_tc = types.index("turn_complete")
        second_turn_start = types.index("turn_start", first_tc + 1)
        second_tc = types.index("turn_complete", first_tc + 1)
        assert first_tc < second_turn_start < second_tc
        assert types.count("turn_complete") == 2
        assert [ev["reason"] for ev in _turn_completes(h.events)] == ["completed", "completed"]
