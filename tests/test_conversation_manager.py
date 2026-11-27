"""Conversation-manager turn-lifecycle tests: state, subscription/emit, history,
cancel and abort archive markers, completion events, streaming callbacks, and
persisted per-conversation state.

Split from the former monolithic ``test_conversation_manager.py`` by concern
(#1421). See the sibling ``test_conversation_manager_{confirmations,enqueue}.py``
files for confirmation and enqueue coverage."""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from decafclaw.archive import read_archive
from decafclaw.context import TaskMode, TokenUsage
from decafclaw.context_types import InjectionCursors
from decafclaw.conversation_manager import (
    _CTX_DRIVEN_FIELDS,
    _PERSISTED_BINDINGS,
    CANCEL_MARKER_TEXT,
    TURN_ABORTED_MARKER_TEXT,
    ConversationManager,
    PersistedTurnState,
    TurnKind,
    _build_message_complete,
    _flatten_side_question_history,
    _write_cancel_archive,
)
from decafclaw.events import EventBus


def _get_inbox_len(config, conv_id):
    from decafclaw.inbox import _read_inbox

    return len(_read_inbox(config, conv_id))


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


def test_get_activity_status(manager):
    assert manager.get_activity_status("unknown") == "idle"

    state = manager._get_or_create("c1")
    assert manager.get_activity_status("c1") == "idle"

    state.busy = True
    assert manager.get_activity_status("c1") == "busy"

    from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest

    state.pending_confirmation = ConfirmationRequest(
        confirmation_id="conf-1",
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        message="Run ls?",
    )
    assert manager.get_activity_status("c1") == "waiting"

    state.busy = False
    assert manager.get_activity_status("c1") == "waiting"


@pytest.mark.asyncio
async def test_activity_status_event_bus_emissions(manager):
    events = []

    async def on_event(ev):
        if ev.get("type") == "conversation_status":
            events.append(ev)

    manager.event_bus.subscribe(on_event)

    from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest

    req = ConfirmationRequest(
        confirmation_id="conf-test",
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        message="allow test",
    )

    # post_confirmation emits "waiting"
    await manager.post_confirmation("c1", req)
    assert any(e["conv_id"] == "c1" and e["status"] == "waiting" for e in events)

    # respond_to_confirmation clears waiting
    events.clear()
    await manager.respond_to_confirmation("c1", "conf-test", approved=True)
    assert any(e["conv_id"] == "c1" and e["status"] == "idle" for e in events)


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


# -- Cancel archive helper (issue #491) ----------------------------------------


def test_write_cancel_archive_with_partial(config):
    """When partial text is present, _write_cancel_archive emits two
    archive rows: the partial assistant message, then the cancel marker."""
    conv_id = "conv-cancel-1"
    _write_cancel_archive(config, conv_id, "Once upon a time, in ")

    messages = read_archive(config, conv_id)
    assert len(messages) == 2
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == "Once upon a time, in "
    assert messages[1]["role"] == "cancel_marker"
    assert messages[1]["content"] == CANCEL_MARKER_TEXT


def test_write_cancel_archive_without_partial(config):
    """Empty partial → only the cancel marker is archived."""
    conv_id = "conv-cancel-2"
    _write_cancel_archive(config, conv_id, "")

    messages = read_archive(config, conv_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "cancel_marker"
    assert messages[0]["content"] == CANCEL_MARKER_TEXT


def test_write_cancel_archive_skips_partial_when_already_archived(config):
    """If the partial was already archived by the agent loop, the helper
    must not double-write it — only the cancel marker is appended."""
    conv_id = "conv-cancel-3"
    _write_cancel_archive(
        config,
        conv_id,
        "partial text",
        partial_already_archived=True,
    )

    messages = read_archive(config, conv_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "cancel_marker"


@pytest.mark.asyncio
async def test_cancel_during_turn_archives_marker_and_partial(
    manager,
    config,
    monkeypatch,
):
    """End-to-end: a turn that streams partial text and then raises
    CancelledError should leave the archive with the streamed partial
    (as an assistant row) followed by the cancel_marker row (issue #491).
    The real run_agent_turn archives the user prompt as part of context
    composition; this fake replaces that with a manual write."""
    from decafclaw.archive import append_message

    conv_id = "c1-cancel-integration"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        # Emulate the real agent's user-archive step.
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "user",
                "content": user_message,
            },
        )
        # Simulate streaming a few text chunks before cancellation.
        assert ctx.on_stream_chunk is not None
        await ctx.on_stream_chunk("text", "Once upon a time, ")
        await ctx.on_stream_chunk("text", "in a faraway kingdom...")
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "decafclaw.agent.run_agent_turn",
        fake_run_agent_turn,
    )

    future = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="write me a 600-word essay",
        user_id="u",
    )
    await asyncio.wait_for(future, timeout=2.0)

    messages = read_archive(config, conv_id)
    roles = [m["role"] for m in messages]
    # user, assistant (partial), cancel_marker — in that order.
    assert roles == ["user", "assistant", "cancel_marker"]
    assert messages[0]["content"] == "write me a 600-word essay"
    assert messages[1]["content"] == "Once upon a time, in a faraway kingdom..."
    assert messages[2]["content"] == CANCEL_MARKER_TEXT


