"""Conversation-manager enqueue tests: message queueing, drain and fanout,
per-kind turn policy (TaskMode), wake delivery/rate-limiting/context inheritance,
startup resume, and the durable JSONL inbox.

Split from the former monolithic ``test_conversation_manager.py`` by concern
(#1421)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.context import TaskMode
from decafclaw.conversation_manager import ConversationManager, TurnKind, parse_task_mode
from decafclaw.events import EventBus


@pytest.fixture
def manager(config):
    bus = EventBus()
    return ConversationManager(config, bus)


# -- Message queueing ---------------------------------------------------------


@pytest.mark.asyncio
async def test_message_queued_when_busy(manager):
    state = manager._get_or_create("conv-1")
    state.busy = True

    await manager.send_message("conv-1", "queued msg", user_id="user")

    assert len(__import__("decafclaw.inbox", fromlist=["_read_inbox"])._read_inbox(manager.config, state.conv_id)) == 1
    assert (
        __import__("decafclaw.inbox", fromlist=["_read_inbox"])._read_inbox(manager.config, state.conv_id)[0]["text"]
        == "queued msg"
    )


# -- Send message with mocked agent turn ---------------------------------------


@pytest.mark.asyncio
async def test_send_message_queues_multiple_when_busy(manager):
    """Multiple messages while busy all get queued."""
    state = manager._get_or_create("conv-1")
    state.busy = True

    await manager.send_message("conv-1", "msg 1", user_id="user")
    await manager.send_message("conv-1", "msg 2", user_id="user")
    await manager.send_message("conv-1", "msg 3", user_id="user")

    assert len(__import__("decafclaw.inbox", fromlist=["_read_inbox"])._read_inbox(manager.config, state.conv_id)) == 3
    assert [
        m["text"]
        for m in __import__("decafclaw.inbox", fromlist=["_read_inbox"])._read_inbox(manager.config, state.conv_id)
    ] == ["msg 1", "msg 2", "msg 3"]


# -- Startup recovery ----------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_scan_resumes_scheduled_wakes(manager):
    """Startup scan resumes pending scheduled wakes (#1103)."""
    from datetime import datetime, timedelta, timezone

    from decafclaw.scheduled_wakes import ScheduledWake, save_scheduled_wakes

    conv_id = "conv-wake-startup"
    now = datetime.now(timezone.utc)
    future_dt = now + timedelta(seconds=120)
    wake = ScheduledWake(
        schedule_id="wake_start_1",
        conv_id=conv_id,
        delay_seconds=120,
        reason="Verify deploy",
        scheduled_at=now.isoformat(),
        fire_at=future_dt.isoformat(),
        fire_at_ts=future_dt.timestamp(),
        metadata={},
    )
    save_scheduled_wakes(manager.config, conv_id, [wake])

    await manager.startup_scan()
    assert "wake_start_1" in manager.scheduled_wakes._tasks

    await manager.shutdown()


# -- Workflow startup recovery -------------------------------------------------


@pytest.mark.asyncio
async def test_startup_scan_workflows_resumes_running(manager):
    """A workflow in status='running' should be re-enqueued with attempts bumped."""
    from decafclaw.archive import append_message
    from decafclaw.workflow.journal import Journal, load_journal, save_journal

    conv_id = "conv-wf-running"
    # iter_conversation_archives only yields dirs that contain archive.jsonl.
    append_message(manager.config, conv_id, {"role": "user", "content": "hi"})
    save_journal(manager.config, conv_id, Journal(workflow_name="interview", status="running"))

    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock) as mock_eq:
        resumed = await manager.startup_scan_workflows()

    assert resumed == 1
    mock_eq.assert_awaited_once()
    _, kwargs = mock_eq.call_args
    assert kwargs["kind"] is TurnKind.WORKFLOW
    assert kwargs["metadata"] == {"workflow_name": "interview", "resume": True}

    reloaded = load_journal(manager.config, conv_id)
    assert reloaded is not None
    assert reloaded.attempts == 1
    assert reloaded.status == "running"


