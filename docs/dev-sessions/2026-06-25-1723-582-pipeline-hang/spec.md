# Pipeline/parallel hang fix Spec

**Goal:** Fix the silent hang in `wf.parallel` and `wf.pipeline` that wedges when all real thunks complete normally and `ctx.cancelled` is a non-None event that never fires. Replace `asyncio.wait(wait_set, FIRST_EXCEPTION)` with a clean race between `asyncio.gather(*tasks)` (default `return_exceptions=False` — load-bearing) and the cancel_watcher using `FIRST_COMPLETED`. Add regression tests that exercise the previously-untested code path.

**Source:** [Issue #582](https://github.com/lmorchard/decafclaw/issues/582). Originally surfaced in #574's smoke (PR #579 Finding 3); diagnosis sharpened by #580's smoke (PR #603) which reproduced the hang with REAL tabstack input, ruling out the "Vertex throttling on degenerate input" hypothesis.

## Current state

Both `wf.parallel` (`src/decafclaw/workflow/handle.py:220-361`) and `wf.pipeline` (`handle.py:363-494`) construct a wait set containing the real-task tasks plus a `cancel_watcher` task that awaits `ctx.cancelled.wait()`. They then call:

```python
done, pending = await asyncio.wait(
    wait_set,
    return_when=asyncio.FIRST_EXCEPTION,
)
```

Per [Python docs](https://docs.python.org/3/library/asyncio-task.html#asyncio.wait), `FIRST_EXCEPTION` "is equivalent to `ALL_COMPLETED`" when no future raises. So when all real thunks complete normally (no exception, no cancellation), `asyncio.wait` waits for the cancel_watcher too — which only completes if the cancel event fires. If the event never fires, **the call hangs forever** (research.md §1). The journal write at lines 359-361 / 492-494 is never reached.

The bug fires only when ALL THREE conditions hold:
1. All real thunks complete normally (no exception raised).
2. `ctx.cancelled is not None` (so the cancel_watcher is created — guarded at lines 278 / 422).
3. `ctx.cancelled.set()` is never called during the wait.

Unit tests miss this because their mock-ctx sets `ctx.cancelled = None`, skipping the watcher entirely. Live workflows always have a real cancel event (`ConversationManager` constructs one per turn), so the bug fires in production the moment any workflow uses `wf.parallel` or `wf.pipeline` with thunks/stages that don't raise.

Standalone repro (8 lines, no decafclaw imports) confirmed the asyncio semantics — see `research.md`.

## Desired end state

Both `wf.parallel` and `wf.pipeline` replace their `asyncio.wait(wait_set, FIRST_EXCEPTION)` call with this shape:

```python
# asyncio.gather(...) returns a _GatheringFuture (already scheduled on
# the loop) — do NOT wrap in asyncio.create_task (3.11+ rejects non-
# coroutines). The Future itself goes into the wait_set, cancels via
# .cancel() with inner-task propagation, and yields .result() either
# as a list (all-succeeded) or by re-raising the first thunk's
# exception. return_exceptions=False (default) is LOAD-BEARING: with
# return_exceptions=True, gather waits for ALL inner tasks to finish,
# so a fast_raiser + slow_hanger pair would deadlock the gather.
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

    # gather_future completed (FIRST_COMPLETED guarantees done is non-empty).
    # If the gather wasn't in done, the watcher fired; handled above.
    if gather_future not in done:
        # Defensive: shouldn't happen since FIRST_COMPLETED + only two futures
        # in wait_set means one of them is in done. If only the watcher is
        # in done, cancel_fired is True. If neither is in done, asyncio is
        # broken — but raise rather than silently miscount.
        raise RuntimeError("workflow.parallel: wait returned with no completed future")

    # gather_future is done — but with return_exceptions=False, .result()
    # either returns the list (all succeeded) or re-raises the first
    # thunk's exception. In the raise case, other tasks may still be in
    # the process of cancellation (gather marks them for cancel but the
    # cancellation handling is async). Cancel any stragglers explicitly
    # so they don't leak past the function and surface as "exception was
    # never retrieved" warnings.
    stragglers = [t for t in tasks if not t.done()]
    if stragglers:
        for t in stragglers:
            t.cancel()
        await asyncio.gather(*stragglers, return_exceptions=True)

    # .result() returns the list or re-raises the first exception.
    # This subsumes the PR #579 / #574 exception-unmasking iteration:
    # gather's "first exception" is the first one raised in time, which
    # IS the real failure (stragglers only get CancelledError after we
    # cancel them above, and they aren't part of gather_future's
    # exception surface anymore because gather already completed).
    results = gather_future.result()
finally:
    # Cancel-watcher cleanup (unchanged from current code).
    if cancel_watcher is not None:
        if not cancel_watcher.done():
            cancel_watcher.cancel()
            try:
                await cancel_watcher
            except asyncio.CancelledError:
                pass
            except _CancelSignal:
                pass
        else:
            try:
                cancel_watcher.exception()
            except (asyncio.CancelledError, _CancelSignal):
                pass
```

Same shape in both primitives. Each primitive's `tasks` list and `_CancelSignal` definition stay where they are; only the wait-and-collect block changes.

Regression test coverage:
- **`test_parallel_returns_when_all_thunks_complete_with_cancel_event_set_but_not_fired`**: ctx with a real `asyncio.Event` that we deliberately never `set()`. Pass 3 thunks that each return a result. Assert `parallel` returns within a short timeout (e.g. `asyncio.wait_for(..., timeout=2.0)`) with the expected results. Pre-fix: would hang. Post-fix: returns immediately.
- Same shape for `wf.pipeline` (`test_pipeline_returns_when_all_items_complete_with_cancel_event_set_but_not_fired`).

Other existing tests in `tests/test_workflow_parallel.py` and `tests/test_workflow_pipeline.py` continue to pass without modification. The cancellation tests (which set the event mid-run) must still trigger the cancel branch correctly.

## Design decisions

- **Decision:** Use `asyncio.gather(*tasks)` (default `return_exceptions=False`) as one composite "all tasks completed" awaitable (a `_GatheringFuture`, not a Task — do NOT wrap in `create_task`), then race it against the cancel_watcher via `asyncio.wait([gather_future, cancel_watcher], FIRST_COMPLETED)`.
  - **Why:** Cleanly separates the two concerns the current `FIRST_EXCEPTION` wait was trying to fuse: (a) "all the real work is done" (gather completion), (b) "user wants to cancel" (watcher fires). `FIRST_COMPLETED` race is the natural asyncio primitive for that semantics — one of two things has to happen first. Eliminates the misuse of `FIRST_EXCEPTION` as "wake when any task exits any way," which Python docs explicitly say it doesn't do. `return_exceptions=False` is load-bearing: with `True`, gather waits for ALL inner tasks regardless, so a fast_raiser + slow_hanger pair would deadlock gather itself.
  - **Rejected:** Loop-with-FIRST_COMPLETED (smallest diff, but a `while True` over wait + branching makes the control flow hard to follow). `return_exceptions=True` + scan the result list (would re-introduce a different hang — gather waits for the slow hanger). Drop-`_CancelSignal`-entirely (cleaner end state but rewrites the cancellation model, larger blast radius than needed for the bug).

- **Decision:** Use `gather`'s built-in inner-task cancellation rather than the current explicit "cancel pending stragglers" cleanup.
  - **Why:** Python docs guarantee: "If `gather()` is cancelled, all submitted awaitables (that have not completed yet) are also cancelled." So `gather_future.cancel()` in the cancel branch propagates to every real task automatically — the explicit straggler-cancellation loop becomes dead code.
  - **Rejected:** Keep the straggler loop (redundant; would obscure that gather handles it).

- **Decision:** Let `gather_future.result()` re-raise the first exception in time; cancel stragglers explicitly before calling it.
  - **Why:** With `return_exceptions=False`, `gather` sets its exception state the moment the first inner task raises and marks the others for cancellation. `gather_future.result()` then re-raises that ORIGINAL failure. Stragglers are still in the process of cancellation (async); we cancel + drain them BEFORE calling `.result()` so their lifecycles complete before `parallel`/`pipeline` returns. PR #579's index-vs-time concern is satisfied here too: `gather`'s first-exception-in-time IS the real failure (stragglers only ever raise CancelledError after we cancel them, and `gather_future`'s exception state is already locked in by then — straggler CancelledErrors never reach the caller).
  - **Rejected:** Scan a gather result list with `return_exceptions=True` for the first real exception (would re-introduce a hang — gather waits for ALL inner tasks before returning the list).

- **Decision:** Keep `_CancelSignal` exactly as it is.
  - **Why:** The sentinel exception is still load-bearing — it makes the watcher's task transition to "has exception" state so `FIRST_COMPLETED` reacts to it without ambiguity. (Without it, awaiting `cancel_event.wait()` returns a `True` result and the watcher completes "normally," which is harder to distinguish from a regular task completion.) Same purpose as before.
  - **Rejected:** Replace with task-cancellation propagation (option C — out of scope per "fix exact failure mode only").

- **Decision:** Same fix in both `parallel` and `pipeline`; no extraction into a shared helper.
  - **Why:** Two call sites is the second occurrence, not the third — per project convention ([feedback_three_callsite_extraction](file:///Users/lorchard/.claude/projects/-Users-lorchard-devel-decafclaw/memory/feedback_three_callsite_extraction.md)). Extracting prematurely obscures intent for one occurrence and saves one occurrence's worth of code.
  - **Rejected:** Extract `_race_tasks_against_cancel(tasks, watcher)` helper now (premature).

## Patterns to follow

- **Reuse `_CancelSignal` exactly as today.** The class definition + body stay in each primitive's local scope (one per primitive — same as current). The watcher body is unchanged: `await cancel_event.wait(); raise _CancelSignal()`.
- **Reuse the `finally` cleanup block exactly as today.** The cancel_watcher cleanup logic (handle.py:341-357 / 474-490) is correct and stays; only the body of the `try` changes.
- **Exception-unmasking iteration:** mirror Phase 5's `[t.result() for t in tasks]` fix but applied to the gather result list:
  ```python
  first_exc = next(
      (r for r in gather_result
       if isinstance(r, BaseException)
       and not isinstance(r, asyncio.CancelledError)),
      None,
  )
  ```
- **Tests:** mirror `tests/test_workflow_parallel.py` and `tests/test_workflow_pipeline.py`'s existing structure. For the regression test, construct a ctx with a real `asyncio.Event` that's NEVER set during the test. Use `asyncio.wait_for(..., timeout=2.0)` to guard against the hang.

## What we're NOT doing

- **Restructuring the cancellation model.** `_CancelSignal` + watcher pattern stays. Option C was rejected for the same reason.
- **Extracting `parallel`/`pipeline`'s shared cancellation logic into a helper.** Two call sites is below the third-callsite extraction threshold.
- **Fixing other minor findings from the #574/#580 PR reviews** (e.g., `_CancelSignal` defined inside each method, the `or "[error: skill activation failed: ...]"` message asymmetry). Out of scope.
- **Adding cancellation tests beyond the regression case.** The existing cancellation tests in both primitives cover the "cancel mid-run" path; they continue to pass under the fix.
- **Touching `wf.subagent` or `wf.tool_call`.** Those primitives don't use the wait_set + watcher pattern.

## Open questions

- **Q: Does `gather_task.cancel()` actually cancel inner tasks in CPython 3.13?**
  - **Default:** Yes. [Python docs](https://docs.python.org/3/library/asyncio-task.html#asyncio.gather): "If `gather()` is cancelled, all submitted awaitables (that have not completed yet) are also cancelled." Plan-phase verification: write a one-off Python repro before locking the fix; if the docs lie, fall back to explicit task cancellation in the cancel branch.

- **Q: Will `gather_task.result()` ever raise CancelledError after a successful FIRST_COMPLETED return?**
  - **Default:** No, because we only call `gather_task.result()` when `cancel_fired` is False (i.e., the gather completed normally, not via cancellation). If somehow gather completed via cancellation despite `cancel_fired` being False, the `.result()` would raise CancelledError; current behavior is to let that propagate. (Plan-phase: confirm via test that this defensive shape doesn't suppress real cancellation signals.)

- **Q: Should the `gather_task not in done` defensive check be present, or is it impossible?**
  - **Default:** Keep it (raises `RuntimeError` with a clear message). With only two tasks in the wait set and `FIRST_COMPLETED`, `done` cannot be empty — but the defensive check catches a hypothetical asyncio regression cheaply.
