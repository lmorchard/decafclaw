"""Conversation-manager confirmation tests: request/response lifecycle, archive
persistence and startup recovery, dispatch races and rollback, and the
confirmation queue.

Split from the former monolithic ``test_conversation_manager.py`` by concern
(#1421)."""

import asyncio

import pytest

from decafclaw.confirmations import (
    ConfirmationAction,
    ConfirmationRequest,
    ConfirmationResponse,
)
from decafclaw.context import TaskMode
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus


@pytest.fixture
def manager(config):
    bus = EventBus()
    return ConversationManager(config, bus)


# -- Confirmation request/response --------------------------------------------


@pytest.mark.asyncio
async def test_request_confirmation_approved(manager):
    conv_id = "conv-1"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow shell command?",
        timeout=2.0,
    )

    # Approve after a short delay
    async def approve():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True)

    asyncio.create_task(approve())
    response = await manager.request_confirmation(conv_id, request)

    assert response.approved is True
    assert response.confirmation_id == request.confirmation_id
    # Pending state should be cleared
    state = manager.get_state(conv_id)
    assert state.pending_confirmation is None


@pytest.mark.asyncio
async def test_request_confirmation_denied(manager):
    conv_id = "conv-1"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.ACTIVATE_SKILL,
        action_data={"skill_name": "test"},
        message="Activate skill?",
        timeout=2.0,
    )

    async def deny():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=False)

    asyncio.create_task(deny())
    response = await manager.request_confirmation(conv_id, request)

    assert response.approved is False


@pytest.mark.asyncio
async def test_request_confirmation_carries_feedback(manager):
    """A denial's feedback reaches the waiting loop (#1559)."""
    conv_id = "conv-1"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "grep -rn TODO src/", "purpose": "Find TODOs"},
        message="Allow shell command?",
        timeout=2.0,
    )

    async def deny_with_feedback():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(
            conv_id,
            request.confirmation_id,
            approved=False,
            feedback="Use workspace_search instead.",
        )

    asyncio.create_task(deny_with_feedback())
    response = await manager.request_confirmation(conv_id, request)

    assert response.approved is False
    assert response.feedback == "Use workspace_search instead."


@pytest.mark.asyncio
async def test_request_confirmation_timeout(manager):
    conv_id = "conv-1"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.CONTINUE_TURN,
        message="Continue?",
        timeout=0.1,
    )

    response = await manager.request_confirmation(conv_id, request)
    assert response.approved is False


@pytest.mark.asyncio
async def test_request_confirmation_fails_fast_on_unattended_turn(ctx):
    """Tools requesting confirmation during unattended turns fail fast with denial (#1119)."""
    from decafclaw.tools.confirmation import request_confirmation

    ctx.task_mode = TaskMode.BACKGROUND_WAKE
    assert ctx.is_unattended is True

    result = await request_confirmation(ctx, "shell", "rm -rf /", "Allow delete?")
    assert result["approved"] is False
    assert "interactive confirmations are not available on unattended turns" in result["reason"]


@pytest.mark.asyncio
async def test_confirmation_emits_request_event(manager):
    conv_id = "conv-1"
    events = []

    async def cb(event):
        events.append(event)

    manager.subscribe(conv_id, cb)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
        timeout=0.1,
    )

    await manager.request_confirmation(conv_id, request)

    # Should have emitted confirmation_request
    req_events = [e for e in events if e["type"] == "confirmation_request"]
    assert len(req_events) == 1
    assert req_events[0]["confirmation_id"] == request.confirmation_id
    assert req_events[0]["message"] == "Allow?"


@pytest.mark.asyncio
async def test_confirmation_persisted_to_archive(manager):
    from decafclaw.archive import read_archive

    conv_id = "conv-persist"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
        timeout=2.0,
    )

    async def approve():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True)

    asyncio.create_task(approve())
    await manager.request_confirmation(conv_id, request)

    # Check archive has both request and response
    messages = read_archive(manager.config, conv_id)
    roles = [m["role"] for m in messages]
    assert "confirmation_request" in roles
    assert "confirmation_response" in roles


def test_confirmation_response_data_serialization_roundtrip():
    """ConfirmationResponse.data is serialized when non-empty and
    deserialized cleanly, including nested structures."""
    resp = ConfirmationResponse(
        confirmation_id="abc",
        approved=True,
        data={"selected": ["a", "b"], "meta": {"ts": 123}},
    )
    msg = resp.to_archive_message()
    assert msg["data"] == {"selected": ["a", "b"], "meta": {"ts": 123}}

    restored = ConfirmationResponse.from_archive_message(msg)
    assert restored.data == resp.data
    assert restored.approved is True


def test_confirmation_response_data_omitted_when_empty():
    """Empty data dict stays out of the archive message to keep
    existing confirmations unchanged on the wire."""
    resp = ConfirmationResponse(confirmation_id="abc", approved=True)
    msg = resp.to_archive_message()
    assert "data" not in msg

    restored = ConfirmationResponse.from_archive_message(msg)
    assert restored.data == {}


@pytest.mark.asyncio
async def test_respond_with_data_field_roundtrips(manager):
    """Widget-shaped responses carry a `data` dict; verify it surfaces
    on the response dataclass, the emitted event, and the archive."""
    from decafclaw.archive import read_archive

    conv_id = "conv-data"
    manager._get_or_create(conv_id)

    events: list[dict] = []

    async def cb(event):
        events.append(event)

    manager.subscribe(conv_id, cb)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.WIDGET_RESPONSE,
        action_data={"widget_type": "multiple_choice"},
        message="",
        timeout=2.0,
    )

    async def respond():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True, data={"selected": "production"}
        )

    asyncio.create_task(respond())
    response = await manager.request_confirmation(conv_id, request)

    assert response.data == {"selected": "production"}

    resp_events = [e for e in events if e.get("type") == "confirmation_response"]
    assert resp_events
    assert resp_events[0]["data"] == {"selected": "production"}

    messages = read_archive(manager.config, conv_id)
    response_msgs = [m for m in messages if m.get("role") == "confirmation_response"]
    assert response_msgs
    assert response_msgs[0]["data"] == {"selected": "production"}


