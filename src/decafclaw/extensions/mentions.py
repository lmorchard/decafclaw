"""Explicit @file and @mcp reference core extension (#1491).

Bare `@path` and `@mcp:server/resource` references are fixed-cost injections:
they are always included, archived with the turn, and deduplicated against
history. They mount in the `TAIL` slot directly before the user message.

Skipped for heartbeat, scheduled, and child-agent turns. Fail-open: read or
publish failures log and degrade to the existing inline error text.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.memory_context import parse_bare_mentions
from decafclaw.util import estimate_tokens

from . import BaseExtension, ContextContribution, ContextSlot, register_extension_type

log = logging.getLogger(__name__)

_MAX_INLINE_CHARS = 8192


class MentionsExtension(BaseExtension):
    """Inject workspace-file and MCP-resource references as a TAIL contribution."""

    def __init__(self, options: dict[str, Any], config: Any):
        super().__init__(name="mentions")
        self.options = options or {}
        self.config = config

    def enable(self, bus: EventBus) -> list[str]:
        self.disable(bus)
        self.subscription_ids = [bus.subscribe(TurnLifecycle.PRE_COMPOSE, self.pre_compose_hook)]
        return self.subscription_ids

    async def pre_compose_hook(self, ctx: Any, user_message: str = "", history: list | None = None):
        try:
            return await self._contribute(ctx, user_message, history or [])
        except Exception:
            log.warning("Mention reference injection failed", exc_info=True)
            return None

    async def _contribute(self, ctx: Any, user_message: str, history: list):
        from decafclaw.context_types import ComposerMode, SourceEntry

        config = getattr(ctx, "config", None) or self.config
        mode = getattr(getattr(ctx, "composer", None), "mode", ComposerMode.INTERACTIVE)
        if mode.is_skip:
            return None

        mentions = parse_bare_mentions(user_message)
        if not mentions:
            return None

        already_injected_files: set[str] = set()
        already_injected_mcp: set[str] = set()
        for msg in history:
            if msg.get("role") == "workspace_references":
                value = msg.get("workspace_file")
                if value:
                    already_injected_files.add(value)
            elif msg.get("role") == "mcp_references":
                value = msg.get("mcp_resource")
                if value:
                    already_injected_mcp.add(value)

        messages: list[dict] = []
        skipped = 0

        for ref in mentions:
            if ref["type"] == "file":
                message = self._file_reference(config, ref, already_injected_files)
                if message is None:
                    skipped += 1
                    continue
                messages.append(message)
            elif ref["type"] == "mcp":
                message = await self._mcp_reference(ref, already_injected_mcp)
                if message is None:
                    skipped += 1
                    continue
                messages.append(message)

        if not messages and skipped == 0:
            return None

        for message in messages:
            await ctx.publish(
                "mention_references",
                text=message["content"],
                ref=message.get("workspace_file") or message.get("mcp_resource"),
            )

        tokens = sum(estimate_tokens(m["content"]) for m in messages)
        return ContextContribution(
            slot=ContextSlot.TAIL,
            messages=messages,
            source_entry=SourceEntry(
                source="mentions",
                tokens_estimated=tokens,
                items_included=len(messages),
                items_truncated=skipped,
            ),
        )

    @staticmethod
    def _file_reference(config: Any, ref: dict, already_injected: set[str]) -> dict | None:
        from decafclaw.web.workspace_paths import (
            PathContainmentError,
            detect_kind,
            is_secret,
            resolve_contained,
            workspace_allowed_roots,
        )

        rel_path = ref["path"]
        if rel_path in already_injected:
            return None

        try:
            resolved = resolve_contained(
                config.workspace_path, rel_path, label="the workspace", allow_roots=workspace_allowed_roots(config)
            )
        except PathContainmentError:
            resolved = None
        if not resolved or not resolved.is_file() or detect_kind(resolved) != "text":
            text = f"[Workspace file '{rel_path}' not found or not readable as text]"
        elif is_secret(rel_path):
            text = f"[Workspace file '{rel_path}' is a secret and cannot be inlined]"
        else:
            try:
                with resolved.open("r", encoding="utf-8") as f:
                    content = f.read(_MAX_INLINE_CHARS + 1)
                if len(content) > _MAX_INLINE_CHARS:
                    content = content[:_MAX_INLINE_CHARS] + f"\n\n[Truncated: only first 8KB of {rel_path} inlined]"
                text = f"[Referenced workspace file: {rel_path}]\n\n{content}"
            except Exception as exc:
                text = f"[Error reading workspace file '{rel_path}': {exc}]"

        return {"role": "workspace_references", "content": text, "workspace_file": rel_path}

    @staticmethod
    async def _mcp_reference(ref: dict, already_injected: set[str]) -> dict | None:
        from decafclaw.mcp_client import ConnState, _convert_resource_response, get_registry

        server_name = ref["server"]
        resource_name = ref["resource"]
        raw_ref = ref["raw"]
        if raw_ref in already_injected:
            return None

        registry = get_registry()
        state = registry.servers.get(server_name) if registry else None
        if not state or state.status != ConnState.CONNECTED or not state.session:
            text = f"[MCP server '{server_name}' is not connected to retrieve '{resource_name}']"
        else:
            target_res = None
            for resource in state.resources:
                if (
                    getattr(resource, "name", "") == resource_name
                    or str(getattr(resource, "uri", "")) == resource_name
                    or str(getattr(resource, "uri", "")).endswith("/" + resource_name)
                ):
                    target_res = resource
                    break

            if not target_res:
                text = f"[MCP resource '{resource_name}' not found on server '{server_name}']"
            else:
                try:
                    timeout_s = state.config.timeout / 1000
                    result = await asyncio.wait_for(
                        state.session.read_resource(str(target_res.uri)),
                        timeout=timeout_s,
                    )
                    tool_res = _convert_resource_response(result)
                    content = tool_res.text
                    if len(content) > _MAX_INLINE_CHARS:
                        content = content[:_MAX_INLINE_CHARS] + f"\n\n[Truncated: only first 8KB of {raw_ref} inlined]"
                    text = f"[Referenced MCP resource: {raw_ref}]\n\n{content}"
                except asyncio.TimeoutError:
                    text = f"[Error: reading MCP resource '{resource_name}' timed out after {timeout_s}s]"
                except Exception as exc:
                    text = f"[Error reading MCP resource '{resource_name}': {exc}]"

        return {"role": "mcp_references", "content": text, "mcp_resource": raw_ref}


register_extension_type("mentions", MentionsExtension, default_enabled=True)
