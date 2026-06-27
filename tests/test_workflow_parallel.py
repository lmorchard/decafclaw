"""Tests for WorkflowHandle.parallel (Phase 5).

Exercises the fan-out / fan-in primitive: outer entry caches the assembled
result list; each thunk gets a sub-handle so its journaled calls land at
hierarchical seqs. Mid-fan-out crash → re-dispatch, each thunk replays its
own cached calls and resumes from the first non-cached one.
"""
import asyncio

import pytest

from decafclaw.workflow.handle import WorkflowHandle
from decafclaw.workflow.journal import Journal, fingerprint


@pytest.mark.asyncio
async def test_parallel_live_path_basic(ctx):
    """Three thunks each return their index; results come back in index order
    and the outer entry caches the assembled list."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    async def make_thunk(i):
        async def thunk(sub):
            return i
        return thunk

    thunks = [await make_thunk(i) for i in range(3)]
    out = await h.parallel(thunks)
    assert out == [0, 1, 2]

    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "parallel"
    assert entry.result == [0, 1, 2]
    expected_fp = fingerprint("parallel", {"count": 3})
    assert entry.args_fingerprint == expected_fp


@pytest.mark.asyncio
async def test_parallel_live_path_with_journaled_calls(ctx):
    """Each thunk does an llm_call. Sub-handle keying lands each at (0, i, 0);
    outer entry at (0,) caches the assembled result list."""
    j = Journal(workflow_name="t")

    call_counter = {"n": 0}

    async def fake_llm(ctx, **kw):
        # Distinguish per-call by prompt; return a tagged dict.
        call_counter["n"] += 1
        return {"answer": kw["user_msg"]}

    h = WorkflowHandle(ctx, j, llm_caller=fake_llm)

    def make_thunk(i):
        async def thunk(sub):
            return await sub.llm_call(
                prompt=f"q{i}", schema={"type": "object"})
        return thunk

    thunks = [make_thunk(i) for i in range(3)]
    out = await h.parallel(thunks)

    assert out == [{"answer": "q0"}, {"answer": "q1"}, {"answer": "q2"}]
    assert call_counter["n"] == 3

    # Child entries at (0, i, 0).
    for i in range(3):
        child = j.get((0, i, 0))
        assert child is not None, f"missing child entry at (0, {i}, 0)"
        assert child.kind == "llm_call"
        assert child.result == {"answer": f"q{i}"}

    # Outer entry at (0,).
    outer = j.get((0,))
    assert outer is not None
    assert outer.kind == "parallel"
    assert outer.result == [{"answer": "q0"}, {"answer": "q1"}, {"answer": "q2"}]


@pytest.mark.asyncio
async def test_parallel_replay_path_full_cache(ctx):
    """When the outer entry is already journaled, replay returns it without
    invoking the thunks."""
    j = Journal(workflow_name="t")
    fp = fingerprint("parallel", {"count": 3})
    j.append((0,), "parallel", fp, ["a", "b", "c"])

    h = WorkflowHandle(ctx, j)

    def boom_thunk_factory(label):
        async def thunk(sub):
            raise AssertionError(
                f"thunk {label} MUST NOT run during full-cache replay")
        return thunk

    thunks = [boom_thunk_factory(i) for i in range(3)]
    out = await h.parallel(thunks)
    assert out == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_parallel_mid_fanout_resume(ctx):
    """Pre-populate child entries for thunks 0 and 1 (their llm_call
    journaled) but NOT the outer entry. On re-dispatch, thunks 0 and 1 hit
    their cached calls; thunk 2 runs live."""
    j = Journal(workflow_name="t")

    # Each thunk does ONE llm_call. The fingerprint is computed from the
    # same prompt/schema/system the thunk passes, so we mirror that here.
    def thunk_fp(i):
        return fingerprint(
            "llm_call",
            {"prompt": f"q{i}", "schema": {"type": "object"}, "system": ""},
        )

    # Pre-seed cache for thunks 0 and 1 only.
    j.append((0, 0, 0), "llm_call", thunk_fp(0), {"cached": "q0"})
    j.append((0, 1, 0), "llm_call", thunk_fp(1), {"cached": "q1"})

    live_calls: list[str] = []

    async def fake_llm(ctx, **kw):
        live_calls.append(kw["user_msg"])
        return {"live": kw["user_msg"]}

    h = WorkflowHandle(ctx, j, llm_caller=fake_llm)

    def make_thunk(i):
        async def thunk(sub):
            return await sub.llm_call(
                prompt=f"q{i}", schema={"type": "object"})
        return thunk

    out = await h.parallel([make_thunk(i) for i in range(3)])

    # Cached calls return their cached payloads; thunk 2 runs live.
    assert out == [{"cached": "q0"}, {"cached": "q1"}, {"live": "q2"}]
    # Only thunk 2's llm_call hit the live caller.
    assert live_calls == ["q2"]

    # Outer entry now written.
    outer = j.get((0,))
    assert outer is not None
    assert outer.kind == "parallel"
    assert outer.result == out


@pytest.mark.asyncio
async def test_parallel_propagates_first_exception(ctx):
    """If any thunk raises, the exception surfaces from `parallel` and the
    outer entry is NOT written (so replay re-dispatches)."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    async def ok_thunk(sub):
        return "ok"

    async def bad_thunk(sub):
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await h.parallel([ok_thunk, bad_thunk, ok_thunk])

    # No outer entry → replay will re-dispatch.
    assert j.get((0,)) is None


