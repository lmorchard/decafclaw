"""Tests for WorkflowHandle.subagent (Phase 4).

These tests mock `run_child_turn` rather than dispatching a real child
agent loop — that's integration territory and is covered by the Phase 8
live smoke. Here we exercise the journaling and fingerprint behavior.
"""
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.workflow.handle import WorkflowHandle
from decafclaw.workflow.journal import Journal, fingerprint


@pytest.mark.asyncio
async def test_subagent_live_path_text_only(ctx):
    """Without `schema`, `wf.subagent` returns the child's text and journals
    that text under kind='subagent'."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    with patch(
        "decafclaw.tools.delegate.run_child_turn",
        new_callable=AsyncMock,
        return_value=("the child said hi", None),
    ) as mock:
        out = await h.subagent("ask the child")

    assert out == "the child said hi"
    mock.assert_called_once()
    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "subagent"
    assert entry.result == "the child said hi"


@pytest.mark.asyncio
async def test_subagent_live_path_with_schema(ctx):
    """With `schema`, `wf.subagent` returns the structured dict (not text)
    and journals the dict."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j)

    structured = {"title": "X", "body": "Y"}
    with patch(
        "decafclaw.tools.delegate.run_child_turn",
        new_callable=AsyncMock,
        return_value=("ignored text", structured),
    ):
        out = await h.subagent("ask", schema={"type": "object"})

    assert out == structured
    entry = j.get((0,))
    assert entry is not None
    assert entry.kind == "subagent"
    assert entry.result == structured


@pytest.mark.asyncio
async def test_subagent_defaults_to_handle_model(ctx):
    """Without an explicit `model=`, wf.subagent passes the handle's
    configured model to run_child_turn — parallel to wf.llm_call. A bare
    "" would let run_child_turn fall through to ctx.active_model, which
    can differ from the workflow's intent."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(ctx, j, model="workflow-default-model")
    with patch(
        "decafclaw.tools.delegate.run_child_turn",
        new_callable=AsyncMock,
        return_value=("ok", None),
    ) as mock:
        await h.subagent("ask")
    assert mock.call_args.kwargs["model"] == "workflow-default-model"


@pytest.mark.asyncio
async def test_subagent_replay_path(ctx):
    """A pre-populated subagent entry is returned verbatim; the live
    `run_child_turn` MUST NOT be called during replay."""
    j = Journal(workflow_name="t")
    fp = fingerprint("subagent", {
        "prompt": "ask",
        "schema": None,
        "allowed_tools": None,
        "allow_vault_retrieval": False,
        "allow_vault_read": False,
    })
    j.append((0,), "subagent", fp, "cached answer")

    h = WorkflowHandle(ctx, j)

    def boom(*a, **kw):
        raise AssertionError("live run_child_turn MUST NOT run during replay")

    with patch("decafclaw.tools.delegate.run_child_turn", side_effect=boom):
        out = await h.subagent("ask")

    assert out == "cached answer"


@pytest.mark.asyncio
async def test_subagent_fingerprint_excludes_model(ctx):
    """`model=` is an execution detail — different per-call model values
    must NOT bust the cache. Run 1 journals with model=A; replay with model=B
    hits the cache without raising WorkflowNonDeterministic."""
    j = Journal(workflow_name="t")
    h1 = WorkflowHandle(ctx, j, model="modelA")

    with patch(
        "decafclaw.tools.delegate.run_child_turn",
        new_callable=AsyncMock,
        return_value=("first run text", None),
    ) as mock_run:
        out1 = await h1.subagent("foo", model="modelA")
    assert out1 == "first run text"
    assert mock_run.call_count == 1

    # Fresh handle with a different default model; replay against the
    # same journal. Should hit the cache, NOT call run_child_turn.
    h2 = WorkflowHandle(ctx, j, model="modelB")

    def boom(*a, **kw):
        raise AssertionError("run_child_turn must not be re-invoked")

    with patch("decafclaw.tools.delegate.run_child_turn", side_effect=boom):
        out2 = await h2.subagent("foo", model="modelB")

    assert out2 == "first run text"


@pytest.mark.asyncio
async def test_subagent_fingerprint_includes_allowed_tools_sorted(ctx):
    """`allowed_tools` ordering shouldn't affect the fingerprint — replay
    determinism must survive a caller reshuffling the list."""
    j1 = Journal(workflow_name="t")
    h1 = WorkflowHandle(ctx, j1)

    with patch(
        "decafclaw.tools.delegate.run_child_turn",
        new_callable=AsyncMock,
        return_value=("ok", None),
    ):
        await h1.subagent("foo", allowed_tools=["a", "b"])
    fp_ab = j1.get((0,)).args_fingerprint

    j2 = Journal(workflow_name="t")
    h2 = WorkflowHandle(ctx, j2)
    with patch(
        "decafclaw.tools.delegate.run_child_turn",
        new_callable=AsyncMock,
        return_value=("ok", None),
    ):
        await h2.subagent("foo", allowed_tools=["b", "a"])
    fp_ba = j2.get((0,)).args_fingerprint

    assert fp_ab == fp_ba


@pytest.mark.asyncio
async def test_subagent_fingerprint_differs_with_schema(ctx):
    """Same prompt but different schema → different fingerprint, so the
    cache doesn't return the no-schema result when a schema is requested."""
    j1 = Journal(workflow_name="t")
    h1 = WorkflowHandle(ctx, j1)
    with patch(
        "decafclaw.tools.delegate.run_child_turn",
        new_callable=AsyncMock,
        return_value=("ok", None),
    ):
        await h1.subagent("foo")
    fp_no_schema = j1.get((0,)).args_fingerprint

    j2 = Journal(workflow_name="t")
    h2 = WorkflowHandle(ctx, j2)
    with patch(
        "decafclaw.tools.delegate.run_child_turn",
        new_callable=AsyncMock,
        return_value=("ok", {"x": 1}),
    ):
        await h2.subagent("foo", schema={"x": 1})
    fp_with_schema = j2.get((0,)).args_fingerprint

    assert fp_no_schema != fp_with_schema


@pytest.mark.asyncio
async def test_subagent_uses_sub_handle_key_prefix(ctx):
    """A sub-handle at prefix (3,) journals its subagent call at seq (3, 0)."""
    j = Journal(workflow_name="t")
    sub = WorkflowHandle(ctx, j, _key_prefix=(3,))

    with patch(
        "decafclaw.tools.delegate.run_child_turn",
        new_callable=AsyncMock,
        return_value=("child text", None),
    ):
        out = await sub.subagent("go")

    assert out == "child text"
    entry = j.get((3, 0))
    assert entry is not None
    assert entry.kind == "subagent"
