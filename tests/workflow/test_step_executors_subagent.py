"""Tests for the subagent step executor.

Covers:
  - Successful synchronous dispatch (mocked) — output shape, next-step resolution.
  - Suspended dispatch — returns StepResult with PAUSED_SUBAGENT and the right pending payload.
  - Output verification — declared outputs files exist as paths in state.
  - Error in child agent — workflow transitions to ERROR (exception propagates).
"""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.workflow.step_executors import StepResult, execute
from decafclaw.workflow.subagent import SubagentResult
from decafclaw.workflow.types import EdgeRef, RunStatus, StepDef, StepKind, WorkflowState

# Patch target: step_executors imports run_subagent_step by name, so we patch
# it in the step_executors module's namespace, not in subagent's namespace.
_PATCH_TARGET = "decafclaw.workflow.step_executors.run_subagent_step"


@pytest.fixture
def workflow_state():
    return WorkflowState(
        workflow="test_wf",
        run_id="run-001",
        conv_id="conv-1",
        initial_step="gather",
        current_step="gather",
        status=RunStatus.RUNNING,
        state={},
        transitions=[],
    )


@pytest.fixture
def subagent_step():
    return StepDef(
        id="gather",
        kind=StepKind.SUBAGENT,
        config={
            "prompt": "Research the topic: {{ state.topic | default('AI') }}",
            "skill": "tabstack",
            "tools": ["tabstack_research", "vault_write"],
            "outputs": ["sources.md"],
            "context-profile": {"memory-retrieval": "off"},
        },
        next_edges=(EdgeRef(to="read_sources"),),
    )


@pytest.fixture
def subagent_step_no_outputs():
    return StepDef(
        id="analyze",
        kind=StepKind.SUBAGENT,
        config={
            "prompt": "Analyze data",
            "tools": [],
            "outputs": [],
        },
    )


@pytest.mark.asyncio
async def test_subagent_success_output_shape(ctx, subagent_step, workflow_state):
    """Successful subagent: output is {text, outputs} dict; next_step resolved."""
    fake_result = SubagentResult(
        suspended=False,
        child_conv_id="child-abc",
        text="I found 5 sources.",
        output_paths={"sources.md": "conversations/conv-1/artifacts/gather/sources.md"},
    )

    with patch(_PATCH_TARGET, new=AsyncMock(return_value=fake_result)):
        result = await execute(ctx, subagent_step, workflow_state)

    assert isinstance(result, StepResult)
    assert result.suspend_status is None
    assert result.next_step == "read_sources"
    assert result.output is not None
    assert result.output["text"] == "I found 5 sources."
    assert result.output["outputs"] == {
        "sources.md": "conversations/conv-1/artifacts/gather/sources.md",
    }


@pytest.mark.asyncio
async def test_subagent_success_no_outputs(ctx, subagent_step_no_outputs, workflow_state):
    """Subagent with no declared outputs: output_paths is empty dict; terminates."""
    workflow_state.current_step = "analyze"
    fake_result = SubagentResult(
        suspended=False,
        child_conv_id="child-def",
        text="Analysis done.",
        output_paths={},
    )

    with patch(_PATCH_TARGET, new=AsyncMock(return_value=fake_result)):
        result = await execute(ctx, subagent_step_no_outputs, workflow_state)

    assert result.suspend_status is None
    assert result.next_step is None  # terminal
    assert result.output["text"] == "Analysis done."
    assert result.output["outputs"] == {}


@pytest.mark.asyncio
async def test_subagent_suspended(ctx, subagent_step, workflow_state):
    """When run_subagent_step returns suspended=True, executor returns PAUSED_SUBAGENT."""
    fake_result = SubagentResult(
        suspended=True,
        child_conv_id="child-xyz",
        text="",
        output_paths={},
    )

    with patch(_PATCH_TARGET, new=AsyncMock(return_value=fake_result)):
        result = await execute(ctx, subagent_step, workflow_state)

    assert result.suspend_status == RunStatus.PAUSED_SUBAGENT
    assert result.output is None
    assert result.next_step is None
    assert result.pending == {
        "step_id": "gather",
        "child_conv_id": "child-xyz",
    }


@pytest.mark.asyncio
async def test_subagent_error_propagates(ctx, subagent_step, workflow_state):
    """When run_subagent_step raises, the exception propagates from execute()."""
    with patch(_PATCH_TARGET, new=AsyncMock(side_effect=RuntimeError("child timed out"))):
        with pytest.raises(RuntimeError, match="child timed out"):
            await execute(ctx, subagent_step, workflow_state)


@pytest.mark.asyncio
async def test_subagent_prompt_rendered_from_state(ctx, subagent_step, workflow_state):
    """Prompt is Jinja-rendered against state before being passed to run_subagent_step."""
    workflow_state.state["topic"] = "sleep hygiene"

    captured_prompt: list[str] = []

    async def fake_run(*args, **kwargs):
        captured_prompt.append(kwargs.get("prompt", ""))
        return SubagentResult(
            suspended=False,
            child_conv_id="child-123",
            text="done",
            output_paths={"sources.md": "artifacts/gather/sources.md"},
        )

    with patch(_PATCH_TARGET, new=fake_run):
        await execute(ctx, subagent_step, workflow_state)

    assert len(captured_prompt) == 1
    assert "sleep hygiene" in captured_prompt[0]


@pytest.mark.asyncio
async def test_subagent_passes_config_fields(ctx, subagent_step, workflow_state):
    """run_subagent_step is called with skill, tools, outputs, context_profile from config."""
    call_kwargs: dict = {}

    async def fake_run(*args, **kwargs):
        call_kwargs.update(kwargs)
        return SubagentResult(
            suspended=False,
            child_conv_id="child-456",
            text="done",
            output_paths={},
        )

    with patch(_PATCH_TARGET, new=fake_run):
        await execute(ctx, subagent_step, workflow_state)

    assert call_kwargs["skill"] == "tabstack"
    assert call_kwargs["tools"] == ["tabstack_research", "vault_write"]
    assert call_kwargs["outputs"] == ["sources.md"]
    assert call_kwargs["context_profile"] == {"memory-retrieval": "off"}
