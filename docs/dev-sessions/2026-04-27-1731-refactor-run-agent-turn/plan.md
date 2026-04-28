# Refactor `run_agent_turn` and `_handle_reflection` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run_agent_turn` a thin shim over a `TurnRunner` class, replace the 200-line iteration-loop body with an `IterationOutcome` dispatcher, and split `_handle_reflection`'s 4-tuple into a `ReflectionOutcome` + three small methods. No behavior change.

**Architecture:** Single `agent.py` file. Class-based turn lifecycle. Tagged-union return types replace control-flow magic numbers. Existing free-function helpers (`_check_cancelled`, `_archive`, `_setup_turn_state`, `_refresh_dynamic_tools`, `_build_tool_list`, `_call_llm_with_events`, `_execute_tool_calls`, `_handle_widget_input_pause`, `_handle_end_turn_confirm`, `_maybe_compact`) stay where they are.

**Tech Stack:** Python 3.12, `dataclasses`, `pytest` + `pytest-xdist`, `make` for lint/typecheck/test.

---

## Reference paths

- Code under refactor: `src/decafclaw/agent.py`
  - `_handle_reflection`: lines 195-310
  - `run_agent_turn`: lines 1053-1384
- Tests: `tests/test_agent*.py` (any test file matching this prefix)
- Spec: `docs/dev-sessions/2026-04-27-1731-refactor-run-agent-turn/spec.md`
- Lint+typecheck: `make check`
- Tests: `make test` (parallel via `pytest-xdist`)

## Workflow notes

- This is a **no-behavior-change refactor**. The bar at every step is "all
  existing tests pass." If a test starts failing, the extraction was wrong —
  back out, narrow the change, retry.
- TDD does not apply in the usual "write a failing test first" sense.
  Existing tests are the safety net. The one new test
  (`test_run_agent_turn_public_surface_unchanged`) goes in early in commit 2
  to lock the wrapper contract.
- Run `make check` after each task that touches code (cheap, ~10s).
- Run `make test` only at the end of each commit's tasks (slow, ~21s; saves
  time vs running it after every task).
- Commit only at the end of each commit's task group, per the spec.

---

# Task 0: Branch setup and baseline

**Files:**
- None modified — environment prep only.

- [ ] **Step 0.1: Verify working tree is clean**

Run: `git status`
Expected: clean (or only untracked files unrelated to this work).

If dirty, stash or commit unrelated changes before proceeding.

- [ ] **Step 0.2: Sync with `origin/main`**

Run: `git fetch origin && git log --oneline main..origin/main`
Expected: empty output (local main is up to date).

If remote main has new commits, fast-forward: `git checkout main && git pull --ff-only`.

- [ ] **Step 0.3: Create the work branch**

Run: `git checkout -b refactor/agent-turn-runner`
Expected: branch created, you're now on it.

- [ ] **Step 0.4: Verify the baseline tests pass**

Run: `make check && make test`
Expected: PASS (lint clean, typecheck clean, ~2200 tests passing in ~21s).

If anything fails on the baseline, stop and fix before starting the
refactor — we need a green starting point to attribute regressions.

- [ ] **Step 0.5: Initialize dev session notes**

Create `docs/dev-sessions/2026-04-27-1731-refactor-run-agent-turn/notes.md`:

```markdown
# Refactor `run_agent_turn` and `_handle_reflection` — session notes

## Status

In progress.

## Live verification results

(Filled in after PR opened; track LV1-LV10 from plan.md.)

## Surprises and follow-ups

(Anything that didn't fit the plan, or new issues to file.)
```

---

# Commit 1: Split `_handle_reflection` into orchestrator + free-function helpers

## Task 1: Add `ReflectionOutcome` dataclass

**Files:**
- Modify: `src/decafclaw/agent.py` (add new class near `_handle_reflection`,
  around line 195)

- [ ] **Step 1.1: Add the dataclass**

Insert immediately above `def _should_reflect(...)` at line 180:

```python
@dataclass(frozen=True)
class ReflectionOutcome:
    """Result of evaluating a candidate final response.

    Replaces the 4-tuple return shape of the old _handle_reflection.
    `text` is None when the caller should retry (critique already
    injected into history/messages by the helper). When `should_retry`
    is False, `text` is the response to deliver (with optional
    exhaustion-escalation suffix appended in the skip path).

    `reflection_retries` and `last_reflection` are mutated on the call
    sites' state directly — they don't appear in this return type.
    """
    text: str | None
    should_retry: bool
```

`@dataclass` is already imported (used by `ReflectionResult` etc.). If not, add `from dataclasses import dataclass` at the top of the file.

- [ ] **Step 1.2: Compile-check**

Run: `make check`
Expected: PASS (no callers yet — the class is unused).

## Task 2: Extract `_reflection_skip` free function

**Files:**
- Modify: `src/decafclaw/agent.py` (add new free function above
  `_handle_reflection`)

- [ ] **Step 2.1: Add the function**

Insert above `_handle_reflection` (line 195):

```python
def _reflection_skip(
    ctx, config, final_text: str,
    reflection_retries: int,
    last_reflection: "ReflectionResult | None",
) -> tuple[str, "ReflectionResult | None"]:
    """The 'reflection did not run' branch. Returns (final_text_maybe_with_escalation,
    cleared_last_reflection).

    Mirrors the early-return block at agent.py:215-229. Appends the
    'I'm not confident' suffix when retries are exhausted; clears
    last_reflection so it won't get archived against this new response.
    """
    reflection_exhausted = (
        reflection_retries >= config.reflection.max_retries
        and last_reflection is not None
        and not last_reflection.passed
    )
    if reflection_exhausted:
        final_text += (
            "\n\n---\n*I'm not confident in this answer. "
            "Try switching to a more capable model in the web UI model picker.*"
        )
    return final_text, None
```

