"""Mattermost client — WebSocket for receiving, REST for sending."""

import asyncio
import json
import logging
import time

import httpx
import websockets

log = logging.getLogger(__name__)


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
        self.cb_max = config.mattermost_circuit_breaker_max
        self.cb_window = config.mattermost_circuit_breaker_window_sec
        self.cb_pause = config.mattermost_circuit_breaker_pause_sec
        self._http = httpx.AsyncClient(
            base_url=self.url + "/api/v4",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
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
        """Send a message to a channel."""
        body = {"channel_id": channel_id, "message": message}
        if root_id:
            body["root_id"] = root_id
        resp = await self._http.post("/posts", json=body)
        resp.raise_for_status()

    async def send_placeholder(self, channel_id, root_id=None, text="\U0001f4ad Thinking..."):
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

    async def send_typing(self, channel_id):
        """Send a typing indicator."""
        try:
            await self._http.post("/users/me/typing", json={
                "channel_id": channel_id,
            })
        except Exception:
            pass  # typing indicators are best-effort

    async def listen(self, on_message):
        """Listen for posted events via WebSocket. Calls on_message(post, channel_type)
        for each incoming user message. Reconnects on disconnect."""
        ws_scheme = "wss" if self.url.startswith("https") else "ws"
        ws_url = f"{ws_scheme}://{self.url.split('://')[1]}/api/v4/websocket"

        while True:
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

                    async for raw in ws:
                        evt = json.loads(raw)
                        if evt.get("event") == "posted":
                            self._handle_posted(evt, on_message)
                        elif evt.get("event") == "hello":
                            log.info("WebSocket hello (server ready)")

            except (websockets.ConnectionClosed, Exception) as e:
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

    async def run(self, app_ctx):
        """Run the bot: connect, listen for messages, and dispatch to the agent."""
        from .agent import run_agent_turn

        await self.connect()

        # All state is keyed by conversation ID (conv_id):
        # - Top-level channel messages use channel_id
        # - Thread messages use root_id
        # This allows threads and top-level to run independently.
        histories = {}              # conv_id -> message history
        pending_msgs = {}           # conv_id -> list of accumulated messages
        debounce_timers = {}        # conv_id -> asyncio.Task
        last_response_time = {}     # conv_id -> timestamp of last response
        conv_busy = {}              # conv_id -> True if agent turn in progress
        conv_turn_times = {}        # conv_id -> list of timestamps (circuit breaker)
        conv_paused_until = {}      # conv_id -> timestamp when pause ends

        # Per-user state
        user_last_msg_time = {}     # user_id -> timestamp of last accepted message

        debounce_sec = self.debounce_ms / 1000.0
        cooldown_sec = self.cooldown_ms / 1000.0
        user_rate_sec = self.user_rate_limit_ms / 1000.0

        def _check_circuit_breaker(conv_id):
            """Check if the circuit breaker has tripped for this conversation.
            Returns True if the conversation is paused."""
            now = time.monotonic()

            # Check if currently paused
            paused_until = conv_paused_until.get(conv_id, 0)
            if now < paused_until:
                return True

            # Clean expired entries and check
            times = conv_turn_times.get(conv_id, [])
            cutoff = now - self.cb_window
            times = [t for t in times if t > cutoff]
            conv_turn_times[conv_id] = times

            if len(times) >= self.cb_max:
                conv_paused_until[conv_id] = now + self.cb_pause
                log.warning(
                    f"Circuit breaker tripped for {conv_id[:8]}: "
                    f"{len(times)} turns in {self.cb_window}s, "
                    f"pausing for {self.cb_pause}s"
                )
                return True
            return False

        def _record_turn(conv_id):
            """Record an agent turn for circuit breaker tracking."""
            if conv_id not in conv_turn_times:
                conv_turn_times[conv_id] = []
            conv_turn_times[conv_id].append(time.monotonic())

        def _conv_id_for_msg(msg):
            """Determine conversation ID: root_id for threads, channel_id for top-level."""
            return msg["root_id"] or msg["channel_id"]

        async def _process_conversation(conv_id, channel_id, msgs):
            """Process accumulated messages for a conversation."""
            # Circuit breaker check
            if _check_circuit_breaker(conv_id):
                log.warning(f"Dropping messages for paused conversation {conv_id[:8]}")
                return

            # One agent turn at a time per conversation
            if conv_busy.get(conv_id):
                log.info(f"Conversation {conv_id[:8]} busy, re-queuing {len(msgs)} message(s)")
                if conv_id not in pending_msgs:
                    pending_msgs[conv_id] = []
                pending_msgs[conv_id] = msgs + pending_msgs[conv_id]
                debounce_timers[conv_id] = asyncio.create_task(_debounce_fire(conv_id, channel_id))
                return

            # Cooldown: wait if we responded too recently
            now = time.monotonic()
            last = last_response_time.get(conv_id, 0)
            wait = cooldown_sec - (now - last)
            if wait > 0:
                log.info(f"Cooldown: waiting {wait:.1f}s before responding in {conv_id[:8]}")
                await asyncio.sleep(wait)

            # Combine accumulated messages into one
            combined_text = "\n".join(m["text"] for m in msgs)
            # Use the last message for threading context
            last_msg = msgs[-1]

            # In DM channels, don't start a new thread unless @-mentioned.
            # Always continue an existing thread.
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

            # Get or create conversation history.
            # Threads get their own history, forked from the channel at creation.
            if root_id:
                hist_id = root_id
                if hist_id not in histories:
                    channel_history = histories.get(channel_id, [])
                    histories[hist_id] = list(channel_history)
                    log.debug(f"Forked thread history {hist_id[:8]} from channel {channel_id[:8]} ({len(channel_history)} msgs)")
            else:
                hist_id = channel_id
                if hist_id not in histories:
                    histories[hist_id] = []
            history = histories[hist_id]

            # Fork a request context and subscribe to progress events
            req_ctx = app_ctx.fork()
            sub_id = self._subscribe_progress(
                req_ctx.event_bus, req_ctx.context_id, placeholder_id
            )
            conv_busy[conv_id] = True
            try:
                response = await run_agent_turn(req_ctx, combined_text, history)
            finally:
                req_ctx.event_bus.unsubscribe(sub_id)
                last_response_time[conv_id] = time.monotonic()
                conv_busy[conv_id] = False
                _record_turn(conv_id)

            # Edit placeholder with the actual response
            if placeholder_id:
                await self.edit_message(placeholder_id, response)
            else:
                await self.send(channel_id, response, root_id=root_id)

        async def _debounce_fire(conv_id, channel_id):
            """Wait for debounce window, then process accumulated messages."""
            await asyncio.sleep(debounce_sec)
            msgs = pending_msgs.pop(conv_id, [])
            debounce_timers.pop(conv_id, None)
            if msgs:
                await _process_conversation(conv_id, channel_id, msgs)

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

            log.info(f"Message from {msg['sender_name']} in {conv_id[:8]} "
                      f"(busy={conv_busy.get(conv_id, False)}): "
                      f"{msg['text'][:50]}")

            # Accumulate message by conversation
            if conv_id not in pending_msgs:
                pending_msgs[conv_id] = []
            pending_msgs[conv_id].append(msg)

            # Reset debounce timer for this conversation
            if conv_id in debounce_timers:
                debounce_timers[conv_id].cancel()
            debounce_timers[conv_id] = asyncio.create_task(_debounce_fire(conv_id, channel_id))

        # Wrap the sync callback from WebSocket into async
        def on_message_sync(msg):
            asyncio.get_event_loop().create_task(on_message(msg))

        await self.listen(on_message_sync)

    def _subscribe_progress(self, event_bus, context_id, placeholder_id):
        """Subscribe to progress events and update the placeholder message.

        Returns the subscription ID for later unsubscribe.
        """
        client = self

        async def on_progress(event):
            if event.get("context_id") != context_id:
                return
            if not placeholder_id:
                return
            event_type = event.get("type")
            if event_type == "tool_status":
                tool_name = event.get("tool", "tool")
                await client.edit_message(placeholder_id, f"\U0001f527 {tool_name}: {event['message']}")
            elif event_type == "tool_start":
                tool_name = event.get("tool", "tool")
                await client.edit_message(placeholder_id, f"\U0001f527 Running {tool_name}...")
            elif event_type == "llm_start":
                await client.edit_message(placeholder_id, "\U0001f4ad Thinking...")

        return event_bus.subscribe(on_progress)

    async def close(self):
        await self._http.aclose()
