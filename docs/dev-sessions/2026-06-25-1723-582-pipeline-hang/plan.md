# Pipeline/parallel hang fix Implementation Plan

**Goal:** Replace the buggy `asyncio.wait(wait_set, FIRST_EXCEPTION)` shape in both `wf.parallel` and `wf.pipeline` with a clean race between `asyncio.gather(*tasks)` (default `return_exceptions=False` — load-bearing) and the cancel_watcher using `FIRST_COMPLETED`. Add regression tests that exercise the previously-untested code path (cancel_event present but never fires + all thunks complete normally).

**Approach:** Verification before fix. Confirm the asyncio docs' claims about `gather().cancel()` semantics with a one-off Python repro (cheap insurance per the spec's open question). Then apply the same fix to both primitives in turn, TDD style — failing regression test first that exercises the hang, then the fix that makes it pass. Live smoke last to confirm `/research` now completes end-to-end on Flash.

**Tech stack:** Python 3.12, asyncio. Changes scoped to `src/decafclaw/workflow/handle.py` (two primitives, near-identical patches). Tests in `tests/test_workflow_parallel.py` and `tests/test_workflow_pipeline.py`.

---

## Phase 1: Verify asyncio semantics

Cheap insurance against the spec's two open questions about `asyncio.gather`/`asyncio.wait` behavior. Write a standalone Python repro that asserts:

1. `asyncio.gather(*tasks, return_exceptions=True)` returns a list with `BaseException` instances in place of failed tasks.
2. Cancelling the `gather_future` (the `_GatheringFuture` returned by `asyncio.gather` — used directly, NOT wrapped in `create_task`) propagates cancellation to all inner tasks.
3. `asyncio.wait([gather_future, watcher], return_when=FIRST_COMPLETED)` returns as soon as either completes — no hang when the watcher is pending.
4. Awaiting a completed `gather_future` via `.result()` returns the list (not raise) when the gather completed normally.

**TDD opt-out:** this phase is verification, not a behavior change. The repro itself IS the test artifact; it gets saved as a session artifact, not added to the production test suite.

**Files:**
- Create: `docs/dev-sessions/2026-06-25-1723-582-pipeline-hang/asyncio-verification.py` — a standalone script (no decafclaw imports).

**Key code:**

```python
"""Verify the asyncio semantics the #582 fix depends on.

If any of these assertions fail, the fix shape in spec.md is wrong and
we need to revisit before touching production code.
"""
import asyncio


async def quick(i):
    await asyncio.sleep(0.01)
    return i


async def boom():
    await asyncio.sleep(0.01)
    raise ValueError("boom")


async def never_fires():
    ev = asyncio.Event()
    await ev.wait()
    raise RuntimeError("unreachable")


async def main():
    # (1) gather(return_exceptions=True) returns BaseException in place of failures.
    tasks = [
        asyncio.create_task(quick(0)),
        asyncio.create_task(boom()),
        asyncio.create_task(quick(2)),
    ]
    result = await asyncio.gather(*tasks, return_exceptions=True)
    assert result[0] == 0, f"got {result[0]!r}"
    assert isinstance(result[1], ValueError), f"got {result[1]!r}"
    assert result[2] == 2, f"got {result[2]!r}"
    print("(1) gather return_exceptions: OK")

    # (2) Cancelling gather_future cancels all inner tasks.
    # NOTE: asyncio.gather() returns a _GatheringFuture, NOT a coroutine.
    # Python 3.11+ rejects asyncio.create_task(gather(...)). Use the gather
    # return value directly as the Future — same shape used in production.
    inner_tasks = [
        asyncio.create_task(asyncio.sleep(10, result=i)) for i in range(3)
    ]
    gather_future = asyncio.gather(*inner_tasks, return_exceptions=True)
    await asyncio.sleep(0)  # let everything start
    gather_future.cancel()
    try:
        await gather_future
    except asyncio.CancelledError:
        pass
    for i, t in enumerate(inner_tasks):
        assert t.cancelled(), f"inner task {i} not cancelled: done={t.done()}"
    print("(2) gather.cancel propagates: OK")

    # (3) FIRST_COMPLETED race: returns when gather completes, watcher pending.
    real_tasks = [asyncio.create_task(quick(i)) for i in range(3)]
    gather_future = asyncio.gather(*real_tasks, return_exceptions=True)
    watcher = asyncio.create_task(never_fires())
    try:
        done, pending = await asyncio.wait_for(
            asyncio.wait([gather_future, watcher],
                         return_when=asyncio.FIRST_COMPLETED),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        raise AssertionError("FIRST_COMPLETED race hung — fix shape is wrong")
    assert gather_future in done, "gather should be done"
    assert watcher in pending, "watcher should be pending"
    print("(3) FIRST_COMPLETED race: OK")
    watcher.cancel()
    try:
        await watcher
    except asyncio.CancelledError:
        pass

    # (4) gather_future.result() returns list on normal completion.
    assert gather_future.result() == [0, 1, 2]
    print("(4) gather_future.result() returns list: OK")


if __name__ == "__main__":
    asyncio.run(main())
    print("\nAll assertions passed. Fix shape from spec.md is sound.")
```

