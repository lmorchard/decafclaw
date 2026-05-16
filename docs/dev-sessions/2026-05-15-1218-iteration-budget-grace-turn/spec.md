# Iteration Budget with Grace Turn Spec

**Goal:** Replace the hard `max_tool_iterations` counter in the agent loop with an `IterationBudget` object that grants one grace turn at exhaustion — giving the model a chance to emit a final summary instead of being cut off mid-conversation.

**Source:** https://github.com/lmorchard/decafclaw/issues/448

## Current state

The agent loop uses a Python `for ... range()` counter (`agent.py:467`) to enforce `config.agent.max_tool_iterations`. When exhausted, `_finalize_max_iterations()` (`agent.py:818-833`) concatenates any text the model emitted alongside tool calls and appends `"[Agent reached max tool iterations (N) without a final response]"`. **No final LLM call is made** — the model has no opportunity to wrap up.

`empty_retries` (`agent.py:_handle_no_tool_calls`) lets the loop retry the LLM once when the model returns an empty response. This retry currently consumes an iteration from the budget even though the model produced nothing usable.

`max_tool_iterations` is defined in `config_types.py:159` (default 200), read via `MAX_TOOL_ITERATIONS` env var (`config.py:396`), and forked per-child-agent in `delegate.py:168-173` (children get `child_max_tool_iterations`, default 10). Children already have independent budgets — no shared state with parent.

Existing tests live in `tests/test_agent_turn.py:568-618` (`test_run_agent_turn_max_iterations`, `test_run_agent_turn_max_iterations_preserves_text`). Both set `max_tool_iterations=2`, mock the LLM to always return a tool-call response, and assert the result contains `"max tool iterations"`.

The loop already has two precedents for a "force final response" LLM call with `tools=[]`: the `end_turn=True` path (`agent.py:653-664`) and the `EndTurnConfirm` presentation call (`agent.py:615-651`).

## Desired end state

A new `src/decafclaw/iteration_budget.py` module exports:

```python
@dataclass
class IterationBudget:
    remaining: int
    _grace_used: bool = False

    def consume(self) -> bool:
        """Decrement remaining. Return True if budget remained; False if exhausted."""
    def refund(self) -> None:
        """Give back one iteration (for 'free' retries like empty-response)."""
    def grace_turn(self) -> bool:
        """Return True the first time it's called after exhaustion; False every time after.
        Used to gate the one-shot final no-tools LLM call."""
```

The agent loop replaces its `for ... range()` with a `while` driven by `budget.consume()`. On exhaustion:

1. If `budget.grace_turn()` returns True, append a directive **user-role** message (`[iteration_limit] You have reached your tool iteration budget. STOP. Do not continue the task you were given — no more tool calls are available. Reply with a brief closing message (1-3 sentences) that summarizes what you accomplished so far and tells the user you've hit your iteration limit.`) to `self.messages`, make one LLM call with `tools=[]`, append the result to `self.history`, archive it, and return `ToolResult(text=content)`. **User-role chosen over system-role** because models weight user messages more heavily mid-turn (matches the reflection critique pattern at `agent.py:800-810`). **Directive wording matters** — a softer "Produce a final response summarizing your progress" caused models to keep trying to do the original task and bail mid-stream when they couldn't call a tool (smoke-test finding).
2. If the grace LLM call raises an exception, log a warning and fall back to today's behavior: assemble `accumulated_text_parts` + `"[Agent reached max tool iterations (N) without a final response]"` notice.

The empty-response retry path (`agent.py:_handle_no_tool_calls` where `empty_retries < 1`) calls `budget.refund()` before returning `_Continue()` so the retry does not eat budget.

User-visible config is unchanged: `max_tool_iterations` still seeds the budget's initial `remaining` value. No new env vars, no new config fields.

Tests:
- New `tests/test_iteration_budget.py` with unit tests: initial state, `consume()` exhaustion semantics, `refund()` increments, `grace_turn()` fires exactly once, refund-after-exhaustion edge case.
- Existing `test_run_agent_turn_max_iterations` and `test_run_agent_turn_max_iterations_preserves_text` updated to assert: (a) one grace LLM call is made (with no tools), (b) its content appears in the result, (c) the "max tool iterations" notice does NOT appear when grace succeeds.
- New `test_run_agent_turn_grace_turn_fallback`: mock the grace LLM call to raise an exception, assert the result falls back to accumulated text + notice.
- New `test_run_agent_turn_empty_retry_refunds_budget`: set `max_tool_iterations=1`, simulate one empty response then a normal response, assert the turn completes successfully (would fail without refund — both the empty retry and the final response would consume the only budget).
- Child-agent test in `tests/test_delegate.py` remains green (independent budget shape is unchanged).
- New `evals/grace-turn.yaml` (real-LLM eval) — drives a sequential-tool prompt with `setup.max_tool_iterations: 3` and asserts the grace turn produces a coherent wrap-up (response mentions the limit; fallback notice absent; ≤3 tool calls). Per-test budget override needs a small extension to the eval framework: `eval/runner.py:_run_one` now honors `setup.max_tool_iterations` when building the per-test config (single-line addition; documented in `docs/eval-loop.md`).

