"""ConversationDisplay — manages Mattermost messages for a single agent turn."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mattermost import MattermostClient

log = logging.getLogger(__name__)

# Status indicators shown in placeholder messages
THINKING_INDICATOR = "\U0001f4ad Thinking..."
THINKING_SUFFIX = "\n\U0001f4ad Thinking..."


class ConversationDisplay:
    """Manages a sequence of Mattermost messages for a single agent turn.

    Instead of editing one placeholder, posts separate messages for text
    segments and tool calls, creating a visible trail of agent activity.
    Works for both streaming and non-streaming mode.
    """

    def __init__(self, client: MattermostClient, channel_id, root_id,
                 throttle_ms=200, initial_post_id=None, config=None,
                 conv_id=None):
        self.client = client
        self._channel_id = channel_id
        self._root_id = root_id
        self._throttle_ms = throttle_ms
        self._initial_post_id = initial_post_id
        self._config = config
        self._conv_id = conv_id

        # Current message state
        self._current_post_id = initial_post_id
        self._current_type = "thinking" if initial_post_id else None
        self._text_buffer = ""
        self._text_has_content = False  # True if any real text was written

        # Tool call state — maps tool_call_id → Mattermost post ID
        self._tool_posts: dict[str, str] = {}
        self._tools_text_finalized = False

        # Throttling
        self._last_edit_time = 0

        # Turn state
        self._finalized = False
        self._stop_attachments: list[dict] | None = None
        self._stop_on_post: str | None = None  # post ID that currently has the button
        self._cancel_sub: str | None = None

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
                attachments=self._stop_attachments,
            )
            self._current_post_id = post_id
            self._current_type = "thinking"
            if self._stop_attachments:
                self._stop_on_post = post_id

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
                attachments=self._stop_attachments,
            )
            self._current_post_id = post_id
            if self._stop_attachments:
                self._stop_on_post = post_id
        self._current_type = None

    async def on_stream_chunk(self, chunk_type, data):
        """Adapter for call_llm_streaming's on_chunk callback."""
        if chunk_type == "text":
            await self.on_text_chunk(data)
        elif chunk_type == "tool_call_start":
            # LLM is switching to tool calls — finalize text if we have any.
            # Don't touch thinking placeholder with no text — on_tool_start
            # will reuse it.
            if self._text_has_content:
                await self._finalize_current_text()
                self._current_type = None
        elif chunk_type == "done":
            # Only finalize text if we actually streamed text content.
            # If the LLM went straight to tool calls, preserve the thinking
            # placeholder for on_tool_start to reuse.
            if self._text_has_content:
                await self.on_text_done()

    # -- Tool call lifecycle ---------------------------------------------------

    async def on_tool_start(self, tool_name, args, tool_call_id=""):
        """Tool execution starting — finalize text, post tool call message."""
        msg = f"\U0001f527 {tool_name}..."

        # First tool in a batch: finalize text / reuse thinking placeholder
        if not self._tools_text_finalized:
            self._tools_text_finalized = True
            if self._current_type == "thinking" and self._current_post_id and not self._text_has_content:
                await self._force_edit(self._current_post_id, msg)
                post_id = self._current_post_id
                self._current_post_id = None
            else:
                await self._finalize_current_text()
                post_id = await self.client.send(
                    self._channel_id, msg, root_id=self._root_id,
                    attachments=self._stop_attachments,
                )
                if self._stop_attachments:
                    self._stop_on_post = post_id
        else:
            # Subsequent concurrent tool — always a new post
            post_id = await self.client.send(
                self._channel_id, msg, root_id=self._root_id,
            )

        self._tool_posts[tool_call_id] = post_id
        self._current_type = "tool"

    async def on_tool_status(self, tool_name, message, tool_call_id=""):
        """Tool status update — edit the tool call's progress message.

        If no existing tool post is found (e.g., vault_retrieval or reflection
        events that arrive without a preceding tool_start), create a standalone post.
        """
        post_id = self._tool_posts.get(tool_call_id)
        if not post_id and self._tool_posts:
            # Fallback: use most recent post if tool_call_id not found
            post_id = list(self._tool_posts.values())[-1]
        if post_id:
            try:
                await self.client.edit_message(
                    post_id,
                    f"\U0001f527 {tool_name}: {message}",
                )
            except Exception as exc:
                log.debug("Mattermost API call failed: %s", exc)
        else:
            # Standalone status (no preceding tool_start) — create a new post
            try:
                await self.client.send(
                    self._channel_id, message, root_id=self._root_id,
                )
            except Exception as exc:
                log.debug("Mattermost API call failed: %s", exc)

    async def on_tool_end(self, tool_name, result_text, display_text, media,
                          tool_call_id="", display_short_text=None):
        """Tool finished — edit tool call message with result, handle media."""
        if display_text:
            msg = display_text
        elif display_short_text:
            msg = f"\U0001f527 {tool_name}: {display_short_text} \u2714\ufe0f"
        else:
            msg = f"\U0001f527 {tool_name} \u2714\ufe0f"

        post_id = self._tool_posts.pop(tool_call_id, None)
        if post_id:
            try:
                await self.client.edit_message(
                    post_id, msg, props={"attachments": []},
                )
                if self._stop_on_post == post_id:
                    self._stop_on_post = None
            except Exception as exc:
                log.debug("Mattermost API call failed: %s", exc)

        # Reset tool state when all concurrent tools are done
        if not self._tool_posts:
            self._current_type = None
            self._tools_text_finalized = False

    async def on_tool_media_uploaded(self, file_ids, tool_call_id=""):
        """Tool media was uploaded — post files as a thread reply."""
        if not file_ids:
            return
        try:
            from .media import MattermostMediaHandler
            handler = MattermostMediaHandler(self.client._http, channel_id=self._channel_id)
            await handler.send_with_media(
                self._channel_id, "", file_ids, root_id=self._root_id,
            )
        except Exception as e:
            log.debug(f"Failed to post tool media: {e}")

    # -- Confirmation ----------------------------------------------------------

    async def on_confirm_request(self, tool_name, command, suggested_pattern,
                                  event_bus, context_id, tool_call_id=""):
        """Tool needs confirmation — show prompt with buttons and/or emoji."""
        from .mattermost_ui import build_confirm_buttons

        config = self._config

        # Build the confirmation message text
        msg = f"\U0001f6a8 **Confirm {tool_name}:**\n```\n{command}\n```"

        # Add emoji instructions (unless disabled)
        # Show emoji instructions if enabled (default: on when HTTP off, off when HTTP on)
        show_emoji = not config or config.mattermost.enable_emoji_confirms
        if show_emoji:
            if tool_name == "shell" and suggested_pattern:
                msg += (
                    f"\nReact: \U0001f44d approve | \U0001f44e deny"
                    f" | \U0001f4d3 allow `{suggested_pattern}`"
                )
            else:
                msg += "\nReact: \U0001f44d approve | \U0001f44e deny | \u2705 always"

        # Build interactive button attachments
        attachments = []
        if config:
            attachments = build_confirm_buttons(
                config, tool_name, command, suggested_pattern,
                context_id, msg, tool_call_id=tool_call_id,
            )
            if attachments:
                import json as _json
                log.debug(f"Confirm buttons: {_json.dumps(attachments, indent=2)}")

        tool_post_id = self._tool_posts.get(tool_call_id)
        if tool_post_id:
            # Edit existing post — can't add attachments to edit, so send new
            if attachments:
                confirm_post_id = await self.client.send(
                    self._channel_id, msg, root_id=self._root_id,
                    attachments=attachments,
                )
            else:
                try:
                    await self.client.edit_message(tool_post_id, msg)
                except Exception as exc:
                    log.debug("Mattermost API call failed: %s", exc)
                confirm_post_id = tool_post_id
        else:
            confirm_post_id = await self.client.send(
                self._channel_id, msg, root_id=self._root_id,
                attachments=attachments,
            )
            self._tool_posts[tool_call_id] = confirm_post_id

        # Start emoji reaction polling if emoji confirms are enabled
        if confirm_post_id and show_emoji:
            asyncio.create_task(
                self.client._poll_confirmation(
                    confirm_post_id, event_bus, context_id, tool_name,
                    tool_call_id=tool_call_id,
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
                except Exception as exc:
                    log.debug("Mattermost API call failed: %s", exc)

        # Strip stop button from whichever post currently has it
        if self._stop_on_post:
            await self._strip_stop_from(self._stop_on_post)
        self._stop_attachments = None

    # -- Helpers ---------------------------------------------------------------

    async def _strip_stop_from(self, post_id):
        """Explicitly strip stop button attachments from a post.

        Fetches the post first to preserve its message text — a props-only
        edit with no message causes Mattermost to show '(message deleted)'.
        """
        if not post_id:
            return
        try:
            resp = await self.client._http.get(f"/posts/{post_id}")
            resp.raise_for_status()
            current_text = resp.json().get("message", "")
            await self.client.edit_message(
                post_id, current_text, props={"attachments": []},
            )
        except Exception as exc:
            log.debug("Mattermost API call failed: %s", exc)
        if self._stop_on_post == post_id:
            self._stop_on_post = None

    async def _finalize_current_text(self):
        """Strip thinking suffix and stop button from current text message."""
        if self._current_type in ("text", "thinking") and self._current_post_id:
            if self._text_has_content and self._text_buffer:
                # Final text edit + strip stop button in one call
                try:
                    await self.client.edit_message(
                        self._current_post_id, self._text_buffer,
                        props={"attachments": []},
                    )
                    if self._stop_on_post == self._current_post_id:
                        self._stop_on_post = None
                except Exception as exc:
                    log.debug("Mattermost API call failed: %s", exc)
            else:
                await self._strip_stop_from(self._current_post_id)
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
        """Edit a post immediately, updating the throttle timer.

        Text-only edit — does not touch props/attachments.
        """
        if not post_id:
            return
        self._last_edit_time = time.monotonic()
        try:
            await self.client.send_typing(self._channel_id)
        except Exception as exc:
            log.debug("Mattermost API call failed: %s", exc)
        try:
            await self.client.edit_message(post_id, text)
        except Exception as exc:
            log.debug("Mattermost API call failed: %s", exc)