@pytest.mark.asyncio
async def test_parallel_surfaces_real_exception_over_cleanup_cancel(ctx):
    """A higher-index thunk raising while a lower-index thunk is still in
    flight must surface the REAL exception, not the CancelledError that the
    cleanup gather raises against the still-pending lower-index thunk.

    Regression: a naive `[t.result() for t in tasks]` in index order would
    consult `tasks[0].result()` first and raise the straggler's cleanup
    CancelledError, masking `tasks[1]`'s ValueError.
    """
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    started = asyncio.Event()
    never_finish = asyncio.Event()  # set only on cancellation path

    async def slow_thunk(sub):
        started.set()  # signal we're in flight
        # Wait forever; will be cancelled by parallel's cleanup gather.
        await never_finish.wait()
        return "unreachable"

    async def fast_raiser(sub):
        await started.wait()  # ensure slow_thunk has started
        raise ValueError("real-error")

    with pytest.raises(ValueError, match="real-error"):
        await h.parallel([slow_thunk, fast_raiser])

    # No outer entry — fan-out crashed, replay will re-dispatch.
    assert j.get((0,)) is None


@pytest.mark.asyncio
async def test_parallel_cancels_inflight_on_ctx_cancelled(ctx):
    """Setting ctx.cancelled mid-run cancels in-flight thunks and raises
    CancelledError; no outer entry is written."""
    ctx.cancelled = asyncio.Event()

    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    started = asyncio.Event()
    never_finish = asyncio.Event()  # set only on cancellation path

    async def slow_thunk(sub):
        started.set()
        # Wait on an event that's never set — only cancellation breaks us out.
        await never_finish.wait()
        return "should-not-happen"

    async def driver():
        await h.parallel([slow_thunk, slow_thunk, slow_thunk])

    task = asyncio.create_task(driver())
    # Wait for thunks to actually start before signalling cancellation.
    await started.wait()
    ctx.cancelled.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    # No outer entry — replay can re-dispatch.
    assert j.get((0,)) is None


@pytest.mark.asyncio
async def test_parallel_returns_when_all_thunks_complete_with_cancel_event_set_but_not_fired(
    ctx,
):
    """Regression for #582: wf.parallel hung when ctx.cancelled was a
    real asyncio.Event that never fired and all thunks completed
    normally. The bug: asyncio.wait(FIRST_EXCEPTION) falls back to
    ALL_COMPLETED when no future raises, and the cancel_watcher (await
    Event.wait()) never completes — so the wait hung forever.

    The fix races asyncio.gather against the watcher with FIRST_COMPLETED,
    so the gather's completion (after all thunks return) ends the wait
    immediately without depending on the watcher firing."""
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


@pytest.mark.asyncio
async def test_parallel_zero_thunks(ctx):
    """`parallel([])` returns [] immediately and journals an empty result."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    out = await h.parallel([])
    assert out == []

    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "parallel"
    assert entry.result == []
    expected_fp = fingerprint("parallel", {"count": 0})
    assert entry.args_fingerprint == expected_fp


@pytest.mark.asyncio
async def test_parallel_nested_in_parallel(ctx):
    """Outer parallel with one thunk that runs an inner parallel of one
    thunk that runs one llm_call. Verify key composition lands at
    (0, 0, 0, 0, 0) for the deepest journaled call."""
    j = Journal(workflow_name="t")

    async def fake_llm(ctx, **kw):
        return {"echo": kw["user_msg"]}

    h = WorkflowHandle(ctx, j, llm_caller=fake_llm)

    async def innermost(inner_sub):
        return await inner_sub.llm_call(prompt="deep", schema={"x": 1})

    async def outer_thunk(sub):
        return await sub.parallel([innermost])

    out = await h.parallel([outer_thunk])

    assert out == [[{"echo": "deep"}]]

    # Deepest llm_call at (0, 0, 0, 0, 0).
    deep = j.get((0, 0, 0, 0, 0))
    assert deep is not None
    assert deep.kind == "llm_call"
    assert deep.result == {"echo": "deep"}

    # Inner parallel outer at (0, 0, 0).
    inner_outer = j.get((0, 0, 0))
    assert inner_outer is not None
    assert inner_outer.kind == "parallel"
    assert inner_outer.result == [{"echo": "deep"}]

    # Top parallel outer at (0,).
    top = j.get((0,))
    assert top is not None
    assert top.kind == "parallel"
    assert top.result == [[{"echo": "deep"}]]
