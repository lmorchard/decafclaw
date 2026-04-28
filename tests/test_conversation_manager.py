"""Tests for the conversation manager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.confirmations import (
    ConfirmationAction,
    ConfirmationRegistry,
    ConfirmationRequest,
    ConfirmationResponse,
)
from decafclaw.conversation_manager import (
    _CTX_DRIVEN_FIELDS,
    _PERSISTED_BINDINGS,
    ConversationManager,
    ConversationState,
    PersistedTurnState,
    TurnKind,
)
from decafclaw.events import EventBus


@pytest.fixture
def manager(config):
    bus = EventBus()
    return ConversationManager(config, bus)


# -- State management ---------------------------------------------------------

def test_get_or_create(manager):
    state = manager._get_or_create("conv-1")
    assert state.conv_id == "conv-1"
    assert state is manager._get_or_create("conv-1")  # same instance


def test_get_state_returns_none_for_unknown(manager):
    assert manager.get_state("nonexistent") is None


# -- Subscription --------------------------------------------------------------

def test_subscribe_and_unsubscribe(manager):
    cb = MagicMock()
    sub_id = manager.subscribe("conv-1", cb)
    state = manager.get_state("conv-1")
    assert state is not None
    assert sub_id in state.subscribers

    manager.unsubscribe("conv-1", sub_id)
    assert sub_id not in state.subscribers


@pytest.mark.asyncio
async def test_emit_calls_subscribers(manager):
    received = []

    async def cb(event):
        received.append(event)

    manager.subscribe("conv-1", cb)
    await manager.emit("conv-1", {"type": "test", "data": 42})

    assert len(received) == 1
    assert received[0]["type"] == "test"
    assert received[0]["conv_id"] == "conv-1"


@pytest.mark.asyncio
async def test_emit_subscriber_error_doesnt_break_others(manager):
    received = []

    def bad_cb(event):
        raise RuntimeError("boom")

    async def good_cb(event):
        received.append(event)

    manager.subscribe("conv-1", bad_cb)
    manager.subscribe("conv-1", good_cb)
    await manager.emit("conv-1", {"type": "test"})

    assert len(received) == 1  # good_cb still called


# -- History -------------------------------------------------------------------

def test_load_history_empty(manager):
    history = manager.load_history("new-conv")
    assert history == []


def test_load_history_cached(manager):
    state = manager._get_or_create("conv-1")
    state.history = [{"role": "user", "content": "hello"}]
    assert manager.load_history("conv-1") == state.history


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
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True)

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
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=False)

    asyncio.create_task(deny())
    response = await manager.request_confirmation(conv_id, request)

    assert response.approved is False


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
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True)

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
            conv_id, request.confirmation_id, approved=True,
            data={"selected": "production"})

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
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True,
            data={"selected": "x"})

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

    await manager.respond_to_confirmation(
        conv_id, "wrong-id", approved=True)

    # Event should NOT be set
    assert not state.confirmation_event.is_set()


# -- Message queueing ---------------------------------------------------------

@pytest.mark.asyncio
async def test_message_queued_when_busy(manager):
    state = manager._get_or_create("conv-1")
    state.busy = True

    await manager.send_message("conv-1", "queued msg", user_id="user")

    assert len(state.pending_messages) == 1
    assert state.pending_messages[0]["text"] == "queued msg"


# -- Cancel turn ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_turn_sets_event(manager):
    state = manager._get_or_create("conv-1")
    state.cancel_event = asyncio.Event()

    async def fake_task():
        await asyncio.sleep(10)

    state.agent_task = asyncio.create_task(fake_task())

    await manager.cancel_turn("conv-1")
    assert state.cancel_event.is_set()
    # Give the task a moment to be cancelled
    await asyncio.sleep(0.05)
    assert state.agent_task.cancelled()


# -- Send message with mocked agent turn ---------------------------------------

@pytest.mark.asyncio
async def test_send_message_queues_multiple_when_busy(manager):
    """Multiple messages while busy all get queued."""
    state = manager._get_or_create("conv-1")
    state.busy = True

    await manager.send_message("conv-1", "msg 1", user_id="user")
    await manager.send_message("conv-1", "msg 2", user_id="user")
    await manager.send_message("conv-1", "msg 3", user_id="user")

    assert len(state.pending_messages) == 3
    assert [m["text"] for m in state.pending_messages] == ["msg 1", "msg 2", "msg 3"]


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
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True, always=True)

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
async def test_drain_pending_resolves_all_queued_futures(
    manager, config, monkeypatch
):
    """Multiple USER messages queued while busy — all callers' futures must
    resolve when the batch drains. Non-last futures fan out from the head
    future and receive the same result."""
    called = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        called.append(user_message)
        from decafclaw.media import ToolResult
        return ToolResult(text="combined-result")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    state = manager._get_or_create("c1")
    state.busy = True

    f1 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="one")
    f2 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="two")
    f3 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="three")
    assert len(state.pending_messages) == 3

    # Release busy, drain manually.
    state.busy = False
    await manager._drain_pending(state)
    # Let the spawned turn task run.
    for fut in (f1, f2, f3):
        await asyncio.wait_for(fut, timeout=2.0)
    # All three resolved — non-last are None, last is the agent's response.
    assert called == ["one\ntwo\nthree"]


@pytest.mark.asyncio
async def test_enqueue_turn_user_kind_runs_same_as_send_message(
    manager, config, monkeypatch
):
    called = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        called.append({"text": user_message, "conv_id": ctx.conv_id})
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    future = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.USER,
        prompt="hello",
        user_id="u",
    )
    await future
    assert called == [{"text": "hello", "conv_id": "c1"}]


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
    await manager.respond_to_confirmation(
        conv_id, request.confirmation_id, approved=True)

    # Pending confirmation should be cleared
    state = manager.get_state(conv_id)
    assert state.pending_confirmation is None


# -- Per-kind policy matrix ----------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_turn_heartbeat_kind_uses_for_task(
    manager, config, monkeypatch
):
    seen_ctx = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_ctx["task_mode"] = ctx.task_mode
        seen_ctx["skip_reflection"] = ctx.skip_reflection
        seen_ctx["skip_vault_retrieval"] = ctx.skip_vault_retrieval
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    future = await manager.enqueue_turn(
        conv_id="heartbeat-T-0",
        kind=TurnKind.HEARTBEAT_SECTION,
        prompt="do section",
        history=[],
        task_mode="heartbeat",
    )
    await future
    assert seen_ctx["task_mode"] == "heartbeat"
    assert seen_ctx["skip_reflection"] is True
    assert seen_ctx["skip_vault_retrieval"] is True


@pytest.mark.asyncio
async def test_enqueue_turn_user_kind_has_empty_task_mode(
    manager, config, monkeypatch
):
    seen_ctx = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_ctx["task_mode"] = ctx.task_mode
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    future = await manager.enqueue_turn(
        conv_id="c1", kind=TurnKind.USER, prompt="hello",
    )
    await future
    assert seen_ctx["task_mode"] == ""


@pytest.mark.asyncio
async def test_enqueue_turn_wake_kind_defaults_to_background_wake_mode(
    manager, config, monkeypatch
):
    seen_ctx = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_ctx["task_mode"] = ctx.task_mode
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    future = await manager.enqueue_turn(
        conv_id="c1", kind=TurnKind.WAKE, prompt="wake nudge", history=[],
        # no explicit task_mode
    )
    await future
    assert seen_ctx["task_mode"] == "background_wake"


@pytest.mark.asyncio
async def test_enqueue_turn_wake_kind_restores_skill_state(
    manager, config, monkeypatch
):
    """WAKE fires on a persistent conv, so activated skills / preserved
    flags / active_model must carry forward onto the ctx."""
    seen_ctx = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_ctx["extra_tools"] = dict(ctx.tools.extra)
        seen_ctx["activated"] = set(ctx.skills.activated)
        seen_ctx["skip_vault_retrieval"] = ctx.skip_vault_retrieval
        seen_ctx["active_model"] = ctx.active_model
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    state = manager._get_or_create("c1")
    state.persisted.extra_tools = {"tool_x": lambda ctx: None}
    state.persisted.extra_tool_definitions = [{"name": "tool_x"}]
    state.persisted.activated_skills = {"skill_x"}
    state.persisted.skip_vault_retrieval = True
    state.persisted.active_model = "fancy-model"

    future = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.WAKE,
        prompt="wake",
        history=[],
    )
    await future

    assert "tool_x" in seen_ctx["extra_tools"]
    assert seen_ctx["activated"] == {"skill_x"}
    assert seen_ctx["skip_vault_retrieval"] is True
    assert seen_ctx["active_model"] == "fancy-model"


@pytest.mark.asyncio
async def test_drain_pending_fires_mixed_kinds_one_at_a_time(
    manager, config, monkeypatch
):
    fires = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        fires.append({"text": user_message, "task_mode": ctx.task_mode})
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # Simulate busy state.
    state = manager._get_or_create("c1")
    state.busy = True

    u1 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="hello1")
    u2 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="hello2")
    wake = await manager.enqueue_turn("c1", kind=TurnKind.WAKE, prompt="wake",
                                       history=[])

    assert len(state.pending_messages) == 3

    # Release busy; manually drain.
    state.busy = False
    await manager._drain_pending(state)

    # Wait for drain-triggered turns (_start_turn runs as asyncio.Task).
    for fut in (u1, u2, wake):
        await asyncio.wait_for(fut, timeout=2.0)

    # Expected: combined USER turn first, then WAKE turn.
    assert len(fires) == 2
    assert fires[0]["text"] == "hello1\nhello2"
    assert fires[0]["task_mode"] == ""
    assert fires[1]["text"] == "wake"


# -- Wake rate limiter ---------------------------------------------------------

@pytest.mark.asyncio
async def test_wake_rate_limiter_drops_after_max(manager, config, monkeypatch):
    """N+1th wake within the window is dropped; earlier wakes still run."""
    from decafclaw.config_types import BackgroundConfig
    config.background = BackgroundConfig(wake_max_per_window=2, wake_window_sec=60)
    # Refresh rate-limiter params on the manager from updated config.
    manager._wake_max_per_window = config.background.wake_max_per_window
    manager._wake_window_sec = config.background.wake_window_sec

    fires = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        fires.append(user_message)
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # Fire 4 wakes — only 2 should actually run (3rd and 4th are dropped by
    # the rate limiter).
    futures = []
    for i in range(4):
        fut = await manager.enqueue_turn(
            "c1", kind=TurnKind.WAKE, prompt=f"wake-{i}", history=[])
        futures.append(fut)

    # Dropped futures resolve to None immediately.
    assert futures[2].done() and futures[2].result() is None
    assert futures[3].done() and futures[3].result() is None

    # Wait for the accepted wakes to complete.
    results = [await asyncio.wait_for(f, timeout=2.0) for f in futures[:2]]

    assert len(fires) == 2
    assert "wake-0" in fires
    assert "wake-1" in fires
    assert results[0] is not None  # ran, returns text
    assert results[1] is not None


@pytest.mark.asyncio
async def test_wake_rate_limiter_window_ages_out(manager, config, monkeypatch):
    """After wake_window_sec elapses, the limiter accepts new wakes again."""
    from decafclaw.config_types import BackgroundConfig
    config.background = BackgroundConfig(wake_max_per_window=1, wake_window_sec=60)
    manager._wake_max_per_window = config.background.wake_max_per_window
    manager._wake_window_sec = config.background.wake_window_sec

    fires = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        fires.append(user_message)
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # Pin time so the window moves deterministically.
    now = [1000.0]
    monkeypatch.setattr("decafclaw.conversation_manager.time.monotonic",
                        lambda: now[0])

    # Fire 1: accepted.
    f1 = await manager.enqueue_turn("c1", kind=TurnKind.WAKE, prompt="w1", history=[])
    await asyncio.wait_for(f1, timeout=2.0)

    # Fire 2 immediately (same window): dropped.
    f2 = await manager.enqueue_turn("c1", kind=TurnKind.WAKE, prompt="w2", history=[])
    assert f2.done() and f2.result() is None

    # Advance past the window.
    now[0] += 61

    # Fire 3: accepted again (old entry aged out).
    f3 = await manager.enqueue_turn("c1", kind=TurnKind.WAKE, prompt="w3", history=[])
    await asyncio.wait_for(f3, timeout=2.0)

    assert "w1" in fires
    assert "w3" in fires
    assert "w2" not in fires


# -- suppress_user_message on WAKE turns -------------------------------------


@pytest.mark.asyncio
async def test_wake_turn_emits_suppress_user_message_when_ok(
    manager, config, monkeypatch
):
    """WAKE turn ending with BACKGROUND_WAKE_OK triggers suppress_user_message=True."""
    events = []
    manager.subscribe("c1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        return ToolResult(text="BACKGROUND_WAKE_OK — nothing to report.")
    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(
        conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
    await asyncio.wait_for(fut, timeout=2.0)

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes
    assert completes[-1].get("suppress_user_message") is True


@pytest.mark.asyncio
async def test_wake_turn_no_suppress_when_agent_responds_normally(
    manager, config, monkeypatch
):
    """WAKE turn with a regular response should NOT suppress."""
    events = []
    manager.subscribe("c1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        return ToolResult(text="Here's what happened with the job.")
    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(
        conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
    await asyncio.wait_for(fut, timeout=2.0)

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes
    assert completes[-1].get("suppress_user_message") is False


@pytest.mark.asyncio
async def test_user_turn_never_suppresses_even_with_sentinel(
    manager, config, monkeypatch
):
    """USER turns NEVER get suppress_user_message=True, even if the agent
    happens to emit the sentinel."""
    events = []
    manager.subscribe("c1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        return ToolResult(text="BACKGROUND_WAKE_OK (agent wrote sentinel incorrectly)")
    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(
        conv_id="c1", kind=TurnKind.USER, prompt="hello")
    await asyncio.wait_for(fut, timeout=2.0)

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes
    assert completes[-1].get("suppress_user_message") is False


@pytest.mark.asyncio
async def test_transport_subscriber_skips_on_suppress(manager, config, monkeypatch):
    """When a wake turn emits suppress_user_message=True, a subscriber that
    treats the flag correctly should NOT process the message for user display."""
    posted_messages = []

    def subscriber(event):
        if event.get("type") == "message_complete":
            if event.get("suppress_user_message"):
                return  # transport correctly skips suppressed messages
            posted_messages.append(event.get("text"))

    manager.subscribe("c1", subscriber)

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        return ToolResult(text="BACKGROUND_WAKE_OK")
    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(
        conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
    await asyncio.wait_for(fut, timeout=2.0)

    assert posted_messages == []  # suppressed — transport didn't post.


# ---------------------------------------------------------------------------
# Item 3: WAKE turns disable the streaming callback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wake_turn_disables_streaming_callback(manager, config, monkeypatch):
    """WAKE turns run with on_stream_chunk disabled — prevents streamed
    text from reaching the user before BACKGROUND_WAKE_OK suppression."""
    seen_stream_callback = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_stream_callback.append(ctx.on_stream_chunk)
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    # Force streaming config on (so the USER case would set the callback).
    monkeypatch.setattr("decafclaw.config.resolve_streaming", lambda c, m: True)
    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(
        conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
    await asyncio.wait_for(fut, timeout=2.0)

    assert seen_stream_callback[0] is None  # WAKE: no streaming


@pytest.mark.asyncio
async def test_user_turn_keeps_streaming_callback(manager, config, monkeypatch):
    """USER turns get the streaming callback when streaming is enabled."""
    seen_stream_callback = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_stream_callback.append(ctx.on_stream_chunk)
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.config.resolve_streaming", lambda c, m: True)
    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(
        conv_id="c1", kind=TurnKind.USER, prompt="hi")
    await asyncio.wait_for(fut, timeout=2.0)

    assert seen_stream_callback[0] is not None  # USER: streaming active


@pytest.mark.asyncio
async def test_drain_pending_fanout_handles_head_exception(
    manager, config, monkeypatch
):
    """_fanout must not raise if the head future completed with set_result (even
    if the agent returned an error string).  This also guards against future
    refactors where the head could receive set_exception — tail futures must
    still resolve to None rather than hanging."""
    # run_agent_turn raising causes _start_turn's except-handler to call
    # future.set_result("[error: ...]") — so we verify the fanout still works
    # and all three futures resolve without hanging.
    call_count = []

    async def boom(ctx, user_message, history, **kwargs):
        call_count.append(1)
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", boom)

    state = manager._get_or_create("c1")
    state.busy = True

    f1 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="one")
    f2 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="two")
    f3 = await manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="three")
    assert len(state.pending_messages) == 3

    state.busy = False
    await manager._drain_pending(state)

    # All three must resolve — they must not hang.
    for fut in (f1, f2, f3):
        await asyncio.wait_for(fut, timeout=2.0)

    # The head future (f3, last in queue) gets the "[error: ...]" result from
    # _start_turn's exception handler via set_result (never set_exception).
    # The _fanout callback propagates that same result to the tail futures so
    # every waiting caller is unblocked.  The primary invariant is that none
    # of them hang — the exact value (error string or None) is secondary.
    assert f3.result() is not None and "error" in f3.result()
    # Tail futures receive the same result value via _fanout.
    assert f1.result() == f3.result()
    assert f2.result() == f3.result()


# -- WAKE inherits last USER turn's transport context --------------------------


@pytest.mark.asyncio
async def test_wake_inherits_last_user_turn_context(manager, config, monkeypatch):
    """WAKE turn without explicit user_id/context_setup inherits from the
    last USER turn on the same conv."""
    from decafclaw.media import ToolResult

    seen = []

    def my_setup(ctx):
        ctx.channel_name = "custom-channel"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen.append({
            "user_id": ctx.user_id,
            "channel_name": getattr(ctx, "channel_name", None),
            "task_mode": ctx.task_mode,
        })
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # 1. USER turn — sets user_id + context_setup
    f1 = await manager.enqueue_turn(
        "c1", kind=TurnKind.USER, prompt="hi",
        user_id="alice", context_setup=my_setup,
    )
    await asyncio.wait_for(f1, timeout=2.0)

    # 2. WAKE turn — no explicit user_id/context_setup — inherits
    f2 = await manager.enqueue_turn(
        "c1", kind=TurnKind.WAKE, prompt="wake", history=[],
    )
    await asyncio.wait_for(f2, timeout=2.0)

    assert len(seen) == 2
    # USER turn
    assert seen[0]["user_id"] == "alice"
    assert seen[0]["channel_name"] == "custom-channel"
    # WAKE turn inherits user_id and context_setup
    assert seen[1]["user_id"] == "alice"
    assert seen[1]["channel_name"] == "custom-channel"
    assert seen[1]["task_mode"] == "background_wake"


@pytest.mark.asyncio
async def test_wake_without_prior_user_turn_uses_empty_context(
    manager, config, monkeypatch
):
    """WAKE on a conv with no prior USER turn gets empty user_id / no setup."""
    from decafclaw.media import ToolResult

    seen = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen.append({
            "user_id": ctx.user_id,
            "channel_name": getattr(ctx, "channel_name", None),
        })
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # No prior USER turn — fire WAKE directly (like a heartbeat-originated wake)
    f = await manager.enqueue_turn(
        "heartbeat-T-0", kind=TurnKind.WAKE, prompt="wake", history=[],
    )
    await asyncio.wait_for(f, timeout=2.0)

    assert len(seen) == 1
    assert seen[0]["user_id"] == ""


@pytest.mark.asyncio
async def test_wake_explicit_context_overrides_inherited(
    manager, config, monkeypatch
):
    """If caller passes explicit user_id/context_setup to WAKE, those win
    over the inherited values."""
    from decafclaw.media import ToolResult

    seen = []

    def user_setup(ctx):
        ctx.channel_name = "user-channel"

    def wake_setup(ctx):
        ctx.channel_name = "wake-channel"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen.append({
            "user_id": ctx.user_id,
            "channel_name": getattr(ctx, "channel_name", None),
        })
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    f1 = await manager.enqueue_turn(
        "c1", kind=TurnKind.USER, prompt="hi",
        user_id="alice", context_setup=user_setup,
    )
    await asyncio.wait_for(f1, timeout=2.0)

    f2 = await manager.enqueue_turn(
        "c1", kind=TurnKind.WAKE, prompt="wake", history=[],
        user_id="explicit-wake-user", context_setup=wake_setup,
    )
    await asyncio.wait_for(f2, timeout=2.0)

    assert seen[1]["user_id"] == "explicit-wake-user"
    assert seen[1]["channel_name"] == "wake-channel"


# -- PersistedTurnState — single source of truth (#378) -----------------------

def test_persisted_field_bindings_exhaustive():
    """Every PersistedTurnState field must have a binding entry — adding
    a field without a binding would silently drop it from save/restore."""
    from dataclasses import fields as dc_fields
    declared = {f.name for f in dc_fields(PersistedTurnState)}
    bound = set(_PERSISTED_BINDINGS.keys())
    assert declared == bound, (
        f"PersistedTurnState fields and _PERSISTED_BINDINGS keys disagree: "
        f"declared-only={declared - bound}, bound-only={bound - declared}"
    )


def test_ctx_driven_fields_subset_of_persisted():
    """_CTX_DRIVEN_FIELDS must reference only declared persisted fields —
    catches typos that would otherwise silently misclassify a field."""
    from dataclasses import fields as dc_fields
    declared = {f.name for f in dc_fields(PersistedTurnState)}
    assert _CTX_DRIVEN_FIELDS <= declared, (
        f"_CTX_DRIVEN_FIELDS contains unknown field(s): "
        f"{_CTX_DRIVEN_FIELDS - declared}"
    )


def test_save_restore_round_trip(manager, config):
    """End-to-end: populate every persisted field, save from a ctx,
    restore onto a fresh ctx, assert all values flow through."""
    from decafclaw.context import Context

    state = manager._get_or_create("rt-conv")

    # Populate ctx with non-default sentinel values for every
    # persisted field. Using `set_flag` for the externally-driven
    # fields and direct ctx writes for the ctx-driven ones — same
    # paths the real code uses.
    save_ctx = Context(config=config, event_bus=manager.event_bus)
    save_ctx.tools.extra = {"sentinel_tool": lambda c: None}
    save_ctx.tools.extra_definitions = [{"name": "sentinel_tool"}]
    save_ctx.skills.activated = {"sentinel_skill"}
    save_ctx.skip_vault_retrieval = True
    manager.set_flag("rt-conv", "active_model", "sentinel-model")

    manager._save_conversation_state(state, save_ctx)

    # Round-trip: a fresh ctx should pick up every field on restore.
    restore_ctx = Context(config=config, event_bus=manager.event_bus)
    manager._restore_per_conv_state(state, restore_ctx)

    assert restore_ctx.tools.extra == {"sentinel_tool": save_ctx.tools.extra["sentinel_tool"]}
    assert restore_ctx.tools.extra_definitions == [{"name": "sentinel_tool"}]
    assert restore_ctx.skills.activated == {"sentinel_skill"}
    assert restore_ctx.skip_vault_retrieval is True
    assert restore_ctx.active_model == "sentinel-model"


def test_save_does_not_overwrite_externally_driven_fields(manager, config):
    """``active_model`` is set via ``set_flag`` from the web UI; save
    runs at turn end and must not clobber it with whatever ctx happens
    to carry."""
    from decafclaw.context import Context

    state = manager._get_or_create("ext-conv")
    manager.set_flag("ext-conv", "active_model", "user-pinned-model")

    # ctx happens to carry a different (or empty) active_model — save
    # should leave the persisted value alone.
    ctx = Context(config=config, event_bus=manager.event_bus)
    ctx.active_model = "some-different-value"
    manager._save_conversation_state(state, ctx)

    assert state.persisted.active_model == "user-pinned-model"


def test_set_flag_writes_through_to_persisted(manager):
    """Persisted-state flags should land on ``state.persisted``, not on
    ``ConversationState`` itself — confirms the new write-through."""
    manager.set_flag("flag-conv", "active_model", "m1")
    manager.set_flag("flag-conv", "skip_vault_retrieval", True)
    state = manager.get_state("flag-conv")
    assert state.persisted.active_model == "m1"
    assert state.persisted.skip_vault_retrieval is True


def test_save_truthy_only_preserves_sticky_semantics(manager, config):
    """Once a ctx-driven flag is True in persisted state, a later save
    with a False ctx value must not clobber it back to False."""
    from decafclaw.context import Context

    state = manager._get_or_create("sticky-conv")
    state.persisted.skip_vault_retrieval = True

    ctx = Context(config=config, event_bus=manager.event_bus)
    ctx.skip_vault_retrieval = False  # ctx happens to be False this turn
    manager._save_conversation_state(state, ctx)

    assert state.persisted.skip_vault_retrieval is True
