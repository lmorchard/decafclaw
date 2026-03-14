"""Mattermost client — WebSocket for receiving, REST for sending."""

import asyncio
import json
import logging

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

    async def send_placeholder(self, channel_id, root_id=None, text="Thinking..."):
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

        # Ignore system messages
        if post.get("type", "") != "":
            return

        # Ignore own messages
        if post.get("user_id") == self.bot_user_id:
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

        on_message({
            "text": message,
            "channel_id": post.get("channel_id", ""),
            "post_id": post.get("id", ""),
            "root_id": post.get("root_id", ""),
            "user_id": post.get("user_id", ""),
            "sender_name": data.get("sender_name", ""),
            "channel_type": channel_type,
            "mentioned": mentioned,
        })

    async def close(self):
        await self._http.aclose()
