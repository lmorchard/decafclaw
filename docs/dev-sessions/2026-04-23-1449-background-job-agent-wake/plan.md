# Background Job Agent Wake — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a background process launched from any conversation (user-interactive, heartbeat, scheduled, child-agent) exits, fire a synthetic agent turn on the originating conversation so the agent can react to its own prior work.

**Architecture:** Prerequisite refactor unifies all turn orchestration through `ConversationManager` (heartbeat / schedules / delegate currently bypass it). Then the background reader task appends a `background_event` archive record, emits the existing user-facing inbox notification, and calls `manager.enqueue_turn(kind=WAKE)` on the originating `conv_id`. The wake turn sees the `background_event` expanded into a synthetic `shell_background_status` tool-call/tool-result pair (tool-role framing — untrusted process output stays out of system context).

**Tech Stack:** Python 3.x, asyncio, pytest/pytest-asyncio, ruff+pyright. See `spec.md` in this directory for the full design.

**Working tree:** `.claude/worktrees/background-job-agent-wake/` on branch `feat/background-job-agent-wake`. Worktree-local venv already installed (`make install` ran). Run `make check` and `make test` after every phase.

---

## Phase 1 — ConversationManager refactor: `TurnKind` + `enqueue_turn`

The user-visible behavior of `send_message` must not change. All existing tests pass. Every commit in this phase leaves the codebase in a green state.

### Task 1.1: Add `TurnKind` enum and widen `pending_messages` entries

**Files:**
- Modify: `src/decafclaw/conversation_manager.py`
- Test: `tests/test_conversation_manager.py` (extend existing — or create if missing)

- [ ] **Step 1: Grep for the existing test file to know the pattern**

Run: `ls tests/test_conversation_manager* 2>/dev/null; grep -n "pending_messages" tests/*.py 2>&1 | head -20`

Decide whether to extend an existing file or create a new one. Use the existing test patterns (fixtures, mocks) rather than inventing new ones.

- [ ] **Step 2: Add `TurnKind` enum**

In `src/decafclaw/conversation_manager.py`, near the top imports block, add:

```python
from enum import Enum


class TurnKind(Enum):
    USER = "user"
    HEARTBEAT_SECTION = "heartbeat_section"
    SCHEDULED_TASK = "scheduled_task"
    CHILD_AGENT = "child_agent"
    WAKE = "wake"
```

- [ ] **Step 3: Widen existing `pending_messages` enqueue site to carry `kind`**

In `send_message`, where `state.pending_messages.append({...})` is called, add `"kind": TurnKind.USER` to the dict. This is the only enqueue site today.

- [ ] **Step 4: Run tests — everything must still pass**

Run: `.venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -10`

Expected: all existing tests pass. The `kind` field is additive.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/conversation_manager.py
git commit -m "feat(conv-mgr): introduce TurnKind enum; tag pending entries"
```

### Task 1.2: Add `enqueue_turn` public method; make `send_message` a wrapper

**Files:**
- Modify: `src/decafclaw/conversation_manager.py`
- Test: `tests/test_conversation_manager.py`

- [ ] **Step 1: Write failing test for `enqueue_turn(kind=USER)` path**

Test the new method delegates correctly. Use a mock `run_agent_turn` to avoid actually running the agent loop.

```python
@pytest.mark.asyncio
async def test_enqueue_turn_user_kind_runs_same_as_send_message(
    manager, config, monkeypatch
):
    called = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        called.append({"text": user_message, "conv_id": ctx.conv_id})
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    future = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.USER,
        prompt="hello",
        user_id="u",
    )
    await future
    assert called == [{"text": "hello", "conv_id": "c1"}]
```

(Use the existing test file's fixtures for `manager` / `config`. If no fixtures exist, copy the bootstrap from other CM tests.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_conversation_manager.py::test_enqueue_turn_user_kind_runs_same_as_send_message -x 2>&1 | tail -15`

Expected: FAIL — `enqueue_turn` doesn't exist.

- [ ] **Step 3: Implement `enqueue_turn`; refactor `send_message` to call it**

Add this new method alongside `send_message`:

```python
async def enqueue_turn(
    self,
    conv_id: str,
    *,
    kind: TurnKind,
    prompt: str,
    history: list | None = None,
    task_mode: str | None = None,
    context_setup: Callable | None = None,
    user_id: str = "",
    archive_text: str = "",
    attachments: list[dict] | None = None,
    command_ctx: Any = None,
    wiki_page: str | None = None,
    metadata: dict | None = None,
) -> asyncio.Future:
    """Submit a turn of any kind. Returns an awaitable that resolves
    when the turn completes."""
    state = self._get_or_create(conv_id)
    future: asyncio.Future = asyncio.get_event_loop().create_future()

    # USER-kind only: circuit breaker check + user_message event emission.
    if kind is TurnKind.USER:
        if self._circuit_breaker_tripped(state):
            log.warning("Dropping message for paused conversation %s", conv_id[:8])
            future.set_result(None)
            return future
        await self.emit(conv_id, {
            "type": "user_message",
            "text": archive_text or prompt,
            "user_id": user_id,
        })

    if state.busy:
        state.pending_messages.append({
            "kind": kind,
            "text": prompt,
            "user_id": user_id,
            "context_setup": context_setup,
            "archive_text": archive_text,
            "attachments": attachments,
            "command_ctx": command_ctx,
            "wiki_page": wiki_page,
            "task_mode": task_mode,
            "history": history,
            "metadata": metadata,
            "future": future,
        })
        return future

    await self._start_turn(
        state,
        prompt,
        kind=kind,
        user_id=user_id,
        context_setup=context_setup,
        archive_text=archive_text,
        attachments=attachments,
        command_ctx=command_ctx,
        wiki_page=wiki_page,
        task_mode=task_mode,
        history=history,
        metadata=metadata,
        future=future,
    )
    return future
```

Then rewrite `send_message` to delegate:

```python
async def send_message(
    self,
    conv_id: str,
    text: str,
    *,
    user_id: str = "",
    context_setup: Callable | None = None,
    archive_text: str = "",
    attachments: list[dict] | None = None,
    command_ctx: Any = None,
    wiki_page: str | None = None,
) -> None:
    """Submit user input (thin wrapper over enqueue_turn for the USER kind)."""
    await self.enqueue_turn(
        conv_id,
        kind=TurnKind.USER,
        prompt=text,
        user_id=user_id,
        context_setup=context_setup,
        archive_text=archive_text,
        attachments=attachments,
        command_ctx=command_ctx,
        wiki_page=wiki_page,
    )
```

Update `_start_turn` signature to accept the new kwargs (`kind`, `task_mode`, `history`, `metadata`, `future`). For now, the old USER behavior stays — kind is ignored, history is either `None` (load as before) or explicit. Set `future.set_result(result_text)` at the end of the turn's `run()` function (in the `finally` block, before `_drain_pending`).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_conversation_manager.py -x -q 2>&1 | tail -10`

Expected: the new test PASSES; all prior tests also PASS (send_message behavior unchanged).

Also run the full suite:

Run: `.venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -10`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/conversation_manager.py tests/test_conversation_manager.py
git commit -m "feat(conv-mgr): add enqueue_turn; send_message becomes a wrapper"
```

### Task 1.3: Implement per-kind policy matrix inside `_start_turn`

Source of truth for behavior differences: the table in `spec.md` Section "Unification shape."

**Files:**
- Modify: `src/decafclaw/conversation_manager.py`
- Test: `tests/test_conversation_manager.py`

- [ ] **Step 1: Write failing test — heartbeat kind uses `Context.for_task` and task_mode**

```python
@pytest.mark.asyncio
async def test_enqueue_turn_heartbeat_kind_uses_for_task(
    manager, config, monkeypatch
):
    seen_ctx = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_ctx["task_mode"] = ctx.task_mode
        seen_ctx["skip_reflection"] = ctx.skip_reflection
        seen_ctx["skip_vault_retrieval"] = ctx.skip_vault_retrieval
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    future = await manager.enqueue_turn(
        conv_id="heartbeat-T-0",
        kind=TurnKind.HEARTBEAT_SECTION,
        prompt="do section",
        history=[],
        task_mode="heartbeat",
    )
    await future
    assert seen_ctx["task_mode"] == "heartbeat"
    assert seen_ctx["skip_reflection"] is True
    assert seen_ctx["skip_vault_retrieval"] is True
```

And a matching test for `USER` kind that asserts `task_mode == ""` (current behavior).

And a test for wake kind that asserts `task_mode == "background_wake"` even if not explicitly passed.

- [ ] **Step 2: Run tests to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_conversation_manager.py -k "per_kind or for_task or background_wake" -x 2>&1 | tail -15`

Expected: new tests FAIL — no per-kind branching yet.

- [ ] **Step 3: Implement the policy matrix in `_start_turn`**

At the top of `_start_turn`, branch on `kind`:

```python
# Task-mode kinds use Context.for_task for sensible skip defaults.
TASK_KINDS = {TurnKind.HEARTBEAT_SECTION, TurnKind.SCHEDULED_TASK,
              TurnKind.CHILD_AGENT, TurnKind.WAKE}
KIND_TASK_MODE = {
    TurnKind.HEARTBEAT_SECTION: "heartbeat",
    TurnKind.SCHEDULED_TASK: "scheduled",
    TurnKind.CHILD_AGENT: "child_agent",
    TurnKind.WAKE: "background_wake",
}

if kind in TASK_KINDS:
    effective_task_mode = task_mode or KIND_TASK_MODE[kind]
    ctx = Context.for_task(
        self.config, self.event_bus,
        user_id=user_id,
        conv_id=conv_id,
        channel_id=conv_id,
        task_mode=effective_task_mode,
    )
else:
    ctx = Context(config=self.config, event_bus=self.event_bus)
    ctx.user_id = user_id
    ctx.channel_id = conv_id
    ctx.conv_id = conv_id
```

- Skip `emit user_message` — already gated in `enqueue_turn`.
- Skip `_circuit_breaker_record` for non-USER kinds.
- Skip `_save_conversation_state` for HEARTBEAT_SECTION / SCHEDULED_TASK / CHILD_AGENT (their convs are ephemeral).
- Keep skill-state/skip_vault_retrieval/active_model restore for USER and WAKE only.

Also: if `history` was passed explicitly (not None), use it directly; don't call `load_history`. If None, fall back to existing `load_history(conv_id)` behavior for the USER/WAKE cases.

Carefully copy ALL of the existing `_start_turn` logic — stream callback, confirmation wiring, event forwarding, emit turn_start/turn_complete, etc. Do NOT drop any of this for USER; the diff is adding branches, not removing behavior.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -10`

Expected: all tests pass, including the new per-kind assertions.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/conversation_manager.py tests/test_conversation_manager.py
git commit -m "feat(conv-mgr): per-kind policy matrix in _start_turn"
```

### Task 1.4: Update `_drain_pending` for mixed-kind queues

**Files:**
- Modify: `src/decafclaw/conversation_manager.py`
- Test: `tests/test_conversation_manager.py`

- [ ] **Step 1: Write failing test — mixed queue drains one-at-a-time**

```python
@pytest.mark.asyncio
async def test_drain_pending_fires_mixed_kinds_one_at_a_time(
    manager, config, monkeypatch
):
    fires = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        fires.append({"text": user_message, "task_mode": ctx.task_mode})
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # Start a USER turn; while it's "busy", enqueue a USER and a WAKE.
    # Simulate busy by acquiring state and setting busy manually.
    state = manager._get_or_create("c1")
    state.busy = True
    state.agent_task = None

    u1 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="hello1")
    u2 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="hello2")
    wake = await manager.enqueue_turn("c1", kind=TurnKind.WAKE, prompt="wake",
                                       history=[])

    assert len(state.pending_messages) == 3

    # Release busy, manually drain.
    state.busy = False
    await manager._drain_pending(state)
    # Wait for drain-triggered turns to complete
    for fut in (u1, u2, wake):
        await fut

    # Expect combined USER turn first, then WAKE.
    assert len(fires) == 2
    assert fires[0]["text"] == "hello1\nhello2"
    assert fires[0]["task_mode"] == ""
    assert fires[1]["text"] == "wake"
    assert fires[1]["task_mode"] == "background_wake"
```

- [ ] **Step 2: Run it — FAIL**

Run: `.venv/bin/python -m pytest tests/test_conversation_manager.py::test_drain_pending_fires_mixed_kinds_one_at_a_time -x 2>&1 | tail -20`

Expected: FAIL (current `_drain_pending` treats the whole queue as user messages, combining everything).

- [ ] **Step 3: Implement mixed-kind drain**

Rewrite `_drain_pending`:

```python
async def _drain_pending(self, state: ConversationState) -> None:
    """Process queued entries. Combine contiguous USER runs into one turn;
    fire other kinds one at a time in FIFO order."""
    if not state.pending_messages:
        return

    # Pop a leading contiguous USER run; otherwise pop a single entry.
    first = state.pending_messages[0]
    if first["kind"] is TurnKind.USER:
        run = []
        while state.pending_messages and state.pending_messages[0]["kind"] is TurnKind.USER:
            run.append(state.pending_messages.pop(0))
        texts = [q["text"] for q in run]
        combined = "\n".join(texts)
        last = run[-1]
        all_attachments: list[dict] = []
        for q in run:
            if q.get("attachments"):
                all_attachments.extend(q["attachments"])
        log.info("Draining %d queued USER message(s) for conv %s",
                 len(run), state.conv_id[:8])
        await self._start_turn(
            state, combined,
            kind=TurnKind.USER,
            user_id=last.get("user_id", ""),
            context_setup=last.get("context_setup"),
            archive_text=last.get("archive_text", ""),
            attachments=all_attachments or None,
            command_ctx=last.get("command_ctx"),
            wiki_page=last.get("wiki_page"),
            future=last.get("future"),
            # Forward other futures by setting result when this turn completes.
            # Simpler: chain them after the first. For v1, set non-last futures
            # to the same result.
        )
        # Chain non-last futures: set result once the head future resolves.
        if len(run) > 1:
            head_fut = last.get("future")

            def _fanout(_):
                result = head_fut.result() if head_fut.done() else None
                for q in run[:-1]:
                    f = q.get("future")
                    if f and not f.done():
                        f.set_result(result)

            if head_fut:
                head_fut.add_done_callback(_fanout)
    else:
        q = state.pending_messages.pop(0)
        log.info("Draining queued %s turn for conv %s",
                 q["kind"].value, state.conv_id[:8])
        await self._start_turn(
            state, q["text"],
            kind=q["kind"],
            user_id=q.get("user_id", ""),
            context_setup=q.get("context_setup"),
            archive_text=q.get("archive_text", ""),
            attachments=q.get("attachments"),
            command_ctx=q.get("command_ctx"),
            wiki_page=q.get("wiki_page"),
            task_mode=q.get("task_mode"),
            history=q.get("history"),
            metadata=q.get("metadata"),
            future=q.get("future"),
        )
```