@pytest.mark.asyncio
async def test_cancel_without_partial_archives_marker_only(
    manager,
    config,
    monkeypatch,
):
    """When no text streamed before cancel, the archive holds only the
    marker for the cancelled turn (no empty assistant row)."""
    from decafclaw.archive import append_message

    conv_id = "c1-cancel-no-partial"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "user",
                "content": user_message,
            },
        )
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "decafclaw.agent.run_agent_turn",
        fake_run_agent_turn,
    )

    future = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="hi",
        user_id="u",
    )
    await asyncio.wait_for(future, timeout=2.0)

    messages = read_archive(config, conv_id)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "cancel_marker"]
    assert messages[1]["content"] == CANCEL_MARKER_TEXT


@pytest.mark.asyncio
async def test_cancel_observed_cleanly_still_writes_marker(
    manager,
    config,
    monkeypatch,
):
    """When the agent loop observes the cancel signal at iteration start
    and returns a ToolResult cleanly (no CancelledError), the manager's
    normal-path emit must still persist the cancel marker (issue #491).
    This guards against the iteration-boundary race where task.cancel()
    hasn't fired yet but cancel_event has been set."""
    from decafclaw.archive import append_message
    from decafclaw.media import ToolResult

    conv_id = "c1-cancel-clean-return"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "user",
                "content": user_message,
            },
        )
        # Set cancel_event AND mark the manager that the agent observed
        # it — emulates _check_cancelled firing at iteration start and
        # returning ToolResult via _Final.
        ctx.cancelled.set()
        ctx.manager.note_cancel_observed_by_agent(ctx.conv_id)
        return ToolResult(text="[Agent turn cancelled by user]")

    monkeypatch.setattr(
        "decafclaw.agent.run_agent_turn",
        fake_run_agent_turn,
    )

    future = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="hi",
        user_id="u",
    )
    await asyncio.wait_for(future, timeout=2.0)

    messages = read_archive(config, conv_id)
    roles = [m["role"] for m in messages]
    assert "cancel_marker" in roles, f"expected cancel marker in archive after clean cancel return; got roles={roles}"
    cancel_idx = roles.index("cancel_marker")
    assert messages[cancel_idx]["content"] == CANCEL_MARKER_TEXT


@pytest.mark.asyncio
async def test_late_cancel_after_completion_no_marker(
    manager,
    config,
    monkeypatch,
):
    """When cancel_event is set AFTER the agent loop already returned
    a real response (a late cancel that didn't actually interrupt
    anything), the archive must NOT carry a cancel marker — the
    response was real and delivered. Distinguishes via the explicit
    cancel_observed_by_agent flag rather than raw cancel_event state.
    Issue #491 Copilot review."""
    from decafclaw.archive import append_message
    from decafclaw.media import ToolResult

    conv_id = "c1-cancel-too-late"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "user",
                "content": user_message,
            },
        )
        # The agent completes a real response. Cancel fires AFTER the
        # agent returned (e.g., the user clicked cancel just after the
        # final chunk landed). The agent never observed it.
        ctx.cancelled.set()
        return ToolResult(text="here is the real answer")

    monkeypatch.setattr(
        "decafclaw.agent.run_agent_turn",
        fake_run_agent_turn,
    )

    future = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="hi",
        user_id="u",
    )
    await asyncio.wait_for(future, timeout=2.0)

    messages = read_archive(config, conv_id)
    roles = [m["role"] for m in messages]
    assert "cancel_marker" not in roles, f"late cancel after real completion must not write a marker; got roles={roles}"


