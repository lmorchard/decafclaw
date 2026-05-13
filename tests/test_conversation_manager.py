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


# -- Concurrency: per-conv lock (issue #440) ----------------------------------

@pytest.mark.asyncio
async def test_concurrent_user_enqueue_serializes_via_lock(
    manager, config, monkeypatch
):
    """Two simultaneous enqueue_turn(USER) calls for the same conv must
    serialize: exactly one runs, the other queues. Guard test for
    issue #440.

    Status: this test PASSES on both pre- and post-lock code because
    asyncio is cooperative and the current `enqueue_turn` / `_start_turn`
    structure has no yield between the busy check (`if state.busy:`)
    and the `state.busy = True` write inside `_start_turn`. The first
    task to resume from `await self.emit(...)` synchronously runs
    through the busy check and into `_start_turn`'s body, setting
    busy=True before yielding again — so subsequent tasks see busy=True
    and queue.

    The test is kept as a **structural guard** of the invariant: if
    someone adds an await between those two points (or in `_start_turn`
    before its `state.busy = True` assignment) the race opens, and
    this test would catch it *only when paired with the lock*. The
    lock makes the invariant explicit; without the lock, the
    invariant is implicit in the current control flow and silently
    regressable.

    See `test_concurrent_confirmation_responses_dont_double_dispatch`
    below for the companion test that DOES reproduce a live race
    against unlocked code (the confirmation recovery dispatch path).
    """
    started = asyncio.Event()
    release = asyncio.Event()
    call_count = 0

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        nonlocal call_count
        call_count += 1
        started.set()
        await release.wait()
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # Subscriber forces `emit` to actually await (real yield point at the
    # user_message emission inside enqueue_turn).
    async def _noop(_event):
        pass
    manager.subscribe("c1", _noop)

    # Fire both enqueues concurrently. If a future change adds a
    # yield between the busy check and `state.busy = True` inside
    # `_start_turn`, both calls could observe `busy=False` and both
    # would call `_start_turn` without the lock. With the lock in
    # place, the second call blocks on the lock and observes
    # `busy=True` set by the first.
    f1, f2 = await asyncio.gather(
        manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="one"),
        manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="two"),
    )

    # Let the started turn run to the point of awaiting `release`.
    await started.wait()

    state = manager.get_state("c1")
    assert state is not None

    # Exactly one turn started; the other is queued.
    assert call_count == 1, (
        f"expected exactly one turn started, got {call_count}"
    )
    assert len(state.pending_messages) == 1, (
        f"expected one pending message, got {len(state.pending_messages)}"
    )

    # Drain: release the parked turn so the queued one also runs.
    release.set()
    await asyncio.wait_for(f1, timeout=2.0)
    await asyncio.wait_for(f2, timeout=2.0)


@pytest.mark.asyncio
async def test_concurrent_confirmation_responses_dont_double_dispatch(
    manager, monkeypatch
):
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

    manager.confirmation_registry.register(
        ConfirmationAction.RUN_SHELL_COMMAND, FakeHandler()
    )

    await asyncio.gather(
        manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True),
        manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True),
    )

    assert handler_calls == 1, (
        f"expected exactly one handler dispatch, got {handler_calls}"
    )
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
    assert cleared is False, (
        "cancel_pending_confirmation should defer to the claimed "
        "response and return False"
    )
    # Slot must still be claimed for request_confirmation to find.
    assert state.confirmation_response is response
    assert state.pending_confirmation is request


@pytest.mark.asyncio
async def test_request_confirmation_timeout_loses_race_to_late_responder(
    manager, monkeypatch
):
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
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True)

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
    responses = [
        m for m in history if m.get("role") == "confirmation_response"
    ]
    assert len(responses) == 1, (
        f"expected exactly one confirmation_response in archive, "
        f"got {len(responses)}: {responses}"
    )
    assert responses[0]["approved"] is True


@pytest.mark.asyncio
async def test_respond_to_confirmation_rolls_back_claim_on_archive_failure(
    manager, monkeypatch
):
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
        await manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True)

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
async def test_cancel_pending_confirmation_rolls_back_on_archive_failure(
    manager, monkeypatch
):
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

    manager.confirmation_registry.register(
        ConfirmationAction.RUN_SHELL_COMMAND, BoomHandler()
    )

    response = ConfirmationResponse(
        confirmation_id=request.confirmation_id, approved=True,
    )
    with pytest.raises(RuntimeError, match="handler exploded"):
        await manager.recover_confirmation(conv_id, response)

    # Pending confirmation must be restored so the next retry can
    # try dispatch again.
    assert state.pending_confirmation is not None
    assert (state.pending_confirmation.confirmation_id
            == request.confirmation_id)