@pytest.mark.asyncio
async def test_startup_scan_workflows_skips_non_running(manager):
    """Journals in done/error/suspended are not re-enqueued."""
    from decafclaw.archive import append_message
    from decafclaw.workflow.journal import Journal, save_journal

    for conv_id, status in (
        ("conv-wf-done", "done"),
        ("conv-wf-error", "error"),
        ("conv-wf-suspended", "suspended"),
    ):
        append_message(manager.config, conv_id, {"role": "user", "content": "hi"})
        save_journal(manager.config, conv_id, Journal(workflow_name="wf-" + status, status=status))

    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock) as mock_eq:
        resumed = await manager.startup_scan_workflows()

    assert resumed == 0
    mock_eq.assert_not_called()


@pytest.mark.asyncio
async def test_startup_scan_workflows_hits_attempt_cap(manager):
    """A running journal at the cap flips to 'error' and is not re-enqueued."""
    from decafclaw.archive import append_message
    from decafclaw.workflow.journal import Journal, load_journal, save_journal

    conv_id = "conv-wf-cap"
    append_message(manager.config, conv_id, {"role": "user", "content": "hi"})
    # Default cap is 3; attempts=3 means "already tried thrice" → next resume
    # would exceed the cap.
    save_journal(manager.config, conv_id, Journal(workflow_name="stuck", status="running", attempts=3))

    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock) as mock_eq:
        resumed = await manager.startup_scan_workflows()

    assert resumed == 0
    mock_eq.assert_not_called()

    reloaded = load_journal(manager.config, conv_id)
    assert reloaded is not None
    assert reloaded.status == "error"
    assert reloaded.attempts == 3


@pytest.mark.asyncio
async def test_startup_scan_workflows_increments_before_enqueue(manager, caplog):
    """If enqueue_turn raises, attempts must already be on disk and the scan
    must fail-open to the next conversation.

    This is the crash-safety + fail-open guarantee: an attempt is 'spent' the
    moment the scan commits to retrying (so a crash inside enqueue_turn cannot
    leak into an infinite retry loop), and a single conversation's enqueue
    failure must not block startup for other conversations.
    """
    import logging

    from decafclaw.archive import append_message
    from decafclaw.workflow.journal import Journal, load_journal, save_journal

    conv_id = "conv-wf-crash"
    append_message(manager.config, conv_id, {"role": "user", "content": "hi"})
    save_journal(manager.config, conv_id, Journal(workflow_name="explodes", status="running"))

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("enqueue failed")

    with patch.object(manager, "enqueue_turn", new=_boom):
        with caplog.at_level(logging.WARNING):
            resumed = await manager.startup_scan_workflows()

    assert resumed == 0
    reloaded = load_journal(manager.config, conv_id)
    assert reloaded is not None
    assert reloaded.attempts == 1
    assert any("Failed to enqueue workflow resume" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_startup_scan_workflows_empty(manager):
    """No conversations → zero resumes, no enqueues."""
    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock) as mock_eq:
        resumed = await manager.startup_scan_workflows()

    assert resumed == 0
    mock_eq.assert_not_called()


@pytest.mark.asyncio
async def test_startup_scan_workflows_missing_journal_file(manager):
    """A conversation dir without workflow.json is silently skipped."""
    from decafclaw.archive import append_message

    conv_id = "conv-no-wf"
    append_message(manager.config, conv_id, {"role": "user", "content": "hi"})

    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock) as mock_eq:
        resumed = await manager.startup_scan_workflows()

    assert resumed == 0
    mock_eq.assert_not_called()


