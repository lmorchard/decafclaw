"""Integration tests for workflow turn support in ConversationManager."""

import pytest

from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus


@pytest.fixture
def make_manager(config):
    """Factory fixture: returns a callable that builds a ConversationManager
    pointing at the test tmp workspace."""
    def _make():
        return ConversationManager(config=config, event_bus=EventBus())
    return _make


@pytest.mark.asyncio
async def test_post_confirmation_sets_pending_without_waiter(make_manager):
    manager = make_manager()
    conv_id = "convP"
    req = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="topic?", action_data={"seq": 0}, timeout=None)

    await manager.post_confirmation(conv_id, req)

    state = manager._conversations[conv_id]
    assert state.pending_confirmation is req
    assert state.confirmation_event is None  # forces recovery dispatch
    assert state.confirmation_response is None


@pytest.mark.asyncio
async def test_post_confirmation_raises_when_slot_busy(make_manager):
    import pytest
    manager = make_manager()
    conv_id = "convBusy"
    req = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="q1?", action_data={"seq": 0}, timeout=None)
    await manager.post_confirmation(conv_id, req)
    req2 = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="q2?", action_data={"seq": 1}, timeout=None)
    with pytest.raises(RuntimeError):
        await manager.post_confirmation(conv_id, req2)


@pytest.mark.asyncio
async def test_post_confirmation_busy_raise_does_not_leave_archive_orphan(
        make_manager, config):
    """Regression: if post_confirmation raises on a busy slot, the rejected
    request must NOT appear in the archive. Otherwise startup_scan would
    recover it as a ghost pending confirmation with no backing workflow
    (Copilot review on PR #573)."""
    import pytest

    from decafclaw.archive import read_archive
    manager = make_manager()
    conv_id = "convOrphan"

    first = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="first", action_data={"seq": 0}, timeout=None)
    await manager.post_confirmation(conv_id, first)

    rejected = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="rejected", action_data={"seq": 1}, timeout=None)
    with pytest.raises(RuntimeError):
        await manager.post_confirmation(conv_id, rejected)

    msgs = read_archive(config, conv_id)
    req_rows = [m for m in msgs if m.get("role") == "confirmation_request"]
    assert len(req_rows) == 1, (
        f"expected only the first (installed) confirmation_request in archive; "
        f"the busy-raised request must not leak. Found {len(req_rows)} rows: "
        f"{[r.get('confirmation_id') for r in req_rows]}"
    )
    assert req_rows[0].get("confirmation_id") == first.confirmation_id


@pytest.mark.asyncio
async def test_workflow_turn_runs_and_suspends_end_to_end(make_manager):
    """Enqueue an interview WORKFLOW turn → it suspends, posting a
    WORKFLOW_USER_INPUT confirmation for the topic question."""
    import decafclaw.workflow.workflows  # noqa: F401 — register interview
    from decafclaw.conversation_manager import TurnKind
    manager = make_manager()
    conv_id = "convWF"

    fut = await manager.enqueue_turn(
        conv_id, kind=TurnKind.WORKFLOW, prompt="",
        metadata={"workflow_name": "interview", "resume": False})
    await fut  # wait for the turn to finish (it suspends, then ends)

    state = manager._conversations[conv_id]
    assert state.pending_confirmation is not None
    from decafclaw.confirmations import ConfirmationAction
    assert state.pending_confirmation.action_type is ConfirmationAction.WORKFLOW_USER_INPUT
    assert state.pending_confirmation.action_data["workflow_name"] == "interview"
    assert state.pending_confirmation.action_data["seq"] == "0"


@pytest.mark.asyncio
async def test_durable_resume_after_simulated_restart(tmp_path):
    """A journal persisted mid-suspend resumes from a freshly reconstructed
    engine (simulating a server restart) with no lost state: the journaled
    answer is replayed (the user is NOT re-asked) and the workflow continues
    to completion."""
    from types import SimpleNamespace

    import decafclaw.workflow.workflows  # noqa: F401 — register interview
    from decafclaw.workflow.engine import run_workflow
    from decafclaw.workflow.journal import Journal, load_journal, save_journal
    from decafclaw.workflow.registry import get_workflow

    cfg = SimpleNamespace(workspace_path=tmp_path)
    conv_id = "convRestart"

    def fresh_ctx():
        # A brand-new ctx each "process" — nothing carried in memory.
        return SimpleNamespace(config=cfg, conv_id=conv_id)

    spec = get_workflow("interview")

    # --- Process 1: start; suspends at the topic question; journal hits disk ---
    out1 = await run_workflow(fresh_ctx(), spec.fn, Journal(workflow_name="interview"))
    assert out1.status == "suspended"
    assert out1.suspend.seq == (0,)

    # --- "Restart": reconstruct ONLY from the on-disk journal ---
    reloaded = load_journal(cfg, conv_id)
    assert reloaded is not None
    assert reloaded.status == "suspended"
    assert len(reloaded.entries) == 0  # nothing journaled yet for the unanswered input

    # --- The resume handler would journal the user's answer at the suspend seq ---
    reloaded.append(out1.suspend.seq, "user_input",
                    out1.suspend.args_fingerprint, "tide pools")
    reloaded.status = "running"
    save_journal(cfg, conv_id, reloaded)

    # --- Process 2 (fresh engine): reload from disk, replay + continue live ---
    live_calls = []

    async def fake_llm(ctx, **kw):
        live_calls.append(kw)
        # One canned dict satisfies BOTH the decision schema {done, question}
        # and the synth schema {title, body}: done=True ends the loop, then the
        # synth call returns the artifact.
        return {"done": True, "question": "",
                "title": "Tide Pools", "body": "A brief."}

    j2 = load_journal(cfg, conv_id)
    out2 = await run_workflow(fresh_ctx(), spec.fn, j2, llm_caller=fake_llm)

    assert out2.status == "done"
    assert out2.result["title"] == "Tide Pools"
    # The recovered answer was REPLAYED from the journal (user_input at seq 0 was
    # NOT re-raised as a suspension), and only llm_calls ran live:
    final = load_journal(cfg, conv_id)
    assert final.get((0,)).kind == "user_input"
    assert final.get((0,)).result == "tide pools"
    # Live calls were the decision + synth llm_calls only (2), never a user_input.
    # Trace: seq0 user_input → cached (no live call); seq1 llm_call decision →
    # live (done=True, break); seq2 llm_call synth → live (artifact). Total: 2.
    assert len(live_calls) == 2
    # The replayed topic actually flowed into the LLM prompts (guards against a
    # wrong-replay-value regression that fake_llm would otherwise mask).
    assert any("tide pools" in (c.get("user_msg") or "") for c in live_calls)