@pytest.mark.asyncio
async def test_drain_pending_defers_when_concurrent_enqueue_won_dispatch(
    manager,
):
    """If a concurrent ``enqueue_turn`` wins the dispatch race after
    the finally-block sets ``busy=False`` but before ``_drain_pending``
    runs, the drain must defer rather than dispatch over the new
    turn — otherwise ``_start_turn`` would overwrite
    ``state.agent_task`` / ``state.cancel_event`` and run two turns
    for the same conv concurrently. (Copilot review on PR #484.)
    """
    state = manager._get_or_create("conv-drain-defer")
    # Simulate the post-finally-block state where busy was just
    # cleared, but a concurrent enqueue has since taken the slot
    # (busy=True, agent_task set) and there's still pending work
    # left over from the original turn.
    state.busy = True
    fake_task = asyncio.create_task(asyncio.sleep(0))
    state.agent_task = fake_task
    state.pending_messages.append({
        "kind": TurnKind.USER,
        "text": "queued",
        "user_id": "u",
        "context_setup": None,
        "archive_text": "",
        "attachments": None,
        "command_ctx": None,
        "wiki_page": None,
        "task_mode": None,
        "history": None,
        "metadata": None,
        "future": None,
    })

    # Drain must defer (no dispatch) when busy is already set.
    await manager._drain_pending(state)

    # The queued message must still be there for the new in-flight
    # turn's finally-block drain to pick up.
    assert len(state.pending_messages) == 1
    # The agent_task must not have been overwritten.
    assert state.agent_task is fake_task

    # Clean up.
    await fake_task


@pytest.mark.asyncio
async def test_request_confirmation_timeout_archive_failure_preserves_state(
    manager, monkeypatch
):
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
async def test_recovered_confirmation_dispatch_uses_captured_request(
    manager, monkeypatch
):
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
    append_message(manager.config, conv_id,
                   original_request.to_archive_message())
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

    manager.confirmation_registry.register(
        ConfirmationAction.RUN_SHELL_COMMAND, CapturingHandler()
    )
    # Also register a sentinel handler under a DIFFERENT action so
    # that if the racer's request is dispatched instead we'd see it.
    class SentinelHandler:
        async def on_approve(self, ctx, req, resp):
            dispatched_requests.append(req)
            return {"oops": True}

        async def on_deny(self, ctx, req, resp):
            return {}

    manager.confirmation_registry.register(
        ConfirmationAction.ACTIVATE_SKILL, SentinelHandler()
    )

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

    await manager.respond_to_confirmation(
        conv_id, original_request.confirmation_id, approved=True)

    # The handler must have been called for the ORIGINAL request, not
    # for the racer. Exactly one dispatch, with original action_type.
    assert len(dispatched_requests) == 1, (
        f"expected exactly one handler dispatch, got "
        f"{len(dispatched_requests)}"
    )
    assert (dispatched_requests[0].action_type
            == ConfirmationAction.RUN_SHELL_COMMAND), (
        f"recovery dispatched the wrong request — should have used the "
        f"captured original, not re-read state.pending_confirmation. "
        f"Got: {dispatched_requests[0].action_type}"
    )
    assert (dispatched_requests[0].confirmation_id
            == original_request.confirmation_id)


@pytest.mark.asyncio
async def test_recovery_dispatch_proceeds_when_new_request_lands_mid_flight(
    manager, monkeypatch
):
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
    append_message(manager.config, conv_id,
                   original_request.to_archive_message())
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

    manager.confirmation_registry.register(
        ConfirmationAction.RUN_SHELL_COMMAND, CapturingHandler()
    )

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

    await manager.respond_to_confirmation(
        conv_id, original_request.confirmation_id, approved=True)

    # Recovery MUST have dispatched the original request, not given
    # up on it just because state.confirmation_response was overwritten
    # by the new request_confirmation.
    assert len(dispatched) == 1, (
        f"recovery should have dispatched the original request "
        f"even with a new request_confirmation in flight; got "
        f"{len(dispatched)} dispatches"
    )
    assert (dispatched[0].confirmation_id
            == original_request.confirmation_id)
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
