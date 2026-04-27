"""Per-conversation scratchpad tools (#299).

Always-loaded ``notes_append`` and ``notes_read`` tools backed by an
append-only markdown file. Recent entries auto-inject into context
at turn start so the model doesn't pay a tool call per turn to read
them. See ``docs/notes.md``.
"""

from __future__ import annotations

import logging

from .. import notes as notes_core
from ..media import ToolResult

log = logging.getLogger(__name__)


def _resolve_conv_id(ctx) -> str:
    """Match the fallback chain `_compose_notes` uses so writes and
    reads always target the same file. ``ctx.conv_id`` may be empty
    in contexts that key off ``channel_id`` instead (top-level
    Mattermost messages, some scheduled/heartbeat code paths)."""
    return ctx.conv_id or ctx.channel_id or "default"


def tool_notes_append(ctx, text: str) -> ToolResult:
    """Append one entry to the conversation's scratchpad."""
    if not ctx.config.notes.enabled:
        return ToolResult(text="[error: notes are disabled in this config]")
    conv_id = _resolve_conv_id(ctx)
    try:
        note = notes_core.append_note(
            ctx.config, conv_id, text,
            max_chars=ctx.config.notes.max_entry_chars,
            max_total_entries=ctx.config.notes.max_total_entries,
        )
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")
    return ToolResult(
        text=f"Saved note ({len(note.text)} chars). Recent notes are "
             f"auto-loaded into context on interactive turns; you can "
             f"also read them back via `notes_read`.",
    )


def tool_notes_read(ctx, limit: int = 20) -> ToolResult:
    """Return the most recent N notes."""
    if not ctx.config.notes.enabled:
        return ToolResult(text="[error: notes are disabled in this config]")
    conv_id = _resolve_conv_id(ctx)
    if limit is None or limit <= 0:
        limit = 20
    items = notes_core.read_notes(ctx.config, conv_id, limit=limit)
    if not items:
        return ToolResult(text="[no notes yet]")
    rendered = notes_core.format_notes_for_context(items)
    return ToolResult(text=rendered)


NOTES_TOOLS = {
    "notes_append": tool_notes_append,
    "notes_read": tool_notes_read,
}


NOTES_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "notes_append",
            "description": (
                "Jot a single note to the per-conversation scratchpad — "
                "use for things you want to remember **across turns within "
                "this conversation**: 'user said X', 'we decided Y', "
                "'try Z next', a partial result you'll come back to. "
                "**Prefer this over `vault_write` for transient "
                "conversation-scoped facts** (vault is for curated, cross-"
                "conversation knowledge), and over `checklist_create` "
                "when you don't have a fixed multi-step plan to execute. "
                "Recent notes are auto-loaded into your context every "
                "turn — you do NOT need to read them back unless you want "
                "older entries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "Single-line note (newlines are collapsed). "
                            "Truncated silently if very long."
                        ),
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "notes_read",
            "description": (
                "Return the most recent N notes from this conversation's "
                "scratchpad. Use this only when you need to scan beyond "
                "the recent window that's already auto-injected at turn "
                "start (e.g. searching for an older detail). For "
                "near-recent notes, just rely on the auto-inject."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max notes to return (default 20).",
                    },
                },
                "required": [],
            },
        },
    },
]