@pytest.mark.asyncio
async def test_request_confirmation_no_timeout(manager):
    """timeout=None disables the await deadline — used by widget requests."""
    conv_id = "conv-no-timeout"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.WIDGET_RESPONSE,
        message="",
        timeout=None,
    )

    async def respond_after_delay():
        # Sleep longer than any reasonable default timeout would be,
        # to prove that None-timeout doesn't raise TimeoutError.
        await asyncio.sleep(0.1)
        await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True, data={"selected": "x"})

    asyncio.create_task(respond_after_delay())
    response = await manager.request_confirmation(conv_id, request)
    assert response.approved is True
    assert response.data == {"selected": "x"}


@pytest.mark.asyncio
async def test_respond_wrong_id_ignored(manager):
    conv_id = "conv-1"
    state = manager._get_or_create(conv_id)
    state.pending_confirmation = ConfirmationRequest(
        action_type=ConfirmationAction.CONTINUE_TURN,
        message="test",
        confirmation_id="correct-id",
    )
    state.confirmation_event = asyncio.Event()

    await manager.respond_to_confirmation(conv_id, "wrong-id", approved=True)

    # Event should NOT be set
    assert not state.confirmation_event.is_set()


@pytest.mark.asyncio
async def test_always_field_in_confirmation_response(manager):
    conv_id = "conv-1"
    manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.ACTIVATE_SKILL,
        action_data={"skill_name": "test"},
        message="Activate?",
        timeout=2.0,
    )

    async def approve_always():
        await asyncio.sleep(0.05)
        await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True, always=True)

    asyncio.create_task(approve_always())
    response = await manager.request_confirmation(conv_id, request)

    assert response.approved is True
    assert response.always is True


# -- Startup recovery ----------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_scan_finds_pending_confirmation(manager):
    """Startup scan should find conversations with unresolved confirmation requests."""
    from decafclaw.archive import append_message

    conv_id = "conv-recovery"
    # Write a confirmation request to the archive (no response)
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls -la"},
        message="Allow shell command?",
    )
    append_message(manager.config, conv_id, request.to_archive_message())

    recovered = await manager.startup_scan()
    assert recovered == 1

    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is not None
    assert state.pending_confirmation.confirmation_id == request.confirmation_id
    assert state.pending_confirmation.action_type == ConfirmationAction.RUN_SHELL_COMMAND


@pytest.mark.asyncio
async def test_startup_scan_ignores_resolved_confirmations(manager):
    """Startup scan should not recover confirmations that have a response."""
    from decafclaw.archive import append_message

    conv_id = "conv-resolved"
    request = ConfirmationRequest(
        action_type=ConfirmationAction.ACTIVATE_SKILL,
        action_data={"skill_name": "test"},
        message="Activate?",
    )
    response = ConfirmationResponse(
        confirmation_id=request.confirmation_id,
        approved=True,
    )
    append_message(manager.config, conv_id, request.to_archive_message())
    append_message(manager.config, conv_id, response.to_archive_message())

    recovered = await manager.startup_scan()
    assert recovered == 0


@pytest.mark.asyncio
async def test_startup_scan_ignores_stale_confirmations(manager):
    """Confirmations older than 24 hours should be ignored."""
    from decafclaw.archive import append_message

    conv_id = "conv-stale"
    request = ConfirmationRequest(
        action_type=ConfirmationAction.CONTINUE_TURN,
        message="Continue?",
        timestamp="2020-01-01T00:00:00",  # very old
    )
    append_message(manager.config, conv_id, request.to_archive_message())

    recovered = await manager.startup_scan()
    assert recovered == 0


@pytest.mark.asyncio
async def test_startup_scan_empty_archive(manager):
    """Startup scan with no archives should recover nothing."""
    recovered = await manager.startup_scan()
    assert recovered == 0


@pytest.mark.asyncio
async def test_respond_to_confirmation_waits_for_startup_recovery(manager):
    """A response from a restored UI waits until recovery repopulates state (#1645)."""
    manager.begin_startup_recovery()

    task = asyncio.create_task(manager.respond_to_confirmation("conv-late", "missing-id", approved=True))
    await asyncio.sleep(0)
    assert not task.done()

    manager.finish_startup_recovery()
    await asyncio.wait_for(task, timeout=2.0)


# -- Responding to recovered confirmations-------------------------------------


@pytest.mark.asyncio
async def test_respond_to_recovered_confirmation(manager):
    """Responding to a recovered confirmation (no running loop) dispatches recovery."""
    from decafclaw.archive import append_message

    conv_id = "conv-recover-respond"
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    append_message(manager.config, conv_id, request.to_archive_message())

    await manager.startup_scan()

    # Respond — should dispatch recovery (no running loop)
    await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True)

    # Pending confirmation should be cleared
    state = manager.get_state(conv_id)
    assert state.pending_confirmation is None