@pytest.mark.asyncio
async def test_startup_scan_workflows_corrupt_journal(manager, caplog):
    """A malformed workflow.json logs a warning and does not raise."""
    import logging

    from decafclaw.archive import append_message
    from decafclaw.workflow.paths import workflow_dir, workflow_path

    conv_id = "conv-wf-corrupt"
    append_message(manager.config, conv_id, {"role": "user", "content": "hi"})
    workflow_dir(manager.config, conv_id, create=True)
    workflow_path(manager.config, conv_id).write_text("{not valid json")

    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock) as mock_eq:
        with caplog.at_level(logging.WARNING, logger="decafclaw.conversation_manager"):
            resumed = await manager.startup_scan_workflows()

    assert resumed == 0
    mock_eq.assert_not_called()
    assert any("workflow" in rec.message.lower() and conv_id in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_startup_scan_workflows_multiple_conversations(manager):
    """Two running + one done → exactly the two running are re-enqueued."""
    from decafclaw.archive import append_message
    from decafclaw.workflow.journal import Journal, save_journal

    for conv_id, name, status in (
        ("conv-a", "flow-a", "running"),
        ("conv-b", "flow-b", "done"),
        ("conv-c", "flow-c", "running"),
    ):
        append_message(manager.config, conv_id, {"role": "user", "content": "hi"})
        save_journal(manager.config, conv_id, Journal(workflow_name=name, status=status))

    with patch.object(manager, "enqueue_turn", new_callable=AsyncMock) as mock_eq:
        resumed = await manager.startup_scan_workflows()

    assert resumed == 2
    assert mock_eq.await_count == 2

    seen = {call.kwargs["metadata"]["workflow_name"] for call in mock_eq.await_args_list}
    assert seen == {"flow-a", "flow-c"}
    for call in mock_eq.await_args_list:
        assert call.kwargs["kind"] is TurnKind.WORKFLOW
        assert call.kwargs["metadata"]["resume"] is True


# -- Drain pending-------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_pending_resolves_all_queued_futures(manager, config, monkeypatch):
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
    assert len(__import__("decafclaw.inbox", fromlist=["_read_inbox"])._read_inbox(manager.config, state.conv_id)) == 3

    # Release busy, drain manually.
    state.busy = False
    await manager._drain_pending(state)
    # Let the spawned turn task run.
    for fut in (f1, f2, f3):
        await asyncio.wait_for(fut, timeout=2.0)
    # All three resolved — non-last are None, last is the agent's response.
    assert called == ["one\ntwo\nthree"]


@pytest.mark.asyncio
async def test_enqueue_turn_user_kind_runs_same_as_send_message(manager, config, monkeypatch):
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


# -- TaskMode boundary parsing -------------------------------------------------


def test_parse_task_mode_none_stays_none():
    """None means "derive from the kind", not INTERACTIVE."""
    assert parse_task_mode(None) is None


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("heartbeat", TaskMode.HEARTBEAT),
        ("scheduled", TaskMode.SCHEDULED),
        ("child_agent", TaskMode.CHILD_AGENT),
        ("background_wake", TaskMode.BACKGROUND_WAKE),
        ("", TaskMode.INTERACTIVE),
    ],
)
def test_parse_task_mode_maps_wire_strings(wire, expected):
    assert parse_task_mode(wire) is expected


def test_parse_task_mode_unknown_falls_back_to_interactive():
    assert parse_task_mode("bogus") is TaskMode.INTERACTIVE


# -- Per-kind policy matrix ----------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_turn_heartbeat_kind_uses_for_task(manager, config, monkeypatch):
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
        task_mode=TaskMode.HEARTBEAT,
    )
    await future
    assert seen_ctx["task_mode"] is TaskMode.HEARTBEAT
    assert seen_ctx["skip_reflection"] is True
    assert seen_ctx["skip_vault_retrieval"] is True


@pytest.mark.asyncio
async def test_enqueue_turn_user_kind_has_empty_task_mode(manager, config, monkeypatch):
    seen_ctx = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_ctx["task_mode"] = ctx.task_mode
        from decafclaw.media import ToolResult

        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    future = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.USER,
        prompt="hello",
    )
    await future
    assert seen_ctx["task_mode"] is TaskMode.INTERACTIVE


