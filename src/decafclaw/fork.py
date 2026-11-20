"""Conversation forking — extract a bounded context slice and build the brief.

A fork is a top-level conversation that starts with a copy of recent parent
context plus the user's branch instruction. It gives meta-concerns, alternative
approaches, and design explorations a clean context window without derailing
the parent session.

This module is transport- and storage-neutral: it reads archive-shaped message
dicts and returns plain text. The web layer owns conversation creation, the
turn, and the parent notice. The ``dissect`` template lives in
:mod:`decafclaw.dissect` and layers a structured failure extractor on the same
focus and formatting primitives.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

ForkTemplate = Literal["general", "dissect"]
FORK_TEMPLATES: tuple[ForkTemplate, ...] = ("general", "dissect")

# The general fork copies the last one or two typed exchanges, bounded so a
# long turn cannot flood the child's context. The dissection template owns its
# own, larger failure budget.
MAX_FORK_EXCHANGES = 2
MAX_FORK_TIMELINE_CHARS = 12_000
MAX_FORK_TIMELINE_MESSAGES = 12
MAX_MESSAGE_CHARS = 4_000

# Roles that carry conversation content we can quote. Runtime bookkeeping
# records (model/mode/compaction/notice) are not part of the fork.
QUOTABLE_ROLES = {"user", "assistant", "tool"}

_SYNTHETIC_SOURCES = {"widget_response", "reflection_retry", "system_update"}
_SYNTHETIC_PREFIXES = ("[reflection]", "[System Update:")

_OMITTED_NOTE = "_(earlier messages omitted to keep this brief bounded)_"


def normalize_fork_template(template: str | None) -> ForkTemplate:
    """Validate a template name, defaulting an omitted one to ``general``."""
    name = (template or "general").strip().lower()
    if name not in FORK_TEMPLATES:
        raise ValueError(f"Unknown fork template: {template!r}")
    return name  # type: ignore[return-value]


def is_synthetic_user_message(msg: dict) -> bool:
    """True for runtime-injected user-role messages, not typed prompts."""
    if msg.get("source") in _SYNTHETIC_SOURCES:
        return True
    content = str(msg.get("content", "")).strip()
    return content.startswith(_SYNTHETIC_PREFIXES)


def is_tool_error(content: object) -> bool:
    """True when a tool result carries a DecafClaw error prefix."""
    return isinstance(content, str) and content.lstrip().startswith("[error")


@dataclass
class ForkFocus:
    """The exchange (or failure) a fork is anchored to."""

    tool_call_id: str = ""
    tool_name: str = ""
    arguments: str = ""
    error: str = ""
    timestamp: str = ""
    user_index: int | None = None
    assistant_index: int | None = None
    tool_index: int | None = None

    @property
    def has_error(self) -> bool:
        return bool(self.error)


@dataclass
class _CallInfo:
    index: int
    name: str
    arguments: str


def tool_calls_by_id(messages: list[dict]) -> dict[str, _CallInfo]:
    """Map tool_call_id → the assistant call that produced it."""
    calls: dict[str, _CallInfo] = {}
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            call_id = str(tc.get("id") or "")
            if not call_id:
                continue
            fn = tc.get("function") or {}
            calls[call_id] = _CallInfo(
                index=i,
                name=str(fn.get("name") or tc.get("name") or ""),
                arguments=str(fn.get("arguments") or ""),
            )
    return calls


def latest_typed_user_index(messages: list[dict]) -> int | None:
    """Index of the newest typed user prompt, ignoring runtime injections."""
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "user" and not is_synthetic_user_message(msg):
            return i
    return None


def find_user_index(messages: list[dict], before: int) -> int | None:
    """Nearest typed user prompt at or before ``before``."""
    for i in range(min(before, len(messages) - 1), -1, -1):
        msg = messages[i]
        if msg.get("role") == "user" and not is_synthetic_user_message(msg):
            return i
    return None


def last_assistant_index(messages: list[dict], before: int) -> int | None:
    for i in range(min(before, len(messages) - 1), -1, -1):
        if messages[i].get("role") == "assistant":
            return i
    return None


def _focus_from_tool(messages: list[dict], tool_index: int, calls: dict[str, _CallInfo]) -> ForkFocus:
    msg = messages[tool_index]
    call_id = str(msg.get("tool_call_id") or "")
    call = calls.get(call_id)
    assistant_index = call.index if call else last_assistant_index(messages, tool_index - 1)
    content = str(msg.get("content") or "")
    error = content if is_tool_error(content) else ""
    return ForkFocus(
        tool_call_id=call_id,
        tool_name=call.name if call else "",
        arguments=call.arguments if call else "",
        error=error,
        timestamp=str(msg.get("timestamp") or ""),
        user_index=find_user_index(messages, assistant_index if assistant_index is not None else tool_index),
        assistant_index=assistant_index,
        # A named successful result is still useful context; only the failing
        # result is rendered separately under "What failed".
        tool_index=tool_index if error else None,
    )


def find_fork_focus(messages: list[dict], tool_call_id: str = "") -> ForkFocus | None:
    """Pick the exchange a fork is anchored to.

    Priority:
    1. the named ``tool_call_id`` when it has a tool result (explicit intent,
       including a card from older history);
    2. a tool error inside the immediate latest turn/exchange only;
    3. the most recent typed user exchange (a conversational fork).

    An empty ``tool_call_id`` never scans older history for an error: an error
    from a previous turn must not hijack a fork of the current conversation.
    Returns ``None`` only when the archive has no user, assistant, or tool
    content at all.
    """
    if not messages:
        return None
    calls = tool_calls_by_id(messages)

    if tool_call_id:
        for i, msg in enumerate(messages):
            if msg.get("role") == "tool" and str(msg.get("tool_call_id") or "") == tool_call_id:
                return _focus_from_tool(messages, i, calls)
        # The named call may have been interrupted before its result landed;
        # fall through to the latest exchange rather than fabricating a focus.

    latest_user = latest_typed_user_index(messages)
    if latest_user is not None:
        exchange_start = latest_user
    else:
        # No typed prompt: the latest turn is the last assistant/tool
        # sequence, never the whole archive.
        last_assistant = last_assistant_index(messages, len(messages) - 1)
        exchange_start = last_assistant if last_assistant is not None else 0

    # Only the immediate latest turn counts. Older failures belong to earlier
    # work and must not be resurrected as the subject of this fork.
    for i in range(len(messages) - 1, exchange_start - 1, -1):
        msg = messages[i]
        if msg.get("role") == "tool" and is_tool_error(msg.get("content")):
            return _focus_from_tool(messages, i, calls)

    # No error in the latest exchange: this is a conversational fork. Seed it
    # with the most recent exchange so the user can branch without copying the
    # whole parent.
    last_content = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if messages[i].get("role") in QUOTABLE_ROLES and str(messages[i].get("content") or "").strip()
        ),
        None,
    )
    if last_content is None:
        return None
    assistant_index = last_assistant_index(messages, last_content)
    if latest_user is not None and assistant_index is not None and assistant_index < latest_user:
        assistant_index = None
    return ForkFocus(
        user_index=latest_user,
        assistant_index=assistant_index,
        timestamp=str(messages[last_content].get("timestamp") or ""),
    )


def clip_text(text: object, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    # Keep both ends: tracebacks lead with the exception and end with the
    # failing frame; long command output often has the useful part in either.
    head = limit * 2 // 3
    tail = limit - head
    return f"{value[:head]}\n… [truncated {len(value) - limit} chars] …\n{value[-tail:]}"


def format_arguments(raw: str, limit: int) -> str:
    if not raw:
        return "(not recorded)"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return clip_text(raw, limit)
    return clip_text(json.dumps(parsed, indent=2, sort_keys=True), limit)


def format_tool_calls(msg: dict, argument_limit: int) -> str:
    lines = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or "tool"
        call_id = tc.get("id") or ""
        args = fn.get("arguments") or tc.get("arguments") or ""
        lines.append(
            f"→ tool call `{name}` (id: {call_id})\n```json\n{format_arguments(str(args), argument_limit)}\n```"
        )
    return "\n\n".join(lines)


def format_message(
    msg: dict,
    calls: dict[str, _CallInfo],
    *,
    message_limit: int = MAX_MESSAGE_CHARS,
    argument_limit: int = 2_000,
) -> str:
    role = msg.get("role")
    content = clip_text(msg.get("content"), message_limit)
    if role == "user":
        return f"### User\n{content}" if content.strip() else ""
    if role == "assistant":
        parts = []
        if content.strip():
            parts.append(content)
        formatted_calls = format_tool_calls(msg, argument_limit)
        if formatted_calls:
            parts.append(formatted_calls)
        return "### Assistant\n" + ("\n\n".join(parts) if parts else "(no text)")
    if role == "tool":
        call = calls.get(str(msg.get("tool_call_id") or ""))
        label = f"Tool result: {call.name}" if call else "Tool result"
        return f"### {label}\n```\n{content}\n```"
    return ""


def bounded_timeline(
    messages: list[dict],
    start: int,
    end: int,
    calls: dict[str, _CallInfo],
    *,
    skip_index: int | None = None,
    max_messages: int,
    max_chars: int,
    message_limit: int = MAX_MESSAGE_CHARS,
    argument_limit: int = 2_000,
    roles: set[str] | None = None,
) -> list[str]:
    """Render a bounded timeline that keeps the most recent messages.

    Callers get a note in place of dropped older blocks, so a long exchange
    never hides the part nearest the branch point. ``roles`` narrows the
    rendered messages; the general fork uses user/assistant exchanges only.
    """
    allowed = roles or QUOTABLE_ROLES
    blocks: list[str] = []
    for i in range(max(start, 0), min(end, len(messages) - 1) + 1):
        msg = messages[i]
        if msg.get("role") not in allowed:
            continue
        if msg.get("role") == "user" and is_synthetic_user_message(msg):
            continue
        if skip_index is not None and i == skip_index:
            continue
        block = format_message(msg, calls, message_limit=message_limit, argument_limit=argument_limit)
        if not block:
            continue
        blocks.append(block)

    omitted = False
    while blocks and (len(blocks) > max_messages or sum(len(b) for b in blocks) > max_chars):
        blocks.pop(0)
        omitted = True
    if omitted:
        blocks.insert(0, _OMITTED_NOTE)
    return blocks


def recent_exchange_start(messages: list[dict], max_exchanges: int = MAX_FORK_EXCHANGES) -> int:
    """Index where the last ``max_exchanges`` typed user turns begin."""
    user_indices = [
        i for i, msg in enumerate(messages) if msg.get("role") == "user" and not is_synthetic_user_message(msg)
    ]
    if not user_indices:
        return 0
    return user_indices[max(0, len(user_indices) - max_exchanges)]


def build_fork_brief(
    messages: list[dict],
    *,
    source_conv_id: str,
    source_title: str = "",
    instruction: str = "",
) -> str:
    """Render the seeded first user message for a general fork."""
    calls = tool_calls_by_id(messages)
    start = recent_exchange_start(messages)
    timeline_blocks = bounded_timeline(
        messages,
        start,
        len(messages) - 1,
        calls,
        max_messages=MAX_FORK_TIMELINE_MESSAGES,
        max_chars=MAX_FORK_TIMELINE_CHARS,
        roles={"user", "assistant"},
    )

    source_label = f"“{source_title}”" if source_title else source_conv_id
    lines = [
        "# Fork request",
        "",
        f"You are in a fork of the parent conversation {source_label} (`{source_conv_id}`).",
        "This fork starts with a clean context window: use it to explore a",
        "meta-concern, alternative approach, or design idea without derailing",
        "the parent session.",
        "",
    ]

    if timeline_blocks:
        lines += [
            "## Recent context",
            "",
            "\n\n".join(timeline_blocks),
            "",
        ]

    lines += [
        "## Your task",
        "",
        instruction.strip()
        or (
            "Explore the most recent thread in this fork. Decide what to investigate"
            " or develop from the recent context and the conversation so far. Report"
            " what you find; the parent conversation is unchanged."
        ),
        "",
        "## Working notes",
        "",
        "- The parent conversation is unchanged. Do not try to edit it.",
        "- The parent archive remains on disk. If you need more history, use"
        f" `conversation_search` to search it (parent conv id: `{source_conv_id}`).",
        "",
    ]
    return "\n".join(lines)
