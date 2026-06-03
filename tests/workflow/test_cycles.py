"""Tests for workflow cycle (back-edge) execution.

Covers:
  - Back-edge: route step sends control to an earlier step.
  - Latest-wins: revisiting a step overwrites state[step_id].
  - _MAX_STEPS guard: runaway cycle raises RuntimeError.
  - log_qa accumulation across cycles: demonstrates explicit
    accumulation under latest-wins state semantics.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from decafclaw.workflow import engine
from decafclaw.workflow.registry import clear, register
from decafclaw.workflow.types import (
    EdgeRef,
    RouteChoice,
    RunStatus,
    StepDef,
    StepKind,
    WorkflowDef,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_registry():
    """Clear the workflow registry before/after each test."""
    clear()
    yield
    clear()


def _two_step_cycle_wf() -> WorkflowDef:
    """A→B→A cycle for testing back-edge, latest-wins, and _MAX_STEPS.

    step_a: python — increments a counter
    step_b: route  — loops back to step_a ("loop") or stops ("done" → terminal)
    """
    step_a = StepDef(
        id="step_a",
        kind=StepKind.PYTHON,
        config={"fn": "bump_counter"},
        next_edges=(EdgeRef(to="step_b"),),
    )
    step_b = StepDef(
        id="step_b",
        kind=StepKind.ROUTE,
        config={"prompt": "Loop or stop?"},
        choices=(
            RouteChoice(id="loop", to="step_a", when="loop back"),
            RouteChoice(id="done", to="", when="stop"),
        ),
    )
    return WorkflowDef(
        name="cycle_wf",
        description="Two-step cycle for testing.",
        initial_step="step_a",
        steps=(step_a, step_b),
        skill_dir=None,
    )


def _make_llm_response(tool_name: str, choice: str) -> dict:
    return {
        "tool_calls": [{
            "function": {
                "name": tool_name,
                "arguments": json.dumps({"choice": choice}),
            }
        }]
    }


def _make_fake_module(fn):
    """Return a SimpleNamespace exposing fn by name.

    We use SimpleNamespace rather than MagicMock to avoid the __spec__
    AttributeError that MagicMock triggers inside importlib.resources.
    """
    ns = SimpleNamespace()
    setattr(ns, fn.__name__, fn)
    return ns


def _run_cycle_wf(ctx, wf, bump_fn, llm_responses):
    """Return an async function that runs the cycle_wf with given mocks.

    The ``call_llm`` patch must be outermost so its target lookup resolves
    against the real ``step_executors`` module before ``importlib`` is
    patched. The ``importlib.import_module`` patch must be innermost so it
    intercepts only the tool-module lookup, not the target resolution of the
    ``call_llm`` patch itself.
    """
    fake_mod = _make_fake_module(bump_fn)
    responses = iter(llm_responses)

    async def fake_call_llm(config, messages, *, tools=None, model_name=None):
        choice = next(responses, "done")
        tool_name = (tools or [{}])[0].get("function", {}).get("name", "choose_step_b")
        return _make_llm_response(tool_name, choice)

    async def run():
        # call_llm patch is outermost — resolves before importlib is patched.
        with patch("decafclaw.workflow.step_executors.call_llm", new=fake_call_llm):
            with patch("decafclaw.workflow.step_executors.importlib.import_module",
                       return_value=fake_mod):
                return await engine.start_workflow(ctx, "cycle_wf")

    return run


# ---------------------------------------------------------------------------
# Back-edge: route returns to an earlier step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_back_edge_revisits_step(ctx):
    """A route step can send control back to an earlier step (back-edge).

    The cycle runs twice: step_a → step_b (loop) → step_a → step_b (done).
    step_a is called twice in total.
    """
    call_count = {"n": 0}

    def bump_counter(state: dict) -> dict:
        call_count["n"] += 1
        return {"count": call_count["n"]}

    wf = _two_step_cycle_wf()
    register(wf)

    run = _run_cycle_wf(ctx, wf, bump_counter, ["loop", "done"])
    state = await run()

    assert call_count["n"] == 2
    assert state.status == RunStatus.DONE


# ---------------------------------------------------------------------------
# Latest-wins: revisiting a step overwrites state[step_id]
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_latest_wins_on_revisit(ctx):
    """Revisiting step_a overwrites state['step_a'] with the latest output."""
    call_count = {"n": 0}

    def bump_counter(state: dict) -> dict:
        call_count["n"] += 1
        return {"count": call_count["n"]}

    wf = _two_step_cycle_wf()
    register(wf)

    run = _run_cycle_wf(ctx, wf, bump_counter, ["loop", "done"])
    state = await run()

    # Latest-wins: state["step_a"] reflects the second visit (count=2)
    assert state.state["step_a"]["count"] == 2


# ---------------------------------------------------------------------------
# _MAX_STEPS guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_steps_guard_raises(ctx):
    """Runaway cycle exceeding _MAX_STEPS raises RuntimeError."""
    def bump_counter(state: dict) -> dict:
        return {"count": state.get("step_a", {}).get("count", 0) + 1}

    wf = _two_step_cycle_wf()
    register(wf)

    # Infinite loop — "loop" every time; _MAX_STEPS fires before done
    run = _run_cycle_wf(ctx, wf, bump_counter, ["loop"] * 10_000)
    with pytest.raises(RuntimeError, match="step limit"):
        await run()


# ---------------------------------------------------------------------------
# log_qa accumulation via latest-wins
# ---------------------------------------------------------------------------

def test_log_qa_accumulates_across_cycles():
    """log_qa extends the prior qa_log list on each call — explicit accumulation.

    This is a pure unit test of the interview tools.py function; no engine
    or LLM needed. It validates the latest-wins escape hatch that the
    interview workflow relies on for building its Q&A history.
    """
    from decafclaw.skills.interview.tools import log_qa

    # First call — no prior log_qa entry in state
    state1 = {
        "pick_question": {"question": "What do you do?"},
        "ask_user": {"value": "I write software."},
    }
    result1 = log_qa(state1)
    assert result1 == {
        "qa_log": [{"q": "What do you do?", "a": "I write software."}]
    }

    # Simulate engine writing result1 to state["log_qa"]
    state2 = {
        **state1,
        "log_qa": result1,
        "pick_question": {"question": "What language?"},
        "ask_user": {"value": "Python mostly."},
    }
    result2 = log_qa(state2)
    assert result2 == {
        "qa_log": [
            {"q": "What do you do?", "a": "I write software."},
            {"q": "What language?", "a": "Python mostly."},
        ]
    }

    # Third cycle: simulate clarify path — same question, new answer
    state3 = {
        **state2,
        "log_qa": result2,
        "pick_question": {"question": "What language?"},
        "ask_user": {"value": "Python, Go sometimes."},
    }
    result3 = log_qa(state3)
    assert len(result3["qa_log"]) == 3
    assert result3["qa_log"][2] == {
        "q": "What language?",
        "a": "Python, Go sometimes.",
    }
