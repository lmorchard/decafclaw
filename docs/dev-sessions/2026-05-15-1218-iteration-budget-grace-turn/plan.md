# Iteration Budget with Grace Turn Implementation Plan

**Goal:** Replace the hard `for ... range(max_tool_iterations)` counter in `agent.py` with an `IterationBudget` object that grants one grace turn at exhaustion, plus a `refund()` path for the empty-response retry.

**Approach:** Add a standalone `iteration_budget.py` module with the class (Phase 1). Wire it into the agent loop, adding a no-tools LLM call ("grace turn") when budget runs out and a fall-back path when the grace call errors (Phase 2). Wire `refund()` into the empty-response retry path so it doesn't eat budget (Phase 3). Update the two doc pages that describe the iteration cap (Phase 4).

**Tech stack:** Python 3.x, `dataclasses`, `asyncio`, `pytest` + `pytest-asyncio`.

---

## Phase 1: `IterationBudget` module + unit tests

Adds a standalone, framework-free class with `consume()`, `refund()`, and `grace_turn()` semantics. No callers yet — Phase 2 wires it in. Independently valuable: the class is fully unit-tested and could be used anywhere a budget pattern is needed.

**Files:**
- Create: `src/decafclaw/iteration_budget.py`
- Create: `tests/test_iteration_budget.py`

**Key changes:**

`src/decafclaw/iteration_budget.py`:

```python
"""Iteration budget for the agent loop with one grace turn at exhaustion."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class IterationBudget:
    """Tracks remaining tool-call iterations with one-shot grace-turn semantics.

    The agent loop calls ``consume()`` at the top of each iteration. When it
    returns False, the budget is exhausted; the loop then calls
    ``grace_turn()`` once to gate a single no-tools final LLM call.

    ``refund()`` gives back one iteration for "free retries" — calls that
    produced nothing usable (e.g. an empty LLM response) and shouldn't count
    against the user-visible budget.
    """

    remaining: int
    _grace_used: bool = False

    def consume(self) -> bool:
        """Try to consume one iteration. Returns True if budget remained
        (and was decremented), False if already exhausted (no change)."""
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True

    def refund(self) -> None:
        """Give back one iteration. Always increments — caller decides
        whether a refund is warranted (e.g. empty-response retry)."""
        self.remaining += 1

    def grace_turn(self) -> bool:
        """Returns True the first time it's called, False on every
        subsequent call. Used to gate the one-shot grace LLM call after
        ``consume()`` returns False."""
        if self._grace_used:
            return False
        self._grace_used = True
        return True
```

`tests/test_iteration_budget.py` — TDD-style unit tests covering:

