"""Tests for the user_input step executor.

Covers:
  - Text mode: returns PAUSED_USER_INPUT with correct pending dict.
  - Choice mode: returns PAUSED_USER_INPUT with choices list in pending.
  - Neither input:text nor choices → RuntimeError.
  - Prompt rendered via Jinja from state.
  - step_id included in pending.
"""

import pytest

from decafclaw.workflow.step_executors import StepResult, execute
from decafclaw.workflow.types import RouteChoice, RunStatus, StepDef, StepKind, WorkflowState


@pytest.fixture
def workflow_state():
    return WorkflowState(
        workflow="interview",
        run_id="run-001",
        conv_id="conv-1",
        initial_step="ask_user",
        current_step="ask_user",
        status=RunStatus.RUNNING,
        state={"pick_question": {"question": "What is your favourite colour?"}},
        transitions=[],
    )


@pytest.fixture
def text_input_step():
    return StepDef(
        id="ask_user",
        kind=StepKind.USER_INPUT,
        config={
            "prompt": "{{ state.pick_question.question }}",
            "input": "text",
        },
    )


@pytest.fixture
def choice_input_step():
    return StepDef(
        id="pick_route",
        kind=StepKind.USER_INPUT,
        config={
            "prompt": "How do you want to proceed?",
        },
        choices=(
            RouteChoice(id="yes", to="confirm", label="Yes, continue"),
            RouteChoice(id="no", to="abort", label="No, stop"),
        ),
    )


@pytest.mark.asyncio
async def test_text_input_suspends_with_paused_status(ctx, text_input_step, workflow_state):
    """user_input(text) returns suspend_status=PAUSED_USER_INPUT."""
    result = await execute(ctx, text_input_step, workflow_state)

    assert isinstance(result, StepResult)
    assert result.suspend_status == RunStatus.PAUSED_USER_INPUT
    assert result.next_step is None
    assert result.output is None


@pytest.mark.asyncio
async def test_text_input_pending_has_step_id_and_mode(ctx, text_input_step, workflow_state):
    """Pending dict has step_id='ask_user' and mode='text'."""
    result = await execute(ctx, text_input_step, workflow_state)

    assert result.pending["step_id"] == "ask_user"
    assert result.pending["mode"] == "text"


@pytest.mark.asyncio
async def test_text_input_prompt_rendered_from_state(ctx, text_input_step, workflow_state):
    """Prompt is Jinja-rendered from workflow state."""
    result = await execute(ctx, text_input_step, workflow_state)

    assert result.pending["prompt"] == "What is your favourite colour?"


@pytest.mark.asyncio
async def test_choice_input_suspends_with_paused_status(ctx, choice_input_step, workflow_state):
    """user_input(choice) returns suspend_status=PAUSED_USER_INPUT."""
    result = await execute(ctx, choice_input_step, workflow_state)

    assert result.suspend_status == RunStatus.PAUSED_USER_INPUT
    assert result.next_step is None
    assert result.output is None


@pytest.mark.asyncio
async def test_choice_input_pending_has_choices(ctx, choice_input_step, workflow_state):
    """Pending dict has mode='choice' and a choices list with id+label."""
    result = await execute(ctx, choice_input_step, workflow_state)

    assert result.pending["mode"] == "choice"
    assert result.pending["step_id"] == "pick_route"
    choices = result.pending["choices"]
    assert len(choices) == 2
    assert choices[0] == {"id": "yes", "label": "Yes, continue"}
    assert choices[1] == {"id": "no", "label": "No, stop"}


@pytest.mark.asyncio
async def test_choice_label_falls_back_to_id(ctx, workflow_state):
    """When RouteChoice.label is empty, label falls back to choice id."""
    step = StepDef(
        id="pick_route",
        kind=StepKind.USER_INPUT,
        config={"prompt": "Choose:"},
        choices=(
            RouteChoice(id="yes", to="confirm"),   # no label
            RouteChoice(id="no", to="abort", label="No"),
        ),
    )
    result = await execute(ctx, step, workflow_state)

    choices = result.pending["choices"]
    assert choices[0] == {"id": "yes", "label": "yes"}  # fallback to id
    assert choices[1] == {"id": "no", "label": "No"}


@pytest.mark.asyncio
async def test_neither_text_nor_choices_raises(ctx, workflow_state):
    """user_input with no input: and no choices → RuntimeError."""
    step = StepDef(
        id="broken",
        kind=StepKind.USER_INPUT,
        config={"prompt": "Oops"},  # no 'input' key, no choices
    )
    with pytest.raises(RuntimeError, match="must have"):
        await execute(ctx, step, workflow_state)
