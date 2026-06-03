"""Tests for the route step executor.

Covers:
  - Success: LLM picks a declared choice → state output + correct next_step.
  - Schema passed to _call_structured includes enum values and LLM-facing descriptions.
  - Choice not in declared set → RuntimeError with clear message.
  - Empty to="" (terminal abort) → next_step is None.
  - Tool name uses step id: choose_<step.id>.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.workflow.step_executors import StepResult, execute
from decafclaw.workflow.types import RouteChoice, RunStatus, StepDef, StepKind, WorkflowState


@pytest.fixture
def workflow_state():
    return WorkflowState(
        workflow="test_wf",
        run_id="run-001",
        conv_id="conv-1",
        initial_step="critique",
        current_step="critique",
        status=RunStatus.RUNNING,
        state={"draft": {"body": "A short draft."}},
        transitions=[],
    )


@pytest.fixture
def critique_step():
    return StepDef(
        id="critique",
        kind=StepKind.ROUTE,
        config={
            "prompt": "Critique this draft: {{ state.draft.body }}",
            "system": "",
        },
        choices=(
            RouteChoice(id="approve", to="publish", when="draft satisfies the brief"),
            RouteChoice(id="revise", to="outline", when="structural rework needed"),
            RouteChoice(id="abort", to="", when="fundamentally broken"),
        ),
    )


def make_llm_response(tool_name: str, args: dict) -> dict:
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
async def test_route_approve_choice(ctx, critique_step, workflow_state):
    """LLM returns 'approve' → next_step='publish', output has choice field."""
    mock_response = make_llm_response("choose_critique", {"choice": "approve"})
    with patch("decafclaw.workflow.step_executors.call_llm",
               new=AsyncMock(return_value=mock_response)):
        result = await execute(ctx, critique_step, workflow_state)

    assert isinstance(result, StepResult)
    assert result.output == {"choice": "approve"}
    assert result.next_step == "publish"
    assert result.suspend_status is None


@pytest.mark.asyncio
async def test_route_revise_choice(ctx, critique_step, workflow_state):
    """LLM returns 'revise' → next_step='outline' (back-edge cycle)."""
    mock_response = make_llm_response("choose_critique", {"choice": "revise"})
    with patch("decafclaw.workflow.step_executors.call_llm",
               new=AsyncMock(return_value=mock_response)):
        result = await execute(ctx, critique_step, workflow_state)

    assert result.output == {"choice": "revise"}
    assert result.next_step == "outline"


@pytest.mark.asyncio
async def test_route_abort_is_terminal(ctx, critique_step, workflow_state):
    """LLM returns 'abort' with empty to='' → next_step is None (terminal)."""
    mock_response = make_llm_response("choose_critique", {"choice": "abort"})
    with patch("decafclaw.workflow.step_executors.call_llm",
               new=AsyncMock(return_value=mock_response)):
        result = await execute(ctx, critique_step, workflow_state)

    assert result.output == {"choice": "abort"}
    assert result.next_step is None  # empty to="" → terminal


@pytest.mark.asyncio
async def test_route_unknown_choice_raises(ctx, critique_step, workflow_state):
    """LLM returns a choice not in declared set → RuntimeError."""
    mock_response = make_llm_response("choose_critique", {"choice": "mystery"})
    with patch("decafclaw.workflow.step_executors.call_llm",
               new=AsyncMock(return_value=mock_response)):
        with pytest.raises(RuntimeError, match="mystery"):
            await execute(ctx, critique_step, workflow_state)


@pytest.mark.asyncio
async def test_route_tool_name_uses_step_id(ctx, critique_step, workflow_state):
    """The forced-tool name is choose_<step.id>."""
    captured_tools: list = []

    async def capture_call_llm(config, messages, *, tools=None, model_name=None):
        if tools:
            captured_tools.extend(tools)
        return make_llm_response("choose_critique", {"choice": "approve"})

    with patch("decafclaw.workflow.step_executors.call_llm", new=capture_call_llm):
        await execute(ctx, critique_step, workflow_state)

    assert len(captured_tools) == 1
    fn = captured_tools[0]["function"]
    assert fn["name"] == "choose_critique"


@pytest.mark.asyncio
async def test_route_schema_includes_enum_and_description(ctx, critique_step, workflow_state):
    """Schema passed to LLM has enum of choice ids and description with when-hints."""
    captured_tools: list = []

    async def capture_call_llm(config, messages, *, tools=None, model_name=None):
        if tools:
            captured_tools.extend(tools)
        return make_llm_response("choose_critique", {"choice": "approve"})

    with patch("decafclaw.workflow.step_executors.call_llm", new=capture_call_llm):
        await execute(ctx, critique_step, workflow_state)

    fn = captured_tools[0]["function"]
    params = fn["parameters"]
    choice_prop = params["properties"]["choice"]
    assert set(choice_prop["enum"]) == {"approve", "revise", "abort"}
    desc = choice_prop["description"]
    assert "approve" in desc
    assert "draft satisfies the brief" in desc
    assert "revise" in desc
    assert "structural rework needed" in desc


@pytest.mark.asyncio
async def test_route_prompt_rendered_from_state(ctx, critique_step, workflow_state):
    """Prompt is Jinja-rendered against state before being sent to the LLM."""
    captured_messages: list = []

    async def capture_call_llm(config, messages, *, tools=None, model_name=None):
        captured_messages.extend(messages)
        return make_llm_response("choose_critique", {"choice": "approve"})

    with patch("decafclaw.workflow.step_executors.call_llm", new=capture_call_llm):
        await execute(ctx, critique_step, workflow_state)

    user_content = next(m["content"] for m in captured_messages if m["role"] == "user")
    assert "A short draft." in user_content