@pytest.mark.asyncio
async def test_cancel_skips_partial_when_already_archived(
    manager,
    config,
    monkeypatch,
):
    """When the agent loop already archived the assistant content before
    cancel propagated, the marker is appended but no duplicate partial
    row is written."""
    from decafclaw.archive import append_message

    conv_id = "c1-cancel-dedupe"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        # Simulate streaming + the agent loop archiving the partial.
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "user",
                "content": user_message,
            },
        )
        assert ctx.on_stream_chunk is not None
        await ctx.on_stream_chunk("text", "Hello ")
        await ctx.on_stream_chunk("text", "world")
        # The real agent does this via _archive() in agent.py — emulate.
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "assistant",
                "content": "Hello world",
            },
        )
        ctx.manager.note_assistant_archived(ctx.conv_id, has_content=True)
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "decafclaw.agent.run_agent_turn",
        fake_run_agent_turn,
    )

    future = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="say hi",
        user_id="u",
    )
    await asyncio.wait_for(future, timeout=2.0)

    messages = read_archive(config, conv_id)
    # user, assistant (from agent), cancel_marker — no duplicate assistant
    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "cancel_marker",
    ]
    assert messages[1]["content"] == "Hello world"


@pytest.mark.asyncio
async def test_partial_assistant_chunks_resets_each_turn(manager):
    """ConversationState.partial_assistant_chunks starts empty and
    tracks streamed text for the current turn only."""
    state = manager._get_or_create("conv-stream-1")
    assert state.partial_assistant_chunks == []
    state.partial_assistant_chunks.append("leftover from prior turn")
    # _start_turn resets this before any chunk could arrive. We assert
    # the field exists and is mutable; the reset behavior is exercised
    # by the integration tests above.
    state.partial_assistant_chunks = []
    assert state.partial_assistant_chunks == []


# -- Turn-aborted archive marker (issue #517) ----------------------------------


def test_write_turn_aborted_marker_once_latches(manager, config):
    """Two consecutive calls produce only one marker row and set the
    latch."""
    conv_id = "conv-aborted-latch"
    state = manager._get_or_create(conv_id)

    manager._write_turn_aborted_marker_once(conv_id, state)
    manager._write_turn_aborted_marker_once(conv_id, state)

    messages = read_archive(config, conv_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "turn_aborted"
    assert messages[0]["content"] == TURN_ABORTED_MARKER_TEXT
    assert state.turn_aborted_marker_written is True


@pytest.mark.asyncio
async def test_exception_during_turn_archives_marker(
    manager,
    config,
    monkeypatch,
):
    """End-to-end: a turn that archives the user prompt then raises a
    non-CancelledError exception must leave a turn_aborted marker in
    the archive (issue #517)."""
    from decafclaw.archive import append_message

    conv_id = "c1-aborted-no-partial"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "user",
                "content": user_message,
            },
        )
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "decafclaw.agent.run_agent_turn",
        fake_run_agent_turn,
    )

    future = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="please do the thing",
        user_id="u",
    )
    await asyncio.wait_for(future, timeout=2.0)

    messages = read_archive(config, conv_id)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "turn_aborted"]
    assert messages[0]["content"] == "please do the thing"
    assert messages[1]["content"] == TURN_ABORTED_MARKER_TEXT


@pytest.mark.asyncio
async def test_exception_during_turn_archives_partial_then_marker(
    manager,
    config,
    monkeypatch,
):
    """When a partial assistant stream was captured before the
    exception, the archive order is user → assistant(partial) →
    turn_aborted, preserving any delivered text."""
    from decafclaw.archive import append_message

    conv_id = "c1-aborted-with-partial"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "user",
                "content": user_message,
            },
        )
        assert ctx.on_stream_chunk is not None
        await ctx.on_stream_chunk("text", "Sure, here goes: ")
        await ctx.on_stream_chunk("text", "the answer is...")
        raise RuntimeError("LLM provider crashed mid-stream")

    monkeypatch.setattr(
        "decafclaw.agent.run_agent_turn",
        fake_run_agent_turn,
    )

    future = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="write me a poem",
        user_id="u",
    )
    await asyncio.wait_for(future, timeout=2.0)

    messages = read_archive(config, conv_id)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "turn_aborted"]
    assert messages[1]["content"] == "Sure, here goes: the answer is..."
    assert messages[2]["content"] == TURN_ABORTED_MARKER_TEXT


@pytest.mark.asyncio
async def test_exception_partial_already_archived_no_duplicate(
    manager,
    config,
    monkeypatch,
):
    """When the agent loop already archived the assistant content
    before raising, the marker is appended but no duplicate partial
    row is written."""
    from decafclaw.archive import append_message

    conv_id = "c1-aborted-dedupe"

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "user",
                "content": user_message,
            },
        )
        assert ctx.on_stream_chunk is not None
        await ctx.on_stream_chunk("text", "Hello ")
        await ctx.on_stream_chunk("text", "world")
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "assistant",
                "content": "Hello world",
            },
        )
        ctx.manager.note_assistant_archived(ctx.conv_id, has_content=True)
        raise RuntimeError("boom after partial archive")

    monkeypatch.setattr(
        "decafclaw.agent.run_agent_turn",
        fake_run_agent_turn,
    )

    future = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="say hi",
        user_id="u",
    )
    await asyncio.wait_for(future, timeout=2.0)

    messages = read_archive(config, conv_id)
    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "turn_aborted",
    ]
    assert messages[1]["content"] == "Hello world"