@pytest.mark.asyncio
async def test_enqueue_turn_wake_kind_defaults_to_background_wake_mode(manager, config, monkeypatch):
    seen_ctx = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_ctx["task_mode"] = ctx.task_mode
        from decafclaw.media import ToolResult

        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    future = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.WAKE,
        prompt="wake nudge",
        history=[],
        # no explicit task_mode
    )
    await future
    assert seen_ctx["task_mode"] is TaskMode.BACKGROUND_WAKE


@pytest.mark.asyncio
async def test_inbox_stores_task_mode_wire_string_and_drain_restores_member(manager, config, monkeypatch):
    """The inbox JSONL stores the wire string; the drained turn sees the member (#1381)."""
    seen_ctx = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_ctx["task_mode"] = ctx.task_mode
        from decafclaw.media import ToolResult

        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    state = manager._get_or_create("c1")
    state.busy = True

    future = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.HEARTBEAT_SECTION,
        prompt="do section",
        history=[],
        task_mode=TaskMode.HEARTBEAT,
    )

    from decafclaw.inbox import _read_inbox

    stored = _read_inbox(manager.config, "c1")
    assert [record["task_mode"] for record in stored] == ["heartbeat"]

    state.busy = False
    await manager._drain_pending(state)
    await asyncio.wait_for(future, timeout=2.0)

    assert seen_ctx["task_mode"] is TaskMode.HEARTBEAT


@pytest.mark.asyncio
async def test_wake_turn_publishes_wake_delivery_when_no_subscribers(manager, config, monkeypatch):
    """When a WAKE turn finishes without subscribers, fallback event is published (#1119 review)."""
    from decafclaw.media import ToolResult

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        return ToolResult(text="Build succeeded on wake")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    received_events = []
    manager.event_bus.subscribe(lambda e: received_events.append(e))

    def setup_channel(ctx):
        ctx.channel_id = "mm-channel-123"
        ctx.thread_id = "mm-root-456"

    future = await manager.enqueue_turn(
        conv_id="c-wake-delivery",
        kind=TurnKind.WAKE,
        prompt="wake nudge",
        context_setup=setup_channel,
        user_id="user_alice",
    )
    await future

    deliveries = [e for e in received_events if e.get("type") == "wake_delivery"]
    assert len(deliveries) == 1
    assert deliveries[0]["channel_id"] == "mm-channel-123"
    assert deliveries[0]["thread_id"] == "mm-root-456"
    assert deliveries[0]["text"] == "Build succeeded on wake"
    assert deliveries[0]["user_id"] == "user_alice"


@pytest.mark.asyncio
async def test_unthreaded_mattermost_wake_delivers_response(manager, config, monkeypatch):
    """Unthreaded Mattermost conversations (conv_id == channel_id) still deliver wake responses (#1119 review)."""
    from decafclaw.media import ToolResult

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        return ToolResult(text="Top-level wake done")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    received_events = []
    manager.event_bus.subscribe(lambda e: received_events.append(e))

    conv_id = "mm-top-channel-888"

    def setup_channel(ctx):
        ctx.channel_id = conv_id
        ctx.thread_id = ""
        ctx.transport = "mattermost"

    future = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.WAKE,
        prompt="wake nudge",
        context_setup=setup_channel,
        user_id="user_bob",
    )
    await future

    deliveries = [e for e in received_events if e.get("type") == "wake_delivery"]
    assert len(deliveries) == 1
    assert deliveries[0]["conv_id"] == conv_id
    assert deliveries[0]["channel_id"] == conv_id
    assert deliveries[0]["transport"] == "mattermost"
    assert deliveries[0]["text"] == "Top-level wake done"