async def test_respond_to_recovered_admin_mutation(manager):
    """Recovering an admin mutation applies changes and clears pending confirmation."""
    from decafclaw.archive import append_message

    conv_id = "conv-recover-admin-mutation"
    manager.config.agent_path.mkdir(parents=True, exist_ok=True)
    target = manager.config.agent_path / "test_recovered_mgr.txt"
    target.write_text("before recovery")

    request = ConfirmationRequest(
        action_type=ConfirmationAction.ADMIN_MUTATION,
        action_data={
            "tool_name": "admin_write",
            "path": "test_recovered_mgr.txt",
            "payload": {"content": "after recovery"},
            "snapshot": {
                "resolved": str(target.resolve()),
                "exists": True,
                "is_dir": False,
                "content": "before recovery",
            },
        },
        message="Approve write?",
    )
    append_message(manager.config, conv_id, request.to_archive_message())

    await manager.startup_scan()

    state = manager.get_state(conv_id)
    assert state.pending_confirmation is not None

    await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True)

    assert state.pending_confirmation is None
    assert target.read_text() == "after recovery"


# -- Confirmation request event publication------------------------------------


@pytest.mark.asyncio
async def test_fallback_confirmation_request_includes_conv_id(manager, config):
    """Fallback confirmation_request published on event bus must include conv_id (#1119 review)."""
    from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest

    conv_id = "conv-conf-fallback"
    state = manager._get_or_create(conv_id)
    state.last_transport = "mattermost"
    state.last_channel_id = "ch-conf-88"
    state.last_thread_id = "th-conf-99"

    received_events = []
    manager.event_bus.subscribe(lambda e: received_events.append(e))

    req = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls -la"},
    )
    # emit with no per-conversation subscribers
    await manager._emit_confirmation_request(state, conv_id, req)

    confs = [e for e in received_events if e.get("type") == "confirmation_request"]
    assert len(confs) == 1
    assert confs[0]["conv_id"] == conv_id
    assert confs[0]["channel_id"] == "ch-conf-88"
    assert confs[0]["thread_id"] == "th-conf-99"
    assert confs[0]["transport"] == "mattermost"


@pytest.mark.asyncio
async def test_confirmation_request_published_to_bus_with_subscriber(manager, config):
    """Out-of-band channels (ntfy, #1494) need confirmation events on the
    global bus even when the web UI holds a per-conversation subscription.

    The Mattermost-only extras stay on the no-subscriber fallback path."""
    from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest

    conv_id = "conv-conf-global"
    state = manager._get_or_create(conv_id)
    state.subscribers["web"] = lambda event: None

    received_events = []
    manager.event_bus.subscribe(lambda e: received_events.append(e))

    req = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
    )
    await manager._emit_confirmation_request(state, conv_id, req)

    confs = [e for e in received_events if e.get("type") == "confirmation_request"]
    assert len(confs) == 1
    assert confs[0]["conv_id"] == conv_id
    assert confs[0]["confirmation_id"] == req.confirmation_id
    assert "transport" not in confs[0]


# -- Concurrency: per-conv lock (issue #440) ----------------------------------


@pytest.mark.asyncio
async def test_concurrent_confirmation_responses_dont_double_dispatch(manager, monkeypatch):
    """Two simultaneous respond_to_confirmation calls for the same
    confirmation_id must not both dispatch recovery. Regression for
    issue #440 — the state.pending_confirmation / state.confirmation_event
    mutations used to be unlocked, so two responses could both observe
    ``pending_confirmation`` set, both pass the id check, and both
    dispatch the registered handler.
    """
    from decafclaw.archive import append_message

    conv_id = "conv-conf-race"
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    # Pre-populate pending state on a recovered conv (no running loop)
    # so respond_to_confirmation goes through the recovery path.
    append_message(manager.config, conv_id, request.to_archive_message())
    await manager.startup_scan()

    handler_calls = 0

    class FakeHandler:
        async def on_approve(self, ctx, req, resp):
            nonlocal handler_calls
            handler_calls += 1
            # Yield so the second responder can race past
            # pending_confirmation check before this one clears it.
            await asyncio.sleep(0)
            return {"ok": True}

        async def on_deny(self, ctx, req, resp):
            return {}

    manager.confirmation_registry.register(ConfirmationAction.RUN_SHELL_COMMAND, FakeHandler())

    await asyncio.gather(
        manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True),
        manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True),
    )

    assert handler_calls == 1, f"expected exactly one handler dispatch, got {handler_calls}"
    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is None


@pytest.mark.asyncio
async def test_cancel_pending_confirmation_after_response_claimed_is_noop(
    manager,
):
    """If `respond_to_confirmation` has already claimed
    `state.confirmation_response`, a racing
    `cancel_pending_confirmation` must NOT clear it — otherwise
    `request_confirmation`'s success path would observe the slot
    nulled out and persist a denial that contradicts the already-
    archived approval. (Copilot review on PR #484.)
    """
    conv_id = "conv-cancel-race"
    state = manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    response = ConfirmationResponse(
        confirmation_id=request.confirmation_id,
        approved=True,
    )
    # Simulate `respond_to_confirmation` having claimed the slot.
    state.pending_confirmation = request
    state.confirmation_event = asyncio.Event()
    state.confirmation_response = response

    cleared = await manager.cancel_pending_confirmation(conv_id)
    assert cleared is False, "cancel_pending_confirmation should defer to the claimed response and return False"
    # Slot must still be claimed for request_confirmation to find.
    assert state.confirmation_response is response
    assert state.pending_confirmation is request