**Verification — automated:**
- [ ] `uv run python docs/dev-sessions/2026-06-25-1723-582-pipeline-hang/asyncio-verification.py` — all 4 assertions pass.

**Verification — manual:**
- [ ] If any assertion fails, STOP and revisit `spec.md`. Don't proceed to Phase 2.

---

## Phase 2: Fix `wf.parallel`

TDD. Failing regression test first (the test reproduces the hang via timeout), then apply the fix from the spec to `wf.parallel`'s body, then watch the test pass.

**Files:**
- Modify: `src/decafclaw/workflow/handle.py` — replace `parallel`'s wait-and-collect block (lines ~289-340) with the spec's race-shape.
- Modify: `tests/test_workflow_parallel.py` — add `test_parallel_returns_when_all_thunks_complete_with_cancel_event_set_but_not_fired`.

**Key changes:**

In `tests/test_workflow_parallel.py`, add (mirror the existing fixture style):

```python
@pytest.mark.asyncio
async def test_parallel_returns_when_all_thunks_complete_with_cancel_event_set_but_not_fired(
    ctx, monkeypatch,
):
    """Regression for #582: wf.parallel hung when ctx.cancelled was a
    real asyncio.Event that never fired and all thunks completed
    normally. The bug: asyncio.wait(FIRST_EXCEPTION) falls back to
    ALL_COMPLETED when no future raises, and the cancel_watcher (await
    Event.wait()) never completes — so the wait hung forever."""
    # Real Event, deliberately never set.
    ctx.cancelled = asyncio.Event()

    async def quick(sub):
        return "done"

    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    # Wrap in wait_for to fail loudly if the bug regresses.
    results = await asyncio.wait_for(
        h.parallel([quick, quick, quick]),
        timeout=2.0,
    )

    assert results == ["done", "done", "done"]
    # Outer parallel entry must have been written.
    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "parallel"
```

In `src/decafclaw/workflow/handle.py`, replace the existing wait-and-collect block. Read the current `parallel` body from line ~244 through 361 first; then replace the section between the task-creation block and the journal write with the race shape from the spec. **The `_CancelSignal` class definition, the cancel_watcher creation, the zero-thunks early-exit, and the `finally` cleanup block all stay unchanged.** Only the `try:` body between `wait_set` construction and the `finally:` keyword changes.