- [ ] **Step 2.2: Compile-check**

Run: `make check`
Expected: PASS.

## Task 3: Extract `_reflection_evaluate` free function

**Files:**
- Modify: `src/decafclaw/agent.py`

- [ ] **Step 3.1: Add the function**

Insert below `_reflection_skip`:

```python
async def _reflection_evaluate(
    ctx, config, history: list,
    final_text: str, user_message: str,
    attachments: list[dict] | None,
    retrieved_context_text: str,
    turn_start_index: int,
    reflection_retries: int,
    accumulated_text_parts: list[str] | None,
) -> "ReflectionResult":
    """Build summaries, annotate user message, call evaluate_response,
    publish reflection_result event.

    Mirrors agent.py:231-279 in the original _handle_reflection.
    Returns the ReflectionResult; caller stores it on its own state
    and decides what to do with the verdict.
    """
    from .reflection import (
        build_prior_turn_summary,
        build_tool_summary,
        evaluate_response,
    )

    tool_summary = build_tool_summary(
        history, turn_start_index,
        max_result_len=config.reflection.max_tool_result_len,
    )
    prior_turn_summary = build_prior_turn_summary(
        history, turn_start_index - 1,
        max_turns=3,
        max_result_len=200,
    )
    judge_user_message = user_message
    if attachments:
        att_desc = ", ".join(
            f"{a.get('filename', '?')} ({a.get('mime_type', '?')})"
            for a in attachments
        )
        judge_user_message += f"\n\n[User attached files: {att_desc}]"
    judge_agent_response = "\n\n".join(
        part for part in [*(accumulated_text_parts or []), final_text]
        if part and part.strip()
    ) or final_text
    result = await evaluate_response(
        config, judge_user_message, judge_agent_response, tool_summary,
        prior_turn_summary=prior_turn_summary,
        retrieved_context=retrieved_context_text,
    )

    log.info("Reflection result: passed=%s, critique=%s, error=%s",
             result.passed, result.critique[:200] if result.critique else "",
             result.error[:100] if result.error else "")
    await ctx.publish("reflection_result",
        passed=result.passed,
        critique=result.critique,
        raw_response=result.raw_response,
        retry_number=reflection_retries + 1,
        error=result.error)
    return result
```

- [ ] **Step 3.2: Compile-check**

Run: `make check`
Expected: PASS.

## Task 4: Extract `_reflection_apply_verdict` free function

**Files:**
- Modify: `src/decafclaw/agent.py`

- [ ] **Step 4.1: Add the function**

Insert below `_reflection_evaluate`:

```python
def _reflection_apply_verdict(
    ctx, history: list, messages: list,
    final_text: str, result: "ReflectionResult",
    reflection_retries: int,
    config,
) -> tuple[ReflectionOutcome, int]:
    """Apply the judge's verdict. On fail-with-real-critique, append
    failed_msg + critique_msg to history/messages, archive both, and
    bump retries. Returns (outcome, new_retries).

    Mirrors agent.py:281-310 in the original _handle_reflection. The
    fail-open path (result.error set) is treated as PASS so the user
    still gets a response when the judge LLM itself fails.
    """
    if not result.passed and not result.error:
        log.info("Reflection failed (retry %d/%d): %s",
                 reflection_retries + 1,
                 config.reflection.max_retries,
                 result.critique[:200])
        failed_msg = {"role": "assistant", "content": final_text}
        history.append(failed_msg)
        messages.append(failed_msg)
        _archive(ctx, failed_msg)

        critique_msg = {
            "role": "user",
            "content": (
                "[reflection] Your previous response may not fully "
                "address the user's request.\n"
                f"Feedback: {result.critique}\n"
                "Please try again, addressing the feedback above."
            ),
        }
        history.append(critique_msg)
        messages.append(critique_msg)
        _archive(ctx, critique_msg)

        return ReflectionOutcome(text=None, should_retry=True), reflection_retries + 1

    return ReflectionOutcome(text=final_text, should_retry=False), reflection_retries
```

- [ ] **Step 4.2: Compile-check**

Run: `make check`
Expected: PASS.

## Task 5: Rewrite `_handle_reflection` as orchestrator + update call site

**Files:**
- Modify: `src/decafclaw/agent.py:195-310` (replace the whole function body)
- Modify: `src/decafclaw/agent.py:1308-1315` (the single call site in
  `run_agent_turn`)

- [ ] **Step 5.1: Replace `_handle_reflection`**

Replace the existing function body (lines 195-310) with this thin orchestrator:

```python
async def _handle_reflection(
    ctx, config, messages, history, final_text,
    user_message, attachments, retrieved_context_text,
    turn_start_index, reflection_retries, last_reflection,
    accumulated_text_parts=None,
) -> tuple[ReflectionOutcome, int, "ReflectionResult | None"]:
    """Run the reflection phase on a candidate final response.

    Returns (outcome, new_reflection_retries, new_last_reflection):
    - Skipped: outcome wraps possibly-suffixed text; last_reflection cleared
    - Evaluated and passed: outcome wraps text; last_reflection set to result
    - Evaluated and failed (retries left): outcome.should_retry=True;
      critique already injected into messages/history
    - Evaluated and failed (no retries left, fail-open error): treated as pass

    `accumulated_text_parts` collects text from earlier iterations of
    this turn (skill-style "report + tool call" patterns) so the judge
    sees the full visible response, not just the trailer.
    """
    if not _should_reflect(ctx, config, final_text, reflection_retries):
        text, cleared = _reflection_skip(
            ctx, config, final_text, reflection_retries, last_reflection,
        )
        return ReflectionOutcome(text=text, should_retry=False), reflection_retries, cleared

    result = await _reflection_evaluate(
        ctx, config, history, final_text, user_message, attachments,
        retrieved_context_text, turn_start_index, reflection_retries,
        accumulated_text_parts,
    )
    last_reflection = result

    outcome, new_retries = _reflection_apply_verdict(
        ctx, history, messages, final_text, result, reflection_retries, config,
    )
    return outcome, new_retries, last_reflection
```