Note: `_start_turn` itself continues to recursively trigger `_drain_pending` when the current turn completes, so the remaining queued entries get processed one-at-a-time naturally.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -10`

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/conversation_manager.py tests/test_conversation_manager.py
git commit -m "feat(conv-mgr): mixed-kind drain — combine USER, FIFO others"
```

### Task 1.5: Phase-1 gate — full `make check` and `make test`

- [ ] **Step 1:** `make check 2>&1 | tail -10`
- [ ] **Step 2:** `make test 2>&1 | tail -5`
- [ ] **Step 3:** Confirm both green; nothing to commit here.

---

## Phase 2 — Migrate heartbeat to `enqueue_turn`

### Task 2.1: Thread `manager` into the heartbeat runner

**Files:**
- Modify: `src/decafclaw/heartbeat.py`
- Modify: `src/decafclaw/runner.py` (call site that starts `run_heartbeat_timer`)

- [ ] **Step 1: Add `manager` parameter to `run_heartbeat_timer`, `run_heartbeat_cycle`, `run_section_turn`**

The new signatures:

```python
async def run_section_turn(
    config, event_bus, manager, section: dict, timestamp: str, index: int,
) -> dict: ...

async def run_heartbeat_cycle(config, event_bus, manager) -> list[dict]: ...

async def run_heartbeat_timer(config, event_bus, manager, shutdown_event,
                              on_cycle=None, on_results=None): ...
```

- [ ] **Step 2: Update `runner.py` to pass the manager**

Find where `run_heartbeat_timer(config, event_bus, ...)` is called. Pass the existing `ConversationManager` instance as the third arg.

- [ ] **Step 3: Update heartbeat_tools._run_heartbeat_to_channel**

`heartbeat_tools.py` also calls `run_section_turn` — update it to accept and forward `manager`. Grep: `grep -n "run_section_turn" src/decafclaw/heartbeat_tools.py` — update each call site.

- [ ] **Step 4: Run tests to see what broke**

Run: `.venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -10`