@pytest.mark.asyncio
async def test_turn_aborted_latch_resets_between_failing_turns(
    manager,
    config,
    monkeypatch,
):
    """Two consecutive failing turns in the same conversation must
    each leave a turn_aborted marker in the archive — the
    `turn_aborted_marker_written` latch has to reset at the start of
    the second turn. Guards against a regression where the reset is
    accidentally removed from _start_turn (Copilot review on #549)."""
    from decafclaw.archive import append_message

    conv_id = "c1-aborted-two-turns"

    call_count = {"n": 0}

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        call_count["n"] += 1
        append_message(
            ctx.config,
            ctx.conv_id,
            {
                "role": "user",
                "content": user_message,
            },
        )
        raise RuntimeError(f"boom turn {call_count['n']}")

    monkeypatch.setattr(
        "decafclaw.agent.run_agent_turn",
        fake_run_agent_turn,
    )

    first = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="first",
        user_id="u",
    )
    await asyncio.wait_for(first, timeout=2.0)

    second = await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.USER,
        prompt="second",
        user_id="u",
    )
    await asyncio.wait_for(second, timeout=2.0)

    messages = read_archive(config, conv_id)
    roles = [m["role"] for m in messages]
    # Both turns must have produced a marker. If the reset were
    # removed from _start_turn, the second marker would be suppressed
    # by the latch carried over from the first turn.
    assert roles == [
        "user",
        "turn_aborted",
        "user",
        "turn_aborted",
    ], f"expected a marker after each failing turn; got roles={roles}"
    assert messages[1]["content"] == TURN_ABORTED_MARKER_TEXT
    assert messages[3]["content"] == TURN_ABORTED_MARKER_TEXT


# -- Turn start rollback-------------------------------------------------------


@pytest.mark.asyncio
async def test_start_turn_rollback_on_cancelled_status_publication(manager, config, monkeypatch):
    """If turn start is cancelled during status publication, state.busy is rolled back (#1119 review)."""
    state = manager._get_or_create("c-cancel-start")

    async def mock_publish_status(conv_id, status):
        if status == "busy":
            raise asyncio.CancelledError()

    monkeypatch.setattr(manager, "_publish_status", mock_publish_status)

    with pytest.raises(asyncio.CancelledError):
        await manager._start_turn(state, "hello", kind=TurnKind.USER)

    # Must NOT be left stuck in busy=True
    assert state.busy is False
    assert state.agent_task is None


# -- Context construction (#1347) ---------------------------------------------


def _prepare_state(manager, conv_id):
    state = manager._get_or_create(conv_id)
    state.cancel_event = asyncio.Event()
    state.steer_event = asyncio.Event()
    return state


@pytest.mark.asyncio
async def test_build_context_user_turn_assembles_interactive_ctx(manager):
    """``_build_context`` returns the wired USER-turn ctx and history (#1347)."""
    state = _prepare_state(manager, "c-build-user")

    built = await manager._build_context(state, kind=TurnKind.USER, user_id="u-42")

    ctx = built.ctx
    assert ctx.conv_id == "c-build-user"
    assert ctx.channel_id == "c-build-user"
    assert ctx.user_id == "u-42"
    assert ctx.cancelled is state.cancel_event
    assert ctx.steer_event is state.steer_event
    assert ctx.manager is manager
    assert callable(ctx.request_confirmation)
    assert state.active_context is ctx
    assert built.user_id == "u-42"
    assert state.last_user_id == "u-42"
    assert built.history == []
    assert state.history is built.history


@pytest.mark.asyncio
async def test_build_context_wake_inherits_last_transport_context(manager):
    """WAKE turns inherit the last USER turn's identity/setup (#1347).

    The inherited ``user_id`` must come back on the result: ``_start_turn``
    uses it in the wake_delivery payload.
    """
    state = _prepare_state(manager, "c-build-wake")
    state.last_user_id = "u-owner"
    seen = []

    def context_setup(ctx):
        seen.append(ctx)
        ctx.channel_id = "chan-from-setup"

    state.last_context_setup = context_setup

    built = await manager._build_context(state, kind=TurnKind.WAKE)

    assert built.user_id == "u-owner"
    assert seen == [built.ctx]
    assert built.ctx.task_mode is TaskMode.BACKGROUND_WAKE
    assert built.ctx.channel_id == "chan-from-setup"
    assert state.last_channel_id == "chan-from-setup"


