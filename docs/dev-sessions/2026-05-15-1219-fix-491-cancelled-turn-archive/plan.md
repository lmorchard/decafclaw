# fix #491 — cancelled-turn archive marker — Implementation Plan

**Goal:** When a user cancels a streaming agent turn, write a strong cancel signal to the conversation archive so the next turn's LLM doesn't re-fulfill the cancelled request.

**Approach:** Introduce a new archive-only `cancel_marker` role (remapped to `user` for the LLM via the existing `ROLE_REMAP` pattern). On every cancel path (iteration-start check, streaming-cancel, `CancelledError` propagation), write any partial assistant content followed by the canonical cancel marker. Partial content accumulated server-side via the existing `on_stream_chunk` callback.

**Tech stack:** Python (`src/decafclaw/`), pytest, existing archive/composer infrastructure.

---

## Phase 1: New `cancel_marker` archive role + composer remap

Register a new role that the archive accepts and that the composer remaps to `"user"` before LLM dispatch — no behavior change yet, just the plumbing.

**Files:**
- Modify: `src/decafclaw/context_composer.py` — add `"cancel_marker": "user"` to `ROLE_REMAP` (line 24-28).
- Modify: `tests/test_context_composer.py` — add a test asserting a `cancel_marker` message in history is sent to the LLM as a `user` message at the correct position.

**Key changes:**

```python
# src/decafclaw/context_composer.py — extend existing ROLE_REMAP
ROLE_REMAP: dict[str, str] = {
    "vault_retrieval": "user",
    "vault_references": "user",
    "conversation_notes": "user",
    "cancel_marker": "user",
}
```

```python
# tests/test_context_composer.py — new test
async def test_cancel_marker_remapped_to_user_in_messages(composer):
    """cancel_marker rows in history reach the LLM as user-role messages
    at the position they occupied in history."""
    history = [
        {"role": "user", "content": "write me an essay"},
        {"role": "assistant", "content": "Once upon a"},
        {"role": "cancel_marker", "content": "[User cancelled this turn. ...]"},
    ]
    composed = await composer.compose(
        ctx=..., history=history, user_message="hello",
    )
    # The composed messages list should contain the cancel marker as a
    # user-role message between the partial assistant and the new "hello".
    roles = [m["role"] for m in composed.messages]
    contents = [m.get("content", "") for m in composed.messages]
    # Find the cancel-marker remap
    assert any(
        r == "user" and "cancelled this turn" in c
        for r, c in zip(roles, contents)
    )
```

**Verification — automated:**
- [ ] `make test` passes (existing tests unchanged + new test green)
- [ ] `make lint` passes
- [ ] `make check` passes
- [ ] `pytest tests/test_context_composer.py -k cancel_marker -v` passes

**Verification — manual:**
- [ ] Read the new ROLE_REMAP entry — `cancel_marker → user` reads correctly next to the existing entries.

---

## Phase 2: Cancel-write helper + partial-text accumulation

Centralize the cancel-archive write into a single helper, and add server-side accumulation of streamed text so the helper has access to whatever was partially delivered.

**Files:**
- Modify: `src/decafclaw/conversation_manager.py`
  - Add `partial_assistant_text: str = ""` field to `ConversationState` (after line 148).
  - Reset `state.partial_assistant_text = ""` inside `_start_turn` after the busy-flag set (so each turn starts clean).
  - In the `on_stream_chunk` callback (line 992-1004), append `data` to `state.partial_assistant_text` in the `chunk_type == "text"` branch.
  - Add a module-level helper `_write_cancel_archive(config, conv_id: str, partial: str) -> None` that writes the two archive entries (partial assistant if non-empty, then the cancel marker).
- Modify: `tests/test_conversation_manager.py` — add unit tests for `_write_cancel_archive` covering empty / non-empty partial.

**Key changes:**

```python
# src/decafclaw/conversation_manager.py — module-level
CANCEL_MARKER_TEXT = (
    "[User cancelled this turn. Do not retry the cancelled request "
    "unless they explicitly ask for it again.]"
)


def _write_cancel_archive(config, conv_id: str, partial: str) -> None:
    """Append the canonical cancel marker (and any partial assistant
    content) to the conversation archive. Idempotent at the call-site
    level: callers should invoke at most once per cancelled turn."""
    from .archive import append_message
    if partial:
        append_message(config, conv_id, {
            "role": "assistant",
            "content": partial,
        })
    append_message(config, conv_id, {
        "role": "cancel_marker",
        "content": CANCEL_MARKER_TEXT,
    })
```