## Design decisions

- **Decision:** New module `src/decafclaw/iteration_budget.py`, not inline in `agent.py`.
  - **Why:** `agent.py` is already ~900 lines. A standalone class is trivially unit-testable in isolation; matches the pattern of small purposeful modules around the agent loop (`compaction_decisions.py`, `context_cleanup.py`).
  - **Rejected:** Inline. Smaller diff but more friction for the unit tests, which want to exercise the class without standing up a full agent context.

- **Decision:** Grace turn appends a directive **user-role** nudge before the no-tools LLM call.
  - **Why:** Most explicit signal to the model that it has hit the limit and needs to wrap up. User-role chosen over system-role because models weight user-role messages more heavily mid-turn — matches the reflection-critique pattern at `agent.py:800-810`. The wording uses imperative "STOP / Do not continue" framing because a softer system-role hint let models keep trying to complete the original task and bail mid-stream when they realized they couldn't call a tool (smoke-test finding).
  - **Rejected:** Plain `tools=[]` with no note. Cleaner code but relies on the model's inference; not all providers handle the empty-tools signal identically.
  - **Rejected:** System-role note. Tried during initial implementation; real-LLM smoke showed models routinely ignored it and tried to keep executing the task.

- **Decision:** `refund()` wired only into the empty-response retry path in this PR.
  - **Why:** Empty-response retry is the unambiguous "free retry" case — the model produced nothing usable, the next call is making good on the dropped attempt. Reflection retries are substantive critique cycles (Reflexion pattern), and treating them as free would meaningfully extend turns when reflection misfires.
  - **Rejected:** Add `refund()` API only with no callers. Unused API is hard to keep correctly designed. Better to ship with one canonical use site we can test.
  - **Rejected:** Wire into reflection retries too. Larger behavior change with non-obvious cost — file follow-up if observation later shows it's needed.

- **Decision:** On grace LLM call exception, fall back to today's accumulated-text + notice behavior.
  - **Why:** A turn should never fail silently with no user-visible output. The existing notice is a known-good degraded path.
  - **Rejected:** Let the exception propagate. Cleaner but a user-visible turn failure where today they would have gotten partial output.

- **Decision:** Child agents continue to receive their own forked `IterationBudget` via `child_max_tool_iterations`; no shared budget with parent.
  - **Why:** Already the existing shape (`delegate.py:168-173` forks `AgentConfig`). Shared budgets would couple unrelated work and surprise users who set a small parent limit. Matches the issue's "Probably independent" answer.
  - **Rejected:** Shared budget. Would require introducing a parent→child reference path; not justified by any observed need.

## Patterns to follow

- **Module shape:** small focused module with a single class — see `src/decafclaw/compaction_decisions.py` and `src/decafclaw/context_cleanup.py` for the established pattern.
- **No-tools LLM call:** mirror `agent.py:653-664` (end_turn=True path) — `await _call_llm_with_events(self.ctx, self.config, self.messages, [], **self.model_override)`, append to history, archive, return `_Final`.
- **Grace-turn nudge injection:** append a `{"role": "user", "content": "[iteration_limit] ..."}` entry to `self.messages` immediately before the grace LLM call. Do **not** add it to `self.history` (history is the conversation archive; the grace prompt is ephemeral plumbing). See `_inject_deferred_tools_message` in `agent.py` for the messages-vs-history distinction, and the reflection-critique pattern at `agent.py:800-810` for the user-role + `[tag]` convention.
- **Test mocking:** patch `decafclaw.agent.call_llm` with `AsyncMock`, use `side_effect` (list of responses) when iterations need to differ — see `tests/test_agent_turn.py:568-618` for the `return_value` form and adapt to `side_effect` for sequenced responses.
- **Dataclass over plain class:** the issue sketch already uses `@dataclass`; keep it that way for `__repr__`, equality, and consistency with surrounding code (`config_types.py`).

## What we're NOT doing

- **No config changes.** `max_tool_iterations` is unchanged; no new fields on `AgentConfig`; no new env vars.
- **No reflection-retry refund.** Out of scope; file follow-up only if observation justifies it.
- **No multi-grace-turn semantics.** Exactly one grace LLM call. If the grace turn itself emits tool calls, ignore them — the call goes out with `tools=[]` so the model can't request any.
- **No changes to `delegate.py` / child-agent budget shape.** Child agents continue to use `child_max_tool_iterations`; the only change there is that the for-loop becomes a while-budget loop in the shared `agent.py` runner.
- **No changes to compaction or reflection trigger points.** Grace turn does not run reflection or compaction on its output — it's the terminal response.
- **No event renaming or new event types.** The grace LLM call publishes the same `llm_start`/`llm_end` events as any other LLM call.
- **No change to the existing `[Agent reached max tool iterations (N) without a final response]` string** other than its role (used only as a fallback for grace-turn failure now, not the default success path).

## Open questions

None — all design questions resolved in brainstorm.