@pytest.mark.asyncio
async def test_build_context_task_turn_honors_mode_and_history(manager):
    """Task kinds default to their mode, accept an override, and keep supplied history (#1347)."""
    state = _prepare_state(manager, "c-build-task")
    supplied = [{"role": "user", "content": "history"}]

    built = await manager._build_context(
        state,
        kind=TurnKind.SCHEDULED_TASK,
        task_mode=TaskMode.INTERACTIVE,
        history=supplied,
    )

    assert built.ctx.task_mode is TaskMode.INTERACTIVE
    assert built.history is supplied
    assert state.history is supplied


@pytest.mark.asyncio
async def test_build_context_resets_partial_accounting(manager):
    """Construction clears the previous turn's streamed/cancel accounting (#1347)."""
    state = _prepare_state(manager, "c-build-reset")
    state.partial_assistant_chunks = ["old"]
    state.partial_assistant_thinking_chunks = ["old"]
    state.current_iteration_assistant_chunks = ["old"]
    state.current_iteration_thinking_chunks = ["old"]
    state.cancel_observed_by_agent = True
    state.cancel_marker_written = True
    state.turn_aborted_marker_written = True

    await manager._build_context(state, kind=TurnKind.USER)

    assert state.partial_assistant_chunks == []
    assert state.partial_assistant_thinking_chunks == []
    assert state.current_iteration_assistant_chunks == []
    assert state.current_iteration_thinking_chunks == []
    assert state.cancel_observed_by_agent is False
    assert state.cancel_marker_written is False
    assert state.turn_aborted_marker_written is False


# -- suppress_user_message on WAKE turns -------------------------------------


