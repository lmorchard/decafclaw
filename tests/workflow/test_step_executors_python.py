"""Tests for the python step executor.

Covers:
  - Sync function: returns dict, written to state.
  - Async function: same.
  - Scalar return wrapped as {"value": <scalar>}.
  - Function not found: RuntimeError with clear error.
  - Function not callable: RuntimeError with clear error.
  - Function raises: exception propagates.
  - next_step resolved from next_edges using step output.
"""

from unittest.mock import patch

import pytest

from decafclaw.workflow.step_executors import StepResult, execute
from decafclaw.workflow.types import EdgeRef, RunStatus, StepDef, StepKind, WorkflowState


@pytest.fixture
def workflow_state():
    return WorkflowState(
        workflow="test_wf",
        run_id="run-001",
        conv_id="conv-1",
        initial_step="word_count",
        current_step="word_count",
        status=RunStatus.RUNNING,
        state={"draft": {"body": "one two three four five"}},
        transitions=[],
    )


@pytest.fixture
def word_count_step():
    return StepDef(
        id="word_count",
        kind=StepKind.PYTHON,
        config={"fn": "count_words"},
        next_edges=(
            EdgeRef(to="shorten", if_expr="state.word_count.count > 3"),
            EdgeRef(to="critique"),
        ),
    )


def _make_mock_module(fn):
    """Return an object whose attributes include the given function by name."""
    class FakeModule:
        pass
    mod = FakeModule()
    setattr(mod, fn.__name__, fn)
    return mod


@pytest.mark.asyncio
async def test_python_sync_function_returns_dict(ctx, word_count_step, workflow_state):
    """Sync function returning dict is called with state; result lands in output."""
    def count_words(state: dict) -> dict:
        body = state.get("draft", {}).get("body", "")
        return {"count": len(body.split())}

    fake_mod = _make_mock_module(count_words)
    with patch("decafclaw.workflow.step_executors.importlib.import_module",
               return_value=fake_mod):
        result = await execute(ctx, word_count_step, workflow_state)

    assert isinstance(result, StepResult)
    assert result.output == {"count": 5}
    assert result.suspend_status is None


@pytest.mark.asyncio
async def test_python_async_function(ctx, word_count_step, workflow_state):
    """Async function is awaited; result is the same as sync."""
    async def count_words(state: dict) -> dict:
        return {"count": len(state.get("draft", {}).get("body", "").split())}

    fake_mod = _make_mock_module(count_words)
    with patch("decafclaw.workflow.step_executors.importlib.import_module",
               return_value=fake_mod):
        result = await execute(ctx, word_count_step, workflow_state)

    assert result.output == {"count": 5}


@pytest.mark.asyncio
async def test_python_scalar_return_wrapped(ctx, workflow_state):
    """Non-dict return is wrapped as {"value": <scalar>}."""
    step = StepDef(
        id="scalar_step",
        kind=StepKind.PYTHON,
        config={"fn": "return_scalar"},
    )
    workflow_state.current_step = "scalar_step"

    def return_scalar(state: dict):
        return 42

    fake_mod = _make_mock_module(return_scalar)
    with patch("decafclaw.workflow.step_executors.importlib.import_module",
               return_value=fake_mod):
        result = await execute(ctx, step, workflow_state)

    assert result.output == {"value": 42}


@pytest.mark.asyncio
async def test_python_function_not_found_raises(ctx, word_count_step, workflow_state):
    """Missing function in module raises RuntimeError with clear message."""
    class EmptyModule:
        pass

    with patch("decafclaw.workflow.step_executors.importlib.import_module",
               return_value=EmptyModule()):
        with pytest.raises(RuntimeError, match="count_words"):
            await execute(ctx, word_count_step, workflow_state)


@pytest.mark.asyncio
async def test_python_not_callable_raises(ctx, word_count_step, workflow_state):
    """Attribute exists but is not callable → RuntimeError."""
    class ModuleWithNonCallable:
        count_words = "I am not a function"

    with patch("decafclaw.workflow.step_executors.importlib.import_module",
               return_value=ModuleWithNonCallable()):
        with pytest.raises(RuntimeError, match="count_words"):
            await execute(ctx, word_count_step, workflow_state)


@pytest.mark.asyncio
async def test_python_function_exception_propagates(ctx, word_count_step, workflow_state):
    """Exception raised inside the fn propagates from execute().

    The step config has fn="count_words", so the mock module must expose
    a function with that exact name.
    """
    def count_words(state: dict) -> dict:
        raise ValueError("intentional test failure")

    fake_mod = _make_mock_module(count_words)
    with patch("decafclaw.workflow.step_executors.importlib.import_module",
               return_value=fake_mod):
        with pytest.raises(ValueError, match="intentional test failure"):
            await execute(ctx, word_count_step, workflow_state)


@pytest.mark.asyncio
async def test_python_next_step_resolved_from_output(ctx, word_count_step, workflow_state):
    """Edge condition uses step output: count > 3 → shorten; else → critique."""
    # State has 5-word draft → count=5 > 3 → shorten
    def count_words(state: dict) -> dict:
        return {"count": 5}

    fake_mod = _make_mock_module(count_words)
    with patch("decafclaw.workflow.step_executors.importlib.import_module",
               return_value=fake_mod):
        result = await execute(ctx, word_count_step, workflow_state)

    assert result.next_step == "shorten"


@pytest.mark.asyncio
async def test_python_next_step_default_edge(ctx, word_count_step, workflow_state):
    """count <= 3 → falls through to default edge (critique)."""
    def count_words(state: dict) -> dict:
        return {"count": 2}

    fake_mod = _make_mock_module(count_words)
    with patch("decafclaw.workflow.step_executors.importlib.import_module",
               return_value=fake_mod):
        result = await execute(ctx, word_count_step, workflow_state)

    assert result.next_step == "critique"


@pytest.mark.asyncio
async def test_python_raises_when_tools_module_missing(ctx, word_count_step, workflow_state):
    """ModuleNotFoundError propagates clearly when the workflow's tools.py doesn't exist."""
    with patch(
        "decafclaw.workflow.step_executors.importlib.import_module",
        side_effect=ModuleNotFoundError("No module named 'decafclaw.skills.test_wf.tools'"),
    ):
        with pytest.raises(ModuleNotFoundError, match="test_wf"):
            await execute(ctx, word_count_step, workflow_state)