@pytest.mark.asyncio
async def test_queued_wake_metadata_restores_context_setup_after_restart(manager, config, monkeypatch):
    """Inbox recovery of queued wake turns restores routing only from authoritative scheduled_wakes (#1119 review)."""
    from decafclaw.inbox import _write_inbox
    from decafclaw.media import ToolResult
    from decafclaw.scheduled_wakes import ScheduledWake, load_scheduled_wakes, save_scheduled_wakes

    conv_id = "c-inbox-recover"
    state = manager._get_or_create(conv_id)

    # Save the authoritative schedule record outside the workspace
    wake = ScheduledWake(
        schedule_id="wake-123",
        conv_id=conv_id,
        delay_seconds=60,
        reason="Test",
        scheduled_at="2026-10-22T00:00:00Z",
        fire_at="2026-10-22T00:01:00Z",
        fire_at_ts=1792644881.0,
        channel_id="ch-restored-456",
        thread_id="th-restored-789",
        transport="mattermost",
    )
    save_scheduled_wakes(config, conv_id, [wake])

    # Simulate an inbox entry queued behind a busy turn before server restarted
    _write_inbox(
        config,
        conv_id,
        [
            {
                "turn_id": "turn-wake-1",
                "kind": "wake",
                "text": "Wake prompt",
                "user_id": "user_alice",
                "metadata": {
                    "schedule_id": "wake-123",
                    "channel_id": "ch-forged-999",  # Attempted override in inbox
                    "thread_id": "th-forged-999",
                    "transport": "mattermost",
                },
            }
        ],
    )

    seen_ctx = {}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen_ctx["channel_id"] = ctx.channel_id
        seen_ctx["thread_id"] = ctx.thread_id
        seen_ctx["transport"] = ctx.transport
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    await manager._drain_pending(state)
    if state.agent_task:
        await state.agent_task

    # Verifies routing came from the authoritative wake record, NOT the forged inbox fields
    assert seen_ctx["channel_id"] == "ch-restored-456"
    assert seen_ctx["thread_id"] == "th-restored-789"
    assert seen_ctx["transport"] == "mattermost"
    # And verifies the queued record was consumed upon turn start
    assert load_scheduled_wakes(config, conv_id) == []


@pytest.mark.asyncio
async def test_enqueue_turn_wake_kind_restores_skill_state(manager, config, monkeypatch):
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
async def test_drain_pending_fires_mixed_kinds_one_at_a_time(manager, config, monkeypatch):
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
    wake = await manager.enqueue_turn("c1", kind=TurnKind.WAKE, prompt="wake", history=[])

    assert len(__import__("decafclaw.inbox", fromlist=["_read_inbox"])._read_inbox(manager.config, state.conv_id)) == 3

    # Release busy; manually drain.
    state.busy = False
    await manager._drain_pending(state)

    # Wait for drain-triggered turns (_start_turn runs as asyncio.Task).
    for fut in (u1, u2, wake):
        await asyncio.wait_for(fut, timeout=2.0)

    # Expected: combined USER turn first, then WAKE turn.
    assert len(fires) == 2
    assert fires[0]["text"] == "hello1\nhello2"
    assert fires[0]["task_mode"] is TaskMode.INTERACTIVE
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
        fut = await manager.enqueue_turn("c1", kind=TurnKind.WAKE, prompt=f"wake-{i}", history=[])
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
    monkeypatch.setattr("decafclaw.conversation_manager.time.monotonic", lambda: now[0])

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


