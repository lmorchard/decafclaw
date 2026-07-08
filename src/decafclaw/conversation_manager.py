"""Conversation manager — central orchestrator for agent loops and state.

The conversation manager owns agent loop lifecycle, conversation history,
confirmation state, and event streams. Transport adapters (WebSocket,
Mattermost, interactive terminal) talk to the manager via its public API.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from dataclasses import fields as dc_fields
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

from .archive import append_message
from .confirmations import (
    ConfirmationAction,
    ConfirmationRegistry,
    ConfirmationRequest,
    ConfirmationResponse,
)
from .conversation_paths import iter_conversation_archives
from .heartbeat import is_background_wake_ok
from .workflow.journal import load_journal, save_journal
from .workflow.paths import workflow_path

log = logging.getLogger(__name__)


# Canonical archive text written when a user cancels an in-progress agent
# turn (issue #491). Strong enough wording that the LLM treats the prior
# request as closed instead of re-fulfilling it on the next turn. The
# message is archived under the cancel_marker role and remapped to "user"
# for the LLM via context_composer.ROLE_REMAP.
CANCEL_MARKER_TEXT = (
    "[User cancelled this turn. Do not retry the cancelled request "
    "unless they explicitly ask for it again.]"
)


# Canonical archive text written when an agent turn aborts via an
# unexpected exception (issue #517, follow-up to #491). Same "turn
# closed" signal shape as CANCEL_MARKER_TEXT but for the exception
# path, so the next user turn doesn't see an open prior request and
# re-fulfill it. Defensive wording — does not echo the raw exception,
# which could leak internal state.
TURN_ABORTED_MARKER_TEXT = (
    "[The previous turn failed unexpectedly. Treat the prior request "
    "as not fulfilled and wait for the user to clarify.]"
)


def _write_cancel_archive(
    config, conv_id: str, partial: str,
    *, partial_already_archived: bool = False,
) -> None:
    """Append optional partial assistant content followed by the
    canonical cancel marker to the conversation archive. Chronological
    order matters: the LLM-side conversation reads
    `user → assistant(partial) → cancel_marker(→user) → user`, so the
    partial must precede the marker. Step-level dedup against retry
    failures is handled in `_write_cancel_marker_once`.
    """
    if partial and not partial_already_archived:
        append_message(config, conv_id, {
            "role": "assistant",
            "content": partial,
        })
    append_message(config, conv_id, {
        "role": "cancel_marker",
        "content": CANCEL_MARKER_TEXT,
    })


class TurnKind(Enum):
    """Classification of agent turn origins."""

    USER = "user"
    HEARTBEAT_SECTION = "heartbeat_section"
    SCHEDULED_TASK = "scheduled_task"
    CHILD_AGENT = "child_agent"
    WAKE = "wake"
    WORKFLOW = "workflow"


# Kinds that use Context.for_task (sensible skip defaults for background work).
TASK_KINDS = {
    TurnKind.HEARTBEAT_SECTION,
    TurnKind.SCHEDULED_TASK,
    TurnKind.CHILD_AGENT,
    TurnKind.WAKE,
}

# Default task_mode string for each task kind (can be overridden by caller).
KIND_TASK_MODE = {
    TurnKind.HEARTBEAT_SECTION: "heartbeat",
    TurnKind.SCHEDULED_TASK: "scheduled",
    TurnKind.CHILD_AGENT: "child_agent",
    TurnKind.WAKE: "background_wake",
}

# Task kinds whose conversations are one-shot — no prior archive history,
# no per-conv state persistence. Distinct from WAKE, which fires on a
# persistent conv and therefore should load history and persist state.
EPHEMERAL_TASK_KINDS = TASK_KINDS - {TurnKind.WAKE}

# Kinds whose conversation state (skills, vault flags) should be persisted.
# Ephemeral task kinds (heartbeat/scheduled/child) don't need per-conv state.
STATE_PERSIST_KINDS = {TurnKind.USER, TurnKind.WAKE}


@dataclass
class PersistedTurnState:
    """Per-conversation state that persists across agent turns (#378).

    Replaces the parallel save/restore field lists that used to live
    inline on `ConversationState` (`skill_state` dict + ad-hoc
    booleans/strings). Two field categories live here:

    - **Ctx-driven** (listed in ``_CTX_DRIVEN_FIELDS`` below): written
      by ``_save_conversation_state`` from the live ctx at turn end,
      read by ``_restore_per_conv_state`` onto the next turn's ctx.
    - **Externally-driven** (everything else in this dataclass): set
      by ``ConversationManager.set_flag`` from web / transport
      handlers; restore reads them onto ctx but save never touches
      them.

    Adding a field requires a matching entry in ``_PERSISTED_BINDINGS``
    (the unit test ``test_persisted_field_bindings_exhaustive``
    enforces this) and an explicit decision about which category it
    belongs to.
    """
    extra_tools: dict = field(default_factory=dict)
    extra_tool_definitions: list = field(default_factory=list)
    activated_skills: set = field(default_factory=set)
    skip_vault_retrieval: bool = False
    active_model: str = ""


# Per-field reader/writer bindings between PersistedTurnState and the
# live Context. Exhaustive over PersistedTurnState fields — readers
# fetch the current ctx value; writers apply a state value back onto
# ctx. Save and restore both walk this table so adding a field can't
# silently drop out of either side. The exhaustiveness check lives in
# ``test_persisted_field_bindings_exhaustive``.
_PERSISTED_BINDINGS: dict[str, tuple[Callable[[Any], Any], Callable[[Any, Any], None]]] = {
    "extra_tools": (
        lambda ctx: ctx.tools.extra,
        lambda ctx, v: setattr(ctx.tools, "extra", v),
    ),
    "extra_tool_definitions": (
        lambda ctx: ctx.tools.extra_definitions,
        lambda ctx, v: setattr(ctx.tools, "extra_definitions", v),
    ),
    "activated_skills": (
        lambda ctx: ctx.skills.activated,
        lambda ctx, v: setattr(ctx.skills, "activated", v),
    ),
    "skip_vault_retrieval": (
        lambda ctx: ctx.skip_vault_retrieval,
        lambda ctx, v: setattr(ctx, "skip_vault_retrieval", v),
    ),
    "active_model": (
        lambda ctx: ctx.active_model,
        lambda ctx, v: setattr(ctx, "active_model", v),
    ),
}


# Subset of PersistedTurnState fields whose value flows
# ctx → state on save. Other fields are externally-driven (e.g. by
# `set_flag` from a transport handler) and never overwritten by save.
_CTX_DRIVEN_FIELDS: frozenset[str] = frozenset({
    "extra_tools",
    "extra_tool_definitions",
    "activated_skills",
    "skip_vault_retrieval",
})


# All declared PersistedTurnState field names — precomputed once at
# module load so hot paths like ``set_flag`` don't reflect on every
# call. Stays in sync with the dataclass automatically.
_PERSISTED_FIELD_NAMES: frozenset[str] = frozenset(
    f.name for f in dc_fields(PersistedTurnState)
)


@dataclass
class _QueuedConfirmation:
    """A confirmation request waiting to become active.

    Holds the request, a per-request asyncio.Event the waiter blocks
    on (``promoted_event``), and a slot for the freshly-installed
    active ``confirmation_event`` (``active_event``). The promote
    helper writes ``active_event`` BEFORE signalling ``promoted_event``
    so the waiter never has to re-read ``state.confirmation_event``
    after waking — that re-read had a narrow race window where a
    concurrent caller could null the field out between wake and lock
    re-acquisition (Copilot review on #485 PR). Issue #485.
    """
    request: ConfirmationRequest
    promoted_event: asyncio.Event = field(default_factory=asyncio.Event)
    active_event: asyncio.Event | None = None


def _confirmation_request_payload(request: ConfirmationRequest) -> dict:
    """Build the ``confirmation_request`` emit payload for a request.

    Shared by the initial-active emit and every promote-emit in
    request_confirmation / respond_to_confirmation /
    cancel_pending_confirmation (issue #485).
    """
    return {
        "type": "confirmation_request",
        "confirmation_id": request.confirmation_id,
        "action_type": request.action_type.value,
        "action_data": request.action_data,
        "message": request.message,
        "approve_label": request.approve_label,
        "deny_label": request.deny_label,
        "tool_call_id": request.tool_call_id,
    }


@dataclass
class ConversationState:
    """Per-conversation state managed by the ConversationManager."""
    conv_id: str = ""
    history: list = field(default_factory=list)
    busy: bool = False
    pending_messages: list = field(default_factory=list)
    agent_task: asyncio.Task | None = None
    cancel_event: asyncio.Event | None = None

    # Streamed assistant text accumulated for the current turn so the
    # cancel handler can persist whatever was delivered before cancel
    # (issue #491). Reset at turn start; appended to in on_stream_chunk
    # (list of chunks — joined lazily to avoid O(n²) concatenation).
    # `partial_assistant_archived` indicates whether the agent loop
    # already wrote the assistant content to the archive — if so, the
    # cancel handler only appends the marker, avoiding a duplicate body.
    # `cancel_observed_by_agent` is set by the agent when it observes
    # cancellation cleanly (iteration-start check or streaming short-
    # circuit). The manager's normal-completion path uses this rather
    # than raw `cancel_event.is_set()` to avoid persisting a cancel
    # marker when cancel arrives *after* the turn fully completed.
    # `cancel_marker_written` is a write-once latch: both the clean
    # cancel-observed path and the CancelledError handler funnel through
    # the same marker write; the latch prevents a double-write when
    # both fire.
    # `turn_aborted_marker_written` is the parallel latch for the
    # exception-path marker (issue #517).
    partial_assistant_chunks: list[str] = field(default_factory=list)
    partial_assistant_archived: bool = False
    cancel_observed_by_agent: bool = False
    cancel_marker_written: bool = False
    turn_aborted_marker_written: bool = False

    # Per-conversation lock guarding the dispatch decision and the
    # confirmation lifecycle (issue #440). Held during:
    #   - enqueue_turn's busy-check → start-or-queue sequence
    #     (including the user_message emit, the _start_turn dispatch
    #     up to asyncio.create_task(run()), and any awaits inside
    #     _start_turn's pre-task setup such as context_setup and the
    #     turn_start emit)
    #   - the agent task's finally-block busy=False reset
    #   - _drain_pending's pop-and-dispatch (same scope as
    #     enqueue_turn's dispatch path); re-checks state.busy and
    #     defers if a concurrent enqueue won
    #   - request_confirmation's pending/event/response triple setup
    #     and post-wait teardown (released across event.wait() so the
    #     responder can acquire it); the post-wait timeout-archive
    #     write also runs inside the lock for durability
    #   - respond_to_confirmation's pending-check + archive write +
    #     response-slot claim — archive happens UNDER the lock so the
    #     durable record exists before any waiter/claimer can observe
    #     the claim
    #   - cancel_pending_confirmation's archive write + claim + state
    #     clear (all under the lock for the same durability reason)
    #   - cancel_turn's paired read of cancel_event/agent_task
    #   - recover_confirmation's atomic pop of pending_confirmation
    #   - confirmation_queue mutations and
    #     _promote_next_confirmation_unlocked side-effects (issue #485)
    #     — enqueue-on-collision, promote-on-clear, and queued-waiter
    #     cleanup all happen under the lock so the queue ordering and
    #     the active-slot transitions stay consistent for concurrent
    #     request_confirmation / respond_to_confirmation /
    #     cancel_pending_confirmation callers
    # NOT held during:
    #   - the agent task body itself (asyncio.create_task returns
    #     immediately so the caller's lock-acquire is released before
    #     run() executes)
    #   - confirmation handler invocations in _dispatch_recovery /
    #     recover_confirmation (handlers may do arbitrary I/O)
    #   - subscriber emit awaits AFTER the claim/clear in
    #     respond_to_confirmation, request_confirmation's timeout
    #     emit, and cancel_pending_confirmation's emit — the durable
    #     state is already in place by then, so emit failures log
    #     but don't roll back
    #   - request_confirmation's event.wait() — released to let the
    #     responder acquire the lock and claim the slot
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Confirmation state
    pending_confirmation: ConfirmationRequest | None = None
    confirmation_event: asyncio.Event | None = None
    confirmation_response: ConfirmationResponse | None = None

    # FIFO queue of confirmations awaiting their turn (issue #485). Each
    # entry is a request that arrived while another was active; the waiter
    # blocks on the entry's ``promoted_event`` until
    # ``_promote_next_confirmation_unlocked`` moves it to the active slot.
    confirmation_queue: list[_QueuedConfirmation] = field(default_factory=list)

    # Per-conversation persistent state (survives across turns).
    # See PersistedTurnState for what lives here and how save/restore
    # interact with it.
    persisted: PersistedTurnState = field(default_factory=PersistedTurnState)

    # Circuit breaker state (rate-limiting turns per conversation)
    turn_times: list = field(default_factory=list)
    paused_until: float = 0

    # Wake rate limiter state (per-conv, prevents wake-storms)
    wake_times: list = field(default_factory=list)

    # Transport context from the last USER turn, used as fallback for WAKE
    # turns that don't supply their own user_id / context_setup.
    # Note: context_setup is a closure capturing transport state at the time
    # of the USER turn; replaying it on a later WAKE may use stale state
    # (e.g. thread IDs, post IDs). Transports that need finer control should
    # pass an explicit context_setup when enqueuing WAKE turns.
    last_user_id: str = ""
    last_context_setup: Callable | None = None

    # Event stream subscribers: sub_id -> callback
    subscribers: dict[str, Callable] = field(default_factory=dict)


class ConversationManager:
    """Central orchestrator for agent loops and conversation state.

    Owns agent loop lifecycle, conversation history, confirmation state,
    and per-conversation event streams. Transports interact via the
    public API methods.
    """

    def __init__(self, config, event_bus,
                 confirmation_registry: ConfirmationRegistry | None = None):
        self.config = config
        self.event_bus = event_bus
        self.confirmation_registry = confirmation_registry or ConfirmationRegistry()

        # Workflow user-input resumes via the confirmation recovery path.
        from .workflow.resume import WorkflowUserInputHandler
        self.confirmation_registry.register(
            ConfirmationAction.WORKFLOW_USER_INPUT,
            WorkflowUserInputHandler(self))

        self._conversations: dict[str, ConversationState] = {}

        # Circuit breaker config (from Mattermost config for now —
        # these should move to a transport-agnostic config section)
        self._cb_max_turns = getattr(config.mattermost, "circuit_breaker_max", 10)
        self._cb_window_sec = getattr(config.mattermost, "circuit_breaker_window_sec", 60)
        self._cb_pause_sec = getattr(config.mattermost, "circuit_breaker_pause_sec", 120)

        # Wake rate limiter config
        bg = getattr(config, "background", None)
        self._wake_max_per_window = bg.wake_max_per_window if bg else 20
        self._wake_window_sec = bg.wake_window_sec if bg else 60

    def _get_or_create(self, conv_id: str) -> ConversationState:
        """Get existing conversation state or create a new one."""
        if conv_id not in self._conversations:
            self._conversations[conv_id] = ConversationState(conv_id=conv_id)
        return self._conversations[conv_id]

    def _circuit_breaker_tripped(self, state: ConversationState) -> bool:
        """Check if the circuit breaker has tripped for a conversation.

        Caller must hold ``state.lock`` — read-then-write of
        ``state.paused_until`` and ``state.turn_times`` must be
        atomic with the surrounding dispatch decision (issue #440).
        """

        now = time.monotonic()
        if now < state.paused_until:
            return True
        cutoff = now - self._cb_window_sec
        state.turn_times = [t for t in state.turn_times if t > cutoff]
        if len(state.turn_times) >= self._cb_max_turns:
            state.paused_until = now + self._cb_pause_sec
            log.warning(
                "Circuit breaker tripped for %s: %d turns in %ds, pausing %ds",
                state.conv_id[:8], len(state.turn_times),
                self._cb_window_sec, self._cb_pause_sec)
            return True
        return False

    def _circuit_breaker_record(self, state: ConversationState) -> None:
        """Record an agent turn completion for circuit breaker tracking.

        Caller must hold ``state.lock``.
        """

        state.turn_times.append(time.monotonic())

    # -- Public API ------------------------------------------------------------

    async def enqueue_turn(
        self,
        conv_id: str,
        *,
        kind: TurnKind,
        prompt: str,
        history: list | None = None,
        task_mode: str | None = None,
        context_setup: Callable | None = None,
        user_id: str = "",
        archive_text: str = "",
        attachments: list[dict] | None = None,
        command_ctx: Any = None,
        wiki_page: str | None = None,
        metadata: dict | None = None,
    ) -> asyncio.Future:
        """Submit a turn of any kind. Returns an awaitable that resolves
        when the turn completes."""
        state = self._get_or_create(conv_id)
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        # Dispatch decision runs under state.lock so the busy-check /
        # circuit-breaker / wake-limiter reads are atomic with the
        # decision to queue vs. start a turn. The lock is held across
        # the user_message emit and the synchronous body of
        # _start_turn (which sets busy=True before scheduling the
        # agent task), but the agent task itself runs without the
        # lock. See issue #440.
        async with state.lock:
            # WAKE-kind: rate limiter — drop excess wakes within the window.
            if kind is TurnKind.WAKE:
                now = time.monotonic()
                cutoff = now - self._wake_window_sec
                state.wake_times = [t for t in state.wake_times if t > cutoff]
                if len(state.wake_times) >= self._wake_max_per_window:
                    log.warning(
                        "Wake rate limit exceeded for conv %s "
                        "(%d wakes in last %ds) — dropping wake",
                        conv_id[:8], len(state.wake_times),
                        self._wake_window_sec,
                    )
                    future.set_result(None)
                    return future
                state.wake_times.append(now)

            # USER-kind only: circuit breaker check + user_message event emission.
            if kind is TurnKind.USER:
                if self._circuit_breaker_tripped(state):
                    log.warning("Dropping message for paused conversation %s",
                                conv_id[:8])
                    future.set_result(None)
                    return future
                await self.emit(conv_id, {
                    "type": "user_message",
                    "text": archive_text or prompt,
                    "user_id": user_id,
                })

            if state.busy:
                # Cancel-on-new-message: cancel the current turn if configured
                # (USER kind only, same as previous send_message behavior)
                if (kind is TurnKind.USER
                        and self.config.agent.turn_on_new_message == "cancel"
                        and state.cancel_event
                        and not state.cancel_event.is_set()):
                    log.info("Conv %s busy, cancelling for new message",
                             conv_id[:8])
                    state.cancel_event.set()
                    if state.agent_task and not state.agent_task.done():
                        state.agent_task.cancel()

                state.pending_messages.append({
                    "kind": kind,
                    "text": prompt,
                    "user_id": user_id,
                    "context_setup": context_setup,
                    "archive_text": archive_text,
                    "attachments": attachments,
                    "command_ctx": command_ctx,
                    "wiki_page": wiki_page,
                    "task_mode": task_mode,
                    "history": history,
                    "metadata": metadata,
                    "future": future,
                })
                log.info("Conv %s busy, queued message (%d pending)",
                         conv_id[:8], len(state.pending_messages))
                return future

            await self._start_turn(
                state,
                prompt,
                kind=kind,
                user_id=user_id,
                context_setup=context_setup,
                archive_text=archive_text,
                attachments=attachments,
                command_ctx=command_ctx,
                wiki_page=wiki_page,
                task_mode=task_mode,
                history=history,
                metadata=metadata,
                future=future,
            )
        return future

    async def send_message(
        self,
        conv_id: str,
        text: str,
        *,
        user_id: str = "",
        context_setup: Callable | None = None,
        archive_text: str = "",
        attachments: list[dict] | None = None,
        command_ctx: Any = None,
        wiki_page: str | None = None,
    ) -> None:
        """Submit user input (thin wrapper over enqueue_turn for the USER kind)."""
        await self.enqueue_turn(
            conv_id,
            kind=TurnKind.USER,
            prompt=text,
            user_id=user_id,
            context_setup=context_setup,
            archive_text=archive_text,
            attachments=attachments,
            command_ctx=command_ctx,
            wiki_page=wiki_page,
        )

    async def respond_to_confirmation(
        self,
        conv_id: str,
        confirmation_id: str,
        approved: bool,
        *,
        always: bool = False,
        add_pattern: bool = False,
        data: dict | None = None,
    ) -> None:
        """Resolve a pending confirmation request.

        Persists the response to the archive and wakes the suspended
        agent loop (if still running) or dispatches recovery (if the
        loop died).

        ``data`` carries free-form response payload (widget selections,
        etc.).

        Concurrency: the pending-confirmation match, archive write,
        response-slot claim, pending-state clear, and waiter capture
        all happen under ``state.lock`` so the durable record exists
        before any waiter or concurrent claimer can observe the
        claim (issue #440 + Copilot review on #484). Emit and the
        downstream wake / recovery dispatch run OUTSIDE the lock:
        emit awaits subscribers (arbitrary I/O) and the recovery
        handler may also do arbitrary I/O.
        """
        state = self._conversations.get(conv_id)
        if not state:
            log.warning("No pending confirmation for conv %s", conv_id)
            return

        async with state.lock:
            if not state.pending_confirmation:
                log.warning("No pending confirmation for conv %s", conv_id)
                return
            if state.pending_confirmation.confirmation_id != confirmation_id:
                log.warning(
                    "Confirmation ID mismatch for conv %s: expected %s, got %s",
                    conv_id, state.pending_confirmation.confirmation_id,
                    confirmation_id)
                return
            # A previously-responded confirmation that hasn't been cleared
            # yet (e.g. running-loop case where request_confirmation hasn't
            # observed the event) — skip rather than double-dispatch.
            if state.confirmation_response is not None:
                log.warning(
                    "Confirmation %s already has a response, ignoring "
                    "duplicate respond_to_confirmation for conv %s",
                    confirmation_id, conv_id)
                return

            response = ConfirmationResponse(
                confirmation_id=confirmation_id,
                approved=approved,
                always=always,
                add_pattern=add_pattern,
                data=data or {},
            )
            # Archive the response under the lock so the durable record
            # is in place BEFORE the waiter or any concurrent claimer
            # can observe the claim. Without this, a `request_confirmation`
            # waiter timing out while we're between claim and archive
            # could consume our (uncommitted) response and clear the
            # slot — and if the archive then failed, rollback couldn't
            # restore (Copilot review on #484). `append_message` is
            # sync filesystem I/O, so holding the per-conv lock across
            # it just briefly blocks other lock acquirers on this conv.
            from .archive import append_message
            try:
                append_message(self.config, conv_id,
                               response.to_archive_message())
            except Exception:
                log.exception(
                    "respond_to_confirmation archive write failed for "
                    "conv %s; pending state untouched, caller may retry",
                    conv_id[:8])
                raise
            # Capture the pending request AND atomically clear all
            # pending state under the lock. Clearing here (rather
            # than after the I/O) means a concurrent
            # `request_confirmation` for a NEW request can land in
            # the gap and write its own pending state without
            # overwriting ours; our recovery dispatch uses
            # `claimed_request` (a local variable), not state.
            # The waiter_event is captured for the running-loop wake
            # path before it's cleared. (Copilot review on #484.)
            claimed_request = state.pending_confirmation
            waiter_event = state.confirmation_event
            state.confirmation_response = response
            state.pending_confirmation = None
            state.confirmation_event = None
            # Queue-promotion ownership splits on waiter presence:
            #   * Live waiter: it clears ``confirmation_response``
            #     itself and calls
            #     ``_clear_active_and_promote_unlocked`` from its
            #     post-wait block.
            #   * No waiter (recovery path below): we clear
            #     ``confirmation_response`` and explicitly call
            #     ``_promote_next_confirmation_unlocked`` after the
            #     recovery handler dispatches, so queued entries
            #     don't get orphaned.
            # We do NOT promote here under the same lock as the claim,
            # because that would clobber a live waiter's view of
            # ``confirmation_response``. (Issue #485.)

        # Emit to subscribers outside the lock (subscriber awaits may
        # do arbitrary I/O). If emit fails, the response is already
        # durable in the archive and the waiter has observed our
        # claim — subscribers may be out of sync but no persistent
        # inconsistency results, so we log and continue.
        try:
            emit_payload: dict[str, Any] = {
                "type": "confirmation_response",
                "confirmation_id": confirmation_id,
                "approved": approved,
            }
            if response.data:
                emit_payload["data"] = response.data
            await self.emit(conv_id, emit_payload)
        except Exception:
            log.exception(
                "respond_to_confirmation emit failed for conv %s "
                "(archive write already succeeded); continuing",
                conv_id[:8])

        if waiter_event is not None:
            # Running loop: wake it. request_confirmation will read
            # state.confirmation_response under the lock (we already
            # cleared pending_confirmation / confirmation_event
            # above; request_confirmation's success path tolerates
            # those being None) and then clear confirmation_response
            # and promote the next queued confirmation (issue #485).
            waiter_event.set()
        else:
            # No running loop — dispatch recovery using the request
            # captured under the lock above. The slot is already
            # cleared (pending_confirmation = None, confirmation_event
            # = None); confirmation_response is still set to mark the
            # claim. On handler failure, restore pending state so the
            # next retry / startup recovery can proceed.
            async with state.lock:
                state.confirmation_response = None
            log.info("No running loop for conv %s, dispatching recovery",
                     conv_id)
            try:
                result = await self._dispatch_recovery(
                    conv_id, claimed_request, response)
                log.info("Recovery result for conv %s: %s", conv_id[:8], result)
            except Exception:
                log.exception(
                    "Recovery dispatch failed for conv %s; restoring "
                    "pending state so retry can proceed",
                    conv_id[:8])
                async with state.lock:
                    # Restore only if nothing else has taken the slot.
                    if state.pending_confirmation is None:
                        state.pending_confirmation = claimed_request
                raise
            # Recovery dispatched successfully. There's no running
            # waiter to promote the queue, so do it ourselves: if any
            # request_confirmation calls landed while the recovered
            # confirmation was active, they're queued and need
            # promotion. ``_promote_next_confirmation_unlocked`` is a
            # no-op if a parallel request_confirmation already won the
            # active slot. The promoted request's own queued waiter
            # will emit its ``confirmation_request`` payload when it
            # wakes; we don't emit here. (Issue #485.)
            async with state.lock:
                self._promote_next_confirmation_unlocked(state, conv_id)

    async def cancel_pending_confirmation(self, conv_id: str) -> bool:
        """Drop a pending confirmation without triggering recovery.

        Used when the caller is itself unwinding (e.g., the agent loop
        racing the confirmation await against a cancel and the cancel
        won). Persists a denial response to the archive so reload sees
        the request as resolved, and clears the manager state so a
        future submission attempt is a no-op rather than reviving the
        cancelled turn. Returns True if a pending confirmation was
        cleared, False otherwise.

        State mutations run under ``state.lock`` so a concurrent
        ``respond_to_confirmation`` can't see the request as still
        pending after we've started clearing it (issue #440). If a
        responder has already claimed the slot
        (``state.confirmation_response is not None``), cancellation is
        a no-op: the responder's answer wins and we must NOT persist a
        denial that contradicts an already-emitted approval
        (Copilot review on #484).
        """
        state = self._conversations.get(conv_id)
        if not state:
            return False
        async with state.lock:
            if not state.pending_confirmation:
                return False
            if state.confirmation_response is not None:
                # A responder already claimed the slot — let their
                # response stand. Cancellation arrived too late.
                log.info("cancel_pending_confirmation arrived after "
                         "responder claimed slot for conv %s; "
                         "deferring to responder",
                         conv_id[:8])
                return False
            request = state.pending_confirmation
            response = ConfirmationResponse(
                confirmation_id=request.confirmation_id,
                approved=False,
            )
            # Capture the waiter's event (if any) BEFORE clearing
            # state.confirmation_event — we must signal it after the
            # archive succeeds so a live request_confirmation with
            # timeout=None doesn't hang forever. (Copilot review on
            # #484.)
            waiter_event = state.confirmation_event
            # Archive UNDER the lock so the durable record is in place
            # before the waiter (if any) can observe the claim. If a
            # `request_confirmation` waiter is timing out and we
            # claimed before archive, the waiter could consume our
            # unpersisted denial and a subsequent archive failure
            # would leave no durable record (Copilot review on #484).
            # append_message is sync filesystem I/O.
            from .archive import append_message
            try:
                append_message(self.config, conv_id,
                               response.to_archive_message())
            except Exception:
                log.exception(
                    "cancel_pending_confirmation archive write failed "
                    "for conv %s; pending state untouched",
                    conv_id[:8])
                raise
            # Archive succeeded. Behavior splits on whether a live
            # waiter is present:
            #   * Live waiter: claim the slot (set
            #     ``confirmation_response``) so the waiter's post-wait
            #     block observes our denial when it re-acquires the
            #     lock, then clear pending/event. The waiter promotes
            #     any queued entry from there.
            #   * No live waiter: nothing will consume a claim, so
            #     just clear pending/event and promote the next queued
            #     confirmation directly. Setting and then clearing
            #     ``confirmation_response`` in this branch would be a
            #     dead write under the same lock (Copilot review on
            #     this PR). Skipping it makes the no-waiter case
            #     symmetric with the recovery-path promotion in
            #     ``respond_to_confirmation``. The promoted request's
            #     own queued waiter emits its ``confirmation_request``
            #     when it wakes; we don't emit here.
            if waiter_event is not None:
                state.confirmation_response = response
                state.pending_confirmation = None
                state.confirmation_event = None
            else:
                state.pending_confirmation = None
                state.confirmation_event = None
                self._promote_next_confirmation_unlocked(state, conv_id)
        # Signal the live waiter outside the lock so its post-wait
        # block can acquire the lock and consume our denial.
        # request_confirmation will null out confirmation_response
        # itself under the lock and promote the next queued entry.
        if waiter_event is not None:
            waiter_event.set()
        return True

    def note_partial_assistant_archived(self, conv_id: str) -> None:
        """Mark that the agent loop has archived an assistant message for
        the current turn. The cancel handler reads this flag to avoid
        double-writing the partial content (issue #491).
        """
        state = self._conversations.get(conv_id)
        if state is not None:
            state.partial_assistant_archived = True

    def note_cancel_observed_by_agent(self, conv_id: str) -> None:
        """Mark that the agent loop observed the cancel signal cleanly
        (iteration-start check or streaming short-circuit). The manager's
        normal-completion path uses this flag rather than raw
        `cancel_event.is_set()` so a cancel arriving *after* the turn
        already completed normally doesn't spuriously persist a cancel
        marker (issue #491).
        """
        state = self._conversations.get(conv_id)
        if state is not None:
            state.cancel_observed_by_agent = True

    def _write_cancel_marker_once(
        self, conv_id: str, state: ConversationState,
    ) -> None:
        """Persist the canonical cancel marker (and any streamed partial)
        for this turn, exactly once across both the iteration-start
        cancel path and the CancelledError handler (issue #491).

        Each archive append updates `state` immediately on success so
        that if marker-write fails mid-flight, a later retry doesn't
        double-write the partial body.
        """
        if state.cancel_marker_written:
            return
        partial = "".join(state.partial_assistant_chunks)
        # Step 1: persist any streamed partial that the agent loop
        # didn't already archive itself.
        if partial and not state.partial_assistant_archived:
            try:
                append_message(self.config, conv_id, {
                    "role": "assistant",
                    "content": partial,
                })
                state.partial_assistant_archived = True
            except Exception as exc:
                log.warning(
                    "Failed to archive cancel partial for %s: %s",
                    conv_id, exc,
                )
        # Step 2: persist the canonical marker. This is the load-bearing
        # signal; if it fails we leave the latch unset so a later cancel
        # call can retry the marker (the partial-archived flag protects
        # against duplicating the partial).
        try:
            append_message(self.config, conv_id, {
                "role": "cancel_marker",
                "content": CANCEL_MARKER_TEXT,
            })
            state.cancel_marker_written = True
        except Exception as exc:
            log.warning(
                "Failed to write cancel marker for %s: %s", conv_id, exc,
            )

    def _write_turn_aborted_marker_once(
        self, conv_id: str, state: ConversationState,
    ) -> None:
        """Persist the canonical turn-aborted marker (and any streamed
        partial) for this turn, exactly once (issue #517).

        Parallel to `_write_cancel_marker_once`: same partial-then-marker
        order, same fail-open posture on archive-write failures, same
        latch-only-on-marker-success behavior so a future retry can
        still land the marker without duplicating the partial.

        Called from the generic `except Exception` handler in the agent
        run task. `CancelledError` is a `BaseException` (not `Exception`)
        so it never reaches this helper — the cancel and turn-aborted
        latches stay independent in practice.
        """
        if state.turn_aborted_marker_written:
            return
        partial = "".join(state.partial_assistant_chunks)
        # Step 1: persist any streamed partial that the agent loop
        # didn't already archive itself.
        if partial and not state.partial_assistant_archived:
            try:
                append_message(self.config, conv_id, {
                    "role": "assistant",
                    "content": partial,
                })
                state.partial_assistant_archived = True
            except Exception as exc:
                log.warning(
                    "Failed to archive turn-aborted partial for %s: %s",
                    conv_id, exc,
                )
        # Step 2: persist the canonical marker. If it fails we leave the
        # latch unset so a later call can retry the marker (the
        # partial-archived flag protects against duplicating the body).
        try:
            append_message(self.config, conv_id, {
                "role": "turn_aborted",
                "content": TURN_ABORTED_MARKER_TEXT,
            })
            state.turn_aborted_marker_written = True
        except Exception as exc:
            log.warning(
                "Failed to write turn-aborted marker for %s: %s",
                conv_id, exc,
            )

    async def cancel_turn(self, conv_id: str) -> None:
        """Cancel an in-progress agent turn.

        Reads ``state.cancel_event`` / ``state.agent_task`` under
        ``state.lock`` so the finally-block's null-out of those fields
        (which also runs under the lock) can't race the cancellation
        request (issue #440).
        """
        state = self._conversations.get(conv_id)
        if not state:
            return
        async with state.lock:
            cancel_event = state.cancel_event
            agent_task = state.agent_task
        if cancel_event:
            cancel_event.set()
        if agent_task and not agent_task.done():
            agent_task.cancel()
            log.info("Cancelled agent turn for conv %s", conv_id[:8])

    async def shutdown(self, timeout: float = 15) -> None:
        """Wait for all in-flight agent turns to complete, then clean up.

        Called during graceful shutdown so running turns can finish
        rather than being abandoned.
        """
        tasks = [
            s.agent_task for s in self._conversations.values()
            if s.agent_task and not s.agent_task.done()
        ]
        if not tasks:
            return
        log.info("Waiting for %d in-flight agent turn(s)...", len(tasks))
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            log.warning("Shutdown timeout: cancelling %d agent turn(s)",
                        len(pending))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def get_state(self, conv_id: str) -> ConversationState | None:
        """Get current conversation state, or None if not tracked."""
        return self._conversations.get(conv_id)

    def set_initial_history(self, conv_id: str, history: list) -> None:
        """Pre-populate conversation history (e.g., for Mattermost thread-fork).

        Only sets the history if the conversation doesn't already have one.
        """
        state = self._get_or_create(conv_id)
        if not state.history:
            state.history = history

    def set_flag(self, conv_id: str, key: str, value) -> None:
        """Set a per-conversation flag (e.g., skip_vault_retrieval, active_model).

        Persisted-state fields (those declared on ``PersistedTurnState``)
        are written through to ``state.persisted.<key>``; other keys
        fall through to direct ``ConversationState`` attributes.
        """
        state = self._get_or_create(conv_id)
        if key in _PERSISTED_FIELD_NAMES:
            setattr(state.persisted, key, value)
            return
        if hasattr(state, key):
            setattr(state, key, value)
            return
        log.warning("Unknown conversation flag: %s", key)

    def subscribe(self, conv_id: str, callback: Callable) -> str:
        """Subscribe to a conversation's event stream. Returns subscription ID."""
        state = self._get_or_create(conv_id)
        sub_id = uuid4().hex[:12]
        state.subscribers[sub_id] = callback
        return sub_id

    def unsubscribe(self, conv_id: str, subscription_id: str) -> None:
        """Remove a subscriber from a conversation's event stream."""
        state = self._conversations.get(conv_id)
        if state:
            state.subscribers.pop(subscription_id, None)

    async def emit(self, conv_id: str, event: dict) -> None:
        """Publish an event to all subscribers of a conversation.

        Awaits each subscriber sequentially. When called from the
        global event bus forwarder, the forwarder uses create_task
        to avoid blocking the bus.
        """
        state = self._conversations.get(conv_id)
        if not state or not state.subscribers:
            return
        event = {**event, "conv_id": conv_id}

        async def _call(sub_id, callback):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception:
                log.exception("Subscriber %s raised for conv %s",
                              sub_id, conv_id)

        await asyncio.gather(
            *(_call(sid, cb) for sid, cb in list(state.subscribers.items())),
            return_exceptions=True,
        )

    def load_history(self, conv_id: str) -> list:
        """Load conversation history from archive.

        Returns in-memory history if available, otherwise loads from disk.
        """
        state = self._conversations.get(conv_id)
        if state and state.history:
            return state.history

        from .archive import restore_history
        history = restore_history(self.config, conv_id) or []

        # Cache in state
        state = self._get_or_create(conv_id)
        state.history = history
        return history

    def _promote_next_confirmation_unlocked(
        self, state: "ConversationState", conv_id: str,
    ) -> ConfirmationRequest | None:
        """Move the next queued confirmation (if any) into the active slot.

        Caller must hold ``state.lock``. Returns the promoted request so
        the caller can emit the ``confirmation_request`` event outside
        the lock, or None when there is nothing to promote (issue #485).

        Defensive precondition: if ``state.pending_confirmation`` is
        already non-None, the active slot is still claimed (e.g. the
        recovery branch's failed-dispatch restored the prior request,
        or a parallel ``request_confirmation`` already won the empty
        slot). In that case promote is a no-op so we don't clobber the
        active claim; the next clear-and-promote pass will pick the
        queue back up.

        Side effects under the caller's lock when a queued entry is
        promoted:
          - ``state.pending_confirmation = queued.request``
          - ``state.confirmation_event = asyncio.Event()``  (fresh)
          - ``state.confirmation_response = None``
          - ``queued.active_event = state.confirmation_event`` (so the
            waker doesn't need to re-read ``state.confirmation_event``
            after wake, avoiding a race window where a concurrent caller
            could null the field; Copilot review on #485 PR)
          - ``queued.promoted_event.set()``  (wakes the queued waiter)
        """
        if state.pending_confirmation is not None:
            return None
        if not state.confirmation_queue:
            return None
        queued = state.confirmation_queue.pop(0)
        state.pending_confirmation = queued.request
        state.confirmation_event = asyncio.Event()
        state.confirmation_response = None
        # Hand the freshly-installed event to the queued waiter
        # directly. Setting active_event BEFORE promoted_event.set()
        # ensures the wake-and-read sequence is race-free even though
        # the lock is released across the waiter's await.
        queued.active_event = state.confirmation_event
        queued.promoted_event.set()
        log.info("Promoted queued confirmation for conv %s: %s",
                 conv_id[:8], queued.request.action_type.value)
        return queued.request

    def _clear_active_and_promote_unlocked(
        self, state: "ConversationState", conv_id: str,
    ) -> None:
        """Null out the active-confirmation triple and promote the next
        queued entry (if any).

        Caller must hold ``state.lock``. This is the post-resolution
        cleanup pattern shared by the three branches of
        ``request_confirmation``'s post-wait claim block and the
        already-promoted limb of the queued-waiter cancellation cleanup
        (issue #485). Extracting it ensures every clear-active path
        drives queue promotion the same way.
        """
        state.pending_confirmation = None
        state.confirmation_event = None
        state.confirmation_response = None
        self._promote_next_confirmation_unlocked(state, conv_id)

    async def request_confirmation(
        self,
        conv_id: str,
        request: ConfirmationRequest,
    ) -> ConfirmationResponse:
        """Request a confirmation from the user. Suspends the agent loop.

        Persists the request to the archive, emits to subscribers, and
        waits for a response (or timeout).

        Concurrency: the waiting-state setup and the post-response /
        post-timeout cleanup run under ``state.lock`` so the
        pending_confirmation / confirmation_event / confirmation_response
        triple is mutated atomically and racing
        ``respond_to_confirmation`` / ``cancel_pending_confirmation``
        callers observe a consistent view (issue #440). The lock is
        released across the ``confirmation_event.wait()`` await so the
        responder can acquire it to set the event.

        Queueing (issue #485): if another confirmation is already active
        on this conv, the new request is appended to
        ``state.confirmation_queue`` and the caller blocks on its own
        ``promoted_event`` (no timeout — ``request.timeout`` only starts
        once the request becomes active). When the active confirmation
        resolves (respond / cancel / timeout) the
        ``_promote_next_confirmation_unlocked`` helper moves the next
        queued request into the active slot and signals its waiter.
        """
        state = self._get_or_create(conv_id)

        # Persist to archive (outside the lock — sync I/O). Archive at
        # enqueue time so crash recovery sees every requested
        # confirmation, not only the currently-active one.
        from .archive import append_message
        append_message(self.config, conv_id, request.to_archive_message())

        # Install as the active confirmation, or queue behind it.
        async with state.lock:
            if state.pending_confirmation is None:
                state.pending_confirmation = request
                state.confirmation_event = asyncio.Event()
                state.confirmation_response = None
                event = state.confirmation_event
                queued: _QueuedConfirmation | None = None
            else:
                queued = _QueuedConfirmation(request=request)
                state.confirmation_queue.append(queued)
                event = None  # filled in after promotion
                log.info(
                    "Conv %s has active confirmation; queued new request "
                    "(%d in queue)",
                    conv_id[:8], len(state.confirmation_queue))

        if queued is None:
            # Active path: emit the confirmation_request event for the
            # transports to render UI (outside the lock — emit awaits
            # subscribers).
            await self.emit(
                conv_id, _confirmation_request_payload(request))
        else:
            # Queued path: wait (no timeout) for promotion. The promote
            # helper will assign state.confirmation_event for our turn
            # before signalling, so we re-read it under the lock.
            #
            # Cancellation handling has two shapes (issue #485):
            #   1. Still queued: just drop our entry so no later
            #      promotion picks the dead waiter.
            #   2. Already promoted but cancel landed before we
            #      consumed the active slot: clear the slot and
            #      promote the next queued entry so the queue doesn't
            #      stall on our corpse. (state.pending_confirmation is
            #      ``request`` only in the just-promoted case — if a
            #      racing path already replaced it, leave the slot
            #      alone.)
            try:
                await queued.promoted_event.wait()
            except asyncio.CancelledError:
                async with state.lock:
                    if queued in state.confirmation_queue:
                        state.confirmation_queue.remove(queued)
                    elif state.pending_confirmation is request:
                        self._clear_active_and_promote_unlocked(
                            state, conv_id)
                raise
            # The promote helper assigned our active event to
            # ``queued.active_event`` BEFORE signalling
            # ``promoted_event``, so we can capture it directly
            # without re-reading ``state.confirmation_event``. That
            # re-read had a narrow race window where a concurrent
            # caller could null the field (Copilot review on this PR).
            event = queued.active_event
            await self.emit(
                conv_id, _confirmation_request_payload(request))

        # By either path (active or queued-then-promoted) ``event`` is
        # bound to the live ``state.confirmation_event`` for this
        # request.
        assert event is not None
        # Wait for response or timeout (lock not held — responder
        # needs to acquire it to claim the slot and set the event).
        try:
            await asyncio.wait_for(event.wait(), timeout=request.timeout)
            timed_out = False
        except asyncio.TimeoutError:
            timed_out = True

        # Claim the response slot under the lock atomically with the
        # state read, so a late responder racing the timeout decision
        # can't slip in between the read and the archive write / emit
        # / null-out. Three possible states inside the lock:
        # 1. responder already claimed `confirmation_response` — honor
        #    it (cooperative path on signaled event, or responder won
        #    a tight race against the timeout)
        # 2. timed out with no responder — synthesize a timeout denial
        #    and persist it
        # 3. signaled but no response — cancel_pending_confirmation
        #    raced and cleared the slot; treat as denial without
        #    archiving (cancel already wrote its own denial)
        # (Copilot review on #484.)
        # Each branch ends by promoting the next queued confirmation
        # (if any) under the same lock so the queue can't stall (issue
        # #485). The promote helper signals the queued waiter; the
        # caller (us) emits the new confirmation_request after lock
        # release.
        needs_timeout_emit = False
        async with state.lock:
            claimed = state.confirmation_response
            if claimed is not None:
                response = claimed
            elif timed_out:
                log.info("Confirmation timed out for conv %s: %s",
                         conv_id[:8], request.message)
                response = ConfirmationResponse(
                    confirmation_id=request.confirmation_id,
                    approved=False,
                )
                try:
                    append_message(
                        self.config, conv_id,
                        response.to_archive_message(),
                    )
                except Exception:
                    log.exception(
                        "Timeout archive write failed for conv %s; "
                        "leaving pending state intact for retry",
                        conv_id[:8])
                    raise
                needs_timeout_emit = True
            else:
                # Signaled but no response — `cancel_pending_confirmation`
                # cleared the slot. Return a denial; cancel has already
                # written its own denial to the archive.
                response = ConfirmationResponse(
                    confirmation_id=request.confirmation_id,
                    approved=False,
                )
                log.info("Confirmation for conv %s was cancelled out "
                         "from under the waiter; returning denial",
                         conv_id[:8])
            # All three branches converge on the same post-resolution
            # cleanup: null out the active-confirmation triple and
            # promote the next queued entry. The shared helper keeps
            # the three branches from drifting (issue #485).
            self._clear_active_and_promote_unlocked(state, conv_id)

        if needs_timeout_emit:
            await self.emit(conv_id, {
                "type": "confirmation_response",
                "confirmation_id": request.confirmation_id,
                "approved": False,
            })
        # Note: when ``_promote_next_confirmation_unlocked`` promoted
        # an entry, the promoted request's own queued waiter (in
        # another ``request_confirmation`` call) is now awake and will
        # emit its own ``confirmation_request`` payload — we
        # deliberately don't emit here to avoid duplicate emits per
        # promotion (issue #485).

        return response

    async def post_confirmation(
        self,
        conv_id: str,
        request: ConfirmationRequest,
    ) -> None:
        """Post a pending confirmation WITHOUT awaiting it.

        Used by workflow suspends: the workflow turn ENDS at a user_input,
        so there is no live waiter. We persist + install the request as the
        active confirmation but deliberately leave ``confirmation_event``
        None, so ``respond_to_confirmation`` routes resolution through the
        recovery dispatch path (registered handler), not a waiter wake.
        """
        from .archive import append_message
        state = self._get_or_create(conv_id)
        async with state.lock:
            if state.pending_confirmation is not None:
                # Unreachable by design: a workflow suspend ENDS the turn and
                # the per-conversation busy flag serializes turns, so the slot
                # is always free when a workflow posts. If we somehow get here,
                # fail loudly — queuing a no-waiter confirmation would let
                # promotion install a live event and route resolution to the
                # waiter-wake branch (nobody listening), silently stalling the
                # workflow. A loud error is diagnosable; a silent stall is not.
                raise RuntimeError(
                    f"post_confirmation: conversation {conv_id[:8]} already has "
                    f"a pending confirmation; workflow-turn serialization "
                    f"invariant violated")
            # Archive under the lock so the durable record is installed
            # atomically with the in-memory state — never one without the
            # other. Without this, the busy-raise branch above would leave
            # an orphan confirmation_request row that startup_scan would
            # later recover as a ghost pending confirmation with no backing
            # workflow (Copilot review on PR #573, mirroring the prior fix
            # to respond_to_confirmation from review on PR #484).
            append_message(self.config, conv_id, request.to_archive_message())
            state.pending_confirmation = request
            state.confirmation_event = None  # no waiter — recovery path
            state.confirmation_response = None
        await self.emit(conv_id, _confirmation_request_payload(request))

    # -- Internal methods ------------------------------------------------------

    async def _start_turn(
        self,
        state: ConversationState,
        text: str,
        *,
        kind: TurnKind = TurnKind.USER,
        user_id: str = "",
        context_setup: Callable | None = None,
        archive_text: str = "",
        attachments: list[dict] | None = None,
        command_ctx: Any = None,
        wiki_page: str | None = None,
        task_mode: str | None = None,
        history: list | None = None,
        metadata: dict | None = None,
        future: asyncio.Future | None = None,
    ) -> None:
        """Start an agent turn as an asyncio task.

        Caller must hold ``state.lock``. The lock is held across this
        method's synchronous setup (so ``state.busy = True`` is set
        before concurrent enqueues observe it) but is released before
        the agent task itself runs — ``asyncio.create_task(run())``
        schedules ``run()`` and returns immediately. The finally
        block inside ``run()`` re-acquires the lock to reset
        ``busy`` / ``agent_task`` / ``cancel_event`` (issue #440).
        """
        from .context import Context

        conv_id = state.conv_id
        state.busy = True
        state.cancel_event = asyncio.Event()

        # -- Persist / inherit transport context --------------------------------
        # USER turns save their user_id and context_setup so that subsequent
        # WAKE turns (which have no transport of their own) can inherit them.
        if kind is TurnKind.USER:
            state.last_user_id = user_id
            state.last_context_setup = context_setup

        # WAKE turns with no explicit user_id / context_setup fall back to the
        # last USER turn's values so transport fields (channel_id, thread_id,
        # media_handler, etc.) propagate into the wake context.
        if kind is TurnKind.WAKE:
            if not user_id and state.last_user_id:
                user_id = state.last_user_id
            if context_setup is None and state.last_context_setup is not None:
                context_setup = state.last_context_setup

        # -- Build context based on turn kind -----------------------------------
        if kind in TASK_KINDS:
            # Background/task turns: use Context.for_task for sensible defaults
            # (skip_reflection=True, skip_vault_retrieval=True by default).
            effective_task_mode = task_mode if task_mode is not None else KIND_TASK_MODE[kind]
            ctx = Context.for_task(
                self.config, self.event_bus,
                user_id=user_id,
                conv_id=conv_id,
                channel_id=conv_id,
                task_mode=effective_task_mode,
            )
            ctx.cancelled = state.cancel_event
            ctx.wiki_page = wiki_page

            # WAKE turns fire on user conversations — restore per-conv state
            # so activated skills and preferences carry forward.
            if kind is TurnKind.WAKE:
                self._restore_per_conv_state(state, ctx)
        else:
            # USER (and WORKFLOW) turns — full interactive Context with
            # per-conv state restored. WORKFLOW deliberately reuses this path
            # for now: the orchestrator only needs ctx.config + ctx.conv_id,
            # and running on the user conv keeps phases visible in the timeline.
            # (Revisit after live smoke: WORKFLOW may want Context.for_task
            # semantics — if so, add it to TASK_KINDS with a KIND_TASK_MODE entry.)
            ctx = Context(config=self.config, event_bus=self.event_bus)
            ctx.user_id = user_id
            ctx.channel_id = conv_id
            ctx.conv_id = conv_id
            ctx.cancelled = state.cancel_event
            ctx.wiki_page = wiki_page

            # Restore per-conversation state from previous turns
            self._restore_per_conv_state(state, ctx)

        # Apply command context if provided
        if command_ctx:
            from .commands import apply_command_ctx
            apply_command_ctx(ctx, command_ctx)
            ctx.tools.extra_definitions = command_ctx.tools.extra_definitions

        # Let the transport set transport-specific fields
        if context_setup:
            if asyncio.iscoroutinefunction(context_setup):
                await context_setup(ctx)
            else:
                context_setup(ctx)

        # Reset cancel/partial accounting so this turn starts clean and
        # a subsequent cancel only persists what we actually streamed.
        state.partial_assistant_chunks = []
        state.partial_assistant_archived = False
        state.cancel_observed_by_agent = False
        state.cancel_marker_written = False
        state.turn_aborted_marker_written = False

        # Set up streaming callback that emits to subscribers.
        # WAKE turns don't stream: the agent may emit BACKGROUND_WAKE_OK to
        # suppress the final message, and streaming would have already
        # delivered the prefix before the suppression gate fires.
        from .config import resolve_streaming
        if kind is not TurnKind.WAKE and resolve_streaming(self.config, ctx.active_model):
            async def on_stream_chunk(chunk_type, data):
                if chunk_type == "text":
                    if isinstance(data, str):
                        state.partial_assistant_chunks.append(data)
                    await self.emit(conv_id, {
                        "type": "chunk", "text": data,
                    })
                elif chunk_type == "done":
                    await self.emit(conv_id, {"type": "stream_done"})
                elif chunk_type == "tool_call_start":
                    name = data.get("name", "") if isinstance(data, dict) else ""
                    await self.emit(conv_id, {
                        "type": "tool_call_start", "name": name,
                    })
            ctx.on_stream_chunk = on_stream_chunk

        # Set up manager-based confirmation on the context so tools
        # route through the manager instead of the event bus
        async def ctx_request_confirmation(request: ConfirmationRequest
                                           ) -> ConfirmationResponse:
            return await self.request_confirmation(conv_id, request)
        ctx.request_confirmation = ctx_request_confirmation
        ctx.manager = self

        # Load history — use caller-supplied history if provided, otherwise
        # load from archive for USER/WAKE, default to [] for other task kinds.
        if history is None:
            if kind in EPHEMERAL_TASK_KINDS:
                history = []
            else:
                history = self.load_history(conv_id)
        state.history = history

        # Emit turn_start
        await self.emit(conv_id, {"type": "turn_start"})

        # Forward global event bus events for this context to subscribers.
        # Uses a sync callback with create_task to avoid blocking the
        # event bus publish loop.
        forward_tasks: set[asyncio.Task] = set()

        def on_global_event(event):
            if event.get("context_id") != ctx.context_id:
                return

            async def _forward():
                await self.emit(conv_id, event)

            task = asyncio.create_task(_forward())
            forward_tasks.add(task)
            task.add_done_callback(forward_tasks.discard)

        bus_sub_id = self.event_bus.subscribe(on_global_event)

        # Create the turn task
        response_text_holder: list[str] = []

        async def run():
            try:
                if kind is TurnKind.WORKFLOW:
                    from .workflow.resume import run_workflow_turn
                    md = metadata or {}
                    result = await run_workflow_turn(
                        ctx, self,
                        workflow_name=md.get("workflow_name", ""),
                        resume=md.get("resume", False))
                else:
                    from .agent import run_agent_turn
                    result = await run_agent_turn(
                        ctx, text, history,
                        archive_text=archive_text,
                        attachments=attachments,
                    )

                response_text = result.text if hasattr(result, "text") else str(result)
                response_text_holder.append(response_text)
                response_media = result.media if hasattr(result, "media") else []
                # If cancel was observed cleanly inside the agent loop
                # (e.g. the iteration-start check returned _Final, or
                # the streaming provider broke out of its SSE loop
                # without raising), we still need to persist the cancel
                # marker. Gated on the agent's explicit cancel-observed
                # flag — NOT raw cancel_event.is_set() — so a cancel
                # signal arriving after the turn already completed
                # normally doesn't spuriously mark the response as
                # cancelled. The latch in _write_cancel_marker_once
                # makes this safe even if the CancelledError handler
                # also fires below. Issue #491.
                if state.cancel_observed_by_agent:
                    self._write_cancel_marker_once(conv_id, state)
                suppress = (kind is TurnKind.WAKE
                            and is_background_wake_ok(response_text))
                await self.emit(conv_id, {
                    "type": "message_complete",
                    "role": "assistant",
                    "text": response_text,
                    "media": response_media,
                    "final": True,
                    "suppress_user_message": suppress,
                    "usage": {
                        "prompt_tokens": ctx.tokens.last_prompt,
                        "completion_tokens": ctx.tokens.total_completion,
                        "total_tokens": (ctx.tokens.total_prompt
                                         + ctx.tokens.total_completion),
                    },
                    "context_limit": self.config.compaction.max_tokens,
                })
            except asyncio.CancelledError:
                log.info("Agent turn cancelled for conv %s", conv_id[:8])
                response_text_holder.append("[cancelled]")
                # Persist a strong cancel signal so the next turn's LLM
                # doesn't re-fulfill the cancelled request (issue #491).
                self._write_cancel_marker_once(conv_id, state)
                await self.emit(conv_id, {
                    "type": "message_complete",
                    "role": "assistant",
                    "text": "[cancelled]",
                    "final": True,
                    "suppress_user_message": False,
                })
            except Exception as e:
                log.error("Agent turn failed for conv %s: %s",
                          conv_id[:8], e, exc_info=True)
                response_text_holder.append(f"[error: {e}]")
                # Persist a turn-closure marker so the next turn's LLM
                # doesn't see an open prior request and re-fulfill it
                # (issue #517, parallel to #491's cancel marker).
                self._write_turn_aborted_marker_once(conv_id, state)
                await self.emit(conv_id, {
                    "type": "error",
                    "message": f"Agent turn failed: {e}",
                })
            finally:
                self.event_bus.unsubscribe(bus_sub_id)
                # Wait for any in-flight event forwards
                if forward_tasks:
                    await asyncio.gather(*forward_tasks,
                                         return_exceptions=True)
                # Persist per-conversation state for kinds that own user convs.
                if kind in STATE_PERSIST_KINDS:
                    self._save_conversation_state(state, ctx)
                # Reset busy/task/cancel atomically under the lock so
                # the busy=False write and the cancel_event/agent_task
                # null-outs become visible to other lock holders as a
                # single transition. After the lock releases, a
                # concurrent enqueue_turn CAN acquire the lock and
                # observe busy=False before _drain_pending has run —
                # see the "Drain queued messages" comment below for
                # how the drain handles that case (issue #440).
                async with state.lock:
                    # Circuit breaker tracking is USER-only (task turns are
                    # externally rate-limited by the scheduler / heartbeat
                    # timer).
                    if kind is TurnKind.USER:
                        self._circuit_breaker_record(state)
                    state.busy = False
                    state.agent_task = None
                    state.cancel_event = None

                await self.emit(conv_id, {"type": "turn_complete"})

                # Resolve the caller's future (if any) before draining pending
                if future is not None and not future.done():
                    result_text = (response_text_holder[0]
                                   if response_text_holder else None)
                    future.set_result(result_text)

                # Drain queued messages. _drain_pending re-acquires the
                # lock around its pop-and-dispatch sequence, so a
                # concurrent enqueue_turn that landed between the
                # finally-block lock release and here will compete on
                # equal footing: whoever grabs the lock first dispatches.
                await self._drain_pending(state)

        state.agent_task = asyncio.create_task(run())

    def _restore_per_conv_state(self, state: ConversationState, ctx) -> None:
        """Restore persisted per-conversation state onto ctx.

        Walks every ``PersistedTurnState`` field through
        ``_PERSISTED_BINDINGS`` so adding a new persisted field can't
        silently drop out of restore. Falsy persisted values are
        skipped — preserves the sticky-once-set semantics that ctx
        defaults rely on (e.g. a fresh ctx born with
        ``skip_vault_retrieval=False`` shouldn't be clobbered by a
        default-False persisted value).

        Called for USER and WAKE turns — the kinds that share a live
        conversation across turns.
        """
        persisted = state.persisted
        for f in dc_fields(PersistedTurnState):
            _, writer = _PERSISTED_BINDINGS[f.name]
            value = getattr(persisted, f.name)
            if not value:
                continue
            writer(ctx, value)

    def _save_conversation_state(self, state: ConversationState, ctx) -> None:
        """Persist ctx-driven state into ``state.persisted``.

        Only fields listed in ``_CTX_DRIVEN_FIELDS`` flow this way —
        externally-driven fields (e.g. ``active_model``, set via
        ``set_flag`` from the web UI) are owned by their setters and
        save never overwrites them.

        Truthy-only writes preserve the sticky semantics that the
        previous inline save logic had: once a ctx-driven flag becomes
        True for a conversation, save never flips it back to False.
        """
        persisted = state.persisted
        for f in dc_fields(PersistedTurnState):
            if f.name not in _CTX_DRIVEN_FIELDS:
                continue
            reader, _ = _PERSISTED_BINDINGS[f.name]
            value = reader(ctx)
            if value:
                setattr(persisted, f.name, value)

    async def _drain_pending(self, state: ConversationState) -> None:
        """Process queued entries one batch at a time.

        Contiguous USER entries at the head of the queue are combined into a
        single turn. Any other kind pops a single entry and fires it on its own.
        The recursive _start_turn→_drain_pending path handles the rest of the
        queue once each batch completes.

        Acquires ``state.lock`` around the pop-and-dispatch sequence so
        a concurrent ``enqueue_turn`` can't observe ``busy=False`` while
        we are mid-dispatch and start a competing turn (issue #440).
        Whoever grabs the lock first wins; the loser sees ``busy=True``
        set by the winner's ``_start_turn`` and queues.

        Re-checks ``state.busy`` under the lock and defers if another
        turn already won the dispatch race after the finally-block's
        ``busy=False`` reset (Copilot review on #484). The deferred
        turn will be picked up by the new in-flight turn's own
        finally-block drain.
        """
        if not state.pending_messages:
            return

        async with state.lock:
            if not state.pending_messages:
                # Another path drained the queue while we were waiting
                # on the lock.
                return
            if state.busy:
                # A concurrent enqueue won the dispatch race after our
                # finally-block reset busy=False and before we got here.
                # Defer: the winner's finally-block will drain whatever
                # is still queued when its turn completes.
                log.debug(
                    "_drain_pending: conv %s already busy with a "
                    "concurrent turn, deferring drain",
                    state.conv_id[:8])
                return

            first = state.pending_messages[0]

            if first["kind"] is TurnKind.USER:
                # Pop all contiguous USER entries from the front.
                run: list[dict] = []
                while (state.pending_messages
                       and state.pending_messages[0]["kind"] is TurnKind.USER):
                    run.append(state.pending_messages.pop(0))

                texts = [q["text"] for q in run]
                combined = "\n".join(texts)
                last = run[-1]

                all_attachments: list[dict] = []
                for q in run:
                    if q.get("attachments"):
                        all_attachments.extend(q["attachments"])

                log.info("Draining %d queued USER message(s) for conv %s",
                         len(run), state.conv_id[:8])

                # Fan-out: when the head future resolves, resolve all tail
                # futures with the same result so every waiting caller gets
                # notified.
                head_fut = last.get("future")
                tail_futs = [q.get("future") for q in run[:-1]]
                tail_futs = [f for f in tail_futs if f is not None]
                if head_fut is not None and tail_futs:
                    def _fanout(fut: asyncio.Future,
                                _tails: list = tail_futs) -> None:
                        # Determine the value to propagate to tail futures.
                        # Propagate None on cancellation or exception (tails
                        # never observe the error — they were coalesced into
                        # the head turn and the head's error path already
                        # returned "[error: ...]" text via set_result).
                        # Propagate the result on normal completion. Calling
                        # fut.result() on an exception future would raise and
                        # leave tails unresolved, so guard explicitly.
                        result = None
                        if fut.done() and not fut.cancelled():
                            exc = fut.exception()
                            if exc is None:
                                result = fut.result()
                        for f in _tails:
                            if not f.done():
                                f.set_result(result)
                    head_fut.add_done_callback(_fanout)
                elif tail_futs:
                    # No head future (edge case) — resolve tails to None so
                    # callers don't hang.
                    for f in tail_futs:
                        if not f.done():
                            f.set_result(None)

                await self._start_turn(
                    state, combined,
                    kind=TurnKind.USER,
                    user_id=last.get("user_id", ""),
                    context_setup=last.get("context_setup"),
                    archive_text=last.get("archive_text", ""),
                    attachments=all_attachments or None,
                    command_ctx=last.get("command_ctx"),
                    wiki_page=last.get("wiki_page"),
                    future=head_fut,
                )
            else:
                # Non-USER kinds fire one at a time, preserving all their own
                # kwargs.
                q = state.pending_messages.pop(0)
                log.info("Draining queued %s turn for conv %s",
                         q["kind"].value, state.conv_id[:8])
                await self._start_turn(
                    state, q["text"],
                    kind=q["kind"],
                    user_id=q.get("user_id", ""),
                    context_setup=q.get("context_setup"),
                    archive_text=q.get("archive_text", ""),
                    attachments=q.get("attachments"),
                    command_ctx=q.get("command_ctx"),
                    wiki_page=q.get("wiki_page"),
                    task_mode=q.get("task_mode"),
                    history=q.get("history"),
                    metadata=q.get("metadata"),
                    future=q.get("future"),
                )

    # -- Startup recovery ------------------------------------------------------

    async def startup_scan(self) -> int:
        """Scan conversation archives for interrupted confirmations.

        Called on server startup before transports connect. Finds
        conversations where the last message is a confirmation_request
        with no matching confirmation_response, and registers them as
        pending confirmations in the manager's state.

        Returns the number of recovered confirmations.
        """
        from datetime import datetime, timedelta

        conversations_dir = self.config.workspace_path / "conversations"
        if not conversations_dir.exists():
            return 0

        # Staleness threshold: ignore confirmations older than 24 hours
        stale_cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        recovered = 0

        for conv_id, archive_file in iter_conversation_archives(self.config):
            try:
                pending = self._scan_archive_for_pending(
                    archive_file, stale_cutoff, conv_id)
                if pending:
                    state = self._get_or_create(conv_id)
                    state.pending_confirmation = pending
                    log.info("Recovered pending confirmation for conv %s: %s",
                             conv_id[:8], pending.action_type.value)
                    recovered += 1
            except Exception as e:
                log.warning("Error scanning archive %s: %s", conv_id, e)

        if recovered:
            log.info("Startup scan: recovered %d pending confirmation(s)",
                     recovered)
        return recovered

    async def startup_scan_workflows(self) -> int:
        """Recover workflows in status='running' by re-enqueueing them (#581).

        Called on server startup after ``startup_scan()``. Walks each
        conversation directory; for every ``workflow.json`` still marked
        ``running``, bumps the persistent ``attempts`` counter and re-enqueues
        a ``TurnKind.WORKFLOW`` turn so the durable replay engine can pick up
        where it left off. If a run exceeds ``config.workflow.max_resume_attempts``
        the journal is flipped to ``status='error'`` and left alone — the cap
        bounds a crash loop from replaying forever.

        Per-conversation errors (unreadable / corrupt journal) log a warning
        and continue; fail-open on the loop itself keeps one bad row from
        stalling startup. The persisted ``attempts`` increment happens before
        ``enqueue_turn`` so a crash inside the turn still consumes an attempt.

        Returns the number of workflows re-enqueued (not counting those that
        hit the cap).
        """
        resumed = 0
        cap = self.config.workflow.max_resume_attempts
        for conv_id, _archive in iter_conversation_archives(self.config):
            path = workflow_path(self.config, conv_id)
            if not path.exists():
                continue
            try:
                journal = load_journal(self.config, conv_id)
            except Exception as exc:
                log.warning(
                    "Failed to load workflow journal for %s: %s", conv_id, exc)
                continue
            if journal is None:
                continue

            if journal.status != "running":
                continue

            if journal.attempts >= cap:
                log.warning(
                    "Workflow %r in %s exceeded resume attempts (%d), "
                    "marked as error.",
                    journal.workflow_name, conv_id, cap)
                journal.status = "error"
                save_journal(self.config, conv_id, journal)
                continue

            journal.attempts += 1
            save_journal(self.config, conv_id, journal)

            log.info(
                "Resuming workflow %r in %s (attempt %d/%d)",
                journal.workflow_name, conv_id, journal.attempts, cap)

            try:
                await self.enqueue_turn(
                    conv_id,
                    kind=TurnKind.WORKFLOW,
                    prompt="",
                    metadata={
                        "workflow_name": journal.workflow_name,
                        "resume": True,
                    },
                )
            except Exception as exc:
                # Fail-open per conversation: attempts already incremented on
                # disk, so the crash-safety guarantee still holds. Skip to the
                # next conversation rather than block startup.
                log.warning(
                    "Failed to enqueue workflow resume for %s: %s",
                    conv_id, exc)
                continue
            resumed += 1

        if resumed:
            log.info("Startup scan: resumed %d workflow(s)", resumed)
        return resumed

    def _scan_archive_for_pending(self, archive_path, stale_cutoff: str,
                                  conv_id: str
                                  ) -> ConfirmationRequest | None:
        """Read the tail of an archive file looking for an unresolved confirmation."""
        import json

        # Read last N lines from the tail of the file to avoid loading
        # the entire archive into memory on startup.
        max_tail_bytes = 64 * 1024  # 64KB — plenty for recent messages
        lines = []
        try:
            with open(archive_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                start = max(0, size - max_tail_bytes)
                f.seek(start)
                tail = f.read().decode("utf-8", errors="replace")
            for raw in tail.splitlines():
                raw = raw.strip()
                if raw:
                    lines.append(raw)
            # If we started mid-file, the first line may be truncated — drop it
            if start > 0 and lines:
                lines = lines[1:]
        except Exception:
            return None

        if not lines:
            return None

        # Scan backward for confirmation messages
        last_request = None
        for line in reversed(lines):
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = msg.get("role", "")
            if role == "confirmation_response":
                # Found a response — no pending confirmation
                return None
            if role == "confirmation_request":
                last_request = msg
                break
            # Keep scanning past non-confirmation messages (assistant, tool, etc.)
            # but stop if we hit a user message (the confirmation would be
            # between the tool result and the user's response)
            if role == "user":
                break

        if not last_request:
            return None

        # Check staleness
        ts = last_request.get("timestamp", "")
        if ts and ts < stale_cutoff:
            log.debug("Skipping stale confirmation in %s (from %s)",
                      conv_id, ts)
            return None

        return ConfirmationRequest.from_archive_message(last_request)

    async def recover_confirmation(self, conv_id: str,
                                   response: ConfirmationResponse) -> dict:
        """Handle a confirmation response for a conversation with no running loop.

        Atomically pops ``state.pending_confirmation`` under
        ``state.lock`` so a concurrent caller can't double-dispatch
        the same recovery (issue #440), then delegates to
        ``_dispatch_recovery``. If dispatch raises, restore the
        pending state so a future retry / startup-recovery can pick
        up where we left off (Copilot review on #484). Public entry
        point retained for external callers;
        ``respond_to_confirmation`` bypasses this wrapper because it
        has already claimed the slot.
        """
        state = self._conversations.get(conv_id)
        if not state:
            return {"error": "No pending confirmation"}
        async with state.lock:
            request = state.pending_confirmation
            if not request:
                return {"error": "No pending confirmation"}
            state.pending_confirmation = None
            state.confirmation_response = None
        try:
            return await self._dispatch_recovery(conv_id, request, response)
        except Exception:
            log.exception(
                "recover_confirmation dispatch failed for conv %s; "
                "restoring pending state so retry can proceed",
                conv_id[:8])
            async with state.lock:
                if state.pending_confirmation is None:
                    state.pending_confirmation = request
            raise

    async def _dispatch_recovery(
        self,
        conv_id: str,
        request: ConfirmationRequest,
        response: ConfirmationResponse,
    ) -> dict:
        """Run a confirmation handler for a recovered (no-running-loop) conv.

        Caller is responsible for clearing ``state.pending_confirmation``
        under ``state.lock`` before invoking — both call sites
        (``respond_to_confirmation`` and ``recover_confirmation``) do
        this so a concurrent responder can't re-enter the same slot.
        """
        if not self.confirmation_registry._handlers:
            log.warning(
                "No confirmation handlers registered — cannot recover "
                "confirmation for conv %s (action: %s)",
                conv_id[:8], request.action_type.value,
            )
            return {"error": "No confirmation handlers registered"}

        # The handler needs config + conv_id to write back to the archive
        # (no running loop means no real ctx). Provide a minimal recovery
        # context that handlers can duck-type against.
        recovery_ctx = _RecoveryContext(config=self.config, conv_id=conv_id)
        return await self.confirmation_registry.dispatch(
            recovery_ctx, request, response,
        )


@dataclass
class _RecoveryContext:
    """Minimal ctx-shaped object passed to confirmation handlers during
    recovery, when there's no live agent loop to provide a real ctx."""
    config: Any
    conv_id: str
