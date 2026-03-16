"""Mattermost client — WebSocket for receiving, REST for sending."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import httpx
import websockets

log = logging.getLogger(__name__)

# Status indicators shown in placeholder messages
THINKING_INDICATOR = "\U0001f4ad Thinking..."
THINKING_SUFFIX = "\n\U0001f4ad Thinking..."


# -- Per-conversation state ----------------------------------------------------


@dataclass
class ConversationState:
    """Per-conversation state tracked during the bot's lifetime."""
    history: list = field(default_factory=list)
    skill_state: dict | None = None
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
        self.url = config.mattermost_url.rstrip("/")
        self.token = config.mattermost_token
        self.bot_user_id = None
        self.bot_username = config.mattermost_bot_username
        self.ignore_bots = config.mattermost_ignore_bots
        self.ignore_webhooks = config.mattermost_ignore_webhooks
        self.debounce_ms = config.mattermost_debounce_ms
        self.cooldown_ms = config.mattermost_cooldown_ms
        self.require_mention = config.mattermost_require_mention
        self.user_rate_limit_ms = config.mattermost_user_rate_limit_ms
        self.channel_blocklist = set(
            ch.strip() for ch in config.mattermost_channel_blocklist.split(",")
            if ch.strip()
        )
        self.circuit_breaker = CircuitBreaker(
            max_turns=config.mattermost_circuit_breaker_max,
            window_sec=config.mattermost_circuit_breaker_window_sec,
            pause_sec=config.mattermost_circuit_breaker_pause_sec,
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

    async def send(self, channel_id, message, root_id=None):
        """Send a message to a channel. Returns the post ID."""
        body = {"channel_id": channel_id, "message": message}
        if root_id:
            body["root_id"] = root_id
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

    async def edit_message(self, post_id, message):
        """Edit an existing post's content."""
        resp = await self._http.put(f"/posts/{post_id}/patch", json={
            "id": post_id,
            "message": message,
        })
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
        if not message:
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

        on_message({
            "text": message,
            "channel_id": channel_id,
            "post_id": post.get("id", ""),
            "root_id": post.get("root_id", ""),
            "user_id": post.get("user_id", ""),
            "sender_name": data.get("sender_name", ""),
            "channel_type": channel_type,
            "mentioned": mentioned,
        })

    # -- Conversation processing -----------------------------------------------

    def _get_conv(self, conversations: dict, conv_id: str) -> ConversationState:
        """Get or create conversation state."""
        if conv_id not in conversations:
            conversations[conv_id] = ConversationState()
        return conversations[conv_id]

    def _prepare_history(self, conv, conv_id, root_id, channel_id, conversations, config):
        """Get or create conversation history, resuming from archive if available."""
        from .archive import read_archive

        if root_id:
            hist_id = root_id
            if not conv.history:
                # Try archive first, then fork from channel
                archived = read_archive(config, hist_id)
                if archived:
                    conv.history = archived
                    log.info(f"Resumed thread {hist_id[:8]} from archive ({len(archived)} messages)")
                else:
                    channel_conv = conversations.get(channel_id)
                    channel_history = channel_conv.history if channel_conv else []
                    conv.history = list(channel_history)
                    log.debug(f"Forked thread history {hist_id[:8]} from channel {channel_id[:8]} ({len(channel_history)} msgs)")
        else:
            if not conv.history:
                archived = read_archive(config, channel_id)
                if archived:
                    conv.history = archived
                    log.info(f"Resumed channel {channel_id[:8]} from archive ({len(archived)} messages)")

        return conv.history

    async def _build_request_context(self, app_ctx, conv, conv_id, channel_id,
                                     root_id, placeholder_id):
        """Fork a request context and set up streaming, cancellation, and progress."""
        from .media import MattermostMediaHandler

        req_ctx = app_ctx.fork(
            user_id=app_ctx.config.agent_user_id,
            channel_id=channel_id,
            channel_name="",
            thread_id=root_id or "",
            conv_id=conv_id,
        )
        req_ctx.media_handler = MattermostMediaHandler(self._http)

        # Restore per-conversation skill state from previous turns
        if conv.skill_state:
            req_ctx.extra_tools = conv.skill_state.get("extra_tools", {})
            req_ctx.extra_tool_definitions = conv.skill_state.get("extra_tool_definitions", [])
            req_ctx.activated_skills = conv.skill_state.get("activated_skills", set())

        # Create conversation display for this turn
        conv_display = ConversationDisplay(
            self, channel_id, root_id,
            throttle_ms=app_ctx.config.llm_stream_throttle_ms,
            initial_post_id=placeholder_id,
        )

        # Set streaming callback
        if app_ctx.config.llm_streaming:
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

        sub_id = self._subscribe_progress(
            req_ctx.event_bus, req_ctx.context_id,
            channel_id=channel_id, root_id=root_id,
            streaming=app_ctx.config.llm_streaming,
            conv_display=conv_display,
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
            handler = MattermostMediaHandler(self._http)
            file_ids = await upload_and_collect(
                handler, channel_id, response_media
            )
            if file_ids:
                await handler.send_with_media(
                    channel_id, "", file_ids, root_id=root_id
                )

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
            if conv.cancel and not conv.cancel.is_set():
                log.info(f"Conversation {conv_id[:8]} busy, cancelling current turn for new message")
                conv.cancel.set()
            else:
                log.info(f"Conversation {conv_id[:8]} busy, re-queuing {len(msgs)} message(s)")
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

        # Send placeholder immediately
        placeholder_id = await self.send_placeholder(channel_id, root_id=root_id)
        await self.send_typing(channel_id)

        # Prepare history and request context
        history = self._prepare_history(
            conv, conv_id, root_id, channel_id, conversations, app_ctx.config
        )
        req_ctx, conv_display, cancel_task, sub_id = await self._build_request_context(
            app_ctx, conv, conv_id, channel_id, root_id, placeholder_id
        )

        conv.busy = True
        try:
            response = await run_agent_turn(req_ctx, combined_text, history)
        except BaseException as e:
            log.error(f"Agent turn failed: {e}")
            from .media import ToolResult
            response = ToolResult(text=f"[error: agent turn failed: {e}]")
        finally:
            if cancel_task:
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass
            req_ctx.event_bus.unsubscribe(sub_id)
            conv.last_response_time = time.monotonic()
            conv.busy = False
            conv.cancel = None
            self.circuit_breaker.record_turn(conv)

            # Persist per-conversation skill state for next turn
            if getattr(req_ctx, "activated_skills", None):
                conv.skill_state = {
                    "extra_tools": getattr(req_ctx, "extra_tools", {}),
                    "extra_tool_definitions": getattr(req_ctx, "extra_tool_definitions", []),
                    "activated_skills": getattr(req_ctx, "activated_skills", set()),
                }

        await conv_display.finalize()

        await self._post_response(
            response, channel_id, root_id, conv_display
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

    async def run(self, app_ctx):
        """Run the bot: connect, listen for messages, and dispatch to the agent."""
        from .heartbeat import parse_interval, run_heartbeat_timer
        from .mcp_client import init_mcp, shutdown_mcp

        await self.connect()
        await init_mcp(app_ctx.config)

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
            asyncio.get_event_loop().create_task(on_message(msg))

        # Graceful shutdown support
        import signal
        shutdown_event = asyncio.Event()

        def _signal_handler():
            log.info("Shutdown signal received, finishing in-flight turns...")
            shutdown_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        # Start heartbeat timer
        heartbeat_task = None
        if parse_interval(app_ctx.config.heartbeat_interval) is not None:
            cycle_fn = self._make_heartbeat_cycle(
                None, app_ctx.event_bus, app_ctx.config
            )
            heartbeat_task = asyncio.create_task(
                run_heartbeat_timer(
                    app_ctx.config, app_ctx.event_bus, shutdown_event,
                    on_cycle=cycle_fn,
                )
            )
            has_channel = app_ctx.config.heartbeat_channel or app_ctx.config.heartbeat_user
            log.info(f"Heartbeat timer started (reporting={'enabled' if has_channel else 'silent'})")
        else:
            log.info("Heartbeat disabled (interval not set)")

        try:
            await self.listen(on_message_sync, shutdown_event=shutdown_event)
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

            if agent_tasks:
                log.info(f"Waiting for {len(agent_tasks)} in-flight agent turn(s)...")
                await asyncio.gather(*agent_tasks, return_exceptions=True)
            await shutdown_mcp()
            await self.close()
            log.info("Shutdown complete")

    # -- Progress subscription -------------------------------------------------

    def _subscribe_progress(self, event_bus, context_id,
                            channel_id=None, root_id=None, streaming=False,
                            conv_display=None):
        """Subscribe to progress events and route to ConversationDisplay.

        Returns the subscription ID for later unsubscribe.
        """
        client = self
        compaction_post_id = None

        async def on_progress(event):
            nonlocal compaction_post_id
            if event.get("context_id") != context_id:
                return
            event_type = event.get("type")

            if event_type == "llm_start" and conv_display:
                await conv_display.on_llm_start(event.get("iteration", 1))
            elif event_type == "llm_end" and conv_display and not streaming:
                # Non-streaming: show text content via ConversationDisplay
                content = event.get("content")
                if content:
                    await conv_display.on_text_complete(content)
            elif event_type == "tool_start" and conv_display:
                await conv_display.on_tool_start(
                    event.get("tool", "tool"), event.get("args", {}))
            elif event_type == "tool_status" and conv_display:
                await conv_display.on_tool_status(
                    event.get("tool", "tool"), event.get("message", ""))
            elif event_type == "tool_end" and conv_display:
                await conv_display.on_tool_end(
                    event.get("tool", "tool"),
                    event.get("result_text", ""),
                    event.get("display_text"),
                    event.get("media", []),
                )
            elif event_type == "tool_confirm_request" and conv_display:
                await conv_display.on_confirm_request(
                    event.get("tool", "tool"),
                    event.get("command", ""),
                    event.get("suggested_pattern", ""),
                    event_bus, context_id,
                )
            # Compaction stays as-is (separate message)
            elif event_type == "compaction_start" and channel_id:
                import time as _time
                compaction_post_id = await client.send(
                    channel_id, "\U0001f4e6 Compacting conversation...",
                    root_id=root_id,
                )
            elif event_type == "compaction_end" and compaction_post_id:
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

        return event_bus.subscribe(on_progress)

    # -- Reaction polling ------------------------------------------------------

    async def _poll_confirmation(self, post_id, event_bus, context_id, tool_name,
                                 timeout=60, poll_interval=2):
        """Poll a post for thumbs up/down reactions to approve/deny a tool."""

        async def _resolve(approved, always=False, add_pattern=False, label=""):
            await event_bus.publish({
                "type": "tool_confirm_response",
                "context_id": context_id,
                "tool": tool_name,
                "approved": approved,
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
                    if emoji in ("notebook",):
                        log.info(f"Tool approved with pattern by {user_id}")
                        await _resolve(True, add_pattern=True,
                                       label="\U0001f4d3 approved + pattern added")
                        return
                    elif emoji in ("white_check_mark", "heavy_check_mark"):
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


# -- Conversation display ------------------------------------------------------


class ConversationDisplay:
    """Manages a sequence of Mattermost messages for a single agent turn.

    Instead of editing one placeholder, posts separate messages for text
    segments and tool calls, creating a visible trail of agent activity.
    Works for both streaming and non-streaming mode.
    """

    def __init__(self, client, channel_id, root_id, throttle_ms=200,
                 initial_post_id=None):
        self.client = client
        self._channel_id = channel_id
        self._root_id = root_id
        self._throttle_ms = throttle_ms
        self._initial_post_id = initial_post_id

        # Current message state
        self._current_post_id = initial_post_id
        self._current_type = "thinking" if initial_post_id else None
        self._text_buffer = ""
        self._text_has_content = False  # True if any real text was written

        # Tool call state
        self._tool_post_id = None

        # Throttling
        self._last_edit_time = 0

        # Turn state
        self._finalized = False

    # -- Text streaming --------------------------------------------------------

    async def on_llm_start(self, iteration):
        """New LLM generation starting."""
        if iteration == 1:
            # Initial thinking message already posted
            self._current_post_id = self._initial_post_id
            self._current_type = "thinking"
        else:
            # After tool calls — finalize previous text, start new thinking msg
            await self._finalize_current_text()
            self._text_buffer = ""
            self._text_has_content = False
            post_id = await self.client.send(
                self._channel_id, THINKING_INDICATOR, root_id=self._root_id,
            )
            self._current_post_id = post_id
            self._current_type = "thinking"

    async def on_text_chunk(self, text):
        """Streaming text chunk — buffer and throttle-edit."""
        self._text_buffer += text
        self._text_has_content = True
        if self._current_type == "thinking":
            self._current_type = "text"
        await self._throttled_edit(
            self._current_post_id, self._text_buffer + THINKING_SUFFIX
        )

    async def on_text_done(self):
        """Streaming text segment complete — strip thinking suffix."""
        if self._current_post_id and self._text_buffer:
            await self._force_edit(self._current_post_id, self._text_buffer)
        self._current_type = None

    async def on_text_complete(self, text):
        """Non-streaming: full text arrives at once."""
        if not text:
            return
        self._text_buffer = text
        self._text_has_content = True
        if self._current_type == "thinking" and self._current_post_id:
            await self._force_edit(self._current_post_id, text)
        elif self._current_post_id:
            await self._force_edit(self._current_post_id, text)
        else:
            post_id = await self.client.send(
                self._channel_id, text, root_id=self._root_id,
            )
            self._current_post_id = post_id
        self._current_type = None

    async def on_stream_chunk(self, chunk_type, data):
        """Adapter for call_llm_streaming's on_chunk callback."""
        if chunk_type == "text":
            await self.on_text_chunk(data)
        elif chunk_type == "tool_call_start":
            # LLM is switching to tool calls — finalize text
            await self._finalize_current_text()
            self._current_type = None
        elif chunk_type == "done":
            await self.on_text_done()

    # -- Tool call lifecycle ---------------------------------------------------

    async def on_tool_start(self, tool_name, args):
        """Tool execution starting — finalize text, post tool call message."""
        msg = f"\U0001f527 Running {tool_name}..."

        # If still on thinking placeholder with no text, reuse it for the tool call
        if self._current_type == "thinking" and self._current_post_id and not self._text_has_content:
            await self._force_edit(self._current_post_id, msg)
            self._tool_post_id = self._current_post_id
            self._current_post_id = None
        else:
            await self._finalize_current_text()
            self._tool_post_id = await self.client.send(
                self._channel_id, msg, root_id=self._root_id,
            )
        self._current_type = "tool"

    async def on_tool_status(self, tool_name, message):
        """Tool status update — edit tool call message."""
        if self._tool_post_id:
            try:
                await self.client.edit_message(
                    self._tool_post_id,
                    f"\U0001f527 {tool_name}: {message}",
                )
            except Exception:
                pass

    async def on_tool_end(self, tool_name, result_text, display_text, media):
        """Tool finished — edit tool call message with result, handle media."""
        if display_text:
            msg = display_text
        else:
            msg = f"\U0001f527 {tool_name} \u2714\ufe0f"

        if self._tool_post_id:
            try:
                await self.client.edit_message(self._tool_post_id, msg)
            except Exception:
                pass

            # Attach media to the tool call message
            if media:
                try:
                    from .media import MattermostMediaHandler, upload_and_collect
                    handler = MattermostMediaHandler(self.client._http)
                    file_ids = await upload_and_collect(
                        handler, self._channel_id, media,
                    )
                    if file_ids:
                        await handler.send_with_media(
                            self._channel_id, "", file_ids,
                            root_id=self._root_id,
                        )
                except Exception as e:
                    log.debug(f"Failed to attach tool media: {e}")

        self._tool_post_id = None
        self._current_type = None

    # -- Confirmation ----------------------------------------------------------

    async def on_confirm_request(self, tool_name, command, suggested_pattern,
                                  event_bus, context_id):
        """Tool needs confirmation — show prompt on tool call message."""
        msg = (
            f"\U0001f6a8 **Confirm {tool_name}:**\n```\n{command}\n```\n"
            f"React: \U0001f44d approve | \U0001f44e deny | \u2705 always"
        )
        if suggested_pattern and tool_name == "shell":
            msg += f" | \U0001f4d3 allow `{suggested_pattern}`"

        if self._tool_post_id:
            try:
                await self.client.edit_message(self._tool_post_id, msg)
            except Exception:
                pass
            confirm_post_id = self._tool_post_id
        else:
            confirm_post_id = await self.client.send(
                self._channel_id, msg, root_id=self._root_id,
            )
            self._tool_post_id = confirm_post_id

        if confirm_post_id:
            asyncio.create_task(
                self.client._poll_confirmation(
                    confirm_post_id, event_bus, context_id, tool_name,
                )
            )

    # -- Finalization ----------------------------------------------------------

    async def finalize(self):
        """Agent turn complete — strip thinking suffix, clean up."""
        if self._finalized:
            return
        self._finalized = True

        if self._current_type in ("text", "thinking") and self._current_post_id:
            if self._text_has_content and self._text_buffer:
                # Strip thinking suffix — show final text
                await self._force_edit(self._current_post_id, self._text_buffer)
            elif self._current_type == "thinking":
                # Never got any text — delete the thinking placeholder
                try:
                    await self.client.delete_message(self._current_post_id)
                except Exception:
                    pass

    # -- Helpers ---------------------------------------------------------------

    async def _finalize_current_text(self):
        """Strip thinking suffix from current text message if active."""
        if self._current_type in ("text", "thinking") and self._current_post_id:
            if self._text_has_content and self._text_buffer:
                await self._force_edit(self._current_post_id, self._text_buffer)
            self._current_type = None

    async def _throttled_edit(self, post_id, text):
        """Edit a post if enough time has passed since last edit."""
        if not post_id or not text:
            return
        now = time.monotonic()
        if (now - self._last_edit_time) * 1000 < self._throttle_ms:
            return
        await self._force_edit(post_id, text)

    async def _force_edit(self, post_id, text):
        """Edit a post immediately, updating the throttle timer."""
        if not post_id:
            return
        self._last_edit_time = time.monotonic()
        try:
            await self.client.send_typing(self._channel_id)
        except Exception:
            pass
        try:
            await self.client.edit_message(post_id, text)
        except Exception:
            pass