```python
# src/decafclaw/conversation_manager.py — ConversationState
@dataclass
class ConversationState:
    ...
    cancel_event: asyncio.Event | None = None
    partial_assistant_text: str = ""   # accumulated streamed text for the
                                       # current turn; reset at turn start.
```

```python
# src/decafclaw/conversation_manager.py — inside _start_turn,
# alongside the existing on_stream_chunk setup (around line 992)
state.partial_assistant_text = ""

async def on_stream_chunk(chunk_type, data):
    if chunk_type == "text":
        if isinstance(data, str):
            state.partial_assistant_text += data
        await self.emit(conv_id, {
            "type": "chunk", "text": data,
        })
    elif chunk_type == "done":
        ...
```

```python
# tests/test_conversation_manager.py — unit tests
def test_write_cancel_archive_with_partial(tmp_path, config):
    conv_id = "conv-1"
    _write_cancel_archive(config, conv_id, "partial response so far")
    messages = read_archive(config, conv_id)
    assert len(messages) == 2
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == "partial response so far"
    assert messages[1]["role"] == "cancel_marker"
    assert "do not retry" in messages[1]["content"].lower()


def test_write_cancel_archive_without_partial(tmp_path, config):
    conv_id = "conv-2"
    _write_cancel_archive(config, conv_id, "")
    messages = read_archive(config, conv_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "cancel_marker"
```

**Verification — automated:**
- [ ] `make test` passes
- [ ] `make lint` passes
- [ ] `make check` passes
- [ ] `pytest tests/test_conversation_manager.py -k cancel_archive -v` passes

**Verification — manual:**
- [ ] Reset of `partial_assistant_text` happens before any chunk could arrive (in `_start_turn`, not in the agent task body).

---

## Phase 3: Wire the helper into all three cancel paths

Replace ad-hoc writes (or missing writes) on the three cancel paths with calls to `_write_cancel_archive`. Add an integration test that reproduces the bug shape.

