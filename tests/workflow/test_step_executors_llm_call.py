"""Tests for the llm_call step executor — mocks call_llm."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.workflow.step_executors import StepResult, execute
from decafclaw.workflow.types import EdgeRef, RunStatus, StepDef, StepKind, WorkflowState


@pytest.fixture
def workflow_state():
    return WorkflowState(
        workflow="test_wf",
        run_id="run-001",
        conv_id="conv-1",
        initial_step="greet",
        current_step="greet",
        status=RunStatus.RUNNING,
        state={},
        transitions=[],
    )


@pytest.fixture
def greet_step():
    return StepDef(
        id="greet",
        kind=StepKind.LLM_CALL,
        config={
            "prompt": "Say hello to {{ state.name | default('world') }}",
            "schema": {
                "type": "object",
                "properties": {"greeting": {"type": "string"}},
                "required": ["greeting"],
            },
        },
    )


def make_llm_response(tool_name: str, args: dict) -> dict:
    """Simulate a tool-forced LLM response."""
    return {
        "tool_calls": [
            {
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args),
                }
            }
        ]
    }


@pytest.mark.asyncio
async def test_execute_llm_call_returns_step_result(ctx, greet_step, workflow_state):
    mock_response = make_llm_response("submit_greet", {"greeting": "Hello, world!"})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_response)):
        result = await execute(ctx, greet_step, workflow_state)

    assert isinstance(result, StepResult)
    assert result.output == {"greeting": "Hello, world!"}
    assert result.next_step is None   # no next edges → terminal
    assert result.suspend_status is None


@pytest.mark.asyncio
async def test_execute_llm_call_populates_state_output(ctx, greet_step, workflow_state):
    """Output dict matches the schema-shaped LLM response."""
    mock_response = make_llm_response("submit_greet", {"greeting": "Howdy!"})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_response)):
        result = await execute(ctx, greet_step, workflow_state)
    assert result.output["greeting"] == "Howdy!"


@pytest.mark.asyncio
async def test_execute_llm_call_resolves_next_step(ctx, workflow_state):
    """next_edges with unconditional edge → next_step resolved."""
    step = StepDef(
        id="greet",
        kind=StepKind.LLM_CALL,
        config={
            "prompt": "Say hi",
            "schema": {"type": "object", "properties": {"greeting": {"type": "string"}}},
        },
        next_edges=(EdgeRef(to="step2"),),
    )
    mock_response = make_llm_response("submit_greet", {"greeting": "Hi!"})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_response)):
        result = await execute(ctx, step, workflow_state)
    assert result.next_step == "step2"


@pytest.mark.asyncio
async def test_execute_llm_call_resolves_conditional_next(ctx, workflow_state):
    """Conditional edge: first matching condition wins."""
    step = StepDef(
        id="decide",
        kind=StepKind.LLM_CALL,
        config={
            "prompt": "Decide",
            "schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
        },
        next_edges=(
            EdgeRef(to="positive", if_expr="state.decide.x > 0"),
            EdgeRef(to="negative"),
        ),
    )
    mock_response = make_llm_response("submit_decide", {"x": 5})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_response)):
        result = await execute(ctx, step, workflow_state)
    assert result.next_step == "positive"


@pytest.mark.asyncio
async def test_execute_llm_call_falls_to_default_edge(ctx, workflow_state):
    """Conditional edge: non-matching condition falls to default."""
    step = StepDef(
        id="decide",
        kind=StepKind.LLM_CALL,
        config={
            "prompt": "Decide",
            "schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
        },
        next_edges=(
            EdgeRef(to="positive", if_expr="state.decide.x > 0"),
            EdgeRef(to="negative"),
        ),
    )
    mock_response = make_llm_response("submit_decide", {"x": -1})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_response)):
        result = await execute(ctx, step, workflow_state)
    assert result.next_step == "negative"


@pytest.mark.asyncio
async def test_execute_llm_call_terminal_edge(ctx, workflow_state):
    """Edge with to='' is terminal — returns next_step=None."""
    step = StepDef(
        id="end",
        kind=StepKind.LLM_CALL,
        config={
            "prompt": "End",
            "schema": {"type": "object"},
        },
        next_edges=(EdgeRef(to=""),),
    )
    mock_response = make_llm_response("submit_end", {})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_response)):
        result = await execute(ctx, step, workflow_state)
    assert result.next_step is None


@pytest.mark.asyncio
async def test_execute_user_input_raises_not_implemented(ctx, workflow_state):
    """execute() with user_input step raises NotImplementedError in Phase 1.

    Phase 3 rewrites _execute_user_input with ctx.request_confirmation.
    Until then, any attempt to execute a user_input step raises NotImplementedError.
    """
    step = StepDef(
        id="input_step",
        kind=StepKind.USER_INPUT,
        config={},
    )
    with pytest.raises(NotImplementedError, match="Phase 3"):
        await execute(ctx, step, workflow_state)


@pytest.mark.asyncio
async def test_execute_llm_call_retries_on_narrate(ctx, greet_step, workflow_state):
    """On model narrate (no tool_calls), executor retries and eventually succeeds."""
    narrate_response = {"content": "Sure, I'd be happy to greet!"}
    tool_response = make_llm_response("submit_greet", {"greeting": "Hello!"})
    responses = [narrate_response, tool_response]
    call_count = 0

    async def fake_call_llm(*args, **kwargs):
        nonlocal call_count
        result = responses[call_count]
        call_count += 1
        return result

    with patch("decafclaw.workflow.step_executors.call_llm", new=fake_call_llm):
        result = await execute(ctx, greet_step, workflow_state)

    assert call_count == 2
    assert result.output["greeting"] == "Hello!"


@pytest.mark.asyncio
async def test_execute_llm_call_raises_after_exhausted_retries(ctx, greet_step, workflow_state):
    """_call_structured raises RuntimeError when all retries are exhausted (model keeps narrating)."""
    narrate_response = {"content": "Sure, I'd be happy to help!"}
    call_count = 0

    async def always_narrate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return narrate_response

    with patch("decafclaw.workflow.step_executors.call_llm", new=always_narrate):
        with pytest.raises(RuntimeError) as exc_info:
            await execute(ctx, greet_step, workflow_state)

    # Should have tried initial attempt + 1 retry = 2 calls total (retries=1 default)
    assert call_count == 2
    err = str(exc_info.value)
    assert "submit_greet" in err
    assert "2" in err  # attempt count mentioned in error