# -- Drain pending fanout------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_pending_fanout_handles_head_exception(manager, config, monkeypatch):
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
    assert len(__import__("decafclaw.inbox", fromlist=["_read_inbox"])._read_inbox(manager.config, state.conv_id)) == 3

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
        assert f3.result() is not None and "error" in f3.result().text
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
        seen.append(
            {
                "user_id": ctx.user_id,
                "channel_name": getattr(ctx, "channel_name", None),
                "task_mode": ctx.task_mode,
            }
        )
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # 1. USER turn — sets user_id + context_setup
    f1 = await manager.enqueue_turn(
        "c1",
        kind=TurnKind.USER,
        prompt="hi",
        user_id="alice",
        context_setup=my_setup,
    )
    await asyncio.wait_for(f1, timeout=2.0)

    # 2. WAKE turn — no explicit user_id/context_setup — inherits
    f2 = await manager.enqueue_turn(
        "c1",
        kind=TurnKind.WAKE,
        prompt="wake",
        history=[],
    )
    await asyncio.wait_for(f2, timeout=2.0)

    assert len(seen) == 2
    # USER turn
    assert seen[0]["user_id"] == "alice"
    assert seen[0]["channel_name"] == "custom-channel"
    # WAKE turn inherits user_id and context_setup
    assert seen[1]["user_id"] == "alice"
    assert seen[1]["channel_name"] == "custom-channel"
    assert seen[1]["task_mode"] is TaskMode.BACKGROUND_WAKE


@pytest.mark.asyncio
async def test_wake_without_prior_user_turn_uses_empty_context(manager, config, monkeypatch):
    """WAKE on a conv with no prior USER turn gets empty user_id / no setup."""
    from decafclaw.media import ToolResult

    seen = []

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen.append(
            {
                "user_id": ctx.user_id,
                "channel_name": getattr(ctx, "channel_name", None),
            }
        )
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # No prior USER turn — fire WAKE directly (like a heartbeat-originated wake)
    f = await manager.enqueue_turn(
        "heartbeat-T-0",
        kind=TurnKind.WAKE,
        prompt="wake",
        history=[],
    )
    await asyncio.wait_for(f, timeout=2.0)

    assert len(seen) == 1
    assert seen[0]["user_id"] == ""


@pytest.mark.asyncio
async def test_wake_explicit_context_overrides_inherited(manager, config, monkeypatch):
    """If caller passes explicit user_id/context_setup to WAKE, those win
    over the inherited values."""
    from decafclaw.media import ToolResult

    seen = []

    def user_setup(ctx):
        ctx.channel_name = "user-channel"

    def wake_setup(ctx):
        ctx.channel_name = "wake-channel"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        seen.append(
            {
                "user_id": ctx.user_id,
                "channel_name": getattr(ctx, "channel_name", None),
            }
        )
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    f1 = await manager.enqueue_turn(
        "c1",
        kind=TurnKind.USER,
        prompt="hi",
        user_id="alice",
        context_setup=user_setup,
    )
    await asyncio.wait_for(f1, timeout=2.0)

    f2 = await manager.enqueue_turn(
        "c1",
        kind=TurnKind.WAKE,
        prompt="wake",
        history=[],
        user_id="explicit-wake-user",
        context_setup=wake_setup,
    )
    await asyncio.wait_for(f2, timeout=2.0)

    assert seen[1]["user_id"] == "explicit-wake-user"
    assert seen[1]["channel_name"] == "wake-channel"


# -- Concurrency: per-conv lock (issue #440) ----------------------------------


@pytest.mark.asyncio
async def test_concurrent_user_enqueue_serializes_via_lock(manager, config, monkeypatch):
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
    assert call_count == 1, f"expected exactly one turn started, got {call_count}"
    assert (
        len(__import__("decafclaw.inbox", fromlist=["_read_inbox"])._read_inbox(manager.config, state.conv_id)) == 1
    ), (
        f"expected one pending message, got {len(__import__('decafclaw.inbox', fromlist=['_read_inbox'])._read_inbox(manager.config, state.conv_id))}"
    )

    # Drain: release the parked turn so the queued one also runs.
    release.set()
    await asyncio.wait_for(f1, timeout=2.0)
    await asyncio.wait_for(f2, timeout=2.0)


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
    __import__("decafclaw.inbox", fromlist=["_append_inbox"])._append_inbox(
        manager.config,
        "conv-drain-defer",
        {
            "turn_id": "mock_id",
            "kind": "USER",
            "text": "queued",
            "user_id": "u",
            "archive_text": "",
            "wiki_page": None,
            "task_mode": None,
            "metadata": None,
        },
    )
    state.inmemory_turn_data["mock_id"] = {
        "context_setup": None,
        "command_ctx": None,
        "attachments": None,
        "history": None,
        "future": None,
    }

    # Drain must defer (no dispatch) when busy is already set.
    await manager._drain_pending(state)

    # The queued message must still be there for the new in-flight
    # turn's finally-block drain to pick up.
    assert len(__import__("decafclaw.inbox", fromlist=["_read_inbox"])._read_inbox(manager.config, state.conv_id)) == 1
    # The agent_task must not have been overwritten.
    assert state.agent_task is fake_task

    # Clean up.
    await fake_task