- [ ] **Step 5.2: Update the single call site in `run_agent_turn`**

Find the call at agent.py:1308-1315:

```python
content, should_retry, reflection_retries, last_reflection = (
    await _handle_reflection(
        ctx, config, messages, history, content,
        user_message, attachments, retrieved_context_text,
        turn_start_index, reflection_retries, last_reflection,
        accumulated_text_parts=accumulated_text_parts,
    )
)
if should_retry:
    continue
```

Replace with:

```python
outcome, reflection_retries, last_reflection = await _handle_reflection(
    ctx, config, messages, history, content,
    user_message, attachments, retrieved_context_text,
    turn_start_index, reflection_retries, last_reflection,
    accumulated_text_parts=accumulated_text_parts,
)
if outcome.should_retry:
    continue
content = outcome.text
```

- [ ] **Step 5.3: Compile-check**

Run: `make check`
Expected: PASS.

## Task 6: Verify and commit commit 1

- [ ] **Step 6.1: Run the full test suite**

Run: `make test`
Expected: PASS (2200+ tests, ~21s).

If anything fails, the reflection split changed behavior — investigate the
failing test (likely `tests/test_agent_reflection*.py`), revert the relevant
extraction, narrow the change.

- [ ] **Step 6.2: Commit**

```bash
git add src/decafclaw/agent.py
git commit -m "$(cat <<'EOF'
refactor(agent): split _handle_reflection into orchestrator + helpers

Replace the 117-line _handle_reflection 4-tuple return with a
ReflectionOutcome dataclass + three free-function helpers
(_reflection_skip, _reflection_evaluate, _reflection_apply_verdict).
The orchestrator is now ~25 lines; each helper has a single
responsibility.

No behavior change. Existing tests pass unchanged.

Refs #382.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Commit 2: Introduce `TurnRunner` and `IterationOutcome`; fold reflection helpers onto class

## Task 7: Add `IterationOutcome` types

**Files:**
- Modify: `src/decafclaw/agent.py` (add near `_handle_reflection` / above
  `run_agent_turn`)

- [ ] **Step 7.1: Add the types**

Insert immediately above `async def run_agent_turn(...)` (line 1053):

```python
class IterationOutcome:
    """Tagged-union return type from TurnRunner._run_iteration.

    Two variants: _Continue (loop again) and _Final (return this
    ToolResult from the turn). Cancellation collapses into _Final
    since the outer loop treats it identically.
    """


@dataclass(frozen=True)
class _Continue(IterationOutcome):
    """Loop again — used for tool-call iterations, retries, widget
    injection, EndTurnConfirm-approved continuation."""


@dataclass(frozen=True)
class _Final(IterationOutcome):
    """Turn is done; return this ToolResult."""
    result: "ToolResult"
```

- [ ] **Step 7.2: Compile-check**

Run: `make check`
Expected: PASS.

## Task 8: Add the wrapper-contract test (locks the public surface)

**Files:**
- Create or modify: `tests/test_agent.py` (add at end)

- [ ] **Step 8.1: Find or pick a test file**

Run: `ls tests/test_agent*.py`

Add the test to `tests/test_agent.py` if it exists, or create
`tests/test_agent_public_surface.py` if it doesn't.

- [ ] **Step 8.2: Add the test**

```python
import inspect
from decafclaw.agent import run_agent_turn


def test_run_agent_turn_public_surface_unchanged():
    """Lock the public signature so future refactors don't drift the
    contract callers depend on (conversation_manager.py, eval/runner,
    etc.)."""
    sig = inspect.signature(run_agent_turn)
    params = sig.parameters

    assert list(params.keys()) == [
        "ctx", "user_message", "history", "archive_text", "attachments",
    ], "Positional/keyword arg order changed"
    assert params["archive_text"].default == "", \
        "archive_text default changed"
    assert params["attachments"].default is None, \
        "attachments default changed"
    assert inspect.iscoroutinefunction(run_agent_turn), \
        "run_agent_turn must remain async"
```

- [ ] **Step 8.3: Run the test**

Run: `pytest tests/test_agent.py::test_run_agent_turn_public_surface_unchanged -v`
(Or the equivalent path if you created a new file.)

Expected: PASS.

## Task 9: Add `TurnRunner` skeleton; move `run_agent_turn` body verbatim

**Files:**
- Modify: `src/decafclaw/agent.py:1053-1384` (entire `run_agent_turn` body
  moves to `TurnRunner.run`)

- [ ] **Step 9.1: Insert `TurnRunner` class above `run_agent_turn`**

Insert this skeleton above `async def run_agent_turn(...)`:

```python
@dataclass
class TurnRunner:
    """Owns the mutable state of a single agent turn.

    State that was local variables in the original run_agent_turn
    becomes fields here. State written through ctx helpers
    (ctx.tokens, ctx.skills, ctx.history) stays on ctx.
    """
    ctx: Any
    config: Any
    history: list
    user_message: str
    archive_text: str
    attachments: list[dict] | None

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
    composed: "ComposedContext | None" = None
    composer: "ContextComposer | None" = None

    async def run(self) -> "ToolResult":
        # Body filled in step 9.2.
        raise NotImplementedError
