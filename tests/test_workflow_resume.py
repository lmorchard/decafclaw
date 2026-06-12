from types import SimpleNamespace

import pytest

from decafclaw.confirmations import (
    ConfirmationAction,
    ConfirmationRequest,
    ConfirmationResponse,
)
from decafclaw.workflow.journal import (
    Journal,
    fingerprint,
    load_journal,
    save_journal,
)


def test_workflow_user_input_action_exists():
    assert ConfirmationAction.WORKFLOW_USER_INPUT.value == "workflow_user_input"


async def _noop(*args, **kwargs):
    pass


def _ctx(tmp_path, conv_id="convR"):
    return SimpleNamespace(config=SimpleNamespace(workspace_path=tmp_path),
                           conv_id=conv_id,
                           publish=_noop)


@pytest.mark.asyncio
async def test_handler_journals_answer_and_enqueues_resume(tmp_path):
    from decafclaw.workflow.resume import WorkflowUserInputHandler

    cfg = SimpleNamespace(workspace_path=tmp_path)
    j = Journal(workflow_name="interview", status="suspended")
    save_journal(cfg, "convR", j)

    fp = fingerprint("user_input",
                     {"prompt": "What should this interview be about?",
                      "choices": None})
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="What should this interview be about?",
        action_data={"workflow_name": "interview", "seq": "0",
                     "args_fingerprint": fp, "prompt": "...", "choices": None},
        timeout=None,
    )
    response = ConfirmationResponse(confirmation_id="x", approved=True,
                                    data={"value": "tide pools"})

    enqueued = []

    class FakeManager:
        config = cfg
        async def enqueue_turn(self, conv_id, **kw):
            enqueued.append((conv_id, kw))

    handler = WorkflowUserInputHandler(FakeManager())
    out = await handler.on_approve(_ctx(tmp_path), request, response)

    reloaded = load_journal(cfg, "convR")
    assert reloaded.get((0,)).kind == "user_input"
    assert reloaded.get((0,)).result == "tide pools"
    assert reloaded.status == "running"
    assert enqueued and enqueued[0][1]["metadata"]["resume"] is True
    assert enqueued[0][1]["metadata"]["workflow_name"] == "interview"
    assert out == {"continue_loop": False}


@pytest.mark.asyncio
async def test_run_workflow_turn_fresh_start_suspends_and_posts(tmp_path):
    import decafclaw.workflow.workflows  # noqa: F401 — register interview
    from decafclaw.workflow.resume import run_workflow_turn

    posted = []

    class FakeManager:
        config = SimpleNamespace(workspace_path=tmp_path)
        async def post_confirmation(self, conv_id, request):
            posted.append(request)

    ctx = _ctx(tmp_path, "convT")
    result = await run_workflow_turn(
        ctx, FakeManager(),
        workflow_name="interview", resume=False)
    assert posted, "a confirmation should be posted on suspend"
    assert posted[0].action_type == ConfirmationAction.WORKFLOW_USER_INPUT
    # action_data carries the tuple-path seq as a dotted string for JSON safety.
    assert posted[0].action_data["seq"] == "0"
    assert posted[0].action_data["workflow_name"] == "interview"
    from decafclaw.media import ToolResult
    assert isinstance(result, ToolResult)
    assert result.text.startswith("_")  # italic-wrapped prompt


@pytest.mark.asyncio
async def test_run_workflow_turn_done_archives_artifact(config):
    """On status=done, run_workflow_turn must persist the rendered artifact
    as a role=assistant archive row, so a conversation reload still has the
    final output visible (verification finding #2 for PR #573)."""
    from decafclaw.archive import read_archive
    from decafclaw.workflow.journal import Journal, save_journal
    from decafclaw.workflow.registry import workflow as register_workflow
    from decafclaw.workflow.resume import run_workflow_turn

    conv_id = "convDone"

    @register_workflow("artifact_done_test")
    async def _wf(wf):
        return {"title": "Tide Pools", "body": "Brief notes."}

    # Pre-seed an empty journal so run_workflow_turn treats this as a resume
    # path without needing user_input — the workflow returns immediately.
    save_journal(config, conv_id, Journal(workflow_name="artifact_done_test"))

    from types import SimpleNamespace

    async def _noop(*args, **kwargs):
        pass

    class FakeManager:
        async def post_confirmation(self, conv_id, request):
            raise AssertionError("done path should not post a confirmation")

    ctx = SimpleNamespace(config=config, conv_id=conv_id, publish=_noop)
    result = await run_workflow_turn(
        ctx, FakeManager(),
        workflow_name="artifact_done_test", resume=True)
    assert "Tide Pools" in result.text

    msgs = read_archive(config, conv_id)
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
    assert assistant_msgs, (
        "expected a role=assistant archive row for the workflow artifact"
    )
    assert "Tide Pools" in (assistant_msgs[-1].get("content") or "")


@pytest.mark.asyncio
async def test_handler_deny_marks_error_and_archives(tmp_path):
    from decafclaw.workflow.resume import WorkflowUserInputHandler

    cfg = SimpleNamespace(workspace_path=tmp_path)
    j = Journal(workflow_name="interview", status="suspended")
    save_journal(cfg, "convD", j)
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="q?", action_data={"workflow_name": "interview", "seq": 0,
        "args_fingerprint": "fp", "prompt": "q?", "choices": None}, timeout=None)
    response = ConfirmationResponse(confirmation_id="x", approved=False)

    class FakeManager:
        config = cfg
        async def enqueue_turn(self, conv_id, **kw):
            raise AssertionError("deny must NOT enqueue a resume turn")

    handler = WorkflowUserInputHandler(FakeManager())
    out = await handler.on_deny(_ctx(tmp_path, "convD"), request, response)
    assert out == {"continue_loop": False}
    assert load_journal(cfg, "convD").status == "error"