@pytest.mark.asyncio
async def test_request_confirmation_timeout_loses_race_to_late_responder(manager, monkeypatch):
    """A responder that wins the race against `request_confirmation`'s
    timeout — claiming `state.confirmation_response` between
    `wait_for` raising TimeoutError and the timeout-path archive
    write — must have its response honored. The timeout path must
    NOT archive a denial that contradicts the responder's answer.
    (Copilot review on PR #484.)

    We deterministically force the race by patching `asyncio.wait_for`
    to raise TimeoutError only AFTER the responder has run and
    claimed the slot. This exercises the exact post-wait code path
    the fix protects, rather than relying on wall-clock timing.
    """
    from decafclaw import conversation_manager as cm

    conv_id = "conv-timeout-race"

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
        timeout=10.0,  # large; we control timeout via the patched wait_for
    )

    responder_ran = asyncio.Event()

    async def respond_when_signaled():
        # Wait until request_confirmation is in `wait_for` (set up by
        # the patched wait_for below), then claim the slot.
        await responder_ran.wait()
        await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True)

    async def fake_wait_for(fut, timeout):
        # Close the underlying coroutine without awaiting it — we're
        # simulating a timeout, so the wait never completes. Closing
        # avoids the "coroutine never awaited" RuntimeWarning.
        if asyncio.iscoroutine(fut):
            fut.close()
        # Release the responder, then yield enough times so it
        # acquires the lock and claims state.confirmation_response.
        # Finally raise TimeoutError — putting request_confirmation's
        # post-wait block on the timeout path with the slot already
        # claimed.
        responder_ran.set()
        for _ in range(5):
            await asyncio.sleep(0)
        raise asyncio.TimeoutError()

    monkeypatch.setattr(cm.asyncio, "wait_for", fake_wait_for)

    responder_task = asyncio.create_task(respond_when_signaled())
    response = await manager.request_confirmation(conv_id, request)
    await responder_task

    state = manager.get_state(conv_id)
    assert state is not None
    assert state.confirmation_response is None
    assert state.pending_confirmation is None

    # The responder claimed the slot first, so request_confirmation's
    # timeout path honored their approval rather than synthesizing a
    # denial.
    assert response.approved is True, (
        "responder claimed slot under the lock before the timeout path "
        "ran; request_confirmation must honor that response, not "
        f"synthesize a denial. Got: {response}"
    )

    # Archive must have exactly one confirmation_response row (the
    # responder's approval) — never both a timeout denial and an
    # approval.
    from decafclaw.archive import restore_history

    history = restore_history(manager.config, conv_id) or []
    responses = [m for m in history if m.get("role") == "confirmation_response"]
    assert len(responses) == 1, (
        f"expected exactly one confirmation_response in archive, got {len(responses)}: {responses}"
    )
    assert responses[0]["approved"] is True


@pytest.mark.asyncio
async def test_respond_to_confirmation_rolls_back_claim_on_archive_failure(manager, monkeypatch):
    """If `append_message` raises while `respond_to_confirmation`
    is dispatching, the response-slot claim must NEVER be written
    so a retry isn't treated as a duplicate and a running waiter
    doesn't hang until timeout. Archive write now happens UNDER the
    lock BEFORE the claim is set; on failure the raise propagates
    with pending state untouched. Emit happens after the lock and
    is best-effort — emit failures are logged but the durable
    record + claim are already in place, so this test does NOT
    cover the emit-failure path (which is intentionally non-
    rollback). (Copilot review on PR #484.)
    """
    conv_id = "conv-rollback-test"
    state = manager._get_or_create(conv_id)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    # Set up pending state as if request_confirmation had run.
    state.pending_confirmation = request
    state.confirmation_event = asyncio.Event()
    state.confirmation_response = None

    def boom(*args, **kwargs):
        raise RuntimeError("archive write failed")

    # respond_to_confirmation does a function-level `from .archive
    # import append_message`, so patch the source module.
    monkeypatch.setattr("decafclaw.archive.append_message", boom)

    with pytest.raises(RuntimeError, match="archive write failed"):
        await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True)

    # Claim must be rolled back so retry works.
    assert state.confirmation_response is None, (
        "respond_to_confirmation must roll back its claim on I/O "
        "failure; otherwise a retry would be silently dropped as a "
        "duplicate."
    )
    # Pending request must still be there for the retry.
    assert state.pending_confirmation is request
    # Event must NOT be set — running waiter is still waiting and
    # the retry will signal it.
    assert state.confirmation_event is not None
    assert not state.confirmation_event.is_set()


@pytest.mark.asyncio
async def test_cancel_pending_confirmation_rolls_back_on_archive_failure(manager, monkeypatch):
    """If `append_message` fails during
    `cancel_pending_confirmation`, the in-memory pending state must
    not be cleared — otherwise the manager would be left with no
    pending confirmation and no archive denial, leaving the user
    unable to retry and startup recovery seeing the request as
    still unresolved. (Copilot review on PR #484.)
    """
    conv_id = "conv-cancel-rollback"
    state = manager._get_or_create(conv_id)
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    state.pending_confirmation = request
    state.confirmation_event = asyncio.Event()
    state.confirmation_response = None

    def boom(*args, **kwargs):
        raise RuntimeError("archive write failed")

    monkeypatch.setattr("decafclaw.archive.append_message", boom)

    with pytest.raises(RuntimeError, match="archive write failed"):
        await manager.cancel_pending_confirmation(conv_id)

    # In-memory state must be preserved so retry can persist a denial.
    assert state.pending_confirmation is request
    assert state.confirmation_response is None
    assert state.confirmation_event is not None


