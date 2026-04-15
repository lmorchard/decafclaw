"""Conversation manager — central orchestrator for agent loops and state.

The conversation manager owns agent loop lifecycle, conversation history,
confirmation state, and event streams. Transport adapters (WebSocket,
Mattermost, interactive terminal) talk to the manager via its public API.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from .confirmations import (
    ConfirmationRegistry,
    ConfirmationRequest,
    ConfirmationResponse,
)

log = logging.getLogger(__name__)


@dataclass
class ConversationState:
    """Per-conversation state managed by the ConversationManager."""
    conv_id: str = ""
    history: list = field(default_factory=list)
    busy: bool = False
    pending_messages: list = field(default_factory=list)
    agent_task: asyncio.Task | None = None
    cancel_event: asyncio.Event | None = None

    # Confirmation state
    pending_confirmation: ConfirmationRequest | None = None
    confirmation_event: asyncio.Event | None = None
    confirmation_response: ConfirmationResponse | None = None

    # Per-conversation persistent state (survives across turns)
    skill_state: dict | None = None
    skip_vault_retrieval: bool = False
    active_model: str = ""

    # Circuit breaker state (rate-limiting turns per conversation)
    turn_times: list = field(default_factory=list)
    paused_until: float = 0

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
        self._conversations: dict[str, ConversationState] = {}

        # Circuit breaker config (from Mattermost config for now —
        # these should move to a transport-agnostic config section)
        self._cb_max_turns = getattr(config.mattermost, "circuit_breaker_max", 10)
        self._cb_window_sec = getattr(config.mattermost, "circuit_breaker_window_sec", 60)
        self._cb_pause_sec = getattr(config.mattermost, "circuit_breaker_pause_sec", 120)

    def _get_or_create(self, conv_id: str) -> ConversationState:
        """Get existing conversation state or create a new one."""
        if conv_id not in self._conversations:
            self._conversations[conv_id] = ConversationState(conv_id=conv_id)
        return self._conversations[conv_id]

    def _circuit_breaker_tripped(self, state: ConversationState) -> bool:
        """Check if the circuit breaker has tripped for a conversation."""

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
        """Record an agent turn completion for circuit breaker tracking."""

        state.turn_times.append(time.monotonic())

    # -- Public API ------------------------------------------------------------

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
        """Submit user input to a conversation. Starts or queues an agent turn.

        If the conversation is busy (turn in progress), the message is queued
        and will be drained when the current turn completes.

        Args:
            conv_id: Conversation identifier.
            text: User message text.
            user_id: User identifier.
            context_setup: Optional callback to configure transport-specific
                context fields (media_handler, channel_name, etc.).
            archive_text: If set, archive this instead of text.
            attachments: Optional media attachments.
            command_ctx: Optional pre-configured command context.
            wiki_page: Optional wiki page to inject as context.
        """
        state = self._get_or_create(conv_id)

        # Circuit breaker: drop messages for paused conversations
        if self._circuit_breaker_tripped(state):
            log.warning("Dropping message for paused conversation %s",
                        conv_id[:8])
            return

        # Notify subscribers of the user message (for multi-tab sync)
        await self.emit(conv_id, {
            "type": "user_message",
            "text": archive_text or text,
            "user_id": user_id,
        })

        if state.busy:
            # Cancel-on-new-message: cancel the current turn if configured
            if (self.config.agent.turn_on_new_message == "cancel"
                    and state.cancel_event
                    and not state.cancel_event.is_set()):
                log.info("Conv %s busy, cancelling for new message", conv_id[:8])
                state.cancel_event.set()
                if state.agent_task and not state.agent_task.done():
                    state.agent_task.cancel()

            state.pending_messages.append({
                "text": text,
                "user_id": user_id,
                "context_setup": context_setup,
                "archive_text": archive_text,
                "attachments": attachments,
                "command_ctx": command_ctx,
                "wiki_page": wiki_page,
            })
            log.info("Conv %s busy, queued message (%d pending)",
                     conv_id[:8], len(state.pending_messages))
            return

        await self._start_turn(state, text, user_id=user_id,
                               context_setup=context_setup,
                               archive_text=archive_text,
                               attachments=attachments,
                               command_ctx=command_ctx,
                               wiki_page=wiki_page)

    async def respond_to_confirmation(
        self,
        conv_id: str,
        confirmation_id: str,
        approved: bool,
        *,
        always: bool = False,
        add_pattern: bool = False,
    ) -> None:
        """Resolve a pending confirmation request.

        Persists the response to the archive and wakes the suspended
        agent loop (if still running) or dispatches recovery (if the
        loop died).
        """
        state = self._conversations.get(conv_id)
        if not state or not state.pending_confirmation:
            log.warning("No pending confirmation for conv %s", conv_id)
            return
        if state.pending_confirmation.confirmation_id != confirmation_id:
            log.warning("Confirmation ID mismatch for conv %s: expected %s, got %s",
                        conv_id, state.pending_confirmation.confirmation_id,
                        confirmation_id)
            return

        response = ConfirmationResponse(
            confirmation_id=confirmation_id,
            approved=approved,
            always=always,
            add_pattern=add_pattern,
        )

        # Persist to archive
        from .archive import append_message
        append_message(self.config, conv_id, response.to_archive_message())

        # Emit to subscribers
        await self.emit(conv_id, {
            "type": "confirmation_response",
            "confirmation_id": confirmation_id,
            "approved": approved,
        })

        # Wake the waiting agent loop or dispatch recovery
        state.confirmation_response = response
        if state.confirmation_event:
            state.confirmation_event.set()
        else:
            # No running loop — dispatch recovery
            log.info("No running loop for conv %s, dispatching recovery",
                     conv_id)
            result = await self.recover_confirmation(conv_id, response)
            log.info("Recovery result for conv %s: %s", conv_id[:8], result)

    async def cancel_turn(self, conv_id: str) -> None:
        """Cancel an in-progress agent turn."""
        state = self._conversations.get(conv_id)
        if not state:
            return
        if state.cancel_event:
            state.cancel_event.set()
        if state.agent_task and not state.agent_task.done():
            state.agent_task.cancel()
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
        """Set a per-conversation flag (e.g., skip_vault_retrieval, active_model)."""
        state = self._get_or_create(conv_id)
        if hasattr(state, key):
            setattr(state, key, value)
        else:
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

    async def request_confirmation(
        self,
        conv_id: str,
        request: ConfirmationRequest,
    ) -> ConfirmationResponse:
        """Request a confirmation from the user. Suspends the agent loop.

        Persists the request to the archive, emits to subscribers, and
        waits for a response (or timeout).
        """
        state = self._get_or_create(conv_id)

        # Persist to archive
        from .archive import append_message
        append_message(self.config, conv_id, request.to_archive_message())

        # Set up waiting state
        state.pending_confirmation = request
        state.confirmation_event = asyncio.Event()
        state.confirmation_response = None

        # Emit to subscribers so transports can show the confirmation UI
        await self.emit(conv_id, {
            "type": "confirmation_request",
            "confirmation_id": request.confirmation_id,
            "action_type": request.action_type.value,
            "action_data": request.action_data,
            "message": request.message,
            "approve_label": request.approve_label,
            "deny_label": request.deny_label,
            "tool_call_id": request.tool_call_id,
        })

        # Wait for response or timeout
        try:
            await asyncio.wait_for(
                state.confirmation_event.wait(),
                timeout=request.timeout,
            )
        except asyncio.TimeoutError:
            log.info("Confirmation timed out for conv %s: %s",
                     conv_id[:8], request.message)
            # Create a timeout denial
            response = ConfirmationResponse(
                confirmation_id=request.confirmation_id,
                approved=False,
            )
            append_message(self.config, conv_id, response.to_archive_message())
            await self.emit(conv_id, {
                "type": "confirmation_response",
                "confirmation_id": request.confirmation_id,
                "approved": False,
            })
            state.pending_confirmation = None
            state.confirmation_event = None
            return response

        response = state.confirmation_response
        assert response is not None, "confirmation_event set but no response"
        state.pending_confirmation = None
        state.confirmation_event = None
        state.confirmation_response = None
        return response

    # -- Internal methods ------------------------------------------------------

    async def _start_turn(
        self,
        state: ConversationState,
        text: str,
        *,
        user_id: str = "",
        context_setup: Callable | None = None,
        archive_text: str = "",
        attachments: list[dict] | None = None,
        command_ctx: Any = None,
        wiki_page: str | None = None,
    ) -> None:
        """Start an agent turn as an asyncio task."""
        from .context import Context

        conv_id = state.conv_id
        state.busy = True
        state.cancel_event = asyncio.Event()

        # Build context
        ctx = Context(config=self.config, event_bus=self.event_bus)
        ctx.user_id = user_id
        ctx.channel_id = conv_id
        ctx.conv_id = conv_id
        ctx.cancelled = state.cancel_event
        ctx.wiki_page = wiki_page

        # Restore per-conversation state from previous turns
        if state.skill_state:
            ctx.tools.extra = state.skill_state.get("extra_tools", {})
            ctx.tools.extra_definitions = state.skill_state.get(
                "extra_tool_definitions", [])
            ctx.skills.activated = state.skill_state.get(
                "activated_skills", set())
        if state.skip_vault_retrieval:
            ctx.skip_vault_retrieval = True
        if state.active_model:
            ctx.active_model = state.active_model

        # Apply command context if provided
        if command_ctx:
            from .commands import apply_command_ctx
            apply_command_ctx(ctx, command_ctx)
            ctx.tools.extra_definitions = command_ctx.tools.extra_definitions

        # Let the transport set transport-specific fields
        if context_setup:
            context_setup(ctx)

        # Set up streaming callback that emits to subscribers
        from .config import resolve_streaming
        if resolve_streaming(self.config, ctx.active_model):
            async def on_stream_chunk(chunk_type, data):
                if chunk_type == "text":
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

        # Load history
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
        async def run():
            try:
                from .agent import run_agent_turn
                result = await run_agent_turn(
                    ctx, text, history,
                    archive_text=archive_text,
                    attachments=attachments,
                )

                response_text = result.text if hasattr(result, "text") else str(result)
                response_media = result.media if hasattr(result, "media") else []
                await self.emit(conv_id, {
                    "type": "message_complete",
                    "role": "assistant",
                    "text": response_text,
                    "media": response_media,
                    "final": True,
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
                await self.emit(conv_id, {
                    "type": "message_complete",
                    "role": "assistant",
                    "text": "[cancelled]",
                    "final": True,
                })
            except Exception as e:
                log.error("Agent turn failed for conv %s: %s",
                          conv_id[:8], e, exc_info=True)
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
                # Persist per-conversation state and record turn for circuit breaker
                self._save_conversation_state(state, ctx)
                self._circuit_breaker_record(state)
                state.busy = False
                state.agent_task = None
                state.cancel_event = None

                await self.emit(conv_id, {"type": "turn_complete"})

                # Drain queued messages
                await self._drain_pending(state)

        state.agent_task = asyncio.create_task(run())

    def _save_conversation_state(self, state: ConversationState, ctx) -> None:
        """Persist relevant context state back to conversation state."""
        if ctx.skills.activated:
            state.skill_state = {
                "extra_tools": ctx.tools.extra,
                "extra_tool_definitions": ctx.tools.extra_definitions,
                "activated_skills": ctx.skills.activated,
            }
        if ctx.skip_vault_retrieval:
            state.skip_vault_retrieval = True

    async def _drain_pending(self, state: ConversationState) -> None:
        """Process queued messages after a turn completes."""
        if not state.pending_messages:
            return

        queued = state.pending_messages
        state.pending_messages = []

        # Combine all queued messages
        texts = [q["text"] for q in queued]
        combined = "\n".join(texts)
        last = queued[-1]

        # Merge attachments from all queued messages
        all_attachments: list[dict] = []
        for q in queued:
            if q.get("attachments"):
                all_attachments.extend(q["attachments"])

        log.info("Draining %d queued message(s) for conv %s",
                 len(queued), state.conv_id[:8])

        await self._start_turn(
            state, combined,
            user_id=last.get("user_id", ""),
            context_setup=last.get("context_setup"),
            archive_text=last.get("archive_text", ""),
            attachments=all_attachments or None,
            command_ctx=last.get("command_ctx"),
            wiki_page=last.get("wiki_page"),
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

        for archive_file in conversations_dir.glob("*.jsonl"):
            # Skip compacted sidecar files
            if archive_file.stem.endswith(".compacted"):
                continue

            conv_id = archive_file.stem
            try:
                pending = self._scan_archive_for_pending(
                    archive_file, stale_cutoff)
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

    def _scan_archive_for_pending(self, archive_path, stale_cutoff: str
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
                      archive_path.stem, ts)
            return None

        return ConfirmationRequest.from_archive_message(last_request)

    async def recover_confirmation(self, conv_id: str,
                                   response: ConfirmationResponse) -> dict:
        """Handle a confirmation response for a conversation with no running loop.

        Dispatches to the appropriate confirmation handler based on the
        action type. Returns the handler result dict.
        """
        state = self._conversations.get(conv_id)
        if not state or not state.pending_confirmation:
            return {"error": "No pending confirmation"}

        request = state.pending_confirmation

        if not self.confirmation_registry._handlers:
            log.warning(
                "No confirmation handlers registered — cannot recover "
                "confirmation for conv %s (action: %s)",
                conv_id[:8], request.action_type.value,
            )
            state.pending_confirmation = None
            return {"error": "No confirmation handlers registered"}

        result = await self.confirmation_registry.dispatch(
            None,  # no ctx available for recovery
            request, response,
        )

        state.pending_confirmation = None
        return result