```

**Imports check** — before adding the class, verify `agent.py`'s top-of-file imports include:

```python
from dataclasses import dataclass, field
from typing import Any
```

Run: `grep -n "from dataclasses\|from typing" src/decafclaw/agent.py | head -5`

Add any missing imports.

- [ ] **Step 9.2: Move the entire body of `run_agent_turn` into `TurnRunner.run()`**

This is a mechanical translation: every `local_var` in `run_agent_turn`
becomes `self.local_var`; every reference to function-arg `ctx`, `config`,
`history`, `user_message`, `archive_text`, `attachments` becomes
`self.ctx`, `self.config`, etc.

Some inline detail to watch for:

- `model_override = await _setup_turn_state(...)` → `self.model_override = await _setup_turn_state(self.ctx, self.config, self.history)`
- `composed = None; composer = None` → already fields, just assign `self.composed = None; self.composer = None` at top of method
- `messages = composed.messages; ctx.messages = messages` →
  `self.messages = self.composed.messages; self.ctx.messages = self.messages`
- `retrieved_context_text = composed.retrieved_context_text` →
  `self.retrieved_context_text = self.composed.retrieved_context_text`
- `prompt_tokens = 0; empty_retries = 0; ...` → already fields, just
  initialize via `__post_init__` or skip (defaults already cover them).
- `for iteration in range(config.agent.max_tool_iterations)` →
  `for iteration in range(self.config.agent.max_tool_iterations)`
- All inner `ctx`/`config`/`history`/`messages` refs → `self.ctx`/`self.config`/etc.
- The `**model_override` kwargs spread → `**self.model_override`
- `accumulated_text_parts.append(...)` → `self.accumulated_text_parts.append(...)`
- The reflection call site (updated in commit 1) becomes:
  ```python
  outcome, self.reflection_retries, self.last_reflection = await _handle_reflection(
      self.ctx, self.config, self.messages, self.history, content,
      self.user_message, self.attachments, self.retrieved_context_text,
      self.turn_start_index, self.reflection_retries, self.last_reflection,
      accumulated_text_parts=self.accumulated_text_parts,
  )
  if outcome.should_retry:
      continue
  content = outcome.text
  ```
- The `finally` block keeps the same `try` boundary — the whole `try: ...
  finally: ...` structure moves intact.

The method body should otherwise be a line-for-line copy.

- [ ] **Step 9.3: Replace `run_agent_turn` body with the wrapper**

After the `TurnRunner` class, leave `run_agent_turn` defined as:

```python
async def run_agent_turn(ctx, user_message: str, history: list,
                         archive_text: str = "",
                         attachments: list[dict] | None = None) -> "ToolResult":
    """Process a single user message through the agent loop.

    Public entry point. Constructs a TurnRunner and runs it.
    """
    runner = TurnRunner(
        ctx=ctx, config=ctx.config, history=history,
        user_message=user_message, archive_text=archive_text,
        attachments=attachments,
    )
    return await runner.run()
```

- [ ] **Step 9.4: Compile-check**

Run: `make check`
Expected: PASS.

- [ ] **Step 9.5: Run the full test suite**

Run: `make test`
Expected: PASS (no behavior change yet — just moved code).

If a test fails: most likely a `self.` rename was missed, or a closure
captured `ctx` instead of `self.ctx`. `git diff` and look for any
non-`self.` reference to a local that should be on the runner.

## Task 10: Extract `_compose` method

**Files:**
- Modify: `src/decafclaw/agent.py` (`TurnRunner` class)

- [ ] **Step 10.1: Add `_compose` method**

The compose phase covers everything from the start of `try:` through the
`# Track the deferred tools system message...` block (lines ~1079-1124 in
the original). Cut that block out of `run()` and paste into a new method:

```python
async def _compose(self) -> None:
    """Build the composed context, archive composer-added messages,
    initialize message-tracking state on self."""
    if self.ctx.is_child:
        composer_mode = ComposerMode.CHILD_AGENT
    elif self.ctx.task_mode in _TASK_MODE_TO_COMPOSER:
        composer_mode = _TASK_MODE_TO_COMPOSER[self.ctx.task_mode]
    else:
        composer_mode = ComposerMode.INTERACTIVE

    self.composer = ContextComposer(state=self.ctx.composer)
    self.composed = await self.composer.compose(
        self.ctx, self.user_message, self.history,
        mode=composer_mode, attachments=self.attachments,
    )
    self.messages = self.composed.messages
    self.ctx.messages = self.messages
    self.retrieved_context_text = self.composed.retrieved_context_text

    for msg in self.composed.messages_to_archive:
        if self.ctx.task_mode == "background_wake" and msg.get("role") == "user":
            archive_msg: dict = {
                "role": "wake_trigger",
                "content": msg.get("content", ""),
            }
            _archive(self.ctx, archive_msg)
        elif self.archive_text and msg.get("role") == "user":
            archive_msg = {"role": "user", "content": self.archive_text}
            if msg.get("attachments"):
                archive_msg["attachments"] = msg["attachments"]
            _archive(self.ctx, archive_msg)
        else:
            _archive(self.ctx, msg)

    if (len(self.messages) > 1
            and self.messages[1].get("role") == "system"
            and self.composed.deferred_tools):
        self.deferred_msg = self.messages[1]
    else:
        self.deferred_msg = None

    self.turn_start_index = len(self.history)
```

In `run()`, replace the moved block with `await self._compose()` immediately
after `self.model_override = await _setup_turn_state(...)`.

- [ ] **Step 10.2: Run tests**

Run: `make test`
Expected: PASS.

## Task 11: Extract `_write_diagnostics` method

**Files:**
- Modify: `src/decafclaw/agent.py`

- [ ] **Step 11.1: Add `_write_diagnostics` method**

The `finally` block (lines ~1367-1384 in the original) becomes:

