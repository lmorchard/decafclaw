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
    assert j.get(0).kind == "llm_call"
    assert hits == ["q1"]


@pytest.mark.asyncio
async def test_replay_returns_cached_without_executing(tmp_path):
    j = Journal(workflow_name="t")
    fp = fingerprint("llm_call", {"prompt": "q1", "schema": {}, "system": ""})
    j.append(0, "llm_call", fp, {"answer": 42})

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
    assert ei.value.seq == 0
    assert ei.value.prompt == "What topic?"


@pytest.mark.asyncio
async def test_user_input_replays_recorded_answer(tmp_path):
    j = Journal(workflow_name="t")
    fp = fingerprint("user_input", {"prompt": "What topic?", "choices": None})
    j.append(0, "user_input", fp, "tide pools")
    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=None)
    assert await h.user_input("What topic?") == "tide pools"


@pytest.mark.asyncio
async def test_determinism_guard_fires_on_divergent_args(tmp_path):
    j = Journal(workflow_name="t")
    fp = fingerprint("llm_call", {"prompt": "ORIGINAL", "schema": {}, "system": ""})
    j.append(0, "llm_call", fp, {"x": 1})

    async def fake_llm(ctx, **kw):
        return {"x": 1}

    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=fake_llm)
    with pytest.raises(WorkflowNonDeterministic):
        await h.llm_call(prompt="CHANGED", schema={}, model="m")