@pytest.mark.asyncio
async def test_recover_confirmation_restores_on_dispatch_failure(manager):
    """If `_dispatch_recovery` raises (e.g. the registered handler
    fails), `recover_confirmation` must restore
    `state.pending_confirmation` so a future retry / startup
    recovery can pick up where dispatch left off. Otherwise the
    request would be silently lost from memory while remaining
    unresolved in the archive. (Copilot review on PR #484.)
    """
    from decafclaw.archive import append_message

    conv_id = "conv-recover-rollback"
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    append_message(manager.config, conv_id, request.to_archive_message())
    await manager.startup_scan()
    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is not None

    class BoomHandler:
        async def on_approve(self, ctx, req, resp):
            raise RuntimeError("handler exploded")

        async def on_deny(self, ctx, req, resp):
            return {}

    manager.confirmation_registry.register(ConfirmationAction.RUN_SHELL_COMMAND, BoomHandler())

    response = ConfirmationResponse(
        confirmation_id=request.confirmation_id,
        approved=True,
    )
    with pytest.raises(RuntimeError, match="handler exploded"):
        await manager.recover_confirmation(conv_id, response)

    # Pending confirmation must be restored so the next retry can
    # try dispatch again.
    assert state.pending_confirmation is not None
    assert state.pending_confirmation.confirmation_id == request.confirmation_id


@pytest.mark.asyncio
async def test_request_confirmation_timeout_archive_failure_preserves_state(manager, monkeypatch):
    """If the timeout-path archive write fails inside
    `request_confirmation`, the in-memory pending state must NOT be
    cleared — otherwise the request would be lost in memory while
    remaining unresolved in the archive, leaving no way to recover.
    (Copilot review on PR #484.)
    """
    from decafclaw import conversation_manager as cm

    conv_id = "conv-timeout-archive-fail"

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
        timeout=10.0,
    )

    # Force timeout via patched wait_for.
    async def fake_wait_for(fut, timeout):
        if asyncio.iscoroutine(fut):
            fut.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(cm.asyncio, "wait_for", fake_wait_for)

    # Force the timeout-path archive write to fail. The initial
    # request archive write succeeds; only the timeout response
    # write fails.
    from decafclaw import archive as archive_mod

    real_archive_append = archive_mod.append_message

    def boom_on_response(config, conv, msg):
        if msg.get("role") == "confirmation_response":
            raise RuntimeError("timeout archive failed")
        return real_archive_append(config, conv, msg)

    monkeypatch.setattr("decafclaw.archive.append_message", boom_on_response)

    with pytest.raises(RuntimeError, match="timeout archive failed"):
        await manager.request_confirmation(conv_id, request)

    # Pending state must be preserved so retry/recovery can proceed.
    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is not None
    assert state.pending_confirmation.confirmation_id == request.confirmation_id


@pytest.mark.asyncio
async def test_recovered_confirmation_dispatch_uses_captured_request(manager, monkeypatch):
    """In the recovered-confirmation path, ``respond_to_confirmation``
    captures ``state.pending_confirmation`` under the lock so the
    recovery dispatch uses the ORIGINAL recovered request — not
    whatever happens to be in ``state.pending_confirmation`` when the
    second lock block re-acquires the lock.

    A concurrent ``request_confirmation`` (a fresh turn starting up
    during our archive/emit I/O window) can overwrite
    ``state.pending_confirmation`` with a different request. Without
    the in-lock capture, the recovery dispatch would pop and dispatch
    the new request with our old response, calling the wrong handler.
    The capture pins recovery to the request we resolved.
    (Copilot review on PR #484.)
    """
    from decafclaw.archive import append_message

    conv_id = "conv-recovered-capture"
    original_request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    append_message(manager.config, conv_id, original_request.to_archive_message())
    await manager.startup_scan()
    state = manager.get_state(conv_id)
    assert state is not None

    dispatched_requests: list[ConfirmationRequest] = []

    class CapturingHandler:
        async def on_approve(self, ctx, req, resp):
            dispatched_requests.append(req)
            return {"ok": True}

        async def on_deny(self, ctx, req, resp):
            return {}

    manager.confirmation_registry.register(ConfirmationAction.RUN_SHELL_COMMAND, CapturingHandler())

    # Also register a sentinel handler under a DIFFERENT action so
    # that if the racer's request is dispatched instead we'd see it.
    class SentinelHandler:
        async def on_approve(self, ctx, req, resp):
            dispatched_requests.append(req)
            return {"oops": True}

        async def on_deny(self, ctx, req, resp):
            return {}

    manager.confirmation_registry.register(ConfirmationAction.ACTIVATE_SKILL, SentinelHandler())

    # Force the race: subscribe a callback that mutates
    # state.pending_confirmation when our confirmation_response emits.
    # This simulates a concurrent request_confirmation racing in during
    # respond_to_confirmation's I/O window — specifically the case where
    # only pending_confirmation is overwritten (e.g., a partially-
    # complete request_confirmation that landed under the lock just
    # after our claim). The post-emit lock block must use the captured
    # request, not re-read state.pending_confirmation.
    racer_request = ConfirmationRequest(
        action_type=ConfirmationAction.ACTIVATE_SKILL,
        action_data={"skill_name": "x"},
        message="racer",
    )

    def overwrite_on_response_emit(event):
        if event.get("type") != "confirmation_response":
            return
        # Simulate the racer landing inside the I/O window. Mimic the
        # exact mutation request_confirmation would do but preserve our
        # confirmation_response claim — the bug being tested is the
        # capture-vs-re-read, not the response-clear identity check.
        state.pending_confirmation = racer_request

    manager.subscribe(conv_id, overwrite_on_response_emit)

    await manager.respond_to_confirmation(conv_id, original_request.confirmation_id, approved=True)

    # The handler must have been called for the ORIGINAL request, not
    # for the racer. Exactly one dispatch, with original action_type.
    assert len(dispatched_requests) == 1, f"expected exactly one handler dispatch, got {len(dispatched_requests)}"
    assert dispatched_requests[0].action_type == ConfirmationAction.RUN_SHELL_COMMAND, (
        f"recovery dispatched the wrong request — should have used the "
        f"captured original, not re-read state.pending_confirmation. "
        f"Got: {dispatched_requests[0].action_type}"
    )
    assert dispatched_requests[0].confirmation_id == original_request.confirmation_id