```python
async def _write_diagnostics(self) -> None:
    """Persist context diagnostics + skill state on any turn-exit path.

    Fail-open: any failure logs at DEBUG and is swallowed so the
    finally block never raises through to callers.
    """
    conv_id = self.ctx.conv_id or self.ctx.channel_id

    if self.composed is not None and self.composer is not None and conv_id:
        try:
            from .context_composer import write_context_sidecar
            diagnostics = self.composer.build_diagnostics(self.config, self.composed)
            write_context_sidecar(self.config, conv_id, diagnostics)
        except Exception as exc:
            log.debug("context sidecar write failed for %s: %s", conv_id, exc)

    if conv_id:
        activated = self.ctx.skills.activated
        if activated:
            write_skills_state(self.config, conv_id, activated)
        skill_data = self.ctx.skills.data
        if skill_data:
            write_skill_data(self.config, conv_id, skill_data)
```

In `run()`, replace the entire `finally:` body with `await self._write_diagnostics()`.

- [ ] **Step 11.2: Run tests**

Run: `make test`
Expected: PASS.

## Task 12: Extract `_handle_tool_calls` method

**Files:**
- Modify: `src/decafclaw/agent.py`

- [ ] **Step 12.1: Add `_handle_tool_calls` method**

This handles the entire `if tool_calls:` branch in the iteration body
(lines ~1174-1291 in the original). Cut that block from `run()` and paste
as:

```python
async def _handle_tool_calls(
    self, response: dict, tool_calls: list,
) -> IterationOutcome:
    """Append assistant tool-call message, execute tools, dispatch
    on end-turn signals (widget pause, EndTurnConfirm, end_turn=True).

    Returns _Continue to loop again, or _Final(result) to end the turn.
    """
    iter_content = response.get("content")
    assistant_msg = {"role": "assistant", "content": iter_content}
    assistant_msg["tool_calls"] = tool_calls
    self.history.append(assistant_msg)
    self.messages.append(assistant_msg)
    _archive(self.ctx, assistant_msg)

    if iter_content:
        self.accumulated_text_parts.append(iter_content)
        await self.ctx.publish("text_before_tools", text=iter_content)

    cancelled, end_turn_signal = await _execute_tool_calls(
        self.ctx, tool_calls, self.history, self.messages,
    )
    if cancelled:
        return _Final(result=cancelled)

    if isinstance(end_turn_signal, WidgetInputPause):
        inject_content = await _handle_widget_input_pause(
            self.ctx, end_turn_signal,
        )
        if inject_content is None:
            end_turn_signal = True
        else:
            synthetic = {
                "role": "user",
                "source": "widget_response",
                "content": inject_content,
            }
            self.history.append(synthetic)
            self.messages.append(synthetic)
            _archive(self.ctx, synthetic)
            return _Continue()

    if isinstance(end_turn_signal, EndTurnConfirm):
        log.info("EndTurnConfirm — making presentation LLM call before confirmation")
        present_response = await _call_llm_with_events(
            self.ctx, self.config, self.messages, [],
            **self.model_override,
        )
        present_content = present_response.get("content") or ""
        present_msg = {"role": "assistant", "content": present_content}
        self.history.append(present_msg)
        self.messages.append(present_msg)
        _archive(self.ctx, present_msg)

        log.info("EndTurnConfirm — requesting confirmation")
        approved = await _handle_end_turn_confirm(self.ctx, end_turn_signal)
        if approved:
            log.info("EndTurnConfirm approved — continuing agent loop")
            if end_turn_signal.on_approve:
                if asyncio.iscoroutinefunction(end_turn_signal.on_approve):
                    await end_turn_signal.on_approve()
                else:
                    end_turn_signal.on_approve()
            note = f"[User approved: {end_turn_signal.message or 'review'}]"
            self.history.append({"role": "user", "content": note})
            self.messages.append({"role": "user", "content": note})
            return _Continue()
        else:
            log.info("EndTurnConfirm denied — ending turn")
            if end_turn_signal.on_deny:
                if asyncio.iscoroutinefunction(end_turn_signal.on_deny):
                    await end_turn_signal.on_deny()
                else:
                    end_turn_signal.on_deny()
            deny_label = end_turn_signal.deny_label or "denied"
            note = f"[User selected '{deny_label}'. Ask what they'd like changed.]"
            self.history.append({"role": "user", "content": note})
            self.messages.append({"role": "user", "content": note})
            end_turn_signal = True

    if end_turn_signal:
        log.info("Tool signalled end_turn — making final no-tools LLM call")
        final_response = await _call_llm_with_events(
            self.ctx, self.config, self.messages, [],
            **self.model_override,
        )
        content = final_response.get("content") or ""
        final_msg = {"role": "assistant", "content": content}
        self.history.append(final_msg)
        _archive(self.ctx, final_msg)

        return _Final(result=self._extract_workspace_media(content))

    return _Continue()
```

This method also references a small helper `_extract_workspace_media`.
Add it as a method on `TurnRunner`:

```python
def _extract_workspace_media(self, content: str) -> "ToolResult":
    """Extract workspace:// refs only for channels that need it.

    Mattermost strips refs and uploads files; web/terminal render them
    in-place. Returns ToolResult with media when extraction applies,
    otherwise just text.
    """
    handler = self.ctx.media_handler
    should_extract = (handler is None or handler.strips_workspace_refs)
    if should_extract:
        cleaned_text, workspace_media = extract_workspace_media(
            content or "", self.config.workspace_path,
        )
        if workspace_media:
            return ToolResult(text=cleaned_text, media=workspace_media)
    return ToolResult(text=content or "")
```

In `run()`, the `if tool_calls:` block is replaced with:

```python
if tool_calls:
    outcome = await self._handle_tool_calls(response, tool_calls)
    if isinstance(outcome, _Final):
        return outcome.result
    continue
```

- [ ] **Step 12.2: Run tests**

Run: `make test`
Expected: PASS.

This is the highest-risk extraction. If any of `tests/test_agent_widget_input*.py`,
`tests/test_agent_end_turn_confirm*.py`, or `tests/test_agent.py` start
failing, suspect: a dropped `self.`, or fall-through ordering changed
(approved → `continue` vs denied → fall through to `if end_turn_signal:`).

