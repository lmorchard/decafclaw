"""Mattermost client — WebSocket for receiving, REST for sending."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import httpx
import websockets

from .mattermost_display import THINKING_INDICATOR, ConversationDisplay  # noqa: F401

log = logging.getLogger(__name__)


# -- Per-conversation state ----------------------------------------------------


@dataclass
class ConversationState:
    """Per-conversation state tracked during the bot's lifetime."""
    history: list = field(default_factory=list)
    skill_state: dict | None = None
    skip_vault_retrieval: bool = False
    pending_msgs: list = field(default_factory=list)
    debounce_timer: asyncio.Task | None = None
    last_response_time: float = 0
    busy: bool = False
    cancel: asyncio.Event | None = None
    turn_times: list = field(default_factory=list)
    paused_until: float = 0


# -- Circuit breaker -----------------------------------------------------------


class CircuitBreaker:
    """Rate-limits agent turns per conversation to prevent runaway loops."""

    def __init__(self, max_turns: int, window_sec: int, pause_sec: int):
        self.max_turns = max_turns
        self.window_sec = window_sec
        self.pause_sec = pause_sec

    def is_tripped(self, conv: ConversationState, conv_id: str = "") -> bool:
        """Check if the circuit breaker has tripped. Mutates conv state."""
        now = time.monotonic()

        # Check if currently paused
        if now < conv.paused_until:
            return True

        # Clean expired entries and check
        cutoff = now - self.window_sec
        conv.turn_times = [t for t in conv.turn_times if t > cutoff]

        if len(conv.turn_times) >= self.max_turns:
            conv.paused_until = now + self.pause_sec
            log.warning(
                f"Circuit breaker tripped for {conv_id[:8]}: "
                f"{len(conv.turn_times)} turns in {self.window_sec}s, "
                f"pausing for {self.pause_sec}s"
            )
            return True
        return False

    def record_turn(self, conv: ConversationState) -> None:
        """Record an agent turn for tracking."""
        conv.turn_times.append(time.monotonic())


# -- Mattermost client ---------------------------------------------------------


