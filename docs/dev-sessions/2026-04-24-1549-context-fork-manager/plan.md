# context: fork command manager fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the `!command` invocation of `context: fork` skills (`!dream`, `!garden`) by attaching the ConversationManager to the command-dispatch ctx in both web and Mattermost transports.

**Architecture:** Two one-line additions at the transport call sites where `cmd_ctx = Context(...)` is constructed, plus regression tests at both the commands.py contract level and the web transport level. `Context` itself and `dispatch_command`'s signature are unchanged.

**Tech Stack:** Python 3, existing pytest + pytest-asyncio test scaffolding, `unittest.mock` for patching.

**Reference files:**
- Spec: [`docs/dev-sessions/2026-04-24-1549-context-fork-manager/spec.md`](spec.md)
- Existing fork-mode test pattern: `tests/test_commands.py:147-160` (`TestExecuteCommand::test_fork_mode`)
- Transport call sites: `src/decafclaw/web/websocket.py:218-221`, `src/decafclaw/mattermost.py:342-344`
- Error source: `src/decafclaw/tools/delegate.py:101-106`

**Key invariants:**
- Only `commands.py` pathway for `context: fork` is affected. `context: inline` commands (`!health`, `!ingest`, `!postmortem`, `!newsletter`) keep their current behavior.
- Error-text assertion uses a substring match (`"ConversationManager"`) so minor wording tweaks to the error in delegate.py don't break the test.
- `ctx.manager` remains a plain attribute on `Context` (not a dataclass field) — no Context refactor.

---

## File Structure

**Modify:**
- `src/decafclaw/web/websocket.py` — one line added inside `_handle_send` after `cmd_ctx.conv_id = conv_id` to attach `cmd_ctx.manager = manager`.
- `src/decafclaw/mattermost.py` — one line added inside the command-dispatch block, after the existing `cmd_ctx.user_id` assignment, to attach `cmd_ctx.manager = manager`.
- `tests/test_commands.py` — two new tests in `TestExecuteCommand` (or a new test class) pinning fork-mode manager propagation and the defense-in-depth error path.

**Create:**
- `tests/test_web_websocket_commands.py` — a new focused test file for `_handle_send`'s command-dispatch manager attachment. (Alternative: extend `tests/test_web_websocket_notifications.py`. Prefer a dedicated file for clarity.)

No doc updates. This is an internal bug fix; no user-facing feature was added or removed. `docs/newsletter.md` already reflects correct behavior (newsletter uses `context: inline`).

---

## Task 1: Commands.py contract tests — pin manager propagation

Two tests at the `commands.py` level. These pin the existing commands.py behavior (that it passes `ctx.manager` through to `_run_child_turn`) so future refactors can't silently break it.

**Files:**
- Modify: `tests/test_commands.py`

- [ ] **Step 1: Write the positive-path test**

Append to the `TestExecuteCommand` class in `tests/test_commands.py` (find it around line 131-194; insert after `test_fork_mode`):

```python
    @pytest.mark.asyncio
    async def test_fork_mode_propagates_manager_to_child_turn(self, ctx):
        """The ctx handed to _run_child_turn MUST carry the manager from the
        parent ctx — otherwise delegate.py bails and the fork never runs."""
        sentinel_manager = object()
        ctx.manager = sentinel_manager

        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do $ARGUMENTS", context="fork",
        )
        with patch(
            "decafclaw.tools.delegate._run_child_turn",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = "child result"
            mode, result = await execute_command(ctx, skill, "go")

        assert mode == "fork"
        # First positional arg to _run_child_turn is parent_ctx
        called_ctx = mock.call_args.args[0]
        assert called_ctx.manager is sentinel_manager
```

- [ ] **Step 2: Write the negative-path test (defense in depth)**

Append to the same class, right below the positive test:

```python
    @pytest.mark.asyncio
    async def test_fork_mode_without_manager_surfaces_clear_error(self, ctx):
        """If a future transport forgets to attach the manager, the existing
        error in delegate.py should still fire with a readable message —
        not a silent KeyError or None-dereference."""
        ctx.manager = None  # explicit for the test's intent

        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do $ARGUMENTS", context="fork",
        )
        # Do NOT mock _run_child_turn — let the real function hit its own
        # bail-out so the error text is the one real users would see.
        mode, result = await execute_command(ctx, skill, "go")

        assert mode == "fork"
        assert "ConversationManager" in result
```

- [ ] **Step 3: Run the new tests**

Run: `uv run pytest tests/test_commands.py::TestExecuteCommand -v`
Expected: both new tests PASS (and all existing tests in `TestExecuteCommand` still pass). These are contract tests, not TDD red tests — they formalize current behavior rather than driving new code.

- [ ] **Step 4: Run lint + typecheck**

Run:
```bash
make lint && make typecheck
```
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_commands.py
git commit -m "test(commands): pin fork-mode manager propagation (#361)"
```

---

## Task 2: Web transport regression test — TDD RED → GREEN

This test reproduces the bug: it fails BEFORE the websocket.py fix. That's the actual regression-catching test.

**Files:**
- Create: `tests/test_web_websocket_commands.py`
- Modify: `src/decafclaw/web/websocket.py:218-221` (one line added)

- [ ] **Step 1: Create the failing test**

Create `tests/test_web_websocket_commands.py`:

```python
"""WebSocket command-dispatch tests: verify cmd_ctx carries the manager.

Regression test for #361 — without the manager attached, bundled skills
with context: fork (dream, garden) fail their !command invocation with
'delegate_task requires a ConversationManager; no manager on parent ctx'.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.commands import CommandResult
from decafclaw.skills import SkillInfo


@pytest.mark.asyncio
async def test_handle_send_attaches_manager_to_cmd_ctx(monkeypatch, config):
    """When a user sends a message that triggers command dispatch,
    the cmd_ctx passed to dispatch_command MUST have ctx.manager set
    to the conversation manager from state."""
    from decafclaw.web import websocket

    # Capture the ctx passed into dispatch_command so we can assert on it.
    captured = {}

    async def fake_dispatch(ctx, text, **kwargs):
        captured["ctx"] = ctx
        return CommandResult(
            mode="unknown", text="", display_text=text,
            skill=None,
        )

    monkeypatch.setattr(
        "decafclaw.commands.dispatch_command", fake_dispatch,
    )

    # Minimal state: real config + event_bus, sentinel manager.
    from decafclaw.events import EventBus
    bus = EventBus()
    sentinel_manager = MagicMock()
    state = {
        "config": config,
        "event_bus": bus,
        "manager": sentinel_manager,
    }

    # Minimal conversation index with a conv owned by "testuser".
    index = MagicMock()
    conv = MagicMock()
    conv.user_id = "testuser"
    index.get.return_value = conv

    # Capture outbound ws_send messages.
    sent = []

    async def ws_send(msg):
        sent.append(msg)

    msg = {"conv_id": "conv-1", "text": "!dream"}

    await websocket._handle_send(
        ws_send, index, "testuser", msg, state,
    )

    assert "ctx" in captured, "dispatch_command was not invoked"
    assert captured["ctx"].manager is sentinel_manager
```

- [ ] **Step 2: Run the test — expect FAIL**

Run: `uv run pytest tests/test_web_websocket_commands.py -v`
Expected: FAIL with `assert None is <MagicMock ...>` or similar — because websocket.py currently doesn't attach `manager`, so `captured["ctx"].manager` is `None`.

- [ ] **Step 3: Apply the fix in websocket.py**

Open `src/decafclaw/web/websocket.py`. Find the block around line 218-221:

```python
    cmd_ctx = Context(config=state["config"], event_bus=state["event_bus"])
    cmd_ctx.user_id = username
    cmd_ctx.conv_id = conv_id
    cmd_result = await dispatch_command(cmd_ctx, text)
```

Change to:

```python
    cmd_ctx = Context(config=state["config"], event_bus=state["event_bus"])
    cmd_ctx.user_id = username
    cmd_ctx.conv_id = conv_id
    cmd_ctx.manager = manager
    cmd_result = await dispatch_command(cmd_ctx, text)
```

(The variable `manager` is already in scope from line 209: `manager = state.get("manager")`.)

- [ ] **Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_web_websocket_commands.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `make test`
Expected: all tests pass. No regressions.

- [ ] **Step 6: Run lint + typecheck**

Run: `make lint && make typecheck`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/web/websocket.py tests/test_web_websocket_commands.py
git commit -m "fix(web): attach manager to command-dispatch ctx (#361)"
```

---

## Task 3: Mattermost transport fix (symmetric)

Same one-line fix in the Mattermost transport. No dedicated transport test — the dispatch lives deep inside `_process_msgs` and isolating it for a mock test costs more scaffolding than the one-line change is worth. The symmetry with the web fix (reviewable in diff) is enough, and the commands.py contract tests from Task 1 still pin the invariant across transports.

**Files:**
- Modify: `src/decafclaw/mattermost.py:342-344` (one line added)

- [ ] **Step 1: Apply the fix in mattermost.py**

Open `src/decafclaw/mattermost.py`. Find the block around line 342-344:

```python
        cmd_ctx = Context(config=app_ctx.config, event_bus=app_ctx.event_bus)
        cmd_ctx.user_id = app_ctx.config.agent.user_id
        cmd_result = await dispatch_command(cmd_ctx, combined_text, prefixes=["!"])
```

Change to:

```python
        cmd_ctx = Context(config=app_ctx.config, event_bus=app_ctx.event_bus)
        cmd_ctx.user_id = app_ctx.config.agent.user_id
        cmd_ctx.manager = manager
        cmd_result = await dispatch_command(cmd_ctx, combined_text, prefixes=["!"])
```

(The `manager` variable is in scope as a parameter of the enclosing `_process_msgs` method — function signature at roughly line 299.)

- [ ] **Step 2: Run the full test suite**

Run: `make test`
Expected: all existing Mattermost tests still pass. No new tests here — the symmetry with the web fix plus the commands.py contract tests in Task 1 cover the invariant.

- [ ] **Step 3: Run lint + typecheck**

Run: `make lint && make typecheck`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/mattermost.py
git commit -m "fix(mattermost): attach manager to command-dispatch ctx (#361)"
```

---

## Task 4: Final sanity check

Everything above should already be green. This task just confirms the branch is in a shippable state before PR.

**Files:** none modified.

- [ ] **Step 1: Run the whole matrix**

Run:
```bash
make lint
make typecheck
make test
```

Expected: all green. If anything fails, stop and fix — don't open a PR with red tests.

- [ ] **Step 2: Confirm no stray changes**

Run: `git status`
Expected: `working tree clean` and branch ahead of `origin/main` by 3 commits (Task 1, 2, 3 — Task 4 itself adds no commits).

- [ ] **Step 3: Verify branch SHA count**

Run: `git log --oneline origin/main..HEAD`
Expected: 3 commits visible, in order:
1. `test(commands): pin fork-mode manager propagation (#361)`
2. `fix(web): attach manager to command-dispatch ctx (#361)`
3. `fix(mattermost): attach manager to command-dispatch ctx (#361)`

(Plus the spec commit from before we started this plan — so total could be 4 commits on the branch before the final squash.)

---

## Done checklist

After Task 4, verify:

- [ ] `make lint && make typecheck && make test` all green at HEAD
- [ ] Regression test at `tests/test_web_websocket_commands.py` passes (it would fail if the websocket.py fix is reverted)
- [ ] Contract tests at `tests/test_commands.py::TestExecuteCommand::test_fork_mode_propagates_manager_to_child_turn` and `::test_fork_mode_without_manager_surfaces_clear_error` both pass
- [ ] No doc updates — internal bug fix; no user-facing behavior change other than `!dream` / `!garden` now working

Ready for branch self-review and PR.