```python
"""Unit tests for IterationBudget."""

from decafclaw.iteration_budget import IterationBudget


def test_initial_state():
    b = IterationBudget(remaining=3)
    assert b.remaining == 3
    assert b._grace_used is False


def test_consume_decrements_and_returns_true_while_budget_remains():
    b = IterationBudget(remaining=2)
    assert b.consume() is True
    assert b.remaining == 1
    assert b.consume() is True
    assert b.remaining == 0


def test_consume_returns_false_when_exhausted():
    b = IterationBudget(remaining=1)
    assert b.consume() is True
    assert b.consume() is False
    assert b.remaining == 0  # no decrement past zero


def test_consume_returns_false_from_zero_initial():
    b = IterationBudget(remaining=0)
    assert b.consume() is False
    assert b.remaining == 0


def test_refund_increments_remaining():
    b = IterationBudget(remaining=1)
    b.consume()
    assert b.remaining == 0
    b.refund()
    assert b.remaining == 1


def test_refund_after_exhaustion_restores_budget():
    """Refunding after consume returned False puts the budget back above zero
    and lets a subsequent consume succeed."""
    b = IterationBudget(remaining=1)
    assert b.consume() is True
    assert b.consume() is False
    b.refund()
    assert b.consume() is True


def test_grace_turn_fires_once():
    b = IterationBudget(remaining=0)
    assert b.grace_turn() is True
    assert b.grace_turn() is False
    assert b.grace_turn() is False


def test_grace_turn_independent_of_consume_state():
    """grace_turn() can be called whether budget was used up via consume
    or started at zero — it's only gated by its own _grace_used flag."""
    b = IterationBudget(remaining=2)
    b.consume()
    b.consume()
    assert b.consume() is False
    assert b.grace_turn() is True
    assert b.grace_turn() is False
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (new tests added; nothing else should be affected) — 2515 passed (8 new)
- [x] `make check` passes
- [x] `pytest tests/test_iteration_budget.py -v` shows all 8 tests passing

**Verification — manual:**
- [x] Class shape matches the issue sketch (`remaining`, `_grace_used`, `consume`, `refund`, `grace_turn`).
- [x] Module docstring and class docstring explain the grace-turn pattern clearly.

---

## Phase 2: Wire IterationBudget into the agent loop + grace turn

Replace the `for iteration in range(...)` loop in `agent.py:467` with a `while budget.consume()` loop. On budget exhaustion (after the loop), call `budget.grace_turn()`; if it returns True, append a directive user-role nudge (`[iteration_limit] STOP. Do not continue the task...`) and make one no-tools LLM call. On grace LLM exception, fall back to today's `_finalize_max_iterations()` behavior. (User-role chosen over an earlier system-role draft after smoke testing showed models routinely ignored a softer system hint and tried to keep executing the original task.)

**Files:**
- Modify: `src/decafclaw/agent.py` — change the loop, add `_run_grace_turn()` method, keep `_finalize_max_iterations()` as the fall-back path.
- Modify: `tests/test_agent_turn.py` — update the two existing max-iterations tests to assert the new grace-turn behavior; add two new tests (success path, exception fallback).

**Key changes:**

In `agent.py`, replace lines 465-471 (in `TurnRunner.run`):

```python
# Before:
self.accumulated_text_parts = []  # text from iterations that also had tool calls

for iteration in range(self.config.agent.max_tool_iterations):
    outcome = await self._run_iteration(iteration)
    if isinstance(outcome, _Final):
        return outcome.result
return await self._finalize_max_iterations()

# After:
self.accumulated_text_parts = []  # text from iterations that also had tool calls
self.budget = IterationBudget(remaining=self.config.agent.max_tool_iterations)

iteration = 0
while self.budget.consume():
    outcome = await self._run_iteration(iteration)
    if isinstance(outcome, _Final):
        return outcome.result
    iteration += 1

# Budget exhausted — try one grace turn before giving up
if self.budget.grace_turn():
    grace_result = await self._run_grace_turn()
    if grace_result is not None:
        return grace_result
return await self._finalize_max_iterations()
```

Add a new method on `TurnRunner` (sibling to `_finalize_max_iterations`). NOTE: the role and wording shown below were iterated post-implementation after smoke testing — final form is `role: "user"` with directive `[iteration_limit] STOP. Do not continue...` framing (see spec.md and the final commit). Keeping the original draft here as historical record:

```python
async def _run_grace_turn(self) -> "ToolResult | None":
    """One-shot final LLM call after budget exhaustion. Appends a terse
    system note, calls the LLM with tools=[], archives the result.

    Returns the final ToolResult on success, or None on exception (caller
    falls back to _finalize_max_iterations)."""
    log.info("Iteration budget exhausted — making grace-turn LLM call")
    grace_note = {
        "role": "system",
        "content": (
            "You have reached your tool iteration budget. Produce a final "
            "response to the user summarizing your progress; you cannot "
            "call any more tools."
        ),
    }
    self.messages.append(grace_note)
    try:
        response = await _call_llm_with_events(
            self.ctx, self.config, self.messages, [],
            **self.model_override,
        )
    except Exception as exc:
        log.warning(f"Grace-turn LLM call failed: {exc!r} — falling back to notice")
        return None

    content = response.get("content") or ""
    final_msg = {"role": "assistant", "content": content}
    self.history.append(final_msg)
    _archive(self.ctx, final_msg)
    await _maybe_compact(
        self.ctx, self.config, self.history, self.prompt_tokens,
    )
    return self._extract_workspace_media(content)