**Files:**
- Modify: `src/decafclaw/conversation_manager.py` — in the `except asyncio.CancelledError` block (line 1076-1085), call `_write_cancel_archive(self.config, conv_id, state.partial_assistant_text)` before the WebSocket emit.
- Modify: `src/decafclaw/agent.py`
  - Update `_check_cancelled` (line 106-115) so it no longer writes its own bracketed marker — instead, leave a marker that signals "cancelled" without polluting the archive (returns ToolResult only). The actual archive write happens in `conversation_manager.run()` once the iteration loop unwinds via `_Final`. To preserve the existing test invariant (`history.append(final_msg)` makes the loop's `accumulated_text_parts` include the cancel notice), keep the in-history append for the loop's reflection-skip path, but stop archiving it from `_check_cancelled`. NOTE: this changes test_check_cancelled_returns_result_when_cancelled which inspects `history[0]["role"] == "assistant"` — update the assertion shape (history append remains; archive write removed).
  - Streaming-cancel path: in `_handle_no_tool_calls` (line 668+), when `ctx.cancelled.is_set()` and `content == ""`, **skip** the empty-archive write (the empty-retry path already exists; cancellation just propagates to `_Final` and the manager-level handler writes the canonical marker).
- Modify: `tests/test_agent_turn.py` — update `test_check_cancelled_returns_result_when_cancelled` to reflect that archive write moved (assert `history` still has the in-memory marker; assert nothing extra in archive). Existing `test_run_agent_turn_cancellation` should continue to pass.
- Add: `tests/test_conversation_manager.py::test_cancel_during_turn_archives_marker_and_partial` — integration test exercising the full cancel-then-fresh-turn flow:
  1. Mock LLM with a streaming response that accumulates partial text.
  2. Start a turn via `enqueue_turn`.
  3. After partial text streamed, call `manager.cancel_turn(conv_id)`.
  4. Await turn settlement.
  5. Read archive — assert it contains user → assistant(partial) → cancel_marker.
  6. Read composed messages for a follow-up turn — assert no synthesized retry of the original request; cancel marker is between user messages as remapped `user`.

**Key changes:**

```python
# src/decafclaw/conversation_manager.py — inside run() coroutine
except asyncio.CancelledError:
    log.info("Agent turn cancelled for conv %s", conv_id[:8])
    response_text_holder.append("[cancelled]")
    # Persist cancel to archive — partial text + canonical marker.
    try:
        _write_cancel_archive(
            self.config, conv_id, state.partial_assistant_text,
        )
    except Exception as exc:
        log.warning(
            "Failed to write cancel marker for %s: %s", conv_id, exc,
        )
    await self.emit(conv_id, {
        "type": "message_complete",
        "role": "assistant",
        "text": "[cancelled]",
        "final": True,
        "suppress_user_message": False,
    })
```

```python
# src/decafclaw/agent.py — _check_cancelled
def _check_cancelled(ctx, history):
    """Check if the agent turn has been cancelled.

    Appends an in-memory marker to history so the iteration loop's
    accumulated-text logic sees the cancel notice; does NOT write to
    the archive. The canonical cancel-archive write happens in
    ConversationManager.run()'s CancelledError handler once the loop
    unwinds.
    """
    if ctx.cancelled and ctx.cancelled.is_set():
        log.info("Agent turn cancelled by user")
        msg = "[Agent turn cancelled by user]"
        final_msg = {"role": "assistant", "content": msg}
        history.append(final_msg)
        # No _archive(ctx, final_msg) call — manager handles archive on cancel.
        return ToolResult(text=msg)
    return None
```

```python
# src/decafclaw/agent.py — _handle_no_tool_calls,
# at the start of the function before the empty-retry block
async def _handle_no_tool_calls(self, response: dict) -> IterationOutcome:
    content = response.get("content") or ""
    # If cancellation fired during the LLM call and we got an empty
    # response back, don't archive an empty assistant message — let
    # the cancel propagate to the manager which writes the canonical
    # marker (with any partial text accumulated server-side).
    if (not content and self.ctx.cancelled
            and self.ctx.cancelled.is_set()):
        return _Final(result=ToolResult(text=""))
    if not content:
        if self.empty_retries < 1:
            ...
```

```python
# tests/test_conversation_manager.py — new integration test (sketch)
@pytest.mark.asyncio
async def test_cancel_during_turn_archives_marker_and_partial(
    manager, mock_llm_streaming,
):
    """Cancelling mid-stream writes partial text + cancel_marker to archive;
    a follow-up turn's composed messages contain the cancel marker as a
    user-role message between the two user turns."""
    conv_id = "conv-cancel-test"
    mock_llm_streaming.stream_text("Once upon a time, in ")
    mock_llm_streaming.set_cancel_after_chunks(2)

    await manager.enqueue_turn(
        conv_id=conv_id, text="write me a 600-word essay", kind=TurnKind.USER,
    )
    # Cancellation logic fires inside the mock; await settlement.
    state = manager._conversations[conv_id]
    await asyncio.wait_for(state.agent_task, timeout=5)

    messages = read_archive(manager.config, conv_id)
    roles = [m["role"] for m in messages]
    assert "user" in roles  # original prompt
    assert "cancel_marker" in roles
    # Partial assistant only if any text streamed
    partial_idx = roles.index("cancel_marker") - 1
    if messages[partial_idx]["role"] == "assistant":
        assert messages[partial_idx]["content"]  # non-empty
    # Cancel marker text matches the canonical constant
    assert CANCEL_MARKER_TEXT in messages[
        roles.index("cancel_marker")]["content"]
```

**Verification — automated:**
- [ ] `make test` passes
- [ ] `make lint` passes
- [ ] `make check` passes
- [ ] `pytest tests/test_agent_turn.py -k cancel -v` passes
- [ ] `pytest tests/test_conversation_manager.py -k cancel -v` passes (new integration test + existing cancel tests)

**Verification — manual:**
- [ ] Read the diff for `_check_cancelled` — the in-history `final_msg` append stays so the iteration loop's downstream code (reflection skip, accumulated_text) keeps working; only the archive write is removed.
- [ ] Confirm there's no other archive call inside the agent loop that would double-write on cancel (search for `_archive` in `_handle_no_tool_calls` and the surrounding methods).

---

## Phase 4: Docs update

Document the new role in the context-composer doc and add a short note to the conversations doc.

**Files:**
- Modify: `docs/context-composer.md` — extend the existing `ROLE_REMAP` mention (around line 117) with a brief note that `cancel_marker` is included and what it means.
- Modify: `docs/conversations.md` — add a short subsection describing what happens on cancel (archive shape: optional partial + cancel marker).

**Key changes:**

```markdown
<!-- docs/context-composer.md — extend existing paragraph or add a brief subsection -->

### `cancel_marker`

Archived when a user cancels an in-progress agent turn. Persists any
streamed partial assistant content (as an `assistant` row) followed by a
canonical marker telling the next LLM call not to retry the cancelled
request. Remapped to `user` before LLM dispatch so it lands between the
cancelled-user-turn and the next-user-turn at the correct position
across all providers (avoids Gemini's collapse of `system`-role messages
into the top-level `systemInstruction`).
```

```markdown
<!-- docs/conversations.md — new short subsection near the archive/structure section -->

### Cancelled turns

When a user cancels mid-turn (Ctrl+C in the TUI, cancel button in the
web UI), the archive records:

1. Any partial assistant text that was streamed before cancel (as an
   `assistant` row; omitted if no content streamed).
2. A `cancel_marker` row with the canonical "do not retry" text.

The `cancel_marker` is remapped to `user` for the LLM (see
[context-composer.md](context-composer.md#cancel_marker)), so the next
turn's composed message list contains a clear signal that the prior
request was cancelled and shouldn't be re-fulfilled.
```

**Verification — automated:**
- [ ] `make check` passes (no markdown linting in this repo, but check for broken refs)

**Verification — manual:**
- [ ] Read the new doc sections in-place; references to other docs resolve.

---

## Risks and mitigations

- **Existing cancel tests will need updating** (`test_check_cancelled_returns_result_when_cancelled` asserts a history shape that we're keeping; archive-side assertion change is the new shape). Phase 3 handles this.
- **The streaming provider may return a non-empty partial that we don't want to lose.** Confirmed: `_handle_no_tool_calls` writes non-empty `content` to archive via `_archive` (agent.py:691). We only short-circuit on `not content AND cancelled` so non-empty partials get archived through the normal flow — but then we'd double-archive when the manager's helper *also* writes partial.
  - **Mitigation:** the manager's helper reads from `state.partial_assistant_text` for the partial. If the agent loop already archived a non-empty `assistant` message before cancel propagated, the helper would write a duplicate. To avoid this, the helper should be passed a flag (or check) for "did the agent loop already archive an assistant message this turn?". Simplest: track a `assistant_archived_this_turn: bool` on ConversationState, set when the agent loop writes an assistant message; helper skips its partial-write if true.
  - **Alternative simpler mitigation:** the partial that lands in `state.partial_assistant_text` accumulated via `on_stream_chunk` IS the same content the agent loop would archive if it got far enough. If the agent loop did archive it (non-empty content path), we don't need the helper to write it again — just the marker. We can pin this by adding to the agent loop's archive path: set `state.partial_assistant_archived = True` after archiving. Helper checks that flag.

  Pinning during plan since this is load-bearing: **add `partial_assistant_archived: bool = False` to ConversationState**. Reset at turn start. Set to `True` immediately after the agent loop archives a non-empty assistant message in `_handle_no_tool_calls` (around line 691) and in the tool-call branches that archive an assistant message (lines 586, 625, 662). Helper checks this flag: if `True`, write only the cancel marker; if `False`, write partial+marker.

---

## Self-review notes

**Spec coverage check:**
- Acceptance criterion 1 (cancel marker on archive across all paths) → Phase 3.
- Acceptance criterion 2 (partial assistant content preserved) → Phase 2 + 3.
- Acceptance criterion 3 (LLM sees cancel marker as user message at right position) → Phase 1 + integration test in Phase 3.
- Acceptance criterion 4 (new test reproduces bug shape) → Phase 3 integration test.
- Acceptance criterion 5 (existing tests still pass) → Phase 3 test updates.
- Acceptance criterion 6 (`make check` and `make test` clean) → automated verification on every phase.

**Placeholder scan:** none — every code snippet is concrete; the dual-archive-write risk is pinned with a specific state field (`partial_assistant_archived`).

**Type consistency:** `_write_cancel_archive(config, conv_id, partial)` signature used consistently. `CANCEL_MARKER_TEXT` referenced as a module-level constant in both source and tests.