Expected: test failures due to signature changes. Fix call sites in tests (add a manager fixture — instantiate `ConversationManager(config, event_bus)` if none exists in the test's fixtures).

- [ ] **Step 5: Do NOT commit yet** — Task 2.2 replaces the body.

### Task 2.2: Replace `run_agent_turn` call with `manager.enqueue_turn`

**Files:**
- Modify: `src/decafclaw/heartbeat.py`
- Test: `tests/test_heartbeat.py`

- [ ] **Step 1: Write failing test — heartbeat routes through manager**

```python
@pytest.mark.asyncio
async def test_run_section_turn_routes_through_manager(config, event_bus, monkeypatch):
    from decafclaw.conversation_manager import ConversationManager, TurnKind
    manager = ConversationManager(config, event_bus)
    seen = []

    orig_enqueue = manager.enqueue_turn

    async def spy_enqueue(conv_id, *, kind, prompt, **kwargs):
        seen.append({"conv_id": conv_id, "kind": kind, "prompt": prompt[:40]})
        return await orig_enqueue(conv_id, kind=kind, prompt=prompt, **kwargs)

    monkeypatch.setattr(manager, "enqueue_turn", spy_enqueue)

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        return ToolResult(text="HEARTBEAT_OK")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    section = {"title": "General", "body": "Do nothing.", "source": "workspace"}
    result = await run_section_turn(config, event_bus, manager, section, "T", 0)

    assert len(seen) == 1
    assert seen[0]["conv_id"] == "heartbeat-T-0"
    assert seen[0]["kind"] is TurnKind.HEARTBEAT_SECTION
    assert result["is_ok"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_heartbeat.py::test_run_section_turn_routes_through_manager -x 2>&1 | tail -15`

Expected: FAIL — `run_section_turn` still calls `run_agent_turn` directly.

- [ ] **Step 3: Replace the body of `run_section_turn`**

```python
async def run_section_turn(
    config, event_bus, manager, section: dict, timestamp: str, index: int,
) -> dict:
    from .conversation_manager import TurnKind

    title = section["title"]
    log.info(f"Heartbeat section {index + 1}: {title}")

    conv_id = f"heartbeat-{timestamp}-{index}"
    prompt = build_section_prompt(section)

    try:
        future = await manager.enqueue_turn(
            conv_id=conv_id,
            kind=TurnKind.HEARTBEAT_SECTION,
            prompt=prompt,
            history=[],
            task_mode="heartbeat",
            user_id=f"heartbeat-{section.get('source', 'workspace')}",
            metadata={"source": section.get("source", "workspace")},
        )
        result_text = await future or "(no response)"
        ok = is_heartbeat_ok(result_text)
        log.info(f"Heartbeat section '{title}': {'OK' if ok else 'ALERT'}")
        return {
            "title": title,
            "response": result_text,
            "is_ok": ok,
            "context_id": None,  # ctx is owned by manager now
        }
    except Exception as e:
        log.error(f"Heartbeat section '{title}' failed: {e}", exc_info=True)
        return {
            "title": title,
            "response": f"[error: heartbeat section failed: {e}]",
            "is_ok": False,
            "context_id": None,
        }
```

Note: the future's result needs to contain the response text. Ensure Task 1.2's `future.set_result(...)` sets the agent's final text response. If it currently sets something else, fix it now.

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -10`

Expected: all pass (existing heartbeat tests may need their fixtures updated to pass `manager`).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/heartbeat.py src/decafclaw/heartbeat_tools.py src/decafclaw/runner.py tests/test_heartbeat.py
git commit -m "feat(heartbeat): route section turns through ConversationManager"
```

### Task 2.3: Phase-2 gate

- [ ] **Step 1:** `make check 2>&1 | tail -10`
- [ ] **Step 2:** `make test 2>&1 | tail -5`
- [ ] **Step 3:** Smoke: run a quick manual heartbeat cycle in a Python REPL if possible, or defer to Phase 8's live smoke.

---

## Phase 3 — Migrate scheduled tasks to `enqueue_turn`

Same pattern as Phase 2. Mechanical.

### Task 3.1: Thread `manager` through schedule runner

**Files:**
- Modify: `src/decafclaw/schedules.py`
- Modify: `src/decafclaw/runner.py` (schedule timer call site)

- [ ] **Step 1: Add `manager` parameter to `run_schedule_task`, `run_schedule_timer`**
- [ ] **Step 2: Update `runner.py` call site**
- [ ] **Step 3: Run tests to find broken call sites; fix them (add manager fixture to schedule tests)**
- [ ] **Step 4: Commit setup**

```bash
git add src/decafclaw/schedules.py src/decafclaw/runner.py tests/
git commit -m "refactor(schedules): thread manager through the runner"
```

### Task 3.2: Replace `run_agent_turn` with `enqueue_turn` in schedules

**Files:**
- Modify: `src/decafclaw/schedules.py`
- Test: `tests/test_schedules.py`

- [ ] **Step 1: Write failing test — schedule routes through manager**

Same shape as the heartbeat test, but assert `kind is TurnKind.SCHEDULED_TASK`, `task_mode == "scheduled"`, `conv_id` matches the schedule's conv_id convention.

- [ ] **Step 2: Run to confirm FAIL**
- [ ] **Step 3: Replace the body — similar pattern to heartbeat. `conv_id` stays whatever schedules currently use (likely `schedule-{name}-{ts}` — check existing behavior and preserve).**
- [ ] **Step 4: Run `make test`; all pass**
- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/schedules.py tests/test_schedules.py
git commit -m "feat(schedules): route scheduled-task turns through ConversationManager"
```

### Task 3.3: Phase-3 gate — `make check` + `make test`

---

## Phase 4 — Migrate `delegate_task` to `enqueue_turn`

Delegate is the trickiest migration because `_run_child_turn` does elaborate per-child Context setup (skill body inheritance, tool filtering, event routing, timeout). We preserve all that via a `context_setup` callback and keep the timeout handling at the caller.

### Task 4.1: Route `_run_child_turn` through the manager

**Files:**
- Modify: `src/decafclaw/tools/delegate.py`
- Test: `tests/test_delegate.py` (or whatever exists — grep)

- [ ] **Step 1: Write failing test — delegate routes through manager with correct kind**

```python
@pytest.mark.asyncio
async def test_delegate_routes_through_manager(parent_ctx, monkeypatch):
    from decafclaw.conversation_manager import TurnKind
    seen = []

    orig_enqueue = parent_ctx.manager.enqueue_turn

    async def spy_enqueue(conv_id, *, kind, prompt, **kwargs):
        seen.append({"conv_id": conv_id, "kind": kind, "prompt": prompt[:40]})
        return await orig_enqueue(conv_id, kind=kind, prompt=prompt, **kwargs)

    monkeypatch.setattr(parent_ctx.manager, "enqueue_turn", spy_enqueue)

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        return ToolResult(text="done")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    result = await tool_delegate_task(parent_ctx, task="do a thing")
    assert len(seen) == 1
    assert seen[0]["kind"] is TurnKind.CHILD_AGENT
    assert seen[0]["conv_id"].endswith("--child-") or "--child-" in seen[0]["conv_id"]
    assert "done" in (result.text if hasattr(result, "text") else result)
```

(Will need a `parent_ctx` fixture that has `manager` set. If the fixture doesn't exist, create one that builds a ConversationManager and attaches it to ctx.)

- [ ] **Step 2: Add `Context.manager` field**

In `src/decafclaw/context.py`, inside `Context.__init__`, add:

```python
self.manager: Any = None  # ConversationManager | None
```

Set it in `ConversationManager._start_turn` when building the ctx:

```python
ctx.manager = self
```

- [ ] **Step 3: Rewrite `_run_child_turn` to use `enqueue_turn` with `context_setup`**

The callback runs inside `_start_turn` after `ctx` is built (for `CHILD_AGENT` kind, `Context.for_task` is used). In the callback, we mutate ctx to install all the child-specific state:

```python
async def _run_child_turn(parent_ctx, task, model: str = "",
                          max_iterations: int = 0):
    from dataclasses import replace
    from ..conversation_manager import TurnKind
    from . import TOOLS

    config = parent_ctx.config
    activated = parent_ctx.skills.activated
    skill_map = {s.name: s for s in getattr(config, "discovered_skills", [])}
    prompt_parts = [DEFAULT_CHILD_SYSTEM_PROMPT]
    for name in sorted(activated):
        skill = skill_map.get(name)
        if skill and skill.body:
            prompt_parts.append(f"\n\n--- Skill: {name} ---\n{skill.body}")
    child_system_prompt = "\n".join(prompt_parts)

    child_config = replace(
        config,
        agent=replace(config.agent, max_tool_iterations=(
            max_iterations or config.agent.child_max_tool_iterations)),
        system_prompt=child_system_prompt,
    )
    child_config.discovered_skills = []

    parent_conv = getattr(parent_ctx, "conv_id", "") or getattr(parent_ctx, "channel_id", "")
    child_conv_id = f"{parent_conv}--child-{id(object())%0x10000000:08x}"
    parent_event_id = getattr(parent_ctx, "event_context_id", "") or parent_ctx.context_id

    def setup(child_ctx):
        child_ctx.config = child_config
        child_ctx.conv_id = child_conv_id
        child_ctx.cancelled = getattr(parent_ctx, "cancelled", None)
        child_ctx.request_confirmation = getattr(parent_ctx, "request_confirmation", None)
        child_ctx.event_context_id = parent_event_id

        excluded = {"delegate_task", "activate_skill", "refresh_skills", "tool_search"}
        all_tools = set(TOOLS) | set(parent_ctx.tools.extra)
        parent_allowed = parent_ctx.tools.allowed
        if parent_allowed is not None:
            all_tools = all_tools & parent_allowed
        child_ctx.tools.allowed = all_tools - excluded
        child_ctx.tools.extra = parent_ctx.tools.extra
        child_ctx.tools.extra_definitions = parent_ctx.tools.extra_definitions
        child_ctx.skills.data = parent_ctx.skills.data
        child_ctx.skills.activated = set()
        child_ctx.tools.preapproved = parent_ctx.tools.preapproved
        child_ctx.tools.preapproved_shell_patterns = parent_ctx.tools.preapproved_shell_patterns

        child_ctx.on_stream_chunk = None
        child_ctx.is_child = True
        child_ctx.skip_reflection = True
        child_ctx.skip_vault_retrieval = True
        child_ctx.active_model = model if model else parent_ctx.active_model

    timeout = config.agent.child_timeout_sec

    try:
        future = await parent_ctx.manager.enqueue_turn(
            conv_id=child_conv_id,
            kind=TurnKind.CHILD_AGENT,
            prompt=task,
            history=[],
            context_setup=setup,
        )
        result_text = await asyncio.wait_for(future, timeout=timeout)
        return result_text or ""
    except asyncio.TimeoutError:
        return ToolResult(text=f"[error: subtask timed out after {timeout}s]")
    except Exception as e:
        return ToolResult(text=f"[error: subtask failed: {e}]")
```

Note: `_start_turn` for CHILD_AGENT must call the `context_setup` callback AFTER building the ctx via `Context.for_task` but BEFORE handing to `run_agent_turn`. Existing `_start_turn` already handles `context_setup` for USER (via transports). Confirm that codepath works for task kinds too; extend if not.

- [ ] **Step 4: Run `make test`; adapt fixtures as needed**
- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/tools/delegate.py src/decafclaw/context.py src/decafclaw/conversation_manager.py tests/
git commit -m "feat(delegate): route child-agent turns through ConversationManager"
```

### Task 4.2: Phase-4 gate — `make check` + `make test`

---

## Phase 5 — `background_event` archive record + history rendering

### Task 5.1: Define `background_event` record shape helper

**Files:**
- Modify: `src/decafclaw/skills/background/tools.py` (new helper `build_background_event_record`)
- Test: `tests/test_background_tools.py`

- [ ] **Step 1: Write failing test for record builder**

```python
def test_build_background_event_record_truncates_tails():
    from decafclaw.skills.background.tools import build_background_event_record
    from collections import deque

    stdout_buf = deque([f"line-{i}" for i in range(100)], maxlen=500)
    stderr_buf = deque([f"err-{i}" for i in range(100)], maxlen=500)

    rec = build_background_event_record(
        job_id="j1",
        command="echo",
        status="completed",
        exit_code=0,
        stdout_buffer=stdout_buf,
        stderr_buffer=stderr_buf,
        elapsed_ms=1234,
        completion_tail_lines=10,
    )

    assert rec["role"] == "background_event"
    assert rec["job_id"] == "j1"
    assert rec["status"] == "completed"
    assert rec["stdout_tail"].startswith("line-90\n")  # last 10
    assert rec["stdout_tail"].endswith("line-99")
    assert rec["completion_tail_lines"] == 10
    assert "timestamp" in rec
```

And a test for 4KB ceiling:

```python
def test_build_background_event_record_clamps_4kb():
    from decafclaw.skills.background.tools import build_background_event_record
    from collections import deque

    big = deque(["x" * 200 for _ in range(100)], maxlen=500)  # ~20KB total
    rec = build_background_event_record(
        job_id="j1", command="echo", status="completed", exit_code=0,
        stdout_buffer=big, stderr_buffer=deque(), elapsed_ms=0,
        completion_tail_lines=500,
    )
    assert len(rec["stdout_tail"].encode("utf-8")) <= 4096
```

- [ ] **Step 2: Run — FAIL**
- [ ] **Step 3: Implement `build_background_event_record`**

```python
def _tail_clamped(lines: deque, n: int, max_bytes: int = 4096) -> str:
    """Take the last n lines; join with \n; drop oldest lines until under max_bytes."""
    tail = list(lines)[-n:] if n > 0 else []
    s = "\n".join(tail)
    # Drop from the front (oldest) until within budget.
    while len(s.encode("utf-8")) > max_bytes and tail:
        tail = tail[1:]
        s = "\n".join(tail)
    return s


def build_background_event_record(
    *,
    job_id: str,
    command: str,
    status: str,
    exit_code: int | None,
    stdout_buffer: deque,
    stderr_buffer: deque,
    elapsed_ms: int,
    completion_tail_lines: int,
) -> dict:
    """Build a background_event archive record."""
    from datetime import datetime, timezone
    n = max(0, min(completion_tail_lines, _OUTPUT_BUFFER_SIZE))
    return {
        "role": "background_event",
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "job_id": job_id,
        "command": command,
        "status": status,
        "exit_code": exit_code,
        "stdout_tail": _tail_clamped(stdout_buffer, n),
        "stderr_tail": _tail_clamped(stderr_buffer, n),
        "elapsed_ms": elapsed_ms,
        "completion_tail_lines": n,
    }
```

- [ ] **Step 4: Run tests — PASS**
- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/skills/background/tools.py tests/test_background_tools.py
git commit -m "feat(background): build_background_event_record with tail clamping"
```

### Task 5.2: Extract shared `shell_background_status` formatter

The wake turn's synthetic tool result must render identically to a real `shell_background_status` call, so the agent sees consistent formatting.

**Files:**
- Modify: `src/decafclaw/skills/background/tools.py`

- [ ] **Step 1: Extract the markdown-formatting block from `tool_shell_background_status`**

Pull the text-composition lines from `tool_shell_background_status` (search for "parts = [f\"**Job") into a new helper:

```python
def format_status_text(
    *, job_id: str, status: str, command: str, pid: int,
    elapsed_ms: int, remaining_ms: int | None, exit_code: int | None,
    stdout: str, stderr: str,
) -> str:
    parts = [f"**Job `{job_id}`** — {status}"]
    parts.append(f"- **Command:** `{command}`")
    parts.append(f"- **PID:** {pid}")
    parts.append(f"- **Elapsed:** {elapsed_ms / 1000:.1f}s")
    if remaining_ms is not None:
        parts.append(f"- **Remaining:** {remaining_ms / 1000:.1f}s")
    if exit_code is not None:
        parts.append(f"- **Exit code:** {exit_code}")
    if stdout:
        parts.append(f"**stdout:**\n```\n{stdout}\n```")
    if stderr:
        parts.append(f"**stderr:**\n```\n{stderr}\n```")
    return "\n".join(parts)
```

- [ ] **Step 2: Update `tool_shell_background_status` to call `format_status_text`**

Replace the inline `parts = [...]` block with `text = format_status_text(...)`.

- [ ] **Step 3: Run existing tests — all pass (output should be byte-identical)**

Run: `.venv/bin/python -m pytest tests/test_background_tools.py -x -q 2>&1 | tail -5`

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/skills/background/tools.py
git commit -m "refactor(background): extract format_status_text for shared use"
```

### Task 5.3: Expand `background_event` records in ContextComposer history

**Files:**
- Modify: `src/decafclaw/context_composer.py`
- Test: `tests/test_context_composer.py` (or create a focused test if needed)

- [ ] **Step 1: Find where history records are mapped to LLM-formatted messages**

Run: `grep -n "confirmation_request\|confirmation_response\|vault_references" src/decafclaw/context_composer.py | head -20`

Study the existing pattern for how these special records are converted. Follow the same structure.

- [ ] **Step 2: Write failing test — history containing a background_event expands to assistant+tool pair**

```python
def test_background_event_expands_to_tool_call_pair(composer):
    history = [
        {"role": "user", "content": "start a job"},
        {"role": "assistant", "content": "Job started. Job ID abc123..."},
        {"role": "background_event",
         "job_id": "abc123",
         "command": "echo hello",
         "status": "completed",
         "exit_code": 0,
         "stdout_tail": "hello",
         "stderr_tail": "",
         "elapsed_ms": 1234,
         "completion_tail_lines": 50},
    ]

    messages = composer.render_history(history)  # or whatever the existing method is
    # Find the synthetic tool call/result for the event
    kinds = [(m.get("role"), m.get("tool_calls", [{}])[0].get("function", {}).get("name")
              if m.get("tool_calls") else None)
             for m in messages]
    assert ("assistant", "shell_background_status") in kinds
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert any("bg-wake-abc123" == m.get("tool_call_id") for m in tool_msgs)
    # Tool content uses format_status_text shape
    wake_tool = next(m for m in tool_msgs if m.get("tool_call_id") == "bg-wake-abc123")
    assert "Job `abc123`" in wake_tool["content"]
    assert "hello" in wake_tool["content"]
```

(Adapt to whatever the composer's actual history-rendering path is — the test is illustrative.)

- [ ] **Step 3: Run — FAIL**
- [ ] **Step 4: Implement the expansion**

In the composer's history-rendering step, before converting to LLM messages, map each `background_event` record to a pair of synthetic messages:

```python
import json
from decafclaw.skills.background.tools import format_status_text

def _expand_background_event(rec: dict) -> list[dict]:
    job_id = rec.get("job_id", "")
    call_id = f"bg-wake-{job_id}"
    args = json.dumps({"job_id": job_id})
    tool_text = format_status_text(
        job_id=job_id,
        status=rec.get("status", "?"),
        command=rec.get("command", ""),
        pid=0,
        elapsed_ms=rec.get("elapsed_ms", 0),
        remaining_ms=None,
        exit_code=rec.get("exit_code"),
        stdout=rec.get("stdout_tail", ""),
        stderr=rec.get("stderr_tail", ""),
    )
    return [
        {
            "role": "assistant",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": "shell_background_status", "arguments": args},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": tool_text,
        },
    ]
```

Wire it into the composer's history-rendering loop where it dispatches on `role`.

- [ ] **Step 5: Run tests — PASS**
- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/context_composer.py tests/test_context_composer.py
git commit -m "feat(composer): expand background_event records to tool-call pair"
```

### Task 5.4: Phase-5 gate — `make check` + `make test`

---

## Phase 6 — Wake dispatch, rate limit, `completion_tail_lines`, `BACKGROUND_WAKE_OK`

### Task 6.1: Add `completion_tail_lines` to `BackgroundJob` and the tool

**Files:**
- Modify: `src/decafclaw/skills/background/tools.py`
- Test: `tests/test_background_tools.py`

- [ ] **Step 1: Write failing test — value plumbs through from tool to job**

```python
@pytest.mark.asyncio
async def test_completion_tail_lines_plumbs_to_job(ctx, monkeypatch):
    # bypass approval
    async def yes(*a, **k): return {"approved": True}
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", yes)
    result = await tool_shell_background_start(
        ctx, command="true", completion_tail_lines=123)
    mgr = _get_job_manager(ctx)
    job_id = result.data["job_id"]
    job = mgr.get(job_id)
    assert job.completion_tail_lines == 123
```

- [ ] **Step 2: Run — FAIL**
- [ ] **Step 3: Add `completion_tail_lines: int = 50` to `BackgroundJob` dataclass. Add `completion_tail_lines: int = 50` parameter to `BackgroundJobManager.start` and plumb through. Add the kwarg to `tool_shell_background_start`; also add a range clamp (0 ≤ n ≤ 500).**

Update TOOL_DEFINITIONS for `shell_background_start` to document the new parameter.

- [ ] **Step 4: Run tests — PASS**
- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/skills/background/tools.py tests/test_background_tools.py
git commit -m "feat(background): completion_tail_lines param on shell_background_start"
```

### Task 6.2: Add `_finalize_job` helper; call it from all terminal paths

**Files:**
- Modify: `src/decafclaw/skills/background/tools.py`
- Test: `tests/test_background_tools.py`

- [ ] **Step 1: Write failing test — exit path appends a background_event archive record**

```python
@pytest.mark.asyncio
async def test_job_exit_appends_background_event_archive_record(
    ctx, tmp_path, monkeypatch
):
    from decafclaw.archive import restore_history
    # Ensure conv_id is set, archive path exists.
    ctx.conv_id = "c1"
    async def yes(*a, **k): return {"approved": True}
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", yes)

    result = await tool_shell_background_start(ctx, command="true")
    mgr = _get_job_manager(ctx)
    job_id = result.data["job_id"]
    job = mgr.get(job_id)
    await job.reader_task  # wait for completion

    history = restore_history(ctx.config, "c1") or []
    bg_events = [m for m in history if m.get("role") == "background_event"]
    assert len(bg_events) == 1
    assert bg_events[0]["job_id"] == job_id
    assert bg_events[0]["status"] == "completed"
```

- [ ] **Step 2: Run — FAIL (no archive append happens yet)**
- [ ] **Step 3: Implement `_finalize_job`**

```python
async def _finalize_job(job: BackgroundJob) -> None:
    """Append background_event, emit inbox notification, and enqueue wake.
    Call exactly once per job, at the moment status transitions out of 'running'.
    """
    if job.config is None:
        return
    # 1. Append archive record
    try:
        from decafclaw.archive import append_message
        elapsed_ms = int((time.monotonic() - job.started_at) * 1000)
        rec = build_background_event_record(
            job_id=job.job_id,
            command=job.command,
            status=job.status,
            exit_code=job.exit_code,
            stdout_buffer=job.stdout_buffer,
            stderr_buffer=job.stderr_buffer,
            elapsed_ms=elapsed_ms,
            completion_tail_lines=job.completion_tail_lines,
        )
        if job.conv_id:
            append_message(job.config, job.conv_id, rec)
    except Exception as e:
        log.warning(f"Failed to append background_event for {job.job_id}: {e}")

    # 2. Emit inbox notification (existing behavior)
    await _notify_job_exit(job)

    # 3. Enqueue wake (Phase 6.4 will implement this; stub returns early for now).
    await _enqueue_wake(job)
```

Add stub `_enqueue_wake` that just logs:

```python
async def _enqueue_wake(job: BackgroundJob) -> None:
    log.info(f"(stub) would enqueue wake for {job.job_id} on {job.conv_id}")
```

Replace the call to `_notify_job_exit(job)` in `_run_reader` with `await _finalize_job(job)`. Then update `cleanup_expired` (for `expired` status) and `stop` (for `stopped` status) to also call `_finalize_job(job)` after setting the status.

- [ ] **Step 4: Run tests — the new archive-append test passes; existing tests still pass**
- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/skills/background/tools.py tests/test_background_tools.py
git commit -m "feat(background): _finalize_job appends archive + notifies + wake stub"
```

### Task 6.3: Wake rate limiter in `ConversationManager`

**Files:**
- Modify: `src/decafclaw/conversation_manager.py`
- Modify: `src/decafclaw/config_types.py` (new `BackgroundConfig` fields or add to existing sub-dataclass — grep to see where background-related config lives)
- Test: `tests/test_conversation_manager.py`

- [ ] **Step 1: Find where to add config**

Run: `grep -n "class .*Config" src/decafclaw/config_types.py | head -20`

If there's no `BackgroundConfig`, add one. If there is (for notifications-related stuff), extend it.

- [ ] **Step 2: Add config fields**

```python
@dataclass
class BackgroundConfig:
    wake_max_per_window: int = 20
    wake_window_sec: int = 60
    default_completion_tail_lines: int = 50
```

Wire into the top-level `Config` as `self.background: BackgroundConfig`.

- [ ] **Step 3: Write failing test — N+1th wake in a window is dropped**

```python
@pytest.mark.asyncio
async def test_wake_rate_limiter_drops_after_max(manager, config, monkeypatch):
    from decafclaw.conversation_manager import TurnKind
    config.background.wake_max_per_window = 2
    config.background.wake_window_sec = 60

    fires = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        fires.append(user_message)
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    for i in range(4):
        await manager.enqueue_turn(
            conv_id="c1", kind=TurnKind.WAKE, prompt=f"wake-{i}", history=[])
    # Give queued turns a chance to drain
    await asyncio.sleep(0.05)

    assert len(fires) == 2
```

- [ ] **Step 4: Run — FAIL**
- [ ] **Step 5: Implement**

Add `wake_times: list` to `ConversationState`. In `enqueue_turn`, when `kind is TurnKind.WAKE`:

```python
if kind is TurnKind.WAKE:
    now = time.monotonic()
    cutoff = now - self.config.background.wake_window_sec
    state.wake_times = [t for t in state.wake_times if t > cutoff]
    if len(state.wake_times) >= self.config.background.wake_max_per_window:
        log.warning("Wake rate limit exceeded for %s — dropping", conv_id[:8])
        future = asyncio.get_event_loop().create_future()
        future.set_result(None)
        return future
    state.wake_times.append(now)
```

- [ ] **Step 6: Run tests — PASS**
- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/conversation_manager.py src/decafclaw/config_types.py tests/test_conversation_manager.py
git commit -m "feat(conv-mgr): per-conv wake rate limiter"
```

### Task 6.4: Wire `_enqueue_wake` to `manager.enqueue_turn(kind=WAKE)`

**Files:**
- Modify: `src/decafclaw/skills/background/tools.py`
- Modify: `src/decafclaw/context.py` (`manager` field if not yet added by Task 4.1)
- Test: `tests/test_background_tools.py`

- [ ] **Step 1: Plumb `ctx.manager` into `BackgroundJob`**

Add `manager: Any = None` to `BackgroundJob`. Thread it through `BackgroundJobManager.start(... manager=ctx.manager ...)`. Pass `ctx.manager` from `tool_shell_background_start`.

- [ ] **Step 2: Write failing test — wake fires on completion**

```python
@pytest.mark.asyncio
async def test_job_exit_enqueues_wake_turn(ctx, monkeypatch):
    from decafclaw.conversation_manager import TurnKind
    seen_kinds = []
    async def fake_enqueue(conv_id, *, kind, prompt, **kwargs):
        seen_kinds.append(kind)
        fut = asyncio.get_event_loop().create_future()
        fut.set_result(None)
        return fut
    monkeypatch.setattr(ctx.manager, "enqueue_turn", fake_enqueue)
    async def yes(*a, **k): return {"approved": True}
    monkeypatch.setattr("decafclaw.tools.shell_tools.check_shell_approval", yes)

    result = await tool_shell_background_start(ctx, command="true")
    mgr = _get_job_manager(ctx)
    job = mgr.get(result.data["job_id"])
    await job.reader_task
    # Give _enqueue_wake a chance to fire
    await asyncio.sleep(0)
    assert TurnKind.WAKE in seen_kinds
```

- [ ] **Step 3: Run — FAIL**
- [ ] **Step 4: Replace the `_enqueue_wake` stub**

```python
async def _enqueue_wake(job: BackgroundJob) -> None:
    if job.manager is None or not job.conv_id:
        log.debug("No manager or conv_id for job %s — skipping wake", job.job_id)
        return
    from decafclaw.conversation_manager import TurnKind
    nudge = (
        "A background job you started has completed. Its status and output "
        "are in your history above. Review the result and take any follow-up "
        "action (respond to the user, call other tools, or reply with "
        "BACKGROUND_WAKE_OK to end the turn silently if no action is needed)."
    )
    try:
        await job.manager.enqueue_turn(
            conv_id=job.conv_id,
            kind=TurnKind.WAKE,
            prompt=nudge,
            metadata={"job_id": job.job_id},
        )
    except Exception as e:
        log.warning(f"Failed to enqueue wake turn for {job.job_id}: {e}")
```

- [ ] **Step 5: Run tests — PASS**
- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/skills/background/tools.py src/decafclaw/context.py tests/test_background_tools.py
git commit -m "feat(background): _enqueue_wake fires a wake turn on completion"
```

### Task 6.5: `is_background_wake_ok` + `suppress_user_message` on `message_complete`

**Files:**
- Modify: `src/decafclaw/heartbeat.py` (the `is_heartbeat_ok` location — add a sibling)
- Modify: `src/decafclaw/conversation_manager.py`
- Test: new / existing

- [ ] **Step 1: Add `is_background_wake_ok`**

Next to `is_heartbeat_ok` in `heartbeat.py` (or if that feels misplaced, in a new small module), add:

```python
def is_background_wake_ok(response: str | None) -> bool:
    """Return True if the response indicates no user-facing action needed."""
    if not response:
        return False
    return "background_wake_ok" in response[:300].lower()
```

Add a simple unit test.

- [ ] **Step 2: Write failing test — wake turn's message_complete carries suppress_user_message when the prefix matches**

```python
@pytest.mark.asyncio
async def test_wake_turn_emits_suppress_user_message_when_ok(
    manager, config, monkeypatch
):
    from decafclaw.conversation_manager import TurnKind
    events = []
    sub_id = manager.subscribe("c1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        return ToolResult(text="BACKGROUND_WAKE_OK — nothing to report.")
    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(
        conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
    await fut

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes
    assert completes[-1].get("suppress_user_message") is True
```

- [ ] **Step 3: Run — FAIL**
- [ ] **Step 4: In `_start_turn`'s run(), when emitting `message_complete` for a WAKE turn, check the response text:**

```python
suppress = (kind is TurnKind.WAKE and is_background_wake_ok(response_text))
await self.emit(conv_id, {
    "type": "message_complete",
    "role": "assistant",
    "text": response_text,
    "media": response_media,
    "final": True,
    "suppress_user_message": suppress,
    "usage": {...},
    "context_limit": self.config.compaction.max_tokens,
})
```

(Set `suppress_user_message: False` or omit for non-WAKE kinds — consumer code only reads it when True.)

- [ ] **Step 5: Run tests — PASS**
- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/heartbeat.py src/decafclaw/conversation_manager.py tests/
git commit -m "feat(wake): BACKGROUND_WAKE_OK sentinel + suppress_user_message event"
```

### Task 6.6: Transport suppression — Mattermost, web UI, terminal

**Files:**
- Modify: `src/decafclaw/mattermost_display.py` (or wherever Mattermost subscribes to `message_complete`)
- Modify: `src/decafclaw/web/websocket.py`
- Modify: `src/decafclaw/interactive_terminal.py`

- [ ] **Step 1: Find subscribers**

Run: `grep -rn "message_complete" src/decafclaw/ | head -20`

Each site that handles `message_complete` events needs a check: `if event.get("suppress_user_message"): return  # skip user-facing delivery`.

- [ ] **Step 2: Apply the check**

For the Mattermost subscriber: don't post the message to the channel.
For the web UI websocket subscriber: don't forward the chat message to the client.
For the interactive terminal: don't print the message.

In all three, the subscriber should still do any accounting needed (archive, event-loop housekeeping) — suppression is strictly about the user-visible chat surface.

- [ ] **Step 3: Write a test per transport OR a smoke-level test that exercises all three via fakes**
- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/mattermost_display.py src/decafclaw/web/websocket.py src/decafclaw/interactive_terminal.py tests/
git commit -m "feat(transports): honor suppress_user_message on message_complete"
```

### Task 6.7: Phase-6 gate — `make check` + `make test`

---

## Phase 7 — Docs + CLAUDE.md

### Task 7.1: Create `docs/background-wake.md`

**Files:**
- Create: `docs/background-wake.md`

- [ ] **Step 1: Write the doc**

Content: motivation, flow diagram (copy from spec), archive record shape, tool-result-pair framing and injection-safety rationale, `completion_tail_lines` parameter, `BACKGROUND_WAKE_OK` sentinel, rate limiter config, interaction with the notification inbox.

- [ ] **Step 2: Commit**

```bash
git add docs/background-wake.md
git commit -m "docs(background-wake): how completion wakes the agent"
```

### Task 7.2: Cross-link `docs/notifications.md`

**Files:**
- Modify: `docs/notifications.md`

- [ ] **Step 1: Add a paragraph** pointing at `docs/background-wake.md` as the complementary agent-facing path (user-facing notifications remain this doc's territory).

- [ ] **Step 2: Commit**

```bash
git add docs/notifications.md
git commit -m "docs(notifications): link to background-wake"
```

### Task 7.3: Update `docs/index.md`

**Files:**
- Modify: `docs/index.md`

- [ ] **Step 1: Add the new page to the index.**

- [ ] **Step 2: Commit**

```bash
git add docs/index.md
git commit -m "docs(index): add background-wake"
```

### Task 7.4: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the "Agent behavior" section**

Remove the line stating "Heartbeat and scheduled tasks bypass the manager (fire-and-forget, no persistent state)." Replace with a note that all turn orchestration now routes through CM, including heartbeat/scheduled/child-agent; background-job completion fires a WAKE turn via the same path.

- [ ] **Step 2: Add a "TurnKind" mention under the ConversationManager entry in the Key Files section if useful.**

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE): unified turn orchestration + wake turns"
```

---

## Phase 8 — Integration test + live smoke

### Task 8.1: End-to-end integration test

**Files:**
- Create: `tests/test_background_wake_integration.py`

- [ ] **Step 1: Write the full-flow test**

- User-mode conv starts a background job (mock subprocess that exits quickly with known stdout/stderr).
- Await the reader task.
- Assert:
  1. Archive contains a `background_event` record.
  2. Notification inbox has a `background` category entry.
  3. CM fired a wake turn on the same conv_id (verify via a monkeypatched `run_agent_turn` that records calls).
  4. The wake turn's history included the `background_event` expanded into an assistant+tool pair (inspect the `history` passed to `run_agent_turn`).
  5. If the mock agent returned `BACKGROUND_WAKE_OK`, the `message_complete` event carries `suppress_user_message: True`.

- [ ] **Step 2: Also add a heartbeat-originated wake test:**

- Start a job with `conv_id = "heartbeat-T-0"`.
- After completion, assert the wake fires on that conv_id (not on any user conv).

- [ ] **Step 3: Run full test suite**

Run: `make test 2>&1 | tail -5`

Expected: 1000+ tests, all pass.

Also run durations check:

Run: `.venv/bin/python -m pytest tests/test_background_wake_integration.py --durations=10 2>&1 | tail -15`

Expected: no test in the top-10-slowest — if one is, investigate (probably a missing mock).

- [ ] **Step 4: Commit**

```bash
git add tests/test_background_wake_integration.py
git commit -m "test: end-to-end wake flow + heartbeat-originated wake"
```

### Task 8.2: Live smoke — web UI

- [ ] **Step 1: Ask Les to kill any running `make dev`** (one bot instance rule).

- [ ] **Step 2: `make dev` in the worktree**

- [ ] **Step 3: In the web UI, start a short background job** — something like `shell_background_start(command="sleep 3 && echo done")`.

- [ ] **Step 4: Wait ~5s; confirm:**

- Inbox bell shows 1 unread with category `background`.
- A new agent message appears in the conversation, responding to the completion.
- Archive (`.jsonl` for the conv_id) contains a `background_event` record.

- [ ] **Step 5: Test `BACKGROUND_WAKE_OK` path**

Prompt the agent to acknowledge silently when a next job finishes (e.g. "Start `sleep 2` in the background and reply BACKGROUND_WAKE_OK when it completes."). Confirm no chat message appears in the UI but the archive/inbox still update.

- [ ] **Step 6: Capture findings in `notes.md` (next task).**

### Task 8.3: Live smoke — Mattermost

Same as 8.2 but in the Mattermost channel. Confirm parity.

### Task 8.4: Live smoke — heartbeat-originated wake

- [ ] **Step 1: Add a temporary heartbeat section** to `workspace/HEARTBEAT.md` that starts a `sleep 5 && echo ok` background job and exits.

- [ ] **Step 2: Trigger the heartbeat manually** (via tool or by editing the last-run timestamp).

- [ ] **Step 3: Confirm the wake fires** on `heartbeat-{ts}-0` — the archive for that conv gets both the original heartbeat turn AND a wake turn.

- [ ] **Step 4: Remove the temporary section** and commit any doc adjustments.

### Task 8.5: Write session `notes.md`

**Files:**
- Create: `docs/dev-sessions/2026-04-23-1449-background-job-agent-wake/notes.md`

- [ ] **Step 1: Write the retro**

Include: phases that went well, surprises, tests added, smoke results, open follow-ups (e.g. "coalescing multiple rapid wakes" if we see it get noisy).

- [ ] **Step 2: Commit**

```bash
git add docs/dev-sessions/2026-04-23-1449-background-job-agent-wake/notes.md
git commit -m "docs(dev-session): retro for background-job agent wake"
```

### Task 8.6: Final gate — `make check` + `make test`; open PR

- [ ] **Step 1:** `make check 2>&1 | tail -10`
- [ ] **Step 2:** `make test 2>&1 | tail -5`
- [ ] **Step 3:** Push and open PR via `gh pr create`, linking issue #241.
- [ ] **Step 4:** Move project board card #241 to **In review**.

---

## Self-Review Checklist

Before handing off to execution:

**Spec coverage:**
- ✅ Flow: Phases 6.2 + 6.4 cover `_finalize_job` + `_enqueue_wake`.
- ✅ Archive record: Task 5.1.
- ✅ `completion_tail_lines` parameter: Task 6.1.
- ✅ History rendering (tool-result pair): Tasks 5.2 + 5.3.
- ✅ Wake nudge prompt: Task 6.4 (inlined in `_enqueue_wake`).
- ✅ `BACKGROUND_WAKE_OK` sentinel: Task 6.5.
- ✅ Transport suppression: Task 6.6.
- ✅ `TurnKind` enum + `enqueue_turn` + policy matrix: Tasks 1.1–1.3.
- ✅ `_drain_pending` mixed-kind rule: Task 1.4.
- ✅ Wake rate limiter: Task 6.3.
- ✅ `BackgroundConfig` (wake_max_per_window, wake_window_sec, default_completion_tail_lines): Task 6.3.
- ✅ Heartbeat / schedules / delegate migration: Phases 2–4.
- ✅ Docs: Phase 7.
- ✅ End-to-end integration + live smoke: Phase 8.

**Potential plan gaps to watch during execution:**
- The exact `conv_id` convention for scheduled tasks — verify with `grep` in Phase 3, don't invent.
- `context_setup` callback timing inside `_start_turn` — must run after `for_task` builds ctx, before streaming/confirmation wiring. If the existing codepath only invokes `context_setup` for USER kind, Task 1.3 needs to broaden it.
- `ctx.manager` plumbing: the "set on ctx inside _start_turn" step should happen early, because other code in the same function may need it (though today nothing does).
- Composer history rendering path: Task 5.3 assumes a clean location. If history is assembled in multiple places (e.g. `agent.py` and `context_composer.py`), both must handle `background_event`. Confirm during implementation.

---

## Execution Handoff

**Plan complete and committed to `docs/dev-sessions/2026-04-23-1449-background-job-agent-wake/plan.md`. Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — tasks executed in this session with batch checkpoints.

**Which approach?**