```

Add the import at the top of `agent.py` (alphabetical with other intra-package imports):

```python
from .iteration_budget import IterationBudget
```

Update existing tests in `tests/test_agent_turn.py`:

```python
# test_run_agent_turn_max_iterations — was: assert "max tool iterations" in result.text
# After: simulate budget=2 tool-call responses + one grace-turn final response.
@pytest.mark.asyncio
async def test_run_agent_turn_max_iterations_grace_turn(ctx):
    """When budget exhausts, one grace LLM call (tools=[]) produces the final response."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."
    ctx.config.agent.max_tool_iterations = 2

    tool_call_response = _mock_llm_response(
        content=None,
        tool_calls=[{
            "id": "tc1",
            "function": {"name": "memory_recent", "arguments": "{}"},
        }],
    )
    grace_response = _mock_llm_response(content="I ran out of iterations. Here's where I am.")

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        # First two calls: tool-call responses (budget=2). Third: grace-turn final.
        mock_llm.side_effect = [tool_call_response, tool_call_response, grace_response]
        history = []
        result = await run_agent_turn(ctx, "loop forever", history)

    assert "I ran out of iterations" in result.text
    assert "max tool iterations" not in result.text  # grace succeeded, no fallback notice
    # Last LLM call should have tools=[] (the grace call).
    # call_llm is invoked as `call_llm(config, messages, tools=tools, ...)` in
    # agent.py:_call_llm_with_events, so tools is a keyword arg.
    last_call = mock_llm.call_args_list[-1]
    assert last_call.kwargs.get("tools") == []


# test_run_agent_turn_max_iterations_preserves_text — replaced by:
@pytest.mark.asyncio
async def test_run_agent_turn_grace_turn_fallback_on_exception(ctx):
    """When the grace LLM call raises, fall back to accumulated text + notice."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."
    ctx.config.agent.max_tool_iterations = 2

    tool_call_response = _mock_llm_response(
        content="Let me check that for you.",
        tool_calls=[{
            "id": "tc1",
            "function": {"name": "memory_recent", "arguments": "{}"},
        }],
    )

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        # Two tool-call responses, then grace call raises.
        mock_llm.side_effect = [
            tool_call_response, tool_call_response,
            RuntimeError("simulated LLM failure on grace turn"),
        ]
        history = []
        result = await run_agent_turn(ctx, "loop forever", history)

    # Falls back to accumulated text + notice
    assert "Let me check that for you." in result.text
    assert "max tool iterations" in result.text
```

The original `test_run_agent_turn_max_iterations` and `test_run_agent_turn_max_iterations_preserves_text` are **replaced** by these two new tests. Delete the originals — both their assertions are no longer correct under the new behavior.

**Note on `call_llm` signature:** confirm whether `tools` is positional or keyword by reading `agent.py:_call_llm_with_events` and its `call_llm` call site. The existing patterns at `agent.py:617-620` and `agent.py:655-658` show `_call_llm_with_events(self.ctx, self.config, self.messages, [], **self.model_override)` — the `[]` is positional. The test assertion should match.

**Behavior change to verify:** the grace turn publishes the normal `llm_start`/`llm_end` events. No reflection runs on the grace turn output (it bypasses `_handle_no_tool_calls`). Compaction still fires after grace turn (via the `_maybe_compact` call in `_run_grace_turn`).

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (replaced tests assert new behavior; full suite stays green) — 2515 passed
- [x] `make check` passes
- [x] `pytest tests/test_agent_turn.py -v -k "max_iterations or grace"` shows the new + adjusted tests passing

**Verification — manual:**
- [x] Read `agent.py:run` and confirm the while-loop replaces the for-loop cleanly — no leftover `iteration` variable that becomes stale.
- [x] Confirm grace turn fall-back path goes through `_finalize_max_iterations()` so the user always gets *something*.
- [x] Confirm the grace-turn nudge is appended to `self.messages` only (not `self.history`) — it's plumbing, not a conversation artifact. (Note: ended up as a user-role message, not the system note originally drafted — see commit history.)

---

## Phase 3: Wire `refund()` into the empty-response retry path

Today the empty-response retry (`agent.py:_handle_no_tool_calls`, line 674-677) returns `_Continue()` after incrementing `self.empty_retries`. With the new budget, that retry still consumes an iteration via the next `budget.consume()` call. Call `self.budget.refund()` immediately before returning `_Continue()` so the retry is "free."

**Files:**
- Modify: `src/decafclaw/agent.py` — call `self.budget.refund()` in the empty-response branch.
- Modify: `tests/test_agent_turn.py` — add a test asserting refund prevents budget exhaustion in this scenario.

**Key changes:**

In `agent.py:_handle_no_tool_calls`, update the empty-response branch:

```python
# Before:
content = response.get("content") or ""
if not content:
    if self.empty_retries < 1:
        self.empty_retries += 1
        log.warning("LLM returned empty response, retrying")
        return _Continue()
    log.warning("LLM returned empty content with no tool calls (after retry)")