# -- Inbox / JSONL Queue (issue #779) ------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_turn_writes_to_jsonl(manager, config):
    """WHEN enqueue_turn is called, THEN the system SHALL write the incoming turn to a durable JSONL session inbox file."""
    import json

    from decafclaw.conversation_paths import sidecar_path

    conv_id = "conv-inbox-write"
    state = manager._get_or_create(conv_id)
    state.busy = True

    # Actually wait, enqueue_turn returns a Future. We await it to finish the write.
    await manager.enqueue_turn(conv_id, kind="USER", prompt="hello inbox")

    inbox_path = sidecar_path(config, conv_id, "inbox.jsonl")
    assert inbox_path.exists(), "Inbox JSONL file should exist"

    with open(inbox_path, "r") as file:
        lines = file.readlines()

    assert len(lines) >= 1
    data = json.loads(lines[-1])
    assert data["text"] == "hello inbox"
    assert data["kind"] == "USER"


@pytest.mark.asyncio
async def test_inbox_drained_by_worker(manager, config, monkeypatch):
    """GIVEN an active conversation, THEN the system SHALL run a detached background worker task that continuously polls and drains its JSONL inbox serially."""
    import asyncio

    conv_id = "conv-inbox-drain"

    run_called = asyncio.Event()

    async def mock_run_agent_turn(ctx, user_message, history, **kwargs):
        run_called.set()
        from decafclaw.media import ToolResult

        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", mock_run_agent_turn)

    await manager.enqueue_turn(conv_id, kind="USER", prompt="process me")

    # Wait for the turn to be processed
    await asyncio.wait_for(run_called.wait(), timeout=2.0)

    from decafclaw.conversation_paths import sidecar_path

    inbox_path = sidecar_path(config, conv_id, "inbox.jsonl")
    if inbox_path.exists():
        with open(inbox_path, "r") as file:
            lines = file.readlines()
        assert len(lines) == 0, "Inbox should be drained and empty"
    else:
        assert True, "Inbox file deleted because it is empty"


@pytest.mark.asyncio
async def test_pending_inputs_survive_restart(manager, config, monkeypatch):
    """GIVEN a server restart, WHEN the system initializes, THEN it SHALL process any pending turns found in the JSONL session inbox exactly once."""
    import asyncio
    import json

    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.conversation_paths import sidecar_path

    conv_id = "conv-inbox-restart"

    inbox_path = sidecar_path(config, conv_id, "inbox.jsonl")
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    with open(inbox_path, "w") as file:
        file.write(json.dumps({"turn_id": "restart-id", "kind": "USER", "text": "survived restart"}) + "\n")
    with open(inbox_path.parent / "archive.jsonl", "w"):
        pass

    run_called = asyncio.Event()
    processed_prompts = []

    async def mock_run_agent_turn(ctx, user_message, history, **kwargs):
        processed_prompts.append(user_message)
        run_called.set()
        from decafclaw.media import ToolResult

        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", mock_run_agent_turn)

    new_manager = ConversationManager(config, manager.event_bus)
    await new_manager.startup_scan()

    await asyncio.wait_for(run_called.wait(), timeout=2.0)

    assert "survived restart" in processed_prompts