## Task 13: Extract `_handle_no_tool_calls` method

**Files:**
- Modify: `src/decafclaw/agent.py`

- [ ] **Step 13.1: Add `_handle_no_tool_calls` method**

This handles the `# No tool calls — final response` block in the iteration
body (lines ~1294-1354 in the original). Cut that block from `run()` and
paste as:

```python
async def _handle_no_tool_calls(self, response: dict) -> IterationOutcome:
    """Process a no-tool-calls LLM response. Handles empty-retry,
    reflection, archive of last_reflection, and compaction trigger.
    Returns _Continue (retry) or _Final(result) (deliver response)."""
    content = response.get("content") or ""
    if not content:
        if self.empty_retries < 1:
            self.empty_retries += 1
            log.warning("LLM returned empty response, retrying")
            return _Continue()
        log.warning("LLM returned empty content with no tool calls (after retry)")

    log.debug("Reflection check: enabled=%s, retries=%d/%d, skip=%s, has_content=%s",
               self.config.reflection.enabled, self.reflection_retries,
               self.config.reflection.max_retries, self.ctx.skip_reflection, bool(content))
    outcome, self.reflection_retries, self.last_reflection = await _handle_reflection(
        self.ctx, self.config, self.messages, self.history, content,
        self.user_message, self.attachments, self.retrieved_context_text,
        self.turn_start_index, self.reflection_retries, self.last_reflection,
        accumulated_text_parts=self.accumulated_text_parts,
    )
    if outcome.should_retry:
        return _Continue()
    content = outcome.text

    final_msg = {"role": "assistant", "content": content}
    self.history.append(final_msg)
    _archive(self.ctx, final_msg)

    if self.last_reflection is not None:
        visibility = self.config.reflection.visibility
        r = self.last_reflection
        should_archive = (
            visibility == "debug"
            or (visibility == "visible" and not r.passed)
        )
        if should_archive:
            detail = r.raw_response or r.critique or (
                "Response passed evaluation" if r.passed else "No details")
            label = ("reflection: PASS" if r.passed
                     else f"reflection: retry {self.reflection_retries}")
            _archive(self.ctx, {"role": "reflection", "tool": label,
                                "content": detail})

    await _maybe_compact(self.ctx, self.config, self.history, self.prompt_tokens)
    return _Final(result=self._extract_workspace_media(content))
```

In `run()`, the `# No tool calls — final response` block becomes:

```python
outcome = await self._handle_no_tool_calls(response)
if isinstance(outcome, _Final):
    return outcome.result
continue
```

- [ ] **Step 13.2: Run tests**

Run: `make test`
Expected: PASS.

If `tests/test_agent_reflection*.py` fails, suspect: `self.last_reflection`
not preserved across the `_handle_reflection` call (the orchestrator returns
the new value; we must reassign).

## Task 14: Extract `_finalize_max_iterations` method

**Files:**
- Modify: `src/decafclaw/agent.py`

- [ ] **Step 14.1: Add `_finalize_max_iterations` method**

The post-loop block (lines ~1357-1365 in the original) becomes:

```python
async def _finalize_max_iterations(self) -> "ToolResult":
    """Hit max iterations without a final response. Preserve any
    accumulated text from tool-call iterations and append a notice."""
    limit_note = (
        f"\n\n[Agent reached max tool iterations "
        f"({self.config.agent.max_tool_iterations}) without a final response]"
    )
    accumulated = "\n\n".join(self.accumulated_text_parts)
    msg = accumulated + limit_note if accumulated else limit_note.strip()
    final_msg = {"role": "assistant", "content": msg}
    self.history.append(final_msg)
    _archive(self.ctx, final_msg)
    await _maybe_compact(
        self.ctx, self.config, self.history, self.prompt_tokens,
    )
    return ToolResult(text=msg)
```

In `run()`, the post-loop block becomes:

```python
return await self._finalize_max_iterations()
```

- [ ] **Step 14.2: Run tests**

Run: `make test`
Expected: PASS.

## Task 15: Extract `_run_iteration` dispatcher

**Files:**
- Modify: `src/decafclaw/agent.py`

- [ ] **Step 15.1: Add `_run_iteration` method**

After the previous extractions, the `for iteration in range(...)` body in
`run()` should look approximately like:

```python
for iteration in range(self.config.agent.max_tool_iterations):
    cancelled = _check_cancelled(self.ctx, self.history)
    if cancelled:
        return cancelled

    log.debug(f"Agent iteration {iteration + 1}")
    self.ctx._current_iteration = iteration + 1

    _refresh_dynamic_tools(self.ctx)
    all_tools, deferred_text = _build_tool_list(self.ctx)

    # Inject/update deferred tool list...  (about 12 lines)

    response = await _call_llm_with_events(...)

    # Token usage tracking...

    tool_calls = response.get("tool_calls")
    if tool_calls:
        outcome = await self._handle_tool_calls(response, tool_calls)
        if isinstance(outcome, _Final):
            return outcome.result
        continue

    outcome = await self._handle_no_tool_calls(response)
    if isinstance(outcome, _Final):
        return outcome.result
    continue
```

Extract everything inside the `for` body into:

