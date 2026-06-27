"""Tests for WorkflowHandle.pipeline (Phase 6).

Per-item run of stage1 → stage2 → … → stageN. No barrier between stages.
Each item gets its own sub-handle keyed (outer_seq, item_idx); all stages
for that item share the sub-handle's cursor sequentially.
"""
import asyncio

import pytest

from decafclaw.workflow.handle import WorkflowHandle
from decafclaw.workflow.journal import Journal, fingerprint


@pytest.mark.asyncio
async def test_pipeline_live_path_basic(ctx):
    """Three items, two stages. Stage 1 doubles prev; stage 2 adds 1.
    Items [1, 2, 3] -> [3, 5, 7]; outer entry caches the result list."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    async def stage_double(prev, item, idx, sub):
        return prev * 2

    async def stage_plus_one(prev, item, idx, sub):
        return prev + 1

    out = await h.pipeline([1, 2, 3], stage_double, stage_plus_one)
    assert out == [3, 5, 7]

    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "pipeline"
    assert entry.result == [3, 5, 7]
    expected_fp = fingerprint(
        "pipeline", {"items": [1, 2, 3], "stage_count": 2})
    assert entry.args_fingerprint == expected_fp


@pytest.mark.asyncio
async def test_pipeline_live_path_with_journaled_calls(ctx):
    """Two items, two stages, each stage does an llm_call via the sub-handle.
    Journal lands at (0, item_idx, stage_cursor) plus outer at (0,)."""
    j = Journal(workflow_name="t")

    async def fake_llm(ctx, **kw):
        return {"answer": kw["user_msg"]}

    h = WorkflowHandle(ctx, j, llm_caller=fake_llm)

    async def stage_one(prev, item, idx, sub):
        return await sub.llm_call(
            prompt=f"s1-{item}", schema={"type": "object"})

    async def stage_two(prev, item, idx, sub):
        return await sub.llm_call(
            prompt=f"s2-{item}", schema={"type": "object"})

    out = await h.pipeline(["A", "B"], stage_one, stage_two)
    assert out == [{"answer": "s2-A"}, {"answer": "s2-B"}]

    # Per-item, per-stage entries.
    assert j.get((0, 0, 0)) is not None
    assert j.get((0, 0, 0)).result == {"answer": "s1-A"}
    assert j.get((0, 0, 1)) is not None
    assert j.get((0, 0, 1)).result == {"answer": "s2-A"}
    assert j.get((0, 1, 0)) is not None
    assert j.get((0, 1, 0)).result == {"answer": "s1-B"}
    assert j.get((0, 1, 1)) is not None
    assert j.get((0, 1, 1)).result == {"answer": "s2-B"}

    # Outer pipeline entry.
    outer = j.get((0,))
    assert outer is not None
    assert outer.kind == "pipeline"
    assert outer.result == [{"answer": "s2-A"}, {"answer": "s2-B"}]


@pytest.mark.asyncio
async def test_pipeline_replay_path_full_cache(ctx):
    """When the outer entry is already journaled, replay returns it without
    invoking any stage."""
    j = Journal(workflow_name="t")
    fp = fingerprint("pipeline", {"items": [1, 2, 3], "stage_count": 2})
    j.append((0,), "pipeline", fp, [10, 20, 30])

    h = WorkflowHandle(ctx, j)

    async def boom_stage(prev, item, idx, sub):
        raise AssertionError("stage MUST NOT run during full-cache replay")

    out = await h.pipeline([1, 2, 3], boom_stage, boom_stage)
    assert out == [10, 20, 30]


@pytest.mark.asyncio
async def test_pipeline_mid_resume_partial_progress(ctx):
    """Pre-populate (0, 0, 0) with item-0's stage-1 cached result.
    On re-dispatch: stage 1 for item 0 hits cache; stage 1 for item 1 runs
    live; stage 2 for both items runs live."""
    j = Journal(workflow_name="t")

    # Stage 1 fingerprint for item "A" (the prompt the live stage uses).
    s1_a_fp = fingerprint(
        "llm_call",
        {"prompt": "s1-A", "schema": {"type": "object"}, "system": ""},
    )
    j.append((0, 0, 0), "llm_call", s1_a_fp, {"cached": "s1-A"})

    live_calls: list[str] = []

    async def fake_llm(ctx, **kw):
        live_calls.append(kw["user_msg"])
        return {"live": kw["user_msg"]}

    h = WorkflowHandle(ctx, j, llm_caller=fake_llm)

    async def stage_one(prev, item, idx, sub):
        return await sub.llm_call(
            prompt=f"s1-{item}", schema={"type": "object"})

    async def stage_two(prev, item, idx, sub):
        return await sub.llm_call(
            prompt=f"s2-{item}", schema={"type": "object"})

    out = await h.pipeline(["A", "B"], stage_one, stage_two)

    # item 0's stage 1 was cached; everything else ran live.
    # Order of live_calls isn't strictly deterministic (concurrent), so
    # check membership instead.
    assert set(live_calls) == {"s1-B", "s2-A", "s2-B"}
    assert out == [{"live": "s2-A"}, {"live": "s2-B"}]

    # Outer entry now written.
    outer = j.get((0,))
    assert outer is not None
    assert outer.kind == "pipeline"
    assert outer.result == out


@pytest.mark.asyncio
async def test_pipeline_propagates_first_exception(ctx):
    """If any stage raises, the exception surfaces and the outer entry is
    NOT written (so replay re-dispatches)."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    async def stage_one(prev, item, idx, sub):
        return prev

    async def stage_two(prev, item, idx, sub):
        if idx == 1:
            raise ValueError("nope")
        return prev

    with pytest.raises(ValueError, match="nope"):
        await h.pipeline([1, 2, 3], stage_one, stage_two)

    assert j.get((0,)) is None


