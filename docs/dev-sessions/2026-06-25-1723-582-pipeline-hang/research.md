# Research — wf.parallel / wf.pipeline hang investigation

Source: `Explore` subagent + standalone Python repro, 2026-06-25.

## Root cause: `asyncio.wait(FIRST_EXCEPTION)` semantics

Python docs for `asyncio.wait`:

> `FIRST_EXCEPTION`: The function will return when any future finishes by raising an exception. **If no future raises an exception then it is equivalent to ALL_COMPLETED.**

`wf.parallel` (`src/decafclaw/workflow/handle.py:220-361`) and `wf.pipeline` (`handle.py:363-494`) both call:

```python
done, pending = await asyncio.wait(
    wait_set,                         # tasks + [cancel_watcher]
    return_when=asyncio.FIRST_EXCEPTION,
)
```

The wait_set includes a `cancel_watcher` task that awaits `ctx.cancelled.wait()` (lines 270-280 / 420-430). The watcher only completes when:
- `ctx.cancelled.set()` fires (raising `_CancelSignal`), OR
- It's explicitly cancelled in the `finally` cleanup.

**When all real thunks complete normally** (the case the smoke hit):
- No real task raises → `FIRST_EXCEPTION` falls back to `ALL_COMPLETED`.
- `ALL_COMPLETED` waits for the cancel_watcher too.
- Cancel event was never set → watcher never completes.
- **`asyncio.wait` hangs forever.**

The downstream code at lines 295-340 (parallel) / 439-472 (pipeline) — including the journal write — is never reached. Server idle at 0% CPU because the event loop is just waiting on a future that will not fire.

## Standalone repro (8 lines, no decafclaw imports)

```python
import asyncio

async def quick_task(i):
    await asyncio.sleep(0.01)
    return i

async def never_fires():
    ev = asyncio.Event()
    await ev.wait()  # never set
    raise RuntimeError("should never reach here")

async def main():
    tasks = [asyncio.create_task(quick_task(i)) for i in range(3)]
    watcher = asyncio.create_task(never_fires())
    await asyncio.wait(tasks + [watcher], return_when=asyncio.FIRST_EXCEPTION)
    # hangs forever
```

Verified by wrapping in `asyncio.wait_for(..., timeout=2.0)` — TimeoutError after the 3 real tasks completed at ~10ms each.

## Documentarian's Q3 finding was wrong

The earlier subagent claim that "asyncio.wait returns when all tasks complete normally, with cancel_watcher pulled into the done set" is **incorrect**. The 8-line repro confirms `FIRST_EXCEPTION` does NOT exit on all-normal-completion; it falls back to `ALL_COMPLETED` and includes the watcher in the wait. Noting the correction so future readers don't propagate the misread.

## Why unit tests didn't catch this

Test fixtures construct `ctx` with `ctx.cancelled = None` (or similar) — see e.g. `tests/test_workflow_parallel.py`'s mock-ctx setup. The guard at `handle.py:278`:

```python
cancel_watcher = (
    asyncio.create_task(_cancel_watcher_body())
    if cancel_event is not None
    else None
)
```

…skips creating the watcher when `cancel_event` is `None`. So unit tests never construct the wait_set that triggers the bug. The live `/research` smoke (and presumably any real `TurnKind.WORKFLOW` turn) gets a real `asyncio.Event` from `ConversationManager`, so the watcher IS in the wait_set, and the bug fires the moment all thunks finish without raising.

The reviewer of PR #573 / #574 actually flagged exactly this gap in their code-quality review for Phase 5: "tests cover the happy path + propagate path + cancel path, but not 'all-thunks-completed-with-error-results-but-something-async-is-still-pending'." Prescient.

## Mirror-image bug in pipeline

`wf.pipeline` (handle.py:363-494) has the identical wait-set + watcher structure at lines 415-437. Same bug fires for pipeline-with-real-cancel-event-and-no-thunks-raising. The smoke did not reach pipeline because parallel hangs first.

## Cancel paths that DO work

The current code is correct when EITHER:
- Real cancellation fires during the wait (`ctx.cancelled.set()` → watcher raises `_CancelSignal` → `FIRST_EXCEPTION` exits cleanly via the cancel branch at lines 295-311).
- A real task raises during the wait (`FIRST_EXCEPTION` exits, then the exception-unmasking path runs).
- `ctx.cancelled is None` so no watcher exists.

The hang only fires in the "all real tasks complete normally + non-None cancel_event + cancel never set" case.

## Code locations to fix

- `src/decafclaw/workflow/handle.py:289-294` (parallel's `asyncio.wait` call)
- `src/decafclaw/workflow/handle.py:433-438` (pipeline's `asyncio.wait` call)
- The cleanup logic in the surrounding `try/except/finally` blocks may need adjustment depending on the fix approach.

## Fix-shape candidates (for brainstorm)

- **A. Loop with `FIRST_COMPLETED`.** Smallest local change: change `FIRST_EXCEPTION` to `FIRST_COMPLETED`, wrap in a `while not all_done_or_cancelled` loop, decide what to do after each task completes. Same architecture, slightly more code per primitive.
- **B. Race gather-of-tasks against the cancel_watcher.** Drop the watcher from the wait_set; instead `asyncio.wait([asyncio.gather(*tasks, return_exceptions=True), cancel_watcher], return_when=FIRST_COMPLETED)`. Cleaner architecture, ~same total LOC.
- **C. Cancel-via-callback instead of watcher-in-wait-set.** Register `ctx.cancelled.add_done_callback(_propagate_cancel)` (or use `asyncio.create_task` to wait on the event and forward cancellation to tasks). Most invasive; eliminates `_CancelSignal` entirely.

Picking the shape is the brainstorm question.