@pytest.mark.asyncio
async def test_recovery_dispatch_proceeds_when_new_request_lands_mid_flight(manager, monkeypatch):
    """A real concurrent `request_confirmation` (not just an isolated
    overwrite of ``state.pending_confirmation``) can land between the
    initial claim block and the recovery dispatch. The dispatch must
    still proceed using the captured original request — the new
    request belongs to a different turn and is handled by its own
    `request_confirmation` invocation. (Copilot review on PR #484.)
    """
    from decafclaw.archive import append_message

    conv_id = "conv-real-concurrent-request"
    original_request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    append_message(manager.config, conv_id, original_request.to_archive_message())
    await manager.startup_scan()
    state = manager.get_state(conv_id)
    assert state is not None

    dispatched: list[ConfirmationRequest] = []

    class CapturingHandler:
        async def on_approve(self, ctx, req, resp):
            dispatched.append(req)
            return {"ok": True}

        async def on_deny(self, ctx, req, resp):
            return {}

    manager.confirmation_registry.register(ConfirmationAction.RUN_SHELL_COMMAND, CapturingHandler())

    # Mid-flight, a NEW request_confirmation kicks off — writes its
    # own pending_confirmation + confirmation_event + response=None.
    new_request = ConfirmationRequest(
        action_type=ConfirmationAction.ACTIVATE_SKILL,
        action_data={"skill_name": "x"},
        message="new",
    )

    def land_new_request(event):
        if event.get("type") != "confirmation_response":
            return
        # Simulate a fresh request_confirmation landing — writes the
        # full triple, mirroring request_confirmation's claim block.
        state.pending_confirmation = new_request
        state.confirmation_event = asyncio.Event()
        state.confirmation_response = None

    manager.subscribe(conv_id, land_new_request)

    await manager.respond_to_confirmation(conv_id, original_request.confirmation_id, approved=True)

    # Recovery MUST have dispatched the original request, not given
    # up on it just because state.confirmation_response was overwritten
    # by the new request_confirmation.
    assert len(dispatched) == 1, (
        f"recovery should have dispatched the original request "
        f"even with a new request_confirmation in flight; got "
        f"{len(dispatched)} dispatches"
    )
    assert dispatched[0].confirmation_id == original_request.confirmation_id
    # The new request should still be pending — recovery didn't
    # touch it.
    assert state.pending_confirmation is new_request


@pytest.mark.asyncio
async def test_cancel_pending_confirmation_wakes_live_waiter(manager):
    """If a `request_confirmation` waiter is parked on
    `event.wait()` when `cancel_pending_confirmation` is called,
    the waiter must be signaled so its post-wait block can consume
    the denial and unblock. Without this, a confirmation with
    `timeout=None` would hang forever even after cancellation.
    (Copilot review on PR #484.)
    """
    conv_id = "conv-cancel-wake"

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
        timeout=10.0,  # long timeout — proves we don't depend on it
    )

    waiter_started = asyncio.Event()

    async def cancel_after_waiter_parks():
        # Wait until the waiter is actually parked on event.wait().
        await waiter_started.wait()
        # One extra yield to make sure the waiter is at the await.
        await asyncio.sleep(0)
        cleared = await manager.cancel_pending_confirmation(conv_id)
        assert cleared is True

    # Spy on emit so we can detect the waiter has entered the wait.
    async def watch_for_request(event):
        if event.get("type") == "confirmation_request":
            waiter_started.set()

    manager.subscribe(conv_id, watch_for_request)

    asyncio.create_task(cancel_after_waiter_parks())
    response = await asyncio.wait_for(
        manager.request_confirmation(conv_id, request),
        timeout=5.0,  # if the waiter hangs, wait_for catches it
    )

    # Waiter unblocked with the cancellation denial.
    assert response.approved is False
    assert response.confirmation_id == request.confirmation_id


# -- Confirmation queue (issue #485) ------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_request_confirmation_serializes(manager):
    """Two concurrent request_confirmation calls on the same conv resolve
    to their own responses in submission order. Regression for issue #485
    — the second call used to clobber the first's pending slot, orphaning
    the first waiter on a dead event.
    """
    conv_id = "conv-queue-serialize"
    manager._get_or_create(conv_id)

    req_a = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="A?",
        timeout=5.0,
    )
    req_b = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "pwd"},
        message="B?",
        timeout=5.0,
    )

    task_a = asyncio.create_task(manager.request_confirmation(conv_id, req_a))
    # Yield so task A reaches its "install active" point.
    await asyncio.sleep(0)
    task_b = asyncio.create_task(manager.request_confirmation(conv_id, req_b))
    # Yield so task B reaches its "enqueue" point.
    await asyncio.sleep(0)

    # B must NOT be done yet — it's queued behind A.
    assert not task_b.done(), "B should be queued while A is active"

    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is req_a
    assert len(state.confirmation_queue) == 1
    assert state.confirmation_queue[0].request is req_b

    # Respond to A first.
    await manager.respond_to_confirmation(conv_id, req_a.confirmation_id, approved=True)
    resp_a = await asyncio.wait_for(task_a, timeout=2.0)
    assert resp_a.approved is True
    assert resp_a.confirmation_id == req_a.confirmation_id

    # B should now be promoted and observable in state.
    assert state.pending_confirmation is req_b
    assert state.confirmation_queue == []
    assert not task_b.done(), "B should be active but still awaiting response"

    # Respond to B.
    await manager.respond_to_confirmation(conv_id, req_b.confirmation_id, approved=True)
    resp_b = await asyncio.wait_for(task_b, timeout=2.0)
    assert resp_b.approved is True
    assert resp_b.confirmation_id == req_b.confirmation_id

    # Pending state fully cleared.
    assert state.pending_confirmation is None
    assert state.confirmation_response is None


