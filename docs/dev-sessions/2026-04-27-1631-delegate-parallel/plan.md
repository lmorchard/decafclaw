# Plan — `delegate_tasks` parallel dispatch

Single-commit PR. All work in one branch.

## Step 1 — Config

In `src/decafclaw/config_types.py`, add to `AgentConfig`:

```python
max_parallel_delegates: int = 3
max_tasks_per_delegate_call: int = 10
```

No env aliases — autoresolved via the standard `AGENT_*` lift.

## Step 2 — `delegate.py` — implement `tool_delegate_tasks`

Add to `src/decafclaw/tools/delegate.py`:

- New module-level helper `_run_one_delegated(ctx, task, idx, semaphore, progress_state, return_schema, ...)`:
  - Acquires the semaphore.
  - Calls `_run_child_turn` with `event_context_id_override=child_conv_id` (a new optional kwarg on `_run_child_turn`, defaulting to None which preserves the current behavior of routing to the parent's subscriber).
  - Parses structured output if `return_schema` was supplied; mirrors the singular tool's split.
  - On success returns `{"index": idx, "ok": True, "text": prose, "data": parsed}` (or `{"index": idx, "ok": True, "text": text}` if no schema).
  - On error (the wrapper returns a `ToolResult(text="[error: ...]")`) returns `{"index": idx, "ok": False, "error": err_text}`.
  - Bumps the shared progress counter (under an `asyncio.Lock` on `progress_state`) and publishes a `tool_status` event from the parent ctx.

- New `tool_delegate_tasks(ctx, tasks, model, allow_vault_retrieval, allow_vault_read, return_schema)`:
  - Validates `tasks` (non-empty list, all non-empty strings, length ≤ cap).
  - Builds the semaphore, progress state.
  - `await asyncio.gather(*[_run_one_delegated(...) for ...], return_exceptions=True)`.
  - Replaces any unexpected `Exception` slot with `{"index": ..., "ok": False, "error": "delegate_tasks internal error: ..."}` and logs it.
  - Sorts by index (gather preserves order, but be explicit since we may post-process).
  - Builds `summary = {"total": N, "ok": ok_count, "failed": fail_count}`.
  - Returns `ToolResult(text=summary_line, data={"summary": ..., "results": ...})`.

- Add `tool_delegate_tasks` to `DELEGATE_TOOLS` and append a definition to `DELEGATE_TOOL_DEFINITIONS` with `priority: "critical"`, `timeout: None` (owns timeout per-child).

- Tweak `delegate_task` definition's description: change "For parallel work, call delegate_task multiple times in the same response" → "For parallel work over a known list, prefer `delegate_tasks` (plural)."

## Step 3 — Plumb optional event override into `_run_child_turn`

The cleanest path: add a kwarg `event_context_id_override: str | None = None` to `_run_child_turn`. When non-None, the `setup` callback assigns it to `child_ctx.event_context_id` instead of `parent_event_id`. Default behavior unchanged.

## Step 4 — Tests in `tests/test_delegate.py`

Add `class TestDelegateTasks` with the cases from the spec test plan:

1. `test_happy_path_three_tasks` — patch `_run_child_turn` to return per-task strings; assert results have ok=True, indices in order, summary counts right, three `tool_status` events emitted.
2. `test_mixed_failures` — patch to raise on one task (or return a `ToolResult(text="[error: ...]")`); assert one entry has `ok: False`, others succeed.
3. `test_empty_list_errors` — `tasks=[]` returns error `ToolResult`.
4. `test_blank_entry_errors` — `tasks=["", "valid"]` returns error.
5. `test_non_string_entry_errors` — `tasks=[42, "valid"]` returns error.
6. `test_over_cap_errors` — N+1 tasks with cap N returns error mentioning cap.
7. `test_concurrency_cap_honored` — cap 2, 4 tasks; patch `_run_child_turn` to record entry/exit timestamps; assert never more than 2 simultaneous.
8. `test_structured_return_parses_per_task` — each child returns prose + JSON; per-entry `data` is parsed, `text` has the JSON stripped.
9. `test_singular_unchanged` — keep existing tests; verify they pass without modification.
10. `test_event_override_passed_through` — verify the new kwarg on `_run_child_turn` reaches the setup callback (mock `manager.enqueue_turn`, inspect `context_setup`).

For (7), use a small `asyncio.Event` + counter pattern in the patched `_run_child_turn` instead of timestamps — more robust under pytest-xdist.

## Step 5 — Docs

- `docs/delegation.md`: add a section "Parallel dispatch with `delegate_tasks`". Show the params, the result shape, the cap config, the per-child event suppression, and a brief example.
- `docs/config.md`: add the two new fields to the `agent` table with defaults + env-var names.

## Step 6 — Verify

- `make lint` — clean.
- `make test` — all pass.
- `pytest --durations=25` — the new tests should all be sub-second (no real LLM, no fixed sleeps).

## Step 7 — Commit + push + PR

- One commit: `feat(delegate): parallel dispatch via delegate_tasks (#397)`.
- PR body: brief problem statement, summary of design decisions (shared params, suppress per-child events, cap on tasks), reference #397 with `Closes #397`.
- Add Copilot reviewer.

## Out of scope (future work, not in this PR)

- Per-task model / schema / vault overrides — flagged in spec as "promote to dicts later".
- Streaming child progress to parent UI — flagged in spec.
- Retry-on-failure logic.
