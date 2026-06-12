from types import SimpleNamespace

import pytest

from decafclaw.workflow.errors import WorkflowNonDeterministic, WorkflowSuspended
from decafclaw.workflow.handle import WorkflowHandle
from decafclaw.workflow.journal import Journal, fingerprint


def _ctx(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(workspace_path=tmp_path),
                           conv_id="convX")


@pytest.mark.asyncio
async def test_llm_call_executes_live_and_journals(tmp_path):
    j = Journal(workflow_name="t")
    hits = []

    async def fake_llm(ctx, **kw):
        hits.append(kw["user_msg"])
        return {"answer": 42}

    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=fake_llm)
    out = await h.llm_call(prompt="q1", schema={}, model="m")
    assert out == {"answer": 42}
    assert j.get((0,)).kind == "llm_call"
    assert hits == ["q1"]


@pytest.mark.asyncio
async def test_replay_returns_cached_without_executing(tmp_path):
    j = Journal(workflow_name="t")
    fp = fingerprint("llm_call", {"prompt": "q1", "schema": {}, "system": ""})
    j.append((0,), "llm_call", fp, {"answer": 42})

    async def boom(ctx, **kw):
        raise AssertionError("live LLM path must NOT run during replay")

    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=boom)
    out = await h.llm_call(prompt="q1", schema={}, model="m")
    assert out == {"answer": 42}  # sabotage check: cached, not re-executed


@pytest.mark.asyncio
async def test_user_input_suspends_when_unanswered(tmp_path):
    j = Journal(workflow_name="t")
    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=None)
    with pytest.raises(WorkflowSuspended) as ei:
        await h.user_input("What topic?")
    assert ei.value.seq == (0,)
    assert ei.value.prompt == "What topic?"


@pytest.mark.asyncio
async def test_user_input_replays_recorded_answer(tmp_path):
    j = Journal(workflow_name="t")
    fp = fingerprint("user_input", {"prompt": "What topic?", "choices": None})
    j.append((0,), "user_input", fp, "tide pools")
    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=None)
    assert await h.user_input("What topic?") == "tide pools"


@pytest.mark.asyncio
async def test_determinism_guard_fires_on_divergent_args(tmp_path):
    j = Journal(workflow_name="t")
    fp = fingerprint("llm_call", {"prompt": "ORIGINAL", "schema": {}, "system": ""})
    j.append((0,), "llm_call", fp, {"x": 1})

    async def fake_llm(ctx, **kw):
        return {"x": 1}

    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=fake_llm)
    with pytest.raises(WorkflowNonDeterministic):
        await h.llm_call(prompt="CHANGED", schema={}, model="m")


def test_top_level_handle_cursor(tmp_path):
    """Top-level handle (_key_prefix=()) yields (0,), (1,), (2,) from _next_seq."""
    j = Journal(workflow_name="t")
    h = WorkflowHandle(_ctx(tmp_path), j)
    assert h._key_prefix == ()
    assert h._cursor == 0
    assert h._next_seq() == (0,)
    assert h._cursor == 1
    assert h._next_seq() == (1,)
    assert h._cursor == 2
    assert h._next_seq() == (2,)
    assert h._cursor == 3


def test_sub_handle_key_composition_single_level(tmp_path):
    """A sub-handle built from outer_seq=(5,), idx=2 has prefix (5, 2) and fresh cursor."""
    j = Journal(workflow_name="t")
    parent = WorkflowHandle(_ctx(tmp_path), j)
    sub = parent._make_subhandle_at(outer_seq=(5,), idx=2)
    assert sub._key_prefix == (5, 2)
    assert sub._cursor == 0
    assert sub._next_seq() == (5, 2, 0)
    assert sub._next_seq() == (5, 2, 1)


def test_sub_handle_key_composition_nested(tmp_path):
    """A grandchild sub-handle composes prefixes across two levels."""
    j = Journal(workflow_name="t")
    parent = WorkflowHandle(_ctx(tmp_path), j)
    child = parent._make_subhandle_at(outer_seq=(5,), idx=2)
    grandchild = child._make_subhandle_at(outer_seq=(5, 2, 0), idx=3)
    assert grandchild._key_prefix == (5, 2, 0, 3)
    assert grandchild._cursor == 0
    assert grandchild._next_seq() == (5, 2, 0, 3, 0)


def test_sub_handle_shares_ctx_journal_llm_caller_model(tmp_path):
    """Sub-handle shares ctx, journal, llm_caller, model by identity (not copies)."""
    j = Journal(workflow_name="t")
    ctx = _ctx(tmp_path)

    async def custom_llm(ctx, **kw):
        return {"ok": True}

    parent = WorkflowHandle(ctx, j, llm_caller=custom_llm, model="custom-model")
    sub = parent._make_subhandle_at(outer_seq=(0,), idx=0)
    assert sub.ctx is ctx
    assert sub.journal is j
    assert sub._llm_caller is custom_llm
    assert sub._model == "custom-model"


def test_sub_handle_cursor_independent_from_parent(tmp_path):
    """_make_subhandle_at MUST NOT advance the parent's cursor; sub starts at 0."""
    j = Journal(workflow_name="t")
    parent = WorkflowHandle(_ctx(tmp_path), j)
    parent._next_seq()  # advance parent to cursor=1
    assert parent._cursor == 1
    sub = parent._make_subhandle_at(outer_seq=(0,), idx=0)
    # Parent cursor unchanged by sub-handle creation.
    assert parent._cursor == 1
    # Sub-handle starts fresh.
    assert sub._cursor == 0


@pytest.mark.asyncio
async def test_llm_call_uses_key_prefix_via_next_seq(tmp_path):
    """A sub-handle at prefix (7,) journals its llm_call at seq (7, 0)."""
    j = Journal(workflow_name="t")

    async def fake_llm(ctx, **kw):
        return {"sentinel": "v"}

    parent = WorkflowHandle(_ctx(tmp_path), j, llm_caller=fake_llm)
    sub = parent._make_subhandle_at(outer_seq=(7,), idx=0)
    # Reset prefix to (7,) directly by constructing one explicitly: the
    # contract is that a handle whose _key_prefix is (7,) journals at (7, 0).
    sub2 = WorkflowHandle(_ctx(tmp_path), j, llm_caller=fake_llm,
                          _key_prefix=(7,))
    result = await sub2.llm_call(prompt="q", schema={})
    assert result == {"sentinel": "v"}
    entry = j.get((7, 0))
    assert entry is not None
    assert entry.kind == "llm_call"
    # And the _make_subhandle_at construction path produces the same behavior
    # at its own prefix (7, 0):
    result2 = await sub.llm_call(prompt="q2", schema={})
    assert result2 == {"sentinel": "v"}
    assert j.get((7, 0, 0)) is not None


@pytest.mark.asyncio
async def test_user_input_uses_key_prefix_via_next_seq(tmp_path):
    """A sub-handle at prefix (7,) raises WorkflowSuspended with seq (7, 0)."""
    j = Journal(workflow_name="t")
    sub = WorkflowHandle(_ctx(tmp_path), j, llm_caller=None, _key_prefix=(7,))
    with pytest.raises(WorkflowSuspended) as ei:
        await sub.user_input("Pick one")
    assert ei.value.seq == (7, 0)
    assert ei.value.prompt == "Pick one"
