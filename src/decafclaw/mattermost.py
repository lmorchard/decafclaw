"""Mattermost client — WebSocket for receiving, REST for sending.

Transport adapter that handles Mattermost connections, message parsing,
display formatting, debouncing, and circuit breaking. Agent loop lifecycle
and conversation state are owned by the ConversationManager.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import httpx
import websockets

from .mattermost_display import THINKING_INDICATOR, ConversationDisplay  # noqa: F401

log = logging.getLogger(__name__)


# -- Mattermost-specific per-conversation state --------------------------------


@dataclass
class MattermostConvState:
    """Mattermost-specific per-conversation state (transport concerns only).

    Agent loop state (history, busy, skill_state, circuit breaker, etc.)
    is in the ConversationManager's ConversationState.
    """
    debounce_timer: asyncio.Task | None = None
    last_response_time: float = 0
    pending_msgs: list = field(default_factory=list)
    # Current turn display (set per-turn, cleared on completion)
    conv_display: ConversationDisplay | None = None
    cancel_task: asyncio.Task | None = None
    manager_sub_id: str = ""


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
        except Exception as exc:
            log.debug("typing indicator failed (best-effort): %s", exc)

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

    def _get_mm_state(self, mm_conversations: dict, conv_id: str) -> MattermostConvState:
        """Get or create Mattermost-specific conversation state."""
        if conv_id not in mm_conversations:
            mm_conversations[conv_id] = MattermostConvState()
        return mm_conversations[conv_id]

    async def _download_attachments(self, msgs, conv_id, config):
        """Download user-uploaded files from Mattermost and save as conversation attachments."""
        from .attachments import save_attachment

        max_bytes = getattr(getattr(config, "http", None), "max_upload_bytes", None)

        attachments = []
        for msg in msgs:
            file_ids = msg.get("file_ids") or []
            file_metadata = msg.get("file_metadata") or {}
            for fid in file_ids:
                try:
                    resp = await self._http.get(f"/files/{fid}")
                    resp.raise_for_status()
                    data = resp.content

                    if max_bytes and len(data) > max_bytes:
                        log.warning(f"Mattermost file {fid} too large: "
                                    f"{len(data)} bytes > {max_bytes} limit, skipping")
                        continue

                    fmeta = file_metadata.get(fid, {})
                    filename = fmeta.get("name", f"file-{fid}")
                    mime_type = fmeta.get("mime_type", "application/octet-stream")

                    att = save_attachment(config, conv_id, filename, data, mime_type)
                    attachments.append(att)
                    log.info(f"Saved Mattermost attachment: {att['filename']} ({mime_type})")
                except Exception as e:
                    log.warning(f"Failed to download Mattermost file {fid}: {e}")
        return attachments

    async def _post_response(self, response, channel_id, root_id, conv_display):
        """Post any remaining response content not handled by ConversationDisplay."""
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

    async def _process_conversation(self, conv_id, channel_id, msgs,
                                    app_ctx, mm_conversations, manager):
        """Process accumulated messages for a conversation."""
        mm_state = self._get_mm_state(mm_conversations, conv_id)
        cooldown_sec = self.cooldown_ms / 1000.0

        # Check if busy via the manager (circuit breaker is in the manager) — queue locally and re-debounce.
        # Cancel-on-new-message is handled by manager.send_message().
        mgr_state = manager.get_state(conv_id)
        if mgr_state and mgr_state.busy:
            log.info(f"Conversation {conv_id[:8]} busy, queuing {len(msgs)} message(s)")
            mm_state.pending_msgs = msgs + mm_state.pending_msgs
            mm_state.debounce_timer = asyncio.create_task(
                self._debounce_fire(conv_id, channel_id, mm_conversations,
                                    app_ctx, manager)
            )
            return

        # Cooldown: wait if we responded too recently
        now = time.monotonic()
        wait = cooldown_sec - (now - mm_state.last_response_time)
        if wait > 0:
            log.info(f"Cooldown: waiting {wait:.1f}s before responding in {conv_id[:8]}")
            await asyncio.sleep(wait)

        # -- Command dispatch --
        from .commands import dispatch_command
        from .context import Context

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

        cmd_ctx = Context(config=app_ctx.config, event_bus=app_ctx.event_bus)
        cmd_ctx.user_id = app_ctx.config.agent.user_id
        cmd_ctx.manager = manager
        cmd_result = await dispatch_command(cmd_ctx, combined_text, prefixes=["!"])

        if cmd_result.mode in ("help", "unknown", "error", "fork"):
            await self.send(channel_id, cmd_result.text, root_id=root_id)
            return

        # Send placeholder
        placeholder_id = await self.send_placeholder(channel_id, root_id=root_id)
        await self.send_typing(channel_id)

        archive_text = ""
        command_ctx = None
        if cmd_result.mode == "inline":
            combined_text = cmd_result.text
            archive_text = cmd_result.display_text
            command_ctx = cmd_ctx

        # Download user-uploaded files
        attachments = await self._download_attachments(msgs, conv_id, app_ctx.config)

        # Create ConversationDisplay for this turn
        conv_display = ConversationDisplay(
            self, channel_id, root_id,
            throttle_ms=app_ctx.config.mattermost.stream_throttle_ms,
            initial_post_id=placeholder_id,
            config=app_ctx.config,
            conv_id=conv_id,
        )
        mm_state.conv_display = conv_display

        # Attach stop button and start emoji-based cancel polling
        from .mattermost_ui import build_stop_button
        stop_attachments = build_stop_button(app_ctx.config, conv_id)
        if stop_attachments and placeholder_id:
            conv_display._stop_attachments = stop_attachments
            conv_display._stop_on_post = placeholder_id
            await self.edit_message(
                placeholder_id, THINKING_INDICATOR,
                props={"attachments": stop_attachments},
            )

        if placeholder_id:
            # Poll for stop emoji reactions — cancel via manager
            async def poll_and_cancel():
                cancel_sentinel = asyncio.Event()
                await self._poll_cancel(placeholder_id, cancel_sentinel)
                if cancel_sentinel.is_set():
                    await manager.cancel_turn(conv_id)
            mm_state.cancel_task = asyncio.create_task(poll_and_cancel())

        # Pre-load history with Mattermost thread-fork logic.
        # For threads: if no archive exists, fork from the channel history.
        # The manager's load_history will use this pre-loaded history.
        from .archive import restore_history
        restored = restore_history(app_ctx.config, conv_id)
        if restored:
            manager.set_initial_history(conv_id, restored)
        elif root_id:
            # New thread — fork from channel history
            channel_state = manager.get_state(channel_id)
            channel_history = channel_state.history if channel_state else []
            if channel_history:
                manager.set_initial_history(conv_id, list(channel_history))
                log.debug("Forked thread %s history from channel %s (%d msgs)",
                          conv_id[:8], channel_id[:8], len(channel_history))

        # Subscribe to manager events for this conversation
        self._ensure_subscribed(mm_state, conv_id, channel_id, root_id,
                                app_ctx, manager)

        # Transport-specific context setup
        def context_setup(ctx):
            from .media import MattermostMediaHandler
            ctx.media_handler = MattermostMediaHandler(self._http, channel_id=channel_id)
            ctx.channel_id = channel_id
            ctx.channel_name = ""
            ctx.thread_id = root_id or ""
            ctx.user_id = app_ctx.config.agent.user_id

        # Start the turn via the manager
        await manager.send_message(
            conv_id, combined_text,
            user_id=app_ctx.config.agent.user_id,
            context_setup=context_setup,
            archive_text=archive_text,
            attachments=attachments or None,
            command_ctx=command_ctx,
        )

    def _ensure_subscribed(self, mm_state, conv_id, channel_id, root_id,
                           app_ctx, manager):
        """Subscribe to manager events for a conversation, routing to ConversationDisplay."""
        # Unsubscribe previous subscription if any (fresh per-turn display)
        if mm_state.manager_sub_id:
            manager.unsubscribe(conv_id, mm_state.manager_sub_id)
            mm_state.manager_sub_id = ""

        client = self
        config = app_ctx.config
        from .config import resolve_streaming
        reflection_visibility = config.reflection.visibility

        def is_streaming():
            """Resolve streaming for the conversation's active model."""
            conv_state = manager.get_state(conv_id)
            model = conv_state.persisted.active_model if conv_state else ""
            return resolve_streaming(config, model)
        compaction_post_id = None

        async def on_manager_event(event):
            nonlocal compaction_post_id
            cd = mm_state.conv_display
            event_type = event.get("type", "")

            if event_type == "chunk" and cd:
                await cd.on_stream_chunk("text", event.get("text", ""))

            elif event_type == "stream_done" and cd:
                await cd.on_stream_chunk("done", None)

            elif event_type == "tool_call_start" and cd:
                await cd.on_stream_chunk("tool_call_start", event)

            elif event_type == "llm_start" and cd:
                await cd.on_llm_start(event.get("iteration", 1))

            elif event_type == "llm_end" and cd:
                if not is_streaming():
                    content = event.get("content")
                    if content:
                        await cd.on_text_complete(content)

            elif event_type == "text_before_tools" and cd:
                content = event.get("text", "")
                if content and not is_streaming():
                    await cd.on_text_complete(content)

            elif event_type == "tool_start" and cd:
                await cd.on_tool_start(
                    event.get("tool", "tool"), event.get("args", {}),
                    tool_call_id=event.get("tool_call_id", ""))

            elif event_type == "tool_status" and cd:
                await cd.on_tool_status(
                    event.get("tool", "tool"), event.get("message", ""),
                    tool_call_id=event.get("tool_call_id", ""))

            elif event_type == "tool_end" and cd:
                await cd.on_tool_end(
                    event.get("tool", "tool"),
                    event.get("result_text", ""),
                    event.get("display_text"),
                    event.get("media", []),
                    tool_call_id=event.get("tool_call_id", ""),
                    display_short_text=event.get("display_short_text"),
                )

            elif event_type == "tool_media_uploaded" and cd:
                await cd.on_tool_media_uploaded(
                    event.get("file_ids", []),
                    tool_call_id=event.get("tool_call_id", ""),
                )

            elif event_type == "confirmation_request" and cd:
                confirmation_id = event.get("confirmation_id", "")
                action_data = event.get("action_data", {})
                command = action_data.get("command", event.get("message", ""))
                suggested_pattern = action_data.get("suggested_pattern", "")
                action_type = event.get("action_type", "")

                # Widget responses are a web-UI-only interaction. Skip
                # rendering approve/deny buttons in Mattermost — there's
                # no widget surface here, and the buttons would resolve
                # the confirmation with no data, producing an unhelpful
                # "User responded with: {}" inject. The agent's tool
                # description for ask_user_multiple_choice already discourages calls
                # outside the web UI.
                if action_type == "widget_response":
                    log.info(
                        "Skipping Mattermost render for widget_response "
                        "confirmation %s — not a Mattermost-supported "
                        "widget surface",
                        confirmation_id)
                    return

                # Create confirmation post with buttons/emoji (no legacy polling)
                confirm_post_id = await cd.on_confirm_request(
                    action_type, command, suggested_pattern,
                    app_ctx.event_bus, "",  # context_id not needed for manager flow
                    tool_call_id=event.get("tool_call_id", ""),
                    conv_id=conv_id,
                    confirmation_id=confirmation_id,
                )

                # Start emoji polling routed through the manager
                if confirm_post_id:
                    asyncio.create_task(
                        client._poll_confirmation_manager(
                            confirm_post_id, manager, conv_id,
                            confirmation_id, action_type,
                        )
                    )

            elif event_type == "reflection_result" and cd:
                if reflection_visibility == "hidden":
                    pass
                elif reflection_visibility == "debug":
                    passed = event.get("passed", True)
                    critique = event.get("critique", "")
                    retry_num = event.get("retry_number", 0)
                    raw = event.get("raw_response", "")
                    error = event.get("error", "")
                    text = (f"\U0001f50d **Reflection** (retry {retry_num}): "
                            f"{'PASS' if passed else 'FAIL'}")
                    if critique:
                        text += f"\nCritique: {critique}"
                    if error:
                        text += f"\nError: {error}"
                    if raw:
                        text += (f"\n\n<details><summary>Raw judge output"
                                 f"</summary>\n\n{raw}\n</details>")
                    await cd.on_tool_status("reflection", text)
                elif not event.get("passed", True):
                    critique = event.get("critique", "")
                    retry_num = event.get("retry_number", 0)
                    text = f"\U0001f50d Reflection retry {retry_num}: {critique}"
                    await cd.on_tool_status("reflection", text)

            elif event_type == "vault_retrieval" and cd:
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

            elif event_type == "compaction_start":
                if channel_id:
                    compaction_post_id = await client.send(
                        channel_id, "\U0001f4e6 Compacting conversation...",
                        root_id=root_id,
                    )

            elif event_type == "compaction_end":
                if compaction_post_id:
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
                    except Exception as exc:
                        log.debug("compaction post edit failed: %s", exc)
                    compaction_post_id = None

            elif event_type == "message_complete" and event.get("final"):
                if event.get("suppress_user_message"):
                    # WAKE turn ended with BACKGROUND_WAKE_OK — ensure any
                    # buffered text is dropped so finalize() cannot post it.
                    if cd:
                        cd._text_buffer = ""
                        cd._text_has_content = False
                    return

                # Use the final message_complete text as the authoritative
                # user-visible response.  This ensures turns without streamed
                # chunks (streaming OFF, or WAKE with on_stream_chunk=None)
                # still render correctly when finalized.
                final_text = event.get("text")
                if cd and final_text:
                    cd._text_buffer = final_text
                    cd._text_has_content = True

                # Post any response media (workspace image refs, etc.)
                media = event.get("media") or []
                if media and channel_id:
                    try:
                        from .media import MattermostMediaHandler, upload_and_collect
                        handler = MattermostMediaHandler(
                            client._http, channel_id=channel_id)
                        file_ids = await upload_and_collect(
                            handler, channel_id, media)
                        if file_ids:
                            await handler.send_with_media(
                                channel_id, "", file_ids, root_id=root_id)
                    except Exception as e:
                        log.warning("Failed to post response media: %s", e)

            elif event_type == "error" and cd:
                error_msg = event.get("message", "Unknown error")
                cd._text_buffer = f"\u26a0\ufe0f {error_msg}"
                cd._text_has_content = True

            elif event_type == "turn_complete":
                if cd:
                    await cd.finalize()
                # Cancel the emoji stop-polling task
                if mm_state.cancel_task and not mm_state.cancel_task.done():
                    mm_state.cancel_task.cancel()
                    mm_state.cancel_task = None
                mm_state.last_response_time = time.monotonic()
                mm_state.conv_display = None

        mm_state.manager_sub_id = manager.subscribe(conv_id, on_manager_event)

    async def _debounce_fire(self, conv_id, channel_id, mm_conversations,
                             app_ctx, manager):
        """Wait for debounce window, then process accumulated messages."""
        debounce_sec = self.debounce_ms / 1000.0
        await asyncio.sleep(debounce_sec)
        mm_state = self._get_mm_state(mm_conversations, conv_id)
        msgs = mm_state.pending_msgs
        mm_state.pending_msgs = []
        mm_state.debounce_timer = None
        if msgs:
            await self._process_conversation(
                conv_id, channel_id, msgs, app_ctx, mm_conversations, manager
            )

    # -- Main run loop ---------------------------------------------------------

    async def run(self, app_ctx, shutdown_event, manager=None):
        """Run the bot: connect, listen for messages, and dispatch to the agent."""
        await self.connect()

        agent_tasks = set()
        mm_conversations: dict[str, MattermostConvState] = {}
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

            mm_state = self._get_mm_state(mm_conversations, conv_id)
            log.info(f"Message from {msg['sender_name']} in {conv_id[:8]}: "
                      f"{msg['text'][:50]}")

            # Accumulate message by conversation
            mm_state.pending_msgs.append(msg)

            # Reset debounce timer for this conversation
            if mm_state.debounce_timer:
                mm_state.debounce_timer.cancel()

            async def debounce_and_process():
                await asyncio.sleep(debounce_sec)
                mm_inner = self._get_mm_state(mm_conversations, conv_id)
                msgs = mm_inner.pending_msgs
                mm_inner.pending_msgs = []
                mm_inner.debounce_timer = None
                if msgs:
                    task = asyncio.current_task()
                    agent_tasks.add(task)
                    try:
                        await self._process_conversation(
                            conv_id, channel_id, msgs, app_ctx,
                            mm_conversations, manager,
                        )
                    finally:
                        agent_tasks.discard(task)

            mm_state.debounce_timer = asyncio.create_task(debounce_and_process())

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

    # -- Confirmation polling (manager-based) ----------------------------------

    async def _poll_confirmation_manager(self, post_id, manager, conv_id,
                                         confirmation_id, action_type,
                                         timeout=60, poll_interval=2):
        """Poll a post for reactions to resolve a confirmation via the manager."""

        async def _resolve(approved, always=False, add_pattern=False, label=""):
            await manager.respond_to_confirmation(
                conv_id, confirmation_id,
                approved=approved, always=always, add_pattern=add_pattern,
            )
            try:
                resp = await self._http.get(f"/posts/{post_id}")
                original_text = resp.json().get("message", "") if resp.status_code == 200 else ""
                await self.edit_message(post_id, f"{original_text}\n\n**Result:** {label}")
            except Exception as exc:
                log.debug("confirmation result edit failed for post %s: %s", post_id, exc)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = await self._http.get(f"/posts/{post_id}/reactions")
                resp.raise_for_status()
                reactions = resp.json() or []
                for r in reactions:
                    emoji = r.get("emoji_name", "")
                    user_id = r.get("user_id", "")
                    if user_id == self.bot_user_id:
                        continue
                    if emoji in ("notebook",) and action_type == "run_shell_command":
                        log.info(f"Tool approved with pattern by {user_id}")
                        await _resolve(True, add_pattern=True,
                                       label="\U0001f4d3 approved + pattern added")
                        return
                    elif emoji in ("white_check_mark", "heavy_check_mark") and action_type != "run_shell_command":
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

        log.info(f"Confirmation timed out for {action_type}")
        await _resolve(False, label="\u23f0 timed out")

    async def _poll_cancel(self, post_id, cancel_event, poll_interval=2):
        """Poll a placeholder post for stop reaction to cancel the agent turn."""
        while not cancel_event.is_set():
            try:
                resp = await self._http.get(f"/posts/{post_id}/reactions")
                if resp.status_code == 200:
                    reactions = resp.json() or []
                    for r in reactions:
                        emoji = r.get("emoji_name", "")
                        user_id = r.get("user_id", "")
                        if user_id == self.bot_user_id:
                            continue
                        if emoji in ("octagonal_sign", "stop_sign", "hand", "raised_hand"):
                            log.info(f"Agent turn cancelled by {user_id} via reaction")
                            cancel_event.set()
                            return
            except Exception as exc:
                log.debug("reaction poll failed for post %s: %s", post_id, exc)
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

    async def _get_user_id_by_username(self, username: str) -> str | None:
        """Look up a Mattermost user ID by username. Returns None on 404."""
        resp = await self._http.get(f"/users/username/{username}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("id")

    async def post_direct_message(self, username: str, text: str) -> str | None:
        """Post a direct message to a user by username.

        Resolves the username to a user ID, gets or creates the DM channel,
        and sends the message. Returns the new post ID, or ``None`` if the
        user can't be found (the caller decides whether to log or raise).
        """
        user_id = await self._get_user_id_by_username(username)
        if user_id is None:
            return None
        channel_id = await self._get_dm_channel(user_id)
        return await self.send(channel_id, text)

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

    def _make_heartbeat_cycle(self, _channel_id, event_bus, config, manager):
        """Create an on_cycle callback that runs sections and posts results."""

        async def on_cycle():
            from .tools.heartbeat_tools import _guarded_heartbeat
            await _guarded_heartbeat(config, event_bus, manager)

        return on_cycle