@pytest.mark.asyncio
async def test_pipeline_propagates_real_exception_over_cleanup_cancel(ctx):
    """A faster item's stage raising while a slower item is still in flight
    must surface the REAL exception, not the cleanup CancelledError."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    started = asyncio.Event()
    never_finish = asyncio.Event()

    async def slow_stage(prev, item, idx, sub):
        if idx == 0:
            started.set()
            await never_finish.wait()
            return "unreachable"
        await started.wait()
        raise ValueError("real-error")

    async def passthrough(prev, item, idx, sub):
        return prev

    with pytest.raises(ValueError, match="real-error"):
        await h.pipeline(["A", "B"], slow_stage, passthrough)

    assert j.get((0,)) is None


@pytest.mark.asyncio
async def test_pipeline_cancels_inflight_on_ctx_cancelled(ctx):
    """Setting ctx.cancelled mid-run cancels in-flight items and raises
    CancelledError; no outer entry is written."""
    ctx.cancelled = asyncio.Event()

    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    started = asyncio.Event()
    never_finish = asyncio.Event()

    async def slow_stage(prev, item, idx, sub):
        started.set()
        await never_finish.wait()
        return "should-not-happen"

    async def driver():
        await h.pipeline([1, 2, 3], slow_stage)

    task = asyncio.create_task(driver())
    await started.wait()
    ctx.cancelled.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert j.get((0,)) is None


@pytest.mark.asyncio
async def test_pipeline_returns_when_all_items_complete_with_cancel_event_set_but_not_fired(
    ctx,
):
    """Regression for #582: wf.pipeline hung in the same way wf.parallel did
    (mirror-image bug). Same root cause: asyncio.wait(FIRST_EXCEPTION) falls
    back to ALL_COMPLETED when no future raises, so the never-firing
    cancel_watcher kept the wait hung forever."""
    # Real Event, deliberately never set.
    ctx.cancelled = asyncio.Event()

    async def stage(prev, item, idx, sub):
        return f"done:{item}"

    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    # Wrap in wait_for to fail loudly if the bug regresses.
    results = await asyncio.wait_for(
        h.pipeline(["a", "b", "c"], stage),
        timeout=2.0,
    )

    assert results == ["done:a", "done:b", "done:c"]
    # Outer pipeline entry must have been written.
    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "pipeline"


@pytest.mark.asyncio
async def test_pipeline_zero_items(ctx):
    """`pipeline([])` returns [] immediately and journals an empty result."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    async def some_stage(prev, item, idx, sub):
        raise AssertionError("must not run on empty items")

    out = await h.pipeline([], some_stage)
    assert out == []

    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "pipeline"
    assert entry.result == []
    expected_fp = fingerprint(
        "pipeline", {"items": [], "stage_count": 1})
    assert entry.args_fingerprint == expected_fp


