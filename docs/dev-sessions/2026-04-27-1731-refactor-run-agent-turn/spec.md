# Refactor: split `run_agent_turn` and `_handle_reflection`

Tracking issue: [#382](https://github.com/lmorchard/decafclaw/issues/382)

## Background

`agent.run_agent_turn` is ~314 lines and `_handle_reflection` is ~117 lines. Both
mix multiple concerns and are review/onboarding friction whenever turn behavior
changes. This refactor preserves behavior — no observable change to any
transport, tool, or persisted artifact — and reorganizes the code so each phase
of the turn is independently legible and the iteration-loop branching is
visible at a glance.

The work is explicitly out of scope for the PR #381 hygiene sweep, which kept
the "no behavior changes" property by deferring this. This dev session picks it
up as a focused effort.

## Goals

- `run_agent_turn` becomes a thin wrapper that constructs a `TurnRunner` and
  calls `.run()`. Public signature unchanged.
- The 200-line iteration-loop body is replaced by a top-level loop that calls
  `_run_iteration` and dispatches on a tagged-union `IterationOutcome`.
- `_handle_reflection`'s 4-tuple return is replaced by a `ReflectionOutcome`
  dataclass; the function splits into three small methods on `TurnRunner`
  (orchestrator, evaluator, verdict-applier).
- All existing tests pass unchanged. Existing live behavior in Mattermost, the
  web UI, terminal, heartbeat, and scheduled tasks is preserved.

## Non-goals

- No new abstractions beyond `TurnRunner`, `IterationOutcome`, and
  `ReflectionOutcome`.
- No move of `agent.py` helpers into separate modules. They stay where they
  are.
- No changes to `reflection.py` (the `build_*` and `evaluate_response` helpers
  stay put).
- No new unit tests for `TurnRunner` internals — existing end-to-end coverage
  exercises every method.

## Architecture

### `TurnRunner`

A class that owns the mutable per-turn state. State that was local variables
in `run_agent_turn` becomes fields. State written through the existing context
helpers (`ctx.tokens`, `ctx.skills`, etc.) stays on `ctx`.

```python
@dataclass
class TurnRunner:
    ctx: Any
    config: Any
    history: list
    user_message: str
    archive_text: str
    attachments: list[dict] | None

    # Mutable turn state (was local vars in run_agent_turn)
    messages: list = field(default_factory=list)
    deferred_msg: dict | None = None
    prompt_tokens: int = 0
    empty_retries: int = 0
    reflection_retries: int = 0
    last_reflection: "ReflectionResult | None" = None
    turn_start_index: int = 0
    accumulated_text_parts: list[str] = field(default_factory=list)
    model_override: dict[str, str] = field(default_factory=dict)
    retrieved_context_text: str = ""

    # Composition products kept for finally-block diagnostics
    composed: "ComposedContext | None" = None
    composer: "ContextComposer | None" = None

    async def run(self) -> ToolResult: ...
    async def _compose(self) -> None: ...
    async def _run_iteration(self, iteration: int) -> "IterationOutcome": ...
    async def _handle_tool_calls(self, response, tool_calls) -> "IterationOutcome": ...
    async def _handle_no_tool_calls(self, response) -> "IterationOutcome": ...
    async def _handle_reflection(self, content: str) -> "ReflectionOutcome": ...
    async def _finalize_normal_return(self, content: str) -> ToolResult: ...
    async def _finalize_max_iterations(self) -> ToolResult: ...
    async def _write_diagnostics(self) -> None: ...
```

`run_agent_turn` becomes:

```python
async def run_agent_turn(ctx, user_message: str, history: list,
                         archive_text: str = "",
                         attachments: list[dict] | None = None) -> "ToolResult":
    runner = TurnRunner(
        ctx=ctx, config=ctx.config, history=history,
        user_message=user_message, archive_text=archive_text,
        attachments=attachments,
    )
    return await runner.run()
```

The wrapper is intentionally trivial. Reverting the refactor is a single
function-body swap.

### `IterationOutcome`

Tagged union expressing the three exits from one iteration:

```python
class IterationOutcome:
    """Tagged-union return type from _run_iteration."""

@dataclass(frozen=True)
class _Continue(IterationOutcome):
    """Loop again — used for tool-call iterations, retries, widget injection,
    EndTurnConfirm-approved continuation."""

@dataclass(frozen=True)
class _Final(IterationOutcome):
    """Turn is done; return this ToolResult.
    Cancellation collapses into _Final since the outer loop treats it
    identically."""
    result: ToolResult
```

The outer loop:

```python
for iteration in range(self.config.agent.max_tool_iterations):
    outcome = await self._run_iteration(iteration)
    if isinstance(outcome, _Final):
        return outcome.result
    # _Continue → next iteration
return await self._finalize_max_iterations()
```

`_run_iteration` does cancel-check, dynamic-tool refresh, deferred-tool
injection, the LLM call, then dispatches to `_handle_tool_calls` or
`_handle_no_tool_calls`.

### Critical invariant: no fall-through across method boundaries

Today's `EndTurnConfirm` denial path sets `end_turn_signal = True` and falls
through to the `if end_turn_signal:` block in the same iteration. The refactor
preserves this by keeping the denial → final-no-tools-call sequence inside
`_handle_tool_calls`. Method boundaries never split a fall-through.

### Reflection split

`_handle_reflection` becomes three methods on `TurnRunner`:

```python
@dataclass(frozen=True)
class ReflectionOutcome:
    """Result of evaluating a candidate final response."""
    text: str | None        # None when caller should retry
    should_retry: bool      # True → critique was injected, loop again

class TurnRunner:
    async def _handle_reflection(self, content: str) -> ReflectionOutcome:
        """Thin orchestrator. Decides skip vs evaluate vs inject-critique."""
        if not _should_reflect(self.ctx, self.config, content, self.reflection_retries):
            return self._reflection_skip(content)
        result = await self._reflection_evaluate(content)
        return self._reflection_apply_verdict(content, result)

    def _reflection_skip(self, content: str) -> ReflectionOutcome:
        """Exhaustion-escalation suffix logic; clears self.last_reflection."""

    async def _reflection_evaluate(self, content: str) -> ReflectionResult:
        """Build summaries + attachment annotation + accumulated-text concat,
        call evaluate_response, publish reflection_result event, store on
        self.last_reflection."""

    def _reflection_apply_verdict(self, content: str,
                                  result: ReflectionResult) -> ReflectionOutcome:
        """On fail: append failed_msg + critique_msg to history/messages,
        archive both, bump self.reflection_retries, return (None, True).
        On pass / fail-open error: return (content, False)."""
```

The 4-tuple return goes away because state lives on `self`. Only `text` and
`should_retry` are needed at the call site.

## File layout

Everything stays in `agent.py`. The class lives next to `run_agent_turn` and
uses the existing private free-function helpers (`_check_cancelled`,
`_archive`, `_setup_turn_state`, `_refresh_dynamic_tools`, `_build_tool_list`,
`_call_llm_with_events`, `_execute_tool_calls`, `_handle_widget_input_pause`,
`_handle_end_turn_confirm`, `_maybe_compact`) without absorbing them onto the
class — they're called from `TurnRunner` only and a new file would just create
circular-import surface.

Lazy imports in the existing code stay lazy:

- `from .reflection import ...` stays inside `_reflection_evaluate` (avoids
  loading reflection eagerly when `config.reflection.enabled = false`).
- `from .context_composer import write_context_sidecar` stays inside
  `_write_diagnostics`.

The `finally` block (sidecar write + skill-state persistence) moves into
`TurnRunner.run()`'s own `try/finally`. Same fail-open `try/except log.debug`
semantics.

## Public surface

Unchanged. `conversation_manager.py:776`'s `from .agent import run_agent_turn`
and any other callers stay identical.

## Test strategy

This is a no-behavior-change refactor. The bar is "all existing tests pass
unchanged."

- All existing `tests/test_agent*.py` tests must pass without edits. If any
  need editing, that's a red flag we changed behavior. Notable suites:
  - `test_agent.py` — turn-level coverage
  - `test_agent_reflection*.py` — reflection retry, exhaustion, escalation
  - `test_agent_widget_input*.py` — widget pause / resume
  - `test_agent_end_turn_confirm*.py` — approve / deny paths
  - `test_agent_compaction*.py` — `_maybe_compact` integration
- **One new test:** `test_run_agent_turn_public_surface_unchanged` — sanity
  check the wrapper preserves positional/keyword signature and return type.
- **No new tests for `TurnRunner` internals.** The class is an implementation
  detail; existing end-to-end coverage exercises every method indirectly.

## Commits and PR

Single PR, two commits:

1. **`refactor(agent): split _handle_reflection into orchestrator + helpers`**
   — `ReflectionOutcome` dataclass, three helper functions still as
   free functions in this commit (folded onto `TurnRunner` in commit 2).
   Tests pass.
2. **`refactor(agent): introduce TurnRunner for run_agent_turn iteration loop`**
   — `TurnRunner` class, `IterationOutcome` tagged union, fold reflection
   helpers into methods. `run_agent_turn` becomes the thin shim. Tests pass.

Reflection-split first lets a bisect pinpoint which half broke things if a
regression slips in. Single PR because the two are conceptually one refactor
and partial state on `main` would be confusing.

`make test` + `make check` run on each commit, not just the squash, per
existing workflow.

## Live verification

`run_agent_turn` is the hot path for every conversation turn. Per CLAUDE.md,
exercise the following on a branch before merging to `main`:

1. Web UI normal turn — text-only, model selection persists.
2. Web UI tool-using turn — e.g. `tool_search` then act on result; verify
   `text_before_tools` events still flush before tool rows.
3. Web UI widget input — fire a widget tool that pauses; verify pause + resume
   injects the synthetic user message correctly.
4. Web UI `EndTurnConfirm` — both approve and deny paths.
5. Mattermost normal turn — placeholder behavior, attachment upload.
6. Heartbeat / scheduled task — exercises `task_mode` composer branching.
7. Cancellation mid-turn — click stop while a tool runs; verify `[cancelled]`
   archive entry and clean shutdown.
8. Reflection retry — exercise critique injection + escalation suffix on
   exhaustion.
9. Compaction trigger — long conversation that crosses the threshold; verify
   `_maybe_compact` runs after the final response.

Capture results in `notes.md`.

## Risks and mitigations

- **Per-mode regression possible if branching invariants aren't preserved.**
  The fall-through invariant (above) is the highest-risk transformation;
  `EndTurnConfirm` deny is the most likely place to break.
  *Mitigation:* live-verification list explicitly covers approve and deny.
- **Wrapper public surface drift.**
  *Mitigation:* `test_run_agent_turn_public_surface_unchanged`.
- **Hot-path regression in production.**
  *Mitigation:* wrapper-preserving public surface means revert is a single
  function-body swap; two-commit split lets `git bisect` pinpoint
  reflection vs iteration-loop bugs.