```python
async def _run_iteration(self, iteration: int) -> IterationOutcome:
    """Run one LLM iteration: cancel-check, tool refresh, deferred-list
    injection, the LLM call, and dispatch to tool-calls or no-tool-calls
    handler. Returns _Continue or _Final."""
    cancelled = _check_cancelled(self.ctx, self.history)
    if cancelled:
        return _Final(result=cancelled)

    log.debug(f"Agent iteration {iteration + 1}")
    self.ctx._current_iteration = iteration + 1

    _refresh_dynamic_tools(self.ctx)
    all_tools, deferred_text = _build_tool_list(self.ctx)

    if deferred_text:
        new_msg = {"role": "system", "content": deferred_text}
        if self.deferred_msg is not None and self.deferred_msg in self.messages:
            idx = self.messages.index(self.deferred_msg)
            self.messages[idx] = new_msg
        else:
            self.messages.insert(1, new_msg)
        self.deferred_msg = new_msg
    elif self.deferred_msg is not None and self.deferred_msg in self.messages:
        self.messages.remove(self.deferred_msg)
        self.deferred_msg = None

    response = await _call_llm_with_events(
        self.ctx, self.config, self.messages, all_tools,
        **self.model_override,
    )

    usage = response.get("usage")
    if usage:
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        self.ctx.tokens.total_prompt += prompt_tokens
        self.ctx.tokens.total_completion += completion_tokens
        self.ctx.tokens.last_prompt = prompt_tokens
        self.prompt_tokens = prompt_tokens
        self.composer.record_actuals(prompt_tokens, completion_tokens)

    tool_calls = response.get("tool_calls")
    if tool_calls:
        return await self._handle_tool_calls(response, tool_calls)

    return await self._handle_no_tool_calls(response)
```

The `for` body in `run()` becomes:

```python
for iteration in range(self.config.agent.max_tool_iterations):
    outcome = await self._run_iteration(iteration)
    if isinstance(outcome, _Final):
        return outcome.result
return await self._finalize_max_iterations()
```

- [ ] **Step 15.2: Run tests**

Run: `make test`
Expected: PASS.

## Task 16: Move reflection helpers onto `TurnRunner`

**Files:**
- Modify: `src/decafclaw/agent.py`

- [ ] **Step 16.1: Convert `_handle_reflection`, `_reflection_skip`,
  `_reflection_evaluate`, `_reflection_apply_verdict` to `TurnRunner` methods**

These were free functions in commit 1. Move them onto `TurnRunner`:

```python
class TurnRunner:
    # ... existing methods ...

    async def _handle_reflection(self, content: str) -> ReflectionOutcome:
        """Thin orchestrator. Mutates self.reflection_retries and
        self.last_reflection; returns ReflectionOutcome."""
        if not _should_reflect(
            self.ctx, self.config, content, self.reflection_retries,
        ):
            return self._reflection_skip(content)
        result = await self._reflection_evaluate(content)
        self.last_reflection = result
        return self._reflection_apply_verdict(content, result)

    def _reflection_skip(self, content: str) -> ReflectionOutcome:
        """Reflection-did-not-run branch. Appends escalation suffix
        on exhaustion; clears self.last_reflection."""
        reflection_exhausted = (
            self.reflection_retries >= self.config.reflection.max_retries
            and self.last_reflection is not None
            and not self.last_reflection.passed
        )
        self.last_reflection = None
        if reflection_exhausted:
            content += (
                "\n\n---\n*I'm not confident in this answer. "
                "Try switching to a more capable model in the web UI model picker.*"
            )
        return ReflectionOutcome(text=content, should_retry=False)

    async def _reflection_evaluate(self, content: str) -> "ReflectionResult":
        """Build summaries + attachment annotation + accumulated-text
        concat, call evaluate_response, publish reflection_result event."""
        from .reflection import (
            build_prior_turn_summary,
            build_tool_summary,
            evaluate_response,
        )

        tool_summary = build_tool_summary(
            self.history, self.turn_start_index,
            max_result_len=self.config.reflection.max_tool_result_len,
        )
        prior_turn_summary = build_prior_turn_summary(
            self.history, self.turn_start_index - 1,
            max_turns=3,
            max_result_len=200,
        )
        judge_user_message = self.user_message
        if self.attachments:
            att_desc = ", ".join(
                f"{a.get('filename', '?')} ({a.get('mime_type', '?')})"
                for a in self.attachments
            )
            judge_user_message += f"\n\n[User attached files: {att_desc}]"
        judge_agent_response = "\n\n".join(
            part for part in [*self.accumulated_text_parts, content]
            if part and part.strip()
        ) or content
        result = await evaluate_response(
            self.config, judge_user_message, judge_agent_response, tool_summary,
            prior_turn_summary=prior_turn_summary,
            retrieved_context=self.retrieved_context_text,
        )

        log.info("Reflection result: passed=%s, critique=%s, error=%s",
                 result.passed, result.critique[:200] if result.critique else "",
                 result.error[:100] if result.error else "")
        await self.ctx.publish("reflection_result",
            passed=result.passed,
            critique=result.critique,
            raw_response=result.raw_response,
            retry_number=self.reflection_retries + 1,
            error=result.error)
        return result

    def _reflection_apply_verdict(
        self, content: str, result: "ReflectionResult",
    ) -> ReflectionOutcome:
        """Apply the judge's verdict. On fail-with-real-critique,
        append failed_msg + critique_msg, archive, bump retries.
        Fail-open errors treated as PASS."""
        if not result.passed and not result.error:
            log.info("Reflection failed (retry %d/%d): %s",
                     self.reflection_retries + 1,
                     self.config.reflection.max_retries,
                     result.critique[:200])
            failed_msg = {"role": "assistant", "content": content}
            self.history.append(failed_msg)
            self.messages.append(failed_msg)
            _archive(self.ctx, failed_msg)

            critique_msg = {
                "role": "user",
                "content": (
                    "[reflection] Your previous response may not fully "
                    "address the user's request.\n"
                    f"Feedback: {result.critique}\n"
                    "Please try again, addressing the feedback above."
                ),
            }
            self.history.append(critique_msg)
            self.messages.append(critique_msg)
            _archive(self.ctx, critique_msg)

            self.reflection_retries += 1
            return ReflectionOutcome(text=None, should_retry=True)

        return ReflectionOutcome(text=content, should_retry=False)
```