class MattermostClient:
    """Minimal Mattermost client for a single bot."""

    def __init__(self, config):
        self.url = config.mattermost.url.rstrip("/")
        self.token = config.mattermost.token
        self.bot_user_id = None
        self.bot_username = config.mattermost.bot_username
        self.ignore_bots = config.mattermost.ignore_bots
        self.ignore_webhooks = config.mattermost.ignore_webhooks
        self.debounce_ms = config.mattermost.debounce_ms
        self.cooldown_ms = config.mattermost.cooldown_ms
        self.require_mention = config.mattermost.require_mention
        self.user_rate_limit_ms = config.mattermost.user_rate_limit_ms
        self.channel_blocklist = set(config.mattermost.channel_blocklist)
        self.circuit_breaker = CircuitBreaker(
            max_turns=config.mattermost.circuit_breaker_max,
            window_sec=config.mattermost.circuit_breaker_window_sec,
            pause_sec=config.mattermost.circuit_breaker_pause_sec,
        )
        self._http = httpx.AsyncClient(
            base_url=self.url + "/api/v4",
            headers={
                "Authorization": f"Bearer {self.token}",
            },
            timeout=30,
        )

    async def connect(self):
        """Authenticate and get bot info."""
        resp = await self._http.get("/users/me")
        resp.raise_for_status()
        me = resp.json()
        self.bot_user_id = me["id"]
        self.bot_username = me.get("username", self.bot_username)
        log.info(f"Authenticated as @{self.bot_username} ({self.bot_user_id})")

    async def send(self, channel_id, message, root_id=None, attachments=None):
        """Send a message to a channel. Returns the post ID."""
        body = {"channel_id": channel_id, "message": message}
        if root_id:
            body["root_id"] = root_id
        if attachments:
            body["props"] = {"attachments": attachments}
        resp = await self._http.post("/posts", json=body)
        resp.raise_for_status()
        return resp.json().get("id")

    async def send_placeholder(self, channel_id, root_id=None, text=THINKING_INDICATOR):
        """Send a placeholder message and return its post ID."""
        body = {"channel_id": channel_id, "message": text}
        if root_id:
            body["root_id"] = root_id
        resp = await self._http.post("/posts", json=body)
        resp.raise_for_status()
        return resp.json().get("id")

    async def edit_message(self, post_id, message=None, props=None):
        """Edit an existing post's content and/or props."""
        body: dict = {"id": post_id}
        if message is not None:
            body["message"] = message
        if props is not None:
            body["props"] = props
        resp = await self._http.put(f"/posts/{post_id}/patch", json=body)
        resp.raise_for_status()

    async def delete_message(self, post_id):
        """Delete an existing post."""
        resp = await self._http.delete(f"/posts/{post_id}")
        resp.raise_for_status()

    async def send_typing(self, channel_id):
        """Send a typing indicator."""
        try:
            await self._http.post("/users/me/typing", json={
                "channel_id": channel_id,
            })
        except Exception:
            pass  # typing indicators are best-effort

    async def listen(self, on_message, shutdown_event=None):
        """Listen for posted events via WebSocket. Calls on_message(post, channel_type)
        for each incoming user message. Reconnects on disconnect.
        If shutdown_event is set, stops listening gracefully."""
        ws_scheme = "wss" if self.url.startswith("https") else "ws"
        ws_url = f"{ws_scheme}://{self.url.split('://')[1]}/api/v4/websocket"

        while not (shutdown_event and shutdown_event.is_set()):
            try:
                log.info(f"Connecting WebSocket to {ws_url}")
                async with websockets.connect(ws_url, ping_interval=30) as ws:
                    # Authenticate
                    await ws.send(json.dumps({
                        "seq": 1,
                        "action": "authentication_challenge",
                        "data": {"token": self.token},
                    }))
                    log.info("WebSocket connected")

                    while True:
                        if shutdown_event and shutdown_event.is_set():
                            log.info("Shutdown requested, stopping listener")
                            return

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        except websockets.ConnectionClosed:
                            break

                        evt = json.loads(raw)
                        if evt.get("event") == "posted":
                            self._handle_posted(evt, on_message)
                        elif evt.get("event") == "hello":
                            log.info("WebSocket hello (server ready)")

            except (websockets.ConnectionClosed, Exception) as e:
                if shutdown_event and shutdown_event.is_set():
                    return
                log.warning(f"WebSocket disconnected: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)

    def _handle_posted(self, evt, on_message):
        """Parse a posted event and call on_message if it's a real user message."""
        data = evt.get("data", {})
        post_str = data.get("post", "{}")
        try:
            post = json.loads(post_str)
        except json.JSONDecodeError:
            return

        sender = data.get("sender_name", "?")
        channel_type = data.get("channel_type", "?")
        post_type = post.get("type", "")
        user_id = post.get("user_id", "")
        props = post.get("props", {})
        log.debug(f"posted event: sender={sender} channel_type={channel_type} "
                   f"post_type={post_type!r} user_id={user_id[:8]} "
                   f"is_self={user_id == self.bot_user_id} "
                   f"from_bot={props.get('from_bot')} "
                   f"from_webhook={props.get('from_webhook')}")

        # Ignore system messages
        if post_type != "":
            return

        # Ignore own messages
        if user_id == self.bot_user_id:
            return

        # Ignore messages from bots/webhooks based on config
        props = post.get("props", {})
        if self.ignore_bots and props.get("from_bot") == "true":
            return
        if self.ignore_webhooks and props.get("from_webhook") == "true":
            return

        channel_id = post.get("channel_id", "")

        # Channel blocklist
        if channel_id in self.channel_blocklist:
            return

        channel_type = data.get("channel_type", "")
        message = post.get("message", "").strip()
        post_file_ids = post.get("file_ids") or []
        if not message and not post_file_ids:
            return

        # Strip bot @mention and track whether it was present
        mentioned = False
        if self.bot_username:
            mention = f"@{self.bot_username}"
            if mention in message:
                mentioned = True
                message = message.replace(mention, "").strip()

        # In non-DM channels, require @-mention if configured.
        # Always allow thread replies (root_id present) — if we're in the thread,
        # the user expects us to respond.
        is_dm = channel_type == "D"
        in_thread = bool(post.get("root_id"))
        if self.require_mention and not is_dm and not mentioned and not in_thread:
            return

        # Extract file metadata from post for attachment processing
        file_metadata = {}
        for finfo in (post.get("metadata") or {}).get("files") or []:
            if isinstance(finfo, dict) and finfo.get("id"):
                file_metadata[finfo["id"]] = finfo

        on_message({
            "text": message,
            "channel_id": channel_id,
            "post_id": post.get("id", ""),
            "root_id": post.get("root_id", ""),
            "user_id": post.get("user_id", ""),
            "sender_name": data.get("sender_name", ""),
            "channel_type": channel_type,
            "mentioned": mentioned,
            "file_ids": post_file_ids,
            "file_metadata": file_metadata,
        })

    # -- Conversation processing -----------------------------------------------

    def _get_conv(self, conversations: dict, conv_id: str) -> ConversationState:
        """Get or create conversation state."""
        if conv_id not in conversations:
            conversations[conv_id] = ConversationState()
        return conversations[conv_id]

    def _prepare_history(self, conv, conv_id, root_id, channel_id, conversations, config):
        """Get or create conversation history, resuming from archive if available."""
        from .archive import restore_history

        if root_id:
            hist_id = root_id
            if not conv.history:
                restored = restore_history(config, hist_id)
                if restored:
                    conv.history = restored
                    log.info(f"Resumed thread {hist_id[:8]} from archive ({len(restored)} messages)")
                else:
                    channel_conv = conversations.get(channel_id)
                    channel_history = channel_conv.history if channel_conv else []
                    conv.history = list(channel_history)
                    log.debug(f"Forked thread history {hist_id[:8]} from channel {channel_id[:8]} ({len(channel_history)} msgs)")
        else:
            if not conv.history:
                restored = restore_history(config, channel_id)
                if restored:
                    conv.history = restored
                    log.info(f"Resumed channel {channel_id[:8]} from archive ({len(restored)} messages)")

        return conv.history

    async def _build_request_context(self, app_ctx, conv, conv_id, channel_id,
                                     root_id, placeholder_id):
        """Fork a request context and set up streaming, cancellation, and progress."""
        from .media import MattermostMediaHandler

        req_ctx = app_ctx.fork(
            user_id=app_ctx.config.agent.user_id,
            channel_id=channel_id,
            channel_name="",
            thread_id=root_id or "",
            conv_id=conv_id,
        )
        req_ctx.media_handler = MattermostMediaHandler(self._http, channel_id=channel_id)

        # Restore per-conversation state from previous turns
        if conv.skill_state:
            req_ctx.tools.extra = conv.skill_state.get("extra_tools", {})
            req_ctx.tools.extra_definitions = conv.skill_state.get("extra_tool_definitions", [])
            req_ctx.skills.activated = conv.skill_state.get("activated_skills", set())
        if conv.skip_vault_retrieval:
            req_ctx.skip_vault_retrieval = True

        # Create conversation display for this turn
        conv_display = ConversationDisplay(
            self, channel_id, root_id,
            throttle_ms=app_ctx.config.mattermost.stream_throttle_ms,
            initial_post_id=placeholder_id,
            config=app_ctx.config,
            conv_id=conv_id,
        )

        # Set streaming callback
        from .config import resolve_streaming
        if resolve_streaming(app_ctx.config, req_ctx.active_model):
            req_ctx.on_stream_chunk = conv_display.on_stream_chunk

        # Set up cancellation event and poll for stop reaction
        cancel_event = asyncio.Event()
        req_ctx.cancelled = cancel_event
        conv.cancel = cancel_event
        cancel_task = None
        if placeholder_id:
            cancel_task = asyncio.create_task(
                self._poll_cancel(placeholder_id, cancel_event)
            )

        # Attach interactive Stop button to messages during this turn
        from .mattermost_ui import build_stop_button
        stop_attachments = build_stop_button(app_ctx.config, conv_id)
        if stop_attachments and placeholder_id:
            conv_display._stop_attachments = stop_attachments
            conv_display._stop_on_post = placeholder_id
            # Patch placeholder to include the stop button
            await self.edit_message(
                placeholder_id, THINKING_INDICATOR,
                props={"attachments": stop_attachments},
            )
            # Subscribe to cancel_turn events from the button callback
            def on_cancel(event):
                if event.get("type") == "cancel_turn" and event.get("conv_id") == conv_id:
                    cancel_event.set()
            conv_display._cancel_sub = req_ctx.event_bus.subscribe(on_cancel)

        sub_id = self._subscribe_progress(
            req_ctx.event_bus, req_ctx.context_id,
            channel_id=channel_id, root_id=root_id,
            streaming=resolve_streaming(app_ctx.config, req_ctx.active_model),
            conv_display=conv_display,
            reflection_visibility=app_ctx.config.reflection.visibility,
        )

        return req_ctx, conv_display, cancel_task, sub_id

    async def _post_response(self, response, channel_id, root_id, conv_display):
        """Post any remaining response content not handled by ConversationDisplay.

        ConversationDisplay handles text streaming and tool media during the turn.
        This handles:
        - Text-referenced media from the final response (workspace image refs)
        - Error responses that ConversationDisplay may not have seen
        """
        response_media = response.media or []

        if response_media:
            from .media import MattermostMediaHandler, upload_and_collect
            handler = MattermostMediaHandler(self._http, channel_id=channel_id)
            file_ids = await upload_and_collect(
                handler, channel_id, response_media
            )
            if file_ids:
                await handler.send_with_media(
                    channel_id, "", file_ids, root_id=root_id
                )

    async def _download_attachments(self, msgs, conv_id, config):
        """Download user-uploaded files from Mattermost and save as conversation attachments.

        Returns a list of attachment metadata dicts (same format as web UI uploads).
        """
        from .attachments import save_attachment

        max_bytes = getattr(getattr(config, "http", None), "max_upload_bytes", None)

        attachments = []
        for msg in msgs:
            file_ids = msg.get("file_ids") or []
            file_metadata = msg.get("file_metadata") or {}
            for fid in file_ids:
                try:
                    # Download file content
                    resp = await self._http.get(f"/files/{fid}")
                    resp.raise_for_status()
                    data = resp.content

                    # Enforce size limit (same as web UI uploads)
                    if max_bytes and len(data) > max_bytes:
                        log.warning(f"Mattermost file {fid} too large: "
                                    f"{len(data)} bytes > {max_bytes} limit, skipping")
                        continue

                    # Get filename and MIME type from metadata or file info
                    fmeta = file_metadata.get(fid, {})
                    filename = fmeta.get("name", f"file-{fid}")
                    mime_type = fmeta.get("mime_type", "application/octet-stream")

                    # Save to conversation uploads
                    att = save_attachment(config, conv_id, filename, data, mime_type)
                    attachments.append(att)
                    log.info(f"Saved Mattermost attachment: {att['filename']} ({mime_type})")
                except Exception as e:
                    log.warning(f"Failed to download Mattermost file {fid}: {e}")
        return attachments

    async def _prepare_conversation(self, conv, conv_id, channel_id, msgs,
                                    app_ctx, conversations):
        """Prepare a conversation turn: combine messages, check commands, set up context.

        Returns (req_ctx, conv_display, cancel_task, sub_id, combined_text, archive_text, cmd_ctx)
        if the agent turn should proceed, or None if a command was fully handled.
        """
        from .commands import dispatch_command
        from .context import Context

        # Combine accumulated messages into one
        combined_text = "\n".join(m["text"] for m in msgs)
        last_msg = msgs[-1]

        # Thread routing
        is_dm = last_msg.get("channel_type") == "D"
        already_in_thread = bool(last_msg["root_id"])
        mentioned = any(m.get("mentioned") for m in msgs)
        if is_dm and not mentioned and not already_in_thread:
            root_id = None
        else:
            root_id = last_msg["root_id"] or last_msg["post_id"]

        senders = set(m["sender_name"] for m in msgs)
        log.info(f"Processing {len(msgs)} message(s) from {', '.join(senders)} in {conv_id[:8]}")

        # -- Command dispatch (centralized in commands.py) --
        cmd_ctx = Context(config=app_ctx.config, event_bus=app_ctx.event_bus)
        cmd_ctx.user_id = app_ctx.config.agent.user_id
        cmd_result = await dispatch_command(cmd_ctx, combined_text, prefixes=["!"])

        if cmd_result.mode in ("help", "unknown", "error", "fork"):
            await self.send(channel_id, cmd_result.text, root_id=root_id)
            return None

        # Send placeholder immediately
        placeholder_id = await self.send_placeholder(channel_id, root_id=root_id)
        await self.send_typing(channel_id)

        # Prepare history and request context
        self._prepare_history(
            conv, conv_id, root_id, channel_id, conversations, app_ctx.config
        )
        req_ctx, conv_display, cancel_task, sub_id = await self._build_request_context(
            app_ctx, conv, conv_id, channel_id, root_id, placeholder_id
        )

        # Apply command state to the request context (inline mode)
        archive_text = ""
        if cmd_result.mode == "inline":
            combined_text = cmd_result.text
            archive_text = cmd_result.display_text
            from .commands import apply_command_ctx
            apply_command_ctx(req_ctx, cmd_ctx)

        return req_ctx, conv_display, cancel_task, sub_id, combined_text, archive_text

    async def _process_conversation(self, conv_id, channel_id, msgs,
                                    app_ctx, conversations):
        """Process accumulated messages for a conversation."""
        from .agent import run_agent_turn

        conv = self._get_conv(conversations, conv_id)
        cooldown_sec = self.cooldown_ms / 1000.0

        # Circuit breaker check
        if self.circuit_breaker.is_tripped(conv, conv_id):
            log.warning(f"Dropping messages for paused conversation {conv_id[:8]}")
            return

        # One agent turn at a time per conversation
        if conv.busy:
            if (app_ctx.config.agent.turn_on_new_message == "cancel"
                    and conv.cancel and not conv.cancel.is_set()):
                log.info(f"Conversation {conv_id[:8]} busy, cancelling current turn for new message")
                conv.cancel.set()
            else:
                log.info(f"Conversation {conv_id[:8]} busy, queuing {len(msgs)} message(s)")
            conv.pending_msgs = msgs + conv.pending_msgs
            conv.debounce_timer = asyncio.create_task(
                self._debounce_fire(conv_id, channel_id, conversations, app_ctx)
            )
            return

        # Cooldown: wait if we responded too recently
        now = time.monotonic()
        wait = cooldown_sec - (now - conv.last_response_time)
        if wait > 0:
            log.info(f"Cooldown: waiting {wait:.1f}s before responding in {conv_id[:8]}")
            await asyncio.sleep(wait)

        # Prepare conversation: combine messages, dispatch commands, build context
        prepared = await self._prepare_conversation(
            conv, conv_id, channel_id, msgs, app_ctx, conversations
        )
        if prepared is None:
            return  # Command was fully handled

        req_ctx, conv_display, cancel_task, sub_id, combined_text, archive_text = prepared

        # Download user-uploaded files and store as conversation attachments
        attachments = await self._download_attachments(
            msgs, conv_id, app_ctx.config)

        conv.busy = True
        try:
            response = await run_agent_turn(
                req_ctx, combined_text, conv.history, archive_text=archive_text,
                attachments=attachments or None)
        except BaseException as e:
            log.error(f"Agent turn failed: {e}")
            from .media import ToolResult
            response = ToolResult(text=f"[error: agent turn failed: {e}]")
            # Show error on the placeholder instead of leaving it to be deleted
            if conv_display._current_post_id and not conv_display._text_has_content:
                conv_display._text_buffer = f"\u26a0\ufe0f {e}"
                conv_display._text_has_content = True
        finally:
            if cancel_task:
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass
            req_ctx.event_bus.unsubscribe(sub_id)
            cancel_sub = getattr(conv_display, "_cancel_sub", None)
            if cancel_sub is not None:
                req_ctx.event_bus.unsubscribe(cancel_sub)
            conv.last_response_time = time.monotonic()

            # Persist per-conversation state for next turn
            if req_ctx.skills.activated:
                conv.skill_state = {
                    "extra_tools": req_ctx.tools.extra,
                    "extra_tool_definitions": req_ctx.tools.extra_definitions,
                    "activated_skills": req_ctx.skills.activated,
                }
            if req_ctx.skip_vault_retrieval:
                conv.skip_vault_retrieval = True

            await conv_display.finalize()
            conv.busy = False
            conv.cancel = None
            self.circuit_breaker.record_turn(conv)

        await self._post_response(
            response, channel_id, root_id=req_ctx.thread_id or None,
            conv_display=conv_display,
        )

    async def _debounce_fire(self, conv_id, channel_id, conversations, app_ctx):
        """Wait for debounce window, then process accumulated messages."""
        debounce_sec = self.debounce_ms / 1000.0
        await asyncio.sleep(debounce_sec)
        conv = self._get_conv(conversations, conv_id)
        msgs = conv.pending_msgs
        conv.pending_msgs = []
        conv.debounce_timer = None
        if msgs:
            await self._process_conversation(
                conv_id, channel_id, msgs, app_ctx, conversations
            )

    # -- Main run loop ---------------------------------------------------------

    async def run(self, app_ctx, shutdown_event):
        """Run the bot: connect, listen for messages, and dispatch to the agent.

        Called by the top-level orchestrator (runner.py). MCP, HTTP server,
        heartbeat, and signal handling are managed by the orchestrator.
        """
        await self.connect()

        # Track in-flight agent tasks for graceful shutdown
        agent_tasks = set()
        conversations: dict[str, ConversationState] = {}
        user_last_msg_time: dict[str, float] = {}

        debounce_sec = self.debounce_ms / 1000.0
        user_rate_sec = self.user_rate_limit_ms / 1000.0

        def _conv_id_for_msg(msg):
            return msg["root_id"] or msg["channel_id"]

        async def on_message(msg):
            channel_id = msg["channel_id"]
            user_id = msg["user_id"]
            conv_id = _conv_id_for_msg(msg)

            # Per-user rate limit
            now = time.monotonic()
            last_user_time = user_last_msg_time.get(user_id, 0)
            if (now - last_user_time) < user_rate_sec:
                log.info(f"Rate limit: dropping message from {msg['sender_name']}")
                return
            user_last_msg_time[user_id] = now

            conv = self._get_conv(conversations, conv_id)
            log.info(f"Message from {msg['sender_name']} in {conv_id[:8]} "
                      f"(busy={conv.busy}): "
                      f"{msg['text'][:50]}")

            # Accumulate message by conversation
            conv.pending_msgs.append(msg)

            # Reset debounce timer for this conversation
            if conv.debounce_timer:
                conv.debounce_timer.cancel()

            async def debounce_and_process():
                await asyncio.sleep(debounce_sec)
                conv_inner = self._get_conv(conversations, conv_id)
                msgs = conv_inner.pending_msgs
                conv_inner.pending_msgs = []
                conv_inner.debounce_timer = None
                if msgs:
                    task = asyncio.current_task()
                    agent_tasks.add(task)
                    try:
                        await self._process_conversation(
                            conv_id, channel_id, msgs, app_ctx, conversations
                        )
                    finally:
                        agent_tasks.discard(task)

            conv.debounce_timer = asyncio.create_task(debounce_and_process())

        # Wrap the sync callback from WebSocket into async
        def on_message_sync(msg):
            asyncio.get_running_loop().create_task(on_message(msg))

        try:
            await self.listen(on_message_sync, shutdown_event=shutdown_event)
        finally:
            if agent_tasks:
                log.info(f"Waiting for {len(agent_tasks)} in-flight agent turn(s)...")
                await asyncio.gather(*agent_tasks, return_exceptions=True)
            await self.close()
            log.info("Mattermost client stopped")

    # -- Progress subscription -------------------------------------------------

    def _subscribe_progress(self, event_bus, context_id,
                            channel_id=None, root_id=None, streaming=False,
                            conv_display=None, reflection_visibility="hidden"):
        """Subscribe to progress events and route to ConversationDisplay.

        Returns the subscription ID for later unsubscribe.
        """
        client = self
        compaction_post_id = None

        # -- Handlers for each event type --
        # Note: handlers that reference `cd` are only added to the dispatch
        # dict when conv_display is not None (see dispatch table below).
        cd = conv_display  # narrowed alias for closures

        async def handle_llm_start(event):
            assert cd is not None
            await cd.on_llm_start(event.get("iteration", 1))

        async def handle_llm_end(event):
            assert cd is not None
            if not streaming:
                content = event.get("content")
                if content:
                    await cd.on_text_complete(content)

        async def handle_text_before_tools(event):
            assert cd is not None
            content = event.get("text", "")
            if content and not streaming:
                await cd.on_text_complete(content)

        async def handle_tool_start(event):
            assert cd is not None
            await cd.on_tool_start(
                event.get("tool", "tool"), event.get("args", {}),
                tool_call_id=event.get("tool_call_id", ""))

        async def handle_tool_status(event):
            assert cd is not None
            await cd.on_tool_status(
                event.get("tool", "tool"), event.get("message", ""),
                tool_call_id=event.get("tool_call_id", ""))

        async def handle_tool_end(event):
            assert cd is not None
            await cd.on_tool_end(
                event.get("tool", "tool"),
                event.get("result_text", ""),
                event.get("display_text"),
                event.get("media", []),
                tool_call_id=event.get("tool_call_id", ""),
                display_short_text=event.get("display_short_text"),
            )

        async def handle_tool_media_uploaded(event):
            assert cd is not None
            await cd.on_tool_media_uploaded(
                event.get("file_ids", []),
                tool_call_id=event.get("tool_call_id", ""),
            )

        async def handle_tool_confirm_request(event):
            assert cd is not None
            await cd.on_confirm_request(
                event.get("tool", "tool"),
                event.get("command", ""),
                event.get("suggested_pattern", ""),
                event_bus, context_id,
                tool_call_id=event.get("tool_call_id", ""),
            )

        async def handle_reflection_result(event):
            assert cd is not None
            visibility = reflection_visibility
            if visibility == "hidden":
                return
            passed = event.get("passed", True)
            critique = event.get("critique", "")
            retry_num = event.get("retry_number", 0)
            raw = event.get("raw_response", "")
            error = event.get("error", "")
            if visibility == "debug":
                text = (f"\U0001f50d **Reflection** (retry {retry_num}): "
                        f"{'PASS' if passed else 'FAIL'}")
                if critique:
                    text += f"\nCritique: {critique}"
                if error:
                    text += f"\nError: {error}"
                if raw:
                    text += f"\n\n<details><summary>Raw judge output</summary>\n\n{raw}\n</details>"
                await cd.on_tool_status("reflection", text)
            elif not passed:
                text = (f"\U0001f50d Reflection retry {retry_num}: "
                        f"{critique}")
                await cd.on_tool_status("reflection", text)

        async def handle_vault_retrieval(event):
            assert cd is not None
            results = event.get("results") or []
            if results:
                source_counts: dict[str, int] = {}
                for item in results:
                    st = item.get("source_type", "unknown")
                    source_counts[st] = source_counts.get(st, 0) + 1
                parts = []
                for st in ("page", "user", "journal", "wiki", "memory"):
                    if source_counts.get(st):
                        parts.append(f"{source_counts[st]} {st}")
                summary = ", ".join(parts) or "context"
                await cd.on_tool_status(
                    "vault_retrieval",
                    f"\U0001f9e0 Retrieved {summary}")

        async def handle_compaction_start(event):
            nonlocal compaction_post_id
            if channel_id:
                compaction_post_id = await client.send(
                    channel_id, "\U0001f4e6 Compacting conversation...",
                    root_id=root_id,
                )

        async def handle_compaction_end(event):
            nonlocal compaction_post_id
            if not compaction_post_id:
                return
            try:
                elapsed = event.get("elapsed_sec", 0)
                before = event.get("before_messages", 0)
                after = event.get("after_messages", 0)
                est_tokens = event.get("estimated_tokens_before", 0)
                if elapsed < 60:
                    duration = f"{elapsed:.0f}s"
                else:
                    duration = f"{elapsed/60:.1f}m"
                details = f"{duration}"
                if before and after:
                    details += f", {before} → {after} messages"
                if est_tokens:
                    details += f", ~{est_tokens:,} tokens compacted"
                await client.edit_message(
                    compaction_post_id,
                    f"\U0001f4e6 Conversation compacted ({details})",
                )
            except Exception:
                pass  # best effort
            compaction_post_id = None

        # -- Dispatch table --
        # conv_display-dependent handlers require conv_display to be set;
        # compaction handlers work independently.
        from typing import Any, Callable
        dispatch: dict[str, Callable[..., Any]] = {}
        if conv_display:
            dispatch.update({
                "llm_start": handle_llm_start,
                "llm_end": handle_llm_end,
                "text_before_tools": handle_text_before_tools,
                "tool_start": handle_tool_start,
                "tool_status": handle_tool_status,
                "tool_end": handle_tool_end,
                "tool_media_uploaded": handle_tool_media_uploaded,
                "tool_confirm_request": handle_tool_confirm_request,
                "reflection_result": handle_reflection_result,
                "vault_retrieval": handle_vault_retrieval,
            })
        dispatch["compaction_start"] = handle_compaction_start
        dispatch["compaction_end"] = handle_compaction_end

        async def on_progress(event):
            if event.get("context_id") != context_id:
                return
            handler = dispatch.get(event.get("type"))
            if handler:
                await handler(event)

        return event_bus.subscribe(on_progress)

    # -- Reaction polling ------------------------------------------------------

    async def _poll_confirmation(self, post_id, event_bus, context_id, tool_name,
                                 timeout=60, poll_interval=2, tool_call_id=""):
        """Poll a post for thumbs up/down reactions to approve/deny a tool."""

        async def _resolve(approved, always=False, add_pattern=False, label=""):
            await event_bus.publish({
                "type": "tool_confirm_response",
                "context_id": context_id,
                "tool": tool_name,
                "approved": approved,
                **({"tool_call_id": tool_call_id} if tool_call_id else {}),
                **({"always": True} if always else {}),
                **({"add_pattern": True} if add_pattern else {}),
            })
            try:
                resp = await self._http.get(f"/posts/{post_id}")
                original_text = resp.json().get("message", "") if resp.status_code == 200 else ""
                await self.edit_message(post_id, f"{original_text}\n\n**Result:** {label}")
            except Exception:
                pass

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = await self._http.get(f"/posts/{post_id}/reactions")
                resp.raise_for_status()
                reactions = resp.json()
                for r in reactions:
                    emoji = r.get("emoji_name", "")
                    user_id = r.get("user_id", "")
                    if user_id == self.bot_user_id:
                        continue
                    if emoji in ("notebook",) and tool_name == "shell":
                        log.info(f"Tool approved with pattern by {user_id}")
                        await _resolve(True, add_pattern=True,
                                       label="\U0001f4d3 approved + pattern added")
                        return
                    elif emoji in ("white_check_mark", "heavy_check_mark") and tool_name != "shell":
                        log.info(f"Tool always-approved by {user_id}")
                        await _resolve(True, always=True, label="\u2705 always approved")
                        return
                    elif emoji in ("+1", "thumbsup"):
                        log.info(f"Tool approved by {user_id}")
                        await _resolve(True, label="\U0001f44d approved")
                        return
                    elif emoji in ("-1", "thumbsdown"):
                        log.info(f"Tool denied by {user_id}")
                        await _resolve(False, label="\U0001f44e denied")
                        return
            except Exception as e:
                log.debug(f"Reaction poll error: {e}")
            await asyncio.sleep(poll_interval)

        log.info(f"Confirmation timed out for {tool_name}")
        await _resolve(False, label="\u23f0 timed out")

    async def _poll_cancel(self, post_id, cancel_event, poll_interval=2):
        """Poll a placeholder post for stop reaction to cancel the agent turn."""
        while not cancel_event.is_set():
            try:
                resp = await self._http.get(f"/posts/{post_id}/reactions")
                if resp.status_code == 200:
                    reactions = resp.json()
                    for r in reactions:
                        emoji = r.get("emoji_name", "")
                        user_id = r.get("user_id", "")
                        if user_id == self.bot_user_id:
                            continue
                        if emoji in ("octagonal_sign", "stop_sign", "hand", "raised_hand"):
                            log.info(f"Agent turn cancelled by {user_id} via reaction")
                            cancel_event.set()
                            return
            except Exception:
                pass
            await asyncio.sleep(poll_interval)

    async def close(self):
        await self._http.aclose()

    # -- Heartbeat reporting ---------------------------------------------------

    async def _get_dm_channel(self, user_id: str) -> str:
        """Get or create a DM channel between the bot and a user."""
        resp = await self._http.post("/channels/direct",
                                     json=[self.bot_user_id, user_id])
        resp.raise_for_status()
        return resp.json()["id"]

    async def _resolve_heartbeat_channel(self, config) -> str | None:
        """Determine the channel for heartbeat reporting."""
        if config.heartbeat_channel:
            return config.heartbeat_channel
        if config.heartbeat_user:
            try:
                return await self._get_dm_channel(config.heartbeat_user)
            except Exception as e:
                log.error(f"Could not create DM channel for heartbeat user: {e}")
                return None
        return None

    def _make_heartbeat_cycle(self, _channel_id, event_bus, config):
        """Create an on_cycle callback that runs sections and posts results as they complete."""

        async def on_cycle():
            from .tools.heartbeat_tools import _guarded_heartbeat
            await _guarded_heartbeat(config, event_bus)

        return on_cycle