The new `try:` body (verbatim from spec.md, minus comments for brevity here — keep the spec's comments in the final code):

```python
        # asyncio.gather(...) returns a _GatheringFuture (already scheduled
        # on the loop) — do NOT wrap in asyncio.create_task. The Future
        # itself goes into the wait_set; .cancel() propagates to inner
        # tasks; .result() returns the list (or re-raises the first
        # exception). return_exceptions=False (default) is LOAD-BEARING —
        # with True, gather waits for ALL inner tasks regardless, so a
        # fast_raiser + slow_hanger pair would deadlock the gather.
        gather_future = asyncio.gather(*tasks)
        wait_set: list[asyncio.Future] = [gather_future]
        if cancel_watcher is not None:
            wait_set.append(cancel_watcher)

        try:
            done, _ = await asyncio.wait(
                wait_set, return_when=asyncio.FIRST_COMPLETED)

            cancel_fired = (
                cancel_watcher is not None
                and cancel_watcher in done
                and not cancel_watcher.cancelled()
                and isinstance(cancel_watcher.exception(), _CancelSignal)
            )

            if cancel_fired:
                gather_future.cancel()
                try:
                    await gather_future
                except (asyncio.CancelledError, Exception):
                    pass
                raise asyncio.CancelledError()

            if gather_future not in done:
                raise RuntimeError(
                    "wf.parallel: wait returned with no completed future")

            gather_result = gather_future.result()

            first_exc = next(
                (r for r in gather_result
                 if isinstance(r, BaseException)
                 and not isinstance(r, asyncio.CancelledError)),
                None,
            )
            if first_exc is not None:
                raise first_exc

            results = list(gather_result)
        finally:
            # cancel_watcher cleanup — UNCHANGED from current code
            ...
```

The old code constructed `wait_set` then immediately called `asyncio.wait(..., FIRST_EXCEPTION)`. The new code creates `gather_future` first, then builds `wait_set = [gather_future, cancel_watcher?]`. Note `wait_set` is now `[gather_future]` (single Future) plus optionally the watcher — NOT `tasks + [cancel_watcher]`.

**Critical:** the `tasks` list still exists and is created the same way (each thunk wrapped in `asyncio.create_task`). The `gather()` wraps THOSE tasks but DOES NOT itself get wrapped in `create_task` (it's already a Future). Don't remove the task-creation block, and don't add a `create_task` wrapper around the gather.

**Verification — automated:**
- [ ] `cd .claude/worktrees/fix-582-pipeline-hang && make lint`
- [ ] `make check`
- [ ] `make test` — baseline 2943 + 1 new test = 2944.
- [ ] `uv run pytest tests/test_workflow_parallel.py -v` — 11 tests pass (10 existing + 1 new).
- [ ] `pytest --durations=10 tests/test_workflow_parallel.py` — new test under 2.0s (the wait_for timeout); ideally <100ms post-fix.

**Verification — manual:**
- [ ] Pre-fix sanity: temporarily revert the handle.py change and confirm the new test times out at 2.0s. (Don't commit this; just confirm the test exercises the bug.)
- [ ] Post-fix: existing cancellation tests (`test_parallel_cancels_inflight_on_ctx_cancelled`, `test_parallel_surfaces_real_exception_over_cleanup_cancel`) still pass — the fix must not regress the cancel branch or the exception-unmasking branch.

---

## Phase 3: Fix `wf.pipeline`

Mirror Phase 2 against `wf.pipeline`. Same shape, same logic, same test pattern.

**Files:**
- Modify: `src/decafclaw/workflow/handle.py` — replace `pipeline`'s wait-and-collect block (lines ~433-472) with the same race-shape.
- Modify: `tests/test_workflow_pipeline.py` — add `test_pipeline_returns_when_all_items_complete_with_cancel_event_set_but_not_fired`.

**Key changes:**

In `tests/test_workflow_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_pipeline_returns_when_all_items_complete_with_cancel_event_set_but_not_fired(
    ctx, monkeypatch,
):
    """Mirror of test_parallel's #582 regression for wf.pipeline."""
    ctx.cancelled = asyncio.Event()

    async def stage(prev, item, idx, sub):
        return f"done:{item}"

    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    results = await asyncio.wait_for(
        h.pipeline(["a", "b", "c"], stage),
        timeout=2.0,
    )

    assert results == ["done:a", "done:b", "done:c"]
    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "pipeline"
```

In `src/decafclaw/workflow/handle.py`, apply the SAME race-shape to `pipeline`'s wait-and-collect block. The shape is identical to Phase 2's — just lives in the `pipeline` method instead of `parallel`. The `_run_one` inner function, `tasks` construction, `_CancelSignal` definition, cancel_watcher creation, zero-items early-exit, and `finally` cleanup all stay unchanged. Only the `try:` body between `wait_set` construction and the `finally:` keyword changes — same code as Phase 2.

This is deliberate code duplication per the spec's "two call sites is below the third-callsite threshold" decision. Don't refactor into a helper.

**Verification — automated:**
- [ ] `make lint`
- [ ] `make check`
- [ ] `make test` — 2944 + 1 = 2945.
- [ ] `uv run pytest tests/test_workflow_pipeline.py -v` — 13 tests pass (12 existing + 1 new).
- [ ] `pytest --durations=10 tests/test_workflow_pipeline.py` — new test under 2.0s.

**Verification — manual:**
- [ ] Existing cancellation + nested-with-parallel tests still pass.

---

## Phase 4: Live smoke + session artifacts

Walk `/research` end-to-end on `vertex-gemini-flash` from the same worktree that hung in #580's smoke (Run 2). Post-fix, the workflow should reach `wf.subagent` synthesis and land a final report. Capture the journal evolution and update #582 with the resolved-by-PR link.

**Files:**
- Create: `docs/dev-sessions/2026-06-25-1723-582-pipeline-hang/smoke.md` — transcript.
- Modify: `docs/dev-sessions/2026-06-25-1723-582-pipeline-hang/notes.md` — append execute-phase findings + retro.

**Smoke walk:**

1. `cd /Users/lorchard/devel/decafclaw/.claude/worktrees/fix-582-pipeline-hang`
2. `nohup uv run decafclaw > /tmp/decafclaw-582-smoke.log 2>&1 &`
3. Wait for `Uvicorn running on http://0.0.0.0:18894`.
4. Drive `/research kelp forest restoration` via `decafclaw-client`.
5. Respond to the two `user_input` prompts.
6. Poll the journal — verify it advances PAST the 4 `tool_call` children:
   - Outer `(3,)` parallel entry lands (was missing in #580 smoke).
   - Pipeline summarize entries at `(4, i, 0)` appear.
   - Outer `(4,)` pipeline entry lands.
   - Subagent entry at `(5,)` lands.
   - Journal status flips to `"done"`.
7. Final report dict is returned to the client.

**Verification — automated:**
- [ ] `make check`
- [ ] `make test` — still 2945 passing.

**Verification — manual:**
- [ ] Live `/research` walk completes through to a final report (no hang at parallel completion, no hang at pipeline completion).
- [ ] Inspect `workflow.json` on disk: journal status is `"done"`; all expected outer entries present at seqs `(3,)`, `(4,)`, `(5,)` (or equivalent for the actual orchestrator structure).
- [ ] Server log: clean shutdown signal at the end of the run; no "Task exception was never retrieved" warnings; no asyncio deprecation warnings.
- [ ] Update #582 comment: post the merged-by-PR link or "Resolved by #<PR-number>" once the PR opens.
