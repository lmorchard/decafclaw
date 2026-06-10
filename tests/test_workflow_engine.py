from types import SimpleNamespace

import pytest

from decafclaw.workflow.engine import run_workflow
from decafclaw.workflow.journal import Journal, fingerprint


def _ctx(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(workspace_path=tmp_path),
                           conv_id="convE")


@pytest.mark.asyncio
async def test_suspends_on_first_user_input(tmp_path):
    async def wf(h):
        topic = await h.user_input("topic?")
        return {"topic": topic}

    j = Journal(workflow_name="t")
    outcome = await run_workflow(_ctx(tmp_path), wf, j)
    assert outcome.status == "suspended"
    assert outcome.suspend.prompt == "topic?"
    assert j.status == "suspended"


@pytest.mark.asyncio
async def test_completes_when_journal_fully_seeded(tmp_path):
    async def wf(h):
        topic = await h.user_input("topic?")
        return {"topic": topic, "done": True}

    j = Journal(workflow_name="t")
    fp = fingerprint("user_input", {"prompt": "topic?", "choices": None})
    j.append(0, "user_input", fp, "tide pools")

    outcome = await run_workflow(_ctx(tmp_path), wf, j)
    assert outcome.status == "done"
    assert outcome.result == {"topic": "tide pools", "done": True}
    assert j.status == "done"


@pytest.mark.asyncio
async def test_orchestrator_exception_becomes_error_outcome(tmp_path):
    async def wf(h):
        raise ValueError("boom")

    j = Journal(workflow_name="t")
    outcome = await run_workflow(_ctx(tmp_path), wf, j)
    assert outcome.status == "error"
    assert "boom" in outcome.error
    assert j.status == "error"