@pytest.mark.asyncio
async def test_pipeline_zero_stages(ctx):
    """`pipeline([1, 2])` with no stages returns items unchanged."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    out = await h.pipeline([1, 2])
    assert out == [1, 2]

    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "pipeline"
    assert entry.result == [1, 2]
    expected_fp = fingerprint(
        "pipeline", {"items": [1, 2], "stage_count": 0})
    assert entry.args_fingerprint == expected_fp


@pytest.mark.asyncio
async def test_pipeline_stage_signature_includes_sub(ctx):
    """Stage receives (prev, item, idx, sub) — verify sub is a WorkflowHandle
    keyed at (outer_seq, idx)."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    seen: list[tuple[int, tuple[int, ...]]] = []

    async def stage(prev, item, idx, sub):
        assert isinstance(sub, WorkflowHandle)
        seen.append((idx, sub._key_prefix))
        return prev

    await h.pipeline(["x", "y", "z"], stage)

    assert seen == [(0, (0, 0)), (1, (0, 1)), (2, (0, 2))]


@pytest.mark.asyncio
async def test_pipeline_fingerprint_includes_items(ctx):
    """Two runs with different item lists produce different fingerprints,
    so a cached entry from run A doesn't match run B."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    async def stage(prev, item, idx, sub):
        return prev

    fp_a = fingerprint("pipeline", {"items": [1, 2], "stage_count": 1})
    fp_b = fingerprint("pipeline", {"items": [1, 3], "stage_count": 1})
    assert fp_a != fp_b

    # Pre-populate journal entry under fp_a; running with different items
    # would raise WorkflowNonDeterministic (fingerprint mismatch).
    j.append((0,), "pipeline", fp_a, [1, 2])

    from decafclaw.workflow.errors import WorkflowNonDeterministic
    with pytest.raises(WorkflowNonDeterministic):
        await h.pipeline([1, 3], stage)


@pytest.mark.asyncio
async def test_pipeline_nested_with_parallel(ctx):
    """A pipeline stage uses sub.parallel internally. Verify nested journal
    keys: stage-level parallel outer at (0, 0, 0); inner thunk's llm_call
    at (0, 0, 0, 0, 0)."""
    j = Journal(workflow_name="t")

    async def fake_llm(ctx, **kw):
        return {"echo": kw["user_msg"]}

    h = WorkflowHandle(ctx, j, llm_caller=fake_llm)

    async def inner_thunk(inner_sub):
        return await inner_sub.llm_call(prompt="deep", schema={"x": 1})

    async def stage_uses_parallel(prev, item, idx, sub):
        return await sub.parallel([inner_thunk])

    out = await h.pipeline(["only"], stage_uses_parallel)
    assert out == [[{"echo": "deep"}]]

    # Innermost llm_call at (0, 0, 0, 0, 0).
    deep = j.get((0, 0, 0, 0, 0))
    assert deep is not None
    assert deep.kind == "llm_call"
    assert deep.result == {"echo": "deep"}

    # Stage-internal parallel outer at (0, 0, 0).
    inner_outer = j.get((0, 0, 0))
    assert inner_outer is not None
    assert inner_outer.kind == "parallel"
    assert inner_outer.result == [{"echo": "deep"}]

    # Top pipeline outer at (0,).
    top = j.get((0,))
    assert top is not None
    assert top.kind == "pipeline"
    assert top.result == [[{"echo": "deep"}]]