@pytest.mark.asyncio
async def test_wake_turn_emits_suppress_user_message_when_ok(manager, config, monkeypatch):
    """WAKE turn ending with BACKGROUND_WAKE_OK triggers suppress_user_message=True."""
    events = []
    manager.subscribe("c1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        return ToolResult(text="BACKGROUND_WAKE_OK — nothing to report.")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
    await asyncio.wait_for(fut, timeout=2.0)

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes
    assert completes[-1].get("suppress_user_message") is True


@pytest.mark.asyncio
async def test_wake_turn_no_suppress_when_agent_responds_normally(manager, config, monkeypatch):
    """WAKE turn with a regular response should NOT suppress."""
    events = []
    manager.subscribe("c1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        return ToolResult(text="Here's what happened with the job.")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
    await asyncio.wait_for(fut, timeout=2.0)

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes
    assert completes[-1].get("suppress_user_message") is False


@pytest.mark.asyncio
async def test_user_turn_never_suppresses_even_with_sentinel(manager, config, monkeypatch):
    """USER turns NEVER get suppress_user_message=True, even if the agent
    happens to emit the sentinel."""
    events = []
    manager.subscribe("c1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        return ToolResult(text="BACKGROUND_WAKE_OK (agent wrote sentinel incorrectly)")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.USER, prompt="hello")
    await asyncio.wait_for(fut, timeout=2.0)

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes
    assert completes[-1].get("suppress_user_message") is False


@pytest.mark.asyncio
async def test_message_complete_reports_post_compaction_estimate_1005(manager, config, monkeypatch):
    """#1005 — when a turn compacted, message_complete must carry the
    post-compaction estimate on the dedicated `context_usage` field (the
    context meter's value), so the UI meter drops below 100% instead of
    showing the pre-compaction prompt size. `usage.prompt_tokens` retains
    the honest meaning (the turn's last LLM call) and is left intact."""
    events = []
    manager.subscribe("c1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        # The turn's last LLM call reported a prompt size above the
        # compaction threshold (which is why compaction ran), and the
        # compaction pass recorded the post-compaction estimate.
        ctx.tokens.last_prompt = 50_000
        ctx.tokens.last_compaction_estimate = 12_345
        return ToolResult(text="Compacted and answered.")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.USER, prompt="hello")
    await asyncio.wait_for(fut, timeout=2.0)

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes
    # The meter value is the post-compaction estimate.
    assert completes[-1]["context_usage"] == 12_345
    # usage keeps its real, unchanged meaning (the last LLM call) — the
    # recorder stores this verbatim as turn telemetry.
    assert completes[-1]["usage"]["prompt_tokens"] == 50_000


@pytest.mark.asyncio
async def test_message_complete_context_usage_falls_back_without_compaction_1005(
    manager,
    config,
    monkeypatch,
):
    """#1005 — without a compaction, context_usage equals the real last_prompt
    (the meter still tracks the actual prompt size)."""
    events = []
    manager.subscribe("c1", lambda e: events.append(e))

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        ctx.tokens.last_prompt = 9_876
        ctx.tokens.last_compaction_estimate = None
        return ToolResult(text="No compaction needed.")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.USER, prompt="hello")
    await asyncio.wait_for(fut, timeout=2.0)

    completes = [e for e in events if e.get("type") == "message_complete"]
    assert completes
    assert completes[-1]["context_usage"] == 9_876
    assert completes[-1]["usage"]["prompt_tokens"] == 9_876


# -- message_complete payload builder (#1346) ---------------------------------


def test_build_message_complete_payload_shape():
    """#1346 — the extracted builder returns the full message_complete payload:
    usage from the turn's token counters, context_usage falling back to
    last_prompt without a compaction estimate, and the configured limit."""
    tokens = TokenUsage(total_prompt=100, total_completion=50, last_prompt=80)
    payload = _build_message_complete(
        "hello",
        [{"file_id": "f1"}],
        suppress_user_message=False,
        tokens=tokens,
        context_limit=100_000,
    )
    assert payload == {
        "type": "message_complete",
        "role": "assistant",
        "text": "hello",
        "media": [{"file_id": "f1"}],
        "final": True,
        "suppress_user_message": False,
        "usage": {
            "prompt_tokens": 80,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
        "context_usage": 80,
        "context_limit": 100_000,
    }


def test_build_message_complete_prefers_compaction_estimate():
    """#1346/#1005 — a recorded post-compaction estimate wins over last_prompt
    for context_usage; usage keeps the honest per-call counters."""
    tokens = TokenUsage(
        total_prompt=50_000,
        total_completion=10,
        last_prompt=50_000,
        last_compaction_estimate=12_345,
    )
    payload = _build_message_complete("done", [], tokens=tokens, context_limit=100_000)
    assert payload["context_usage"] == 12_345
    assert payload["usage"]["prompt_tokens"] == 50_000


def test_build_message_complete_optional_thinking_and_provenance():
    """#1346 — thinking and provenance appear only when present, so the wire
    shape stays minimal for ordinary turns."""
    tokens = TokenUsage()
    bare = _build_message_complete("plain", [], tokens=tokens, context_limit=10)
    assert "thinking" not in bare
    assert "provenance" not in bare

    with_extras = _build_message_complete(
        "thoughtful",
        [],
        tokens=tokens,
        context_limit=10,
        thinking="hmm",
        provenance={"skill:foo": [{"name": "bar"}]},
    )
    assert with_extras["thinking"] == "hmm"
    assert with_extras["provenance"] == {"skill:foo": [{"name": "bar"}]}


# -- Side-question history flattening (#1354) ---------------------------------


def test_flatten_side_question_history_merges_tool_calls_into_assistant_text():
    """#1354 — assistant tool_calls merge into the message content as readable
    text, since side questions strip all tools from the model call."""
    out = _flatten_side_question_history(
        [
            {
                "role": "assistant",
                "content": "Deploying now.",
                "tool_calls": [
                    {"function": {"name": "run_shell_command", "arguments": '{"command": "make deploy"}'}},
                ],
            },
        ]
    )
    assert out == [
        {
            "role": "assistant",
            "content": 'Deploying now.\n[Tool call: run_shell_command({"command": "make deploy"})]',
        }
    ]


def test_flatten_side_question_history_converts_tool_results_to_user():
    """#1354 — tool results become user messages prefixed with the tool name,
    falling back to the call id or a bare label."""
    out = _flatten_side_question_history(
        [
            {"role": "tool", "name": "run_shell_command", "tool_call_id": "call-1", "content": "ok"},
            {"role": "tool", "tool_call_id": "call-2", "content": "no name"},
            {"role": "tool", "content": "no id"},
        ]
    )
    assert out == [
        {"role": "user", "content": "[Tool result: run_shell_command]: ok"},
        {"role": "user", "content": "[Tool result (call-2)]: no name"},
        {"role": "user", "content": "[Tool result]: no id"},
    ]


def test_flatten_side_question_history_remaps_internal_roles_via_role_remap():
    """#1354 — internal injection roles remap to "user" through the shared
    ROLE_REMAP instead of a private role list; empty rows are dropped."""
    out = _flatten_side_question_history(
        [
            {"role": "vault_retrieval", "content": "retrieved notes"},
            {"role": "cancel_marker", "content": CANCEL_MARKER_TEXT},
            {"role": "skill_hint", "content": "<preempt_tool_hint>\n- read_file\n</preempt_tool_hint>"},
            {"role": "recent_journal", "content": ""},
        ]
    )
    assert out == [
        {"role": "user", "content": "retrieved notes"},
        {"role": "user", "content": CANCEL_MARKER_TEXT},
        {"role": "user", "content": "<preempt_tool_hint>\n- read_file\n</preempt_tool_hint>"},
    ]


def test_flatten_side_question_history_skips_metadata_roles():
    """#1354 — metadata-only archive rows (compaction, model, mode,
    decision_slice, and other non-LLM/non-remap roles) are skipped."""
    out = _flatten_side_question_history(
        [
            {"role": "compaction", "content": "summary"},
            {"role": "model", "content": "gpt-x"},
            {"role": "mode", "content": "interactive"},
            {"role": "decision_slice", "content": "{}"},
            {"role": "reflection", "content": "retry note"},
            {"role": "background_event", "content": "wake"},
            {"role": "user", "content": "real question"},
        ]
    )
    assert out == [{"role": "user", "content": "real question"}]


def test_flatten_side_question_history_preserves_attachments_and_system():
    """#1354 — user/system rows pass through with attachments; empty rows
    without attachments are dropped."""
    out = _flatten_side_question_history(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "", "attachments": [{"filename": "a.png"}]},
            {"role": "user", "content": ""},
        ]
    )
    assert out == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "", "attachments": [{"filename": "a.png"}]},
    ]