@pytest.mark.asyncio
async def test_promoted_confirmation_emits_request_event(manager):
    """Each queued confirmation gets its own confirmation_request emit
    when it promotes to active — the UI sees the second request only
    after the first resolves. Issue #485.
    """
    conv_id = "conv-queue-emit"
    manager._get_or_create(conv_id)

    events = []

    async def cb(event):
        events.append(event)

    manager.subscribe(conv_id, cb)

    req_a = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="A?",
        timeout=5.0,
    )
    req_b = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "pwd"},
        message="B?",
        timeout=5.0,
    )

    task_a = asyncio.create_task(manager.request_confirmation(conv_id, req_a))
    await asyncio.sleep(0)
    task_b = asyncio.create_task(manager.request_confirmation(conv_id, req_b))
    await asyncio.sleep(0)

    # Only A's request event so far.
    req_events = [e for e in events if e["type"] == "confirmation_request"]
    assert len(req_events) == 1
    assert req_events[0]["confirmation_id"] == req_a.confirmation_id

    await manager.respond_to_confirmation(conv_id, req_a.confirmation_id, approved=True)
    await asyncio.wait_for(task_a, timeout=2.0)
    # Yield so the post-respond emit for B's promotion happens.
    await asyncio.sleep(0)

    req_events = [e for e in events if e["type"] == "confirmation_request"]
    assert len(req_events) == 2, f"expected two confirmation_request emits, got {len(req_events)}"
    assert req_events[1]["confirmation_id"] == req_b.confirmation_id

    await manager.respond_to_confirmation(conv_id, req_b.confirmation_id, approved=True)
    await asyncio.wait_for(task_b, timeout=2.0)


@pytest.mark.asyncio
async def test_cancelled_queued_waiter_removed_from_queue(manager):
    """A queued waiter task that gets cancelled mid-await must drop its
    own queue entry so it isn't promoted later. Issue #485.
    """
    conv_id = "conv-queue-cancel"
    manager._get_or_create(conv_id)

    req_a = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="A?",
        timeout=5.0,
    )
    req_b = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "pwd"},
        message="B?",
        timeout=5.0,
    )

    task_a = asyncio.create_task(manager.request_confirmation(conv_id, req_a))
    await asyncio.sleep(0)
    task_b = asyncio.create_task(manager.request_confirmation(conv_id, req_b))
    await asyncio.sleep(0)

    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is req_a
    assert len(state.confirmation_queue) == 1

    # Cancel B's task while it's still queued.
    task_b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_b
    # B's entry should be gone.
    assert state.confirmation_queue == []

    # A still resolves normally and no ghost-promotion happens.
    await manager.respond_to_confirmation(conv_id, req_a.confirmation_id, approved=True)
    resp_a = await asyncio.wait_for(task_a, timeout=2.0)
    assert resp_a.approved is True
    assert state.pending_confirmation is None
    assert state.confirmation_queue == []


@pytest.mark.asyncio
async def test_cancelled_after_promote_drains_to_next(manager):
    """If a queued waiter is cancelled after it has been promoted to the
    active slot (but before it consumed the promote signal), its active
    slot must be cleared and the next queued entry must be promoted —
    otherwise the queue stalls. Issue #485.
    """
    conv_id = "conv-queue-cancel-after-promote"
    manager._get_or_create(conv_id)

    events: list[dict] = []

    async def cb(event):
        events.append(event)

    manager.subscribe(conv_id, cb)

    req_a = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="A?",
        timeout=5.0,
    )
    req_b = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "pwd"},
        message="B?",
        timeout=5.0,
    )
    req_c = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "echo c"},
        message="C?",
        timeout=5.0,
    )

    task_a = asyncio.create_task(manager.request_confirmation(conv_id, req_a))
    await asyncio.sleep(0)
    task_b = asyncio.create_task(manager.request_confirmation(conv_id, req_b))
    await asyncio.sleep(0)
    task_c = asyncio.create_task(manager.request_confirmation(conv_id, req_c))
    await asyncio.sleep(0)

    state = manager.get_state(conv_id)
    assert state is not None
    assert len(state.confirmation_queue) == 2

    # Approve A — B gets promoted, but before B's task observes the
    # promote signal and emits its own confirmation_request, cancel B.
    # We approve A then immediately cancel B; the run-order of A's
    # post-wait promote vs B's wakeup is implementation-defined, but
    # the post-condition we test (C is promoted, queue drains) must
    # hold either way.
    await manager.respond_to_confirmation(conv_id, req_a.confirmation_id, approved=True)
    task_b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_b
    await asyncio.wait_for(task_a, timeout=2.0)
    # Yield so any pending promote work completes.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # C must have been promoted to active. The queue must be empty.
    assert state.pending_confirmation is req_c
    assert state.confirmation_queue == []

    # C is resolvable.
    await manager.respond_to_confirmation(conv_id, req_c.confirmation_id, approved=True)
    resp_c = await asyncio.wait_for(task_c, timeout=2.0)
    assert resp_c.approved is True


