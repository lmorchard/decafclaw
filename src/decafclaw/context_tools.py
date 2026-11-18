"""Tool-schema assembly and per-iteration tool synchronization (#1491)."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

from .context_types import ComposerState, SourceEntry
from .tool_definitions import build_tool_list, collect_all_tool_defs, refresh_dynamic_tools
from .tools.tool_registry import estimate_tool_tokens
from .util import estimate_tokens

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def compose_tools(ctx: "Context") -> tuple[list[dict], list[dict], str | None, str | None, SourceEntry]:
    """Classify tools into active and deferred sets, and render tool guidance.

    Returns (active_tools, deferred_tools, deferred_text, guidance_text, source_entry).
    Uses the same assembly path as iteration requests, with diagnostics.
    """
    active, deferred_text, guidance_text = build_tool_list(ctx, all_defs=collect_all_tool_defs(ctx))
    deferred = ctx.tools.deferred_pool

    tool_tokens = estimate_tool_tokens(active)
    text_tokens = estimate_tokens(deferred_text) if deferred_text else 0
    guidance_tokens = estimate_tokens(guidance_text) if guidance_text else 0
    entry = SourceEntry(
        source="tools",
        tokens_estimated=tool_tokens + text_tokens + guidance_tokens,
        items_included=len(active),
        items_truncated=len(deferred),
        details={
            "deferred_mode": bool(deferred),
            "has_guidance": bool(guidance_text),
        },
    )
    return active, deferred, deferred_text, guidance_text, entry


def update_iteration_tools(ctx: "Context", messages: list[dict], state: ComposerState) -> list[dict]:
    """Rebuild active tools and synchronize deferred/guidance messages in-place.

    Called at the start of each agent iteration in TurnRunner.
    Returns the active tool definitions for the iteration's LLM call.
    """
    refresh_dynamic_tools(ctx)
    all_tools, deferred_text, guidance_text = build_tool_list(ctx)
    sync_tool_messages(messages, deferred_text, guidance_text)
    record_prefix(ctx, messages, all_tools, state)
    return all_tools


def record_prefix(ctx: "Context", messages: list[dict], tools: list[dict], state: ComposerState) -> None:
    """Explain early-prompt changes at the actual iteration boundary (#1257).

    Rebuild from current capabilities every time: cached schemas must never
    preserve a revoked tool or suppress an explicit activation/config update.
    """
    selection = ctx.active_model or ctx.config.default_model
    model_config = ctx.config.model_configs.get(selection)
    components = {
        "model": {"selection": selection, "config": asdict(model_config) if model_config else ctx.config.llm.model},
        "tools": tools,
        "system": [m for m in messages if m.get("role") == "system"],
    }
    fingerprints = {
        name: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        for name, value in components.items()
    }
    previous = state.prefix_fingerprints
    changed = [name for name, digest in fingerprints.items() if previous and previous.get(name) != digest]
    if changed:
        log.info("Prompt prefix reset for conv %s: %s changed", ctx.conv_id, ", ".join(changed))
    state.prefix_fingerprints = fingerprints
    for source in state.last_sources:
        if source.source == "tools":
            source.details["prefix_changes"] = changed


def sync_tool_messages(messages: list[dict], deferred_text: str | None, guidance_text: str | None) -> None:
    """Synchronize <deferred_tools> and <tool_guidance> system messages in-place.

    Maintains the invariant:
    - If deferred_text is present, a <deferred_tools> message exists.
    - If guidance_text is present, a <tool_guidance> message exists, placed
      immediately after <deferred_tools> (or where <deferred_tools> would be).
    - If either is None, any corresponding existing message is removed.
    """
    def_idx = next(
        (
            i
            for i, m in enumerate(messages)
            if m.get("role") == "system" and m.get("content", "").startswith("<deferred_tools>")
        ),
        None,
    )
    gui_idx = next(
        (
            i
            for i, m in enumerate(messages)
            if m.get("role") == "system" and m.get("content", "").startswith("<tool_guidance>")
        ),
        None,
    )

    # Base insertion target: after system prompt (index 0) and vault guide if present
    base_target = 1
    if len(messages) > 1 and messages[1].get("role") == "system":
        content = messages[1].get("content", "")
        if not content.startswith(("<deferred_tools>", "<tool_guidance>")):
            base_target = 2

    # 1. Sync deferred_tools
    if deferred_text:
        def_msg = {"role": "system", "content": deferred_text}
        if def_idx is not None:
            messages[def_idx] = def_msg
        else:
            target = gui_idx if gui_idx is not None else base_target
            messages.insert(target, def_msg)
            if gui_idx is not None and target <= gui_idx:
                gui_idx += 1
            def_idx = target
    elif def_idx is not None:
        messages.pop(def_idx)
        if gui_idx is not None and gui_idx > def_idx:
            gui_idx -= 1
        def_idx = None

    # 2. Sync tool_guidance
    if guidance_text:
        gui_msg = {"role": "system", "content": guidance_text}
        if gui_idx is not None:
            messages[gui_idx] = gui_msg
        else:
            target = (def_idx + 1) if def_idx is not None else base_target
            messages.insert(target, gui_msg)
    elif gui_idx is not None:
        messages.pop(gui_idx)