def test_flatten_side_question_history_mixed_snapshot_shape():
    """#1354 — a mixed archive sequence flattens to the provider-safe snapshot
    run_side_question sends: no tool_calls, no role="tool"."""
    out = _flatten_side_question_history(
        [
            {"role": "user", "content": "Deploy app"},
            {
                "role": "assistant",
                "content": "Deploying now.",
                "tool_calls": [
                    {"function": {"name": "run_shell_command", "arguments": '{"command": "make deploy"}'}},
                ],
            },
            {"role": "tool", "name": "run_shell_command", "tool_call_id": "call-1", "content": "Deployed 1.2.3"},
            {"role": "vault_retrieval", "content": "deployment notes"},
        ]
    )
    for msg in out:
        assert "tool_calls" not in msg
        assert msg["role"] in ("system", "user", "assistant")
    assert out[0] == {"role": "user", "content": "Deploy app"}
    assert "[Tool call: run_shell_command(" in out[1]["content"]
    assert any("[Tool result: run_shell_command]" in m["content"] for m in out)
    assert any("deployment notes" in m.get("content", "") for m in out)


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

    fut = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
    await asyncio.wait_for(fut, timeout=2.0)

    assert posted_messages == []  # suppressed — transport didn't post.


@pytest.mark.asyncio
async def test_wake_rate_limit_bypass_for_calendar(manager, config, monkeypatch):
    """Calendar event wakes must not be dropped by the wake rate limiter."""
    manager._wake_max_per_window = 0

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    dropped = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
    assert dropped.rate_limited is True

    allowed = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.WAKE,
        prompt="calendar",
        history=[],
        bypass_wake_rate_limit=True,
    )
    assert allowed.rate_limited is False
    await asyncio.wait_for(allowed, timeout=2.0)


@pytest.mark.asyncio
async def test_calendar_wake_ignores_background_wake_ok(manager, config, monkeypatch):
    """A non-suppressible wake must deliver even when the agent replies with the sentinel."""
    posted = []

    def subscriber(event):
        if event.get("type") == "message_complete":
            posted.append(event.get("suppress_user_message"))

    manager.subscribe("c1", subscriber)

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        return ToolResult(text="BACKGROUND_WAKE_OK")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    fut = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.WAKE,
        prompt="calendar",
        history=[],
        metadata={"suppressible": False},
    )
    await asyncio.wait_for(fut, timeout=2.0)

    assert posted == [False]


@pytest.mark.asyncio
async def test_calendar_wake_keeps_mattermost_delivery(manager, config, monkeypatch):
    """Calendar wakes keep the existing Mattermost delivery path."""
    published = []

    async def fake_publish(event):
        published.append(event)

    monkeypatch.setattr(manager.event_bus, "publish", fake_publish)

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        return ToolResult(text="reminder")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    def setup(ctx):
        ctx.transport = "mattermost"
        ctx.channel_id = "channel-1"
        ctx.thread_id = "thread-1"

    fut = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.WAKE,
        prompt="calendar",
        history=[],
        metadata={"calendar_event_id": "cal_1234", "suppressible": False},
        context_setup=setup,
    )
    await asyncio.wait_for(fut, timeout=2.0)

    assert sum(1 for event in published if event.get("type") == "wake_delivery") == 1