- [ ] **Step 16.2: Update the call site in `_handle_no_tool_calls`**

Replace the existing call to the free-function `_handle_reflection`:

```python
# OLD
outcome, self.reflection_retries, self.last_reflection = await _handle_reflection(
    self.ctx, self.config, self.messages, self.history, content,
    self.user_message, self.attachments, self.retrieved_context_text,
    self.turn_start_index, self.reflection_retries, self.last_reflection,
    accumulated_text_parts=self.accumulated_text_parts,
)
```

```python
# NEW
outcome = await self._handle_reflection(content)
```

- [ ] **Step 16.3: Delete the free-function copies**

Delete the four free functions added in commit 1: `_handle_reflection`,
`_reflection_skip`, `_reflection_evaluate`, `_reflection_apply_verdict`.

`_should_reflect` stays as a free function — it's pure and stateless.

- [ ] **Step 16.4: Compile-check**

Run: `make check`
Expected: PASS.

- [ ] **Step 16.5: Run the full test suite**

Run: `make test`
Expected: PASS.

If reflection tests fail, suspect: `self.last_reflection` reassignment
order in `_handle_reflection` — `self.last_reflection = result` must run
*before* `_reflection_apply_verdict` (which doesn't read it but relies
on it being the canonical store for archiving downstream).

## Task 17: Final verification and commit commit 2

- [ ] **Step 17.1: Run lint, typecheck, and full test suite**

Run: `make check && make test`
Expected: PASS.

- [ ] **Step 17.2: Sanity-check `run_agent_turn` is now a thin shim**

Open `src/decafclaw/agent.py`, find `async def run_agent_turn(`. The body
should be ≤ 10 lines (just the `TurnRunner(...)` construction + `return
await runner.run()`).

If it's longer, something didn't get extracted into `TurnRunner` — go
back and finish the extraction.

- [ ] **Step 17.3: Sanity-check the iteration loop is now a thin dispatcher**

Find `TurnRunner.run()`. The `for iteration in range(...)` block should
be ≤ 4 lines (call `_run_iteration`, branch on `_Final`, fall through
on `_Continue`).

- [ ] **Step 17.4: Commit**

```bash
git add src/decafclaw/agent.py tests/test_agent.py
git commit -m "$(cat <<'EOF'
refactor(agent): introduce TurnRunner for run_agent_turn iteration loop

Replace the 314-line run_agent_turn function with a thin wrapper over
a TurnRunner class that owns the per-turn mutable state. The 200-line
iteration-loop body becomes a top-level loop calling _run_iteration,
which dispatches on a tagged-union IterationOutcome. EndTurnConfirm
fall-through stays inside _handle_tool_calls so method boundaries
never split a fall-through.

Folds the reflection helpers from commit 1 onto TurnRunner methods
since they all mutate turn state (history, messages, retries,
last_reflection). The free-function copies are removed.

Adds test_run_agent_turn_public_surface_unchanged to lock the wrapper
contract callers depend on.

No behavior change. Existing tests pass unchanged.

Closes #382.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Live verification (after PR is opened, before merge)

These exercises confirm no per-mode regression. Capture results in
`docs/dev-sessions/2026-04-27-1731-refactor-run-agent-turn/notes.md`.

- [ ] **LV1: Web UI normal turn** — Open web UI, send "what's 2+2?" — verify
  text-only response, model selection persists across reload.
- [ ] **LV2: Web UI tool-using turn** — Send a request that triggers
  `tool_search` then a follow-up tool call. Verify `text_before_tools`
  events flush before tool rows in the UI.
- [ ] **LV3: Web UI widget input** — Trigger a widget tool that pauses
  (e.g. a canvas widget). Submit the widget; verify the synthetic user
  message is injected and the loop resumes.
- [ ] **LV4: Web UI EndTurnConfirm — approve path** — Trigger a tool
  returning `EndTurnConfirm`, click Approve, verify the agent continues.
- [ ] **LV5: Web UI EndTurnConfirm — deny path** — Same setup, click Deny,
  verify the agent ends turn with a "what would you like changed?" message.
- [ ] **LV6: Mattermost normal turn** — Send a message in Mattermost, verify
  placeholder + final message appear correctly with attachment upload.
- [ ] **LV7: Heartbeat / scheduled task** — Wait for or trigger a scheduled
  skill (e.g. `dream`); verify it runs to completion.
- [ ] **LV8: Cancellation mid-turn** — Click stop while a long-running tool
  executes. Verify `[cancelled]` archive entry and clean shutdown.
- [ ] **LV9: Reflection retry** — Set `config.reflection.enabled = true`
  with a low-quality model in test config; trigger a response that fails
  judging; verify critique injection + escalation suffix on exhaustion.
- [ ] **LV10: Compaction trigger** — Have a long conversation that crosses
  `compaction.max_tokens`; verify `_maybe_compact` runs and the next
  turn sees the compacted history.

---

# Self-review checklist

After all tasks complete:

- [ ] **Spec coverage:** Every item in
  `docs/dev-sessions/2026-04-27-1731-refactor-run-agent-turn/spec.md`
  Section "Goals" maps to a task above.
- [ ] **Public surface:** `run_agent_turn` signature unchanged (Task 8
  test enforces this).
- [ ] **All existing tests pass unchanged** (Tasks 6, 9.5, 10.2, 11.2,
  12.2, 13.2, 14.2, 15.2, 16.5, 17.1).
- [ ] **No new tests added except `test_run_agent_turn_public_surface_unchanged`**
  (per spec non-goal: don't unit-test TurnRunner internals).
- [ ] **`agent.py` is the only file modified** (plus the one test file in
  Task 8).
- [ ] **Two commits** on the branch: reflection split, then TurnRunner.
- [ ] **`make check && make test` clean before each commit**.