# After:
content = response.get("content") or ""
if not content:
    if self.empty_retries < 1:
        self.empty_retries += 1
        self.budget.refund()  # empty response — don't count against budget
        log.warning("LLM returned empty response, retrying")
        return _Continue()
    log.warning("LLM returned empty content with no tool calls (after retry)")
```

New test in `tests/test_agent_turn.py`:

```python
@pytest.mark.asyncio
async def test_run_agent_turn_empty_retry_refunds_budget(ctx):
    """Empty-response retry refunds the budget so the retry doesn't exhaust it."""
    ctx.config.llm.streaming = False
    ctx.config.system_prompt = "You are a test bot."
    ctx.config.agent.max_tool_iterations = 1  # budget of exactly one iteration

    empty_response = _mock_llm_response(content="")
    final_response = _mock_llm_response(content="Hello after retry.")

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        # First call: empty (triggers refund + retry).
        # Second call: final response.
        # Without refund, budget=1 would be exhausted after the empty call and
        # the retry would never run as a normal iteration — the grace turn would.
        mock_llm.side_effect = [empty_response, final_response]
        history = []
        result = await run_agent_turn(ctx, "hi", history)

    assert result.text == "Hello after retry."
    # No grace-turn fallback notice should appear — the retry succeeded as a real iteration.
    assert "max tool iterations" not in result.text
    assert mock_llm.call_count == 2  # exactly two LLM calls, no grace turn
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes — 2516 passed
- [x] `make check` passes
- [x] `pytest tests/test_agent_turn.py::test_run_agent_turn_empty_retry_refunds_budget -v` passes

**Verification — manual:**
- [x] Confirm `self.budget.refund()` is called *before* `return _Continue()` — otherwise the next iteration starts and consumes before the refund applies.
- [x] Confirm the existing `test_run_agent_turn_empty_response` test still passes (it tests the post-retry-still-empty path).

---

## Phase 4: Update docs

Two doc pages mention the iteration cap and need a one-line update for the grace turn. CLAUDE.md's reflection note (`Retries consume max_tool_iterations budget.`) remains accurate — reflection retries still consume budget — no change needed there.

**Files:**
- Modify: `docs/architecture.md` — line 127 "Iteration loop" bullet.
- Modify: `docs/reflection.md` — line 34 sentence about budget exhaustion ending the turn.

**Key changes:**

`docs/architecture.md` around line 127:

```diff
-3. **Iteration loop** — up to `max_tool_iterations` (default 200):
+3. **Iteration loop** — up to `max_tool_iterations` (default 200) tool-call rounds, plus one **grace turn** (a final no-tools LLM call when the budget is exhausted, so the model can summarize where it is instead of being cut off):
```

`docs/reflection.md` around line 34:

```diff
-Retries consume iterations from the `max_tool_iterations` budget. If that budget is exhausted during a retry, the turn ends with whatever the agent has produced.
+Retries consume iterations from the `max_tool_iterations` budget. If that budget is exhausted during a retry, the loop ends and the agent loop makes one **grace turn** (a final no-tools LLM call) so the model can produce a closing response summarizing its progress.
```

**Verification — automated:**
- [x] `make lint` passes (no Python touched; sanity check)
- [x] `make test` passes — 2516 passed
- [x] `make check` passes

**Verification — manual:**
- [x] Read both updated lines in context to confirm they flow naturally.
- [x] Confirm no other doc page references the iteration cap in a way that now contradicts the grace turn (`grep -rn "max_tool_iterations" docs/` and skim hits outside `docs/dev-sessions/`). `docs/reflection.md:108` (Max iterations hit skip condition) and `docs/config.md:333` (field table) remain accurate — neither describes the exhaustion-cutoff behavior. `CLAUDE.md` reflection note remains accurate (retries still consume budget).