@pytest.mark.asyncio
async def test_regular_wake_still_publishes_delivery(manager, config, monkeypatch):
    """The delivery path still works for wakes without the calendar flag."""
    published = []

    async def fake_publish(event):
        published.append(event)

    monkeypatch.setattr(manager.event_bus, "publish", fake_publish)

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        return ToolResult(text="reminder")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    def setup(ctx):
        ctx.transport = "mattermost"
        ctx.channel_id = "channel-1"
        ctx.thread_id = "thread-1"

    fut = await manager.enqueue_turn(
        conv_id="c1",
        kind=TurnKind.WAKE,
        prompt="wake",
        history=[],
        context_setup=setup,
    )
    await asyncio.wait_for(fut, timeout=2.0)

    assert any(event.get("type") == "wake_delivery" for event in published)


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

    fut = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.WAKE, prompt="wake", history=[])
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

    fut = await manager.enqueue_turn(conv_id="c1", kind=TurnKind.USER, prompt="hi")
    await asyncio.wait_for(fut, timeout=2.0)

    assert seen_stream_callback[0] is not None  # USER: streaming active


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
        f"_CTX_DRIVEN_FIELDS contains unknown field(s): {_CTX_DRIVEN_FIELDS - declared}"
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
    save_ctx.skills.activated = {"sentinel_skill": ""}
    save_ctx.skip_vault_retrieval = True
    sentinel_ts = datetime(2026, 7, 23, 14, 30)
    sentinel_cursors = InjectionCursors(
        notes_ts="2026-07-23T14:30:00Z",
        notes_boundary=frozenset({"note"}),
        journal_ts=sentinel_ts,
        journal_boundary=frozenset({"entry"}),
    )
    save_ctx.composer.injection_cursors = sentinel_cursors
    save_ctx.composer.prefix_fingerprints = {"tools": "sentinel-digest"}
    manager.set_flag("rt-conv", "active_model", "sentinel-model")

    manager._save_conversation_state(state, save_ctx)

    # Round-trip: a fresh ctx should pick up every field on restore.
    restore_ctx = Context(config=config, event_bus=manager.event_bus)
    manager._restore_per_conv_state(state, restore_ctx)

    assert restore_ctx.tools.extra == {"sentinel_tool": save_ctx.tools.extra["sentinel_tool"]}
    assert restore_ctx.tools.extra_definitions == [{"name": "sentinel_tool"}]
    assert restore_ctx.skills.activated == {"sentinel_skill": ""}
    assert restore_ctx.skip_vault_retrieval is True
    assert restore_ctx.active_model == "sentinel-model"
    assert restore_ctx.composer.injection_cursors == sentinel_cursors
    assert restore_ctx.composer.prefix_fingerprints == {"tools": "sentinel-digest"}

    # Reset cursors to None / empty (e.g. after compaction) and verify they persist and restore
    save_ctx.composer.reset_for_compaction()
    manager._save_conversation_state(state, save_ctx)
    restore_ctx2 = Context(config=config, event_bus=manager.event_bus)
    manager._restore_per_conv_state(state, restore_ctx2)
    assert restore_ctx2.composer.injection_cursors == InjectionCursors()
    assert restore_ctx2.composer.prefix_fingerprints == {}


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


def test_set_flag_compaction_threshold_writes_through_to_persisted(manager):
    """#1099 — per-conversation compaction threshold overrides land on
    ``state.persisted`` (externally driven via set_flag / PATCH)."""
    manager.set_flag("flag-conv", "compaction_threshold", 51200)
    manager.set_flag("flag-conv", "compaction_threshold_ratio", 0.5)
    state = manager.get_state("flag-conv")
    assert state.persisted.compaction_threshold == 51200
    assert state.persisted.compaction_threshold_ratio == 0.5


def test_restore_compaction_threshold_onto_ctx(manager, config):
    """#1099 — restore maps persisted compaction overrides onto ctx so the
    agent loop resolves the effective threshold per conversation."""
    from decafclaw.context import Context

    state = manager._get_or_create("rt-conv")
    state.persisted.compaction_threshold = 51200
    state.persisted.compaction_threshold_ratio = 0.4

    ctx = Context(config=config, event_bus=manager.event_bus)
    manager._restore_per_conv_state(state, ctx)

    assert ctx.compaction_threshold_override == 51200
    assert ctx.compaction_threshold_ratio_override == 0.4


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