@pytest.mark.asyncio
async def test_queued_confirmation_timeout_starts_at_promote(manager):
    """A queued confirmation's ``request.timeout`` is the post-promote
    countdown, not the post-submission countdown. While queued, the
    waiter blocks on its ``promoted_event`` indefinitely so a
    long-running active confirmation doesn't silently deny everything
    queued behind it. Issue #485.
    """
    import time

    conv_id = "conv-queue-timeout-promote"
    manager._get_or_create(conv_id)

    req_a = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="A?",
        timeout=5.0,  # long; we control resolution explicitly
    )
    req_b = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "pwd"},
        message="B?",
        timeout=0.1,  # short post-promote deadline
    )

    task_a = asyncio.create_task(manager.request_confirmation(conv_id, req_a))
    await asyncio.sleep(0)
    submitted_at = time.monotonic()
    task_b = asyncio.create_task(manager.request_confirmation(conv_id, req_b))
    await asyncio.sleep(0)

    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is req_a
    assert len(state.confirmation_queue) == 1

    # Wait longer than B's timeout while A is still active. B must NOT
    # time out while queued.
    await asyncio.sleep(0.25)
    assert not task_b.done(), (
        "B should still be queued and not timed out — timeout countdown should only start post-promote"
    )

    # Resolve A. B promotes; its 0.1s timeout starts now.
    await manager.respond_to_confirmation(conv_id, req_a.confirmation_id, approved=True)
    await asyncio.wait_for(task_a, timeout=2.0)
    promoted_at = time.monotonic()

    # B times out. Its task returns a denial. The time-from-promotion
    # must be roughly request.timeout (0.1s), not the time-from-submit.
    resp_b = await asyncio.wait_for(task_b, timeout=2.0)
    timed_out_at = time.monotonic()

    assert resp_b.approved is False, "B should time out post-promote"
    assert resp_b.confirmation_id == req_b.confirmation_id

    post_promote_elapsed = timed_out_at - promoted_at
    post_submit_elapsed = timed_out_at - submitted_at
    # post-promote elapsed should be approximately 0.1s; allow a wide
    # margin for CI jitter.
    assert 0.05 < post_promote_elapsed < 0.5, (
        f"B timeout should fire ~0.1s after promote, got {post_promote_elapsed:.3f}s"
    )
    # post-submit elapsed must clearly exceed the 0.1s timeout — proves
    # the waiter wasn't ticking while queued.
    assert post_submit_elapsed > 0.3, (
        f"B timeout fired too quickly post-submit ({post_submit_elapsed:.3f}s)"
        " — queued waiter may have been ticking instead of awaiting promote"
    )


@pytest.mark.asyncio
async def test_cancel_pending_promotes_next_queued(manager):
    """``cancel_pending_confirmation`` cancels the active confirmation
    and the next queued entry promotes. Cancel-as-cleanup must not
    drain the queue or skip the queued entries. Issue #485.
    """
    conv_id = "conv-cancel-promote"
    manager._get_or_create(conv_id)

    events: list[dict] = []

    async def cb(event):
        events.append(event)

    manager.subscribe(conv_id, cb)

    req_a = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="A?",
        timeout=5.0,
    )
    req_b = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "pwd"},
        message="B?",
        timeout=5.0,
    )

    task_a = asyncio.create_task(manager.request_confirmation(conv_id, req_a))
    await asyncio.sleep(0)
    task_b = asyncio.create_task(manager.request_confirmation(conv_id, req_b))
    await asyncio.sleep(0)

    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is req_a

    cleared = await manager.cancel_pending_confirmation(conv_id)
    assert cleared is True

    # A receives denial; B promotes and is resolvable.
    resp_a = await asyncio.wait_for(task_a, timeout=2.0)
    assert resp_a.approved is False
    assert resp_a.confirmation_id == req_a.confirmation_id

    # Yield so B's queued waiter wakes and the post-promote emit fires.
    await asyncio.sleep(0)
    assert state.pending_confirmation is req_b
    assert state.confirmation_queue == []

    # Emit ordering: two confirmation_request events (A's active, then
    # B's promoted). cancel_pending_confirmation itself does not emit a
    # confirmation_response (pre-existing behavior — cancel is a
    # cleanup side-channel), so we only assert on the request stream.
    request_events = [e for e in events if e["type"] == "confirmation_request"]
    assert [e["confirmation_id"] for e in request_events] == [
        req_a.confirmation_id,
        req_b.confirmation_id,
    ], "expected A's request then B's promoted request"

    await manager.respond_to_confirmation(conv_id, req_b.confirmation_id, approved=True)
    resp_b = await asyncio.wait_for(task_b, timeout=2.0)
    assert resp_b.approved is True


@pytest.mark.asyncio
async def test_cancel_pending_with_empty_queue_unchanged(manager):
    """``cancel_pending_confirmation`` with no queued entries behaves
    exactly as before — the active confirmation gets a denial and no
    extra emits fire. Regression guard for the no-queue path. Issue
    #485.
    """
    conv_id = "conv-cancel-empty-queue"
    manager._get_or_create(conv_id)

    events: list[dict] = []

    async def cb(event):
        events.append(event)

    manager.subscribe(conv_id, cb)

    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="A?",
        timeout=5.0,
    )

    task = asyncio.create_task(manager.request_confirmation(conv_id, request))
    await asyncio.sleep(0)

    state = manager.get_state(conv_id)
    assert state is not None
    assert state.confirmation_queue == []

    cleared = await manager.cancel_pending_confirmation(conv_id)
    assert cleared is True

    response = await asyncio.wait_for(task, timeout=2.0)
    assert response.approved is False
    assert response.confirmation_id == request.confirmation_id

    assert state.pending_confirmation is None
    assert state.confirmation_queue == []

    # Only one confirmation_request emit (the initial one) — no
    # spurious promote-emit on the empty queue.
    request_emits = [e for e in events if e["type"] == "confirmation_request"]
    assert len(request_emits) == 1
