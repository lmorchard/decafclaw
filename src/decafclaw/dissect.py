"""Conversation dissection — extract a failure and build a fork brief (#1570).

A dissection conversation is a top-level fork that starts with the failing
prompt, tool call, and error copied from the parent. It keeps the parent
conversation (the session being protected) free of source-code spelunking,
diffs, and test output.

This module is transport- and storage-neutral: it reads archive-shaped message
dicts and returns plain text. The web layer owns conversation creation, the
turn, and the parent notice so the tool, REST endpoint, and ``/dissect``
command share the same extraction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Truncation budgets. The failure body gets the largest share because exact
# error text (tracebacks, compiler output) is the point of the fork.
MAX_TIMELINE_CHARS = 16_000
MAX_TIMELINE_MESSAGES = 16
MAX_MESSAGE_CHARS = 4_000
MAX_FAILURE_CHARS = 8_000
MAX_ARGUMENT_CHARS = 2_000

# Roles that carry conversation content we can quote. Runtime bookkeeping
# records (model/mode/compaction/notice) are not part of the investigation.
_QUOTABLE_ROLES = {"user", "assistant", "tool"}

_SYNTHETIC_SOURCES = {"widget_response", "reflection_retry", "system_update"}
_SYNTHETIC_PREFIXES = ("[reflection]", "[System Update:")


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
class FailureFocus:
    """The failure (or most recent exchange) copied into a dissection fork."""

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


def _tool_calls_by_id(messages: list[dict]) -> dict[str, _CallInfo]:
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


def _find_user_index(messages: list[dict], before: int) -> int | None:
    """Nearest typed user prompt at or before ``before``."""
    for i in range(min(before, len(messages) - 1), -1, -1):
        msg = messages[i]
        if msg.get("role") == "user" and not is_synthetic_user_message(msg):
            return i
    return None


def _last_assistant_index(messages: list[dict], before: int) -> int | None:
    for i in range(min(before, len(messages) - 1), -1, -1):
        if messages[i].get("role") == "assistant":
            return i
    return None


def _focus_from_tool(messages: list[dict], tool_index: int, calls: dict[str, _CallInfo]) -> FailureFocus:
    msg = messages[tool_index]
    call_id = str(msg.get("tool_call_id") or "")
    call = calls.get(call_id)
    assistant_index = call.index if call else _last_assistant_index(messages, tool_index - 1)
    content = str(msg.get("content") or "")
    error = content if is_tool_error(content) else ""
    return FailureFocus(
        tool_call_id=call_id,
        tool_name=call.name if call else "",
        arguments=call.arguments if call else "",
        error=error,
        timestamp=str(msg.get("timestamp") or ""),
        user_index=_find_user_index(messages, assistant_index if assistant_index is not None else tool_index),
        assistant_index=assistant_index,
        # A named successful result is still useful context; only the failing
        # result is rendered separately under "What failed".
        tool_index=tool_index if error else None,
    )


def find_dissection_focus(messages: list[dict], tool_call_id: str = "") -> FailureFocus | None:
    """Pick the failure to dissect.

    Priority:
    1. the named ``tool_call_id`` when it has a tool result;
    2. the last tool error in the archive;
    3. the most recent typed user exchange, when no tool failed.

    Returns ``None`` only when the archive has no user, assistant, or tool
    content at all.
    """
    if not messages:
        return None
    calls = _tool_calls_by_id(messages)

    if tool_call_id:
        for i, msg in enumerate(messages):
            if msg.get("role") == "tool" and str(msg.get("tool_call_id") or "") == tool_call_id:
                return _focus_from_tool(messages, i, calls)
        # The named call may have been interrupted before its result landed;
        # fall through to the newest error rather than silently dissecting
        # an unrelated exchange.

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "tool" and is_tool_error(msg.get("content")):
            return _focus_from_tool(messages, i, calls)

    # No error: seed with the latest exchange so the user can still fork a
    # promising thread without copying the whole parent.
    last_content = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if messages[i].get("role") in _QUOTABLE_ROLES and str(messages[i].get("content") or "").strip()
        ),
        None,
    )
    if last_content is None:
        return None
    user_index = _find_user_index(messages, last_content)
    assistant_index = _last_assistant_index(messages, last_content)
    return FailureFocus(
        user_index=user_index,
        assistant_index=assistant_index,
        timestamp=str(messages[last_content].get("timestamp") or ""),
    )


def _clip(text: object, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    # Keep both ends: tracebacks lead with the exception and end with the
    # failing frame; long command output often has the useful part in either.
    head = limit * 2 // 3
    tail = limit - head
    return f"{value[:head]}\n… [truncated {len(value) - limit} chars] …\n{value[-tail:]}"


def _format_arguments(raw: str) -> str:
    if not raw:
        return "(not recorded)"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return _clip(raw, MAX_ARGUMENT_CHARS)
    return _clip(json.dumps(parsed, indent=2, sort_keys=True), MAX_ARGUMENT_CHARS)


def _format_tool_calls(msg: dict) -> str:
    lines = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or "tool"
        call_id = tc.get("id") or ""
        args = fn.get("arguments") or tc.get("arguments") or ""
        lines.append(f"→ tool call `{name}` (id: {call_id})\n```json\n{_format_arguments(str(args))}\n```")
    return "\n\n".join(lines)


def _format_message(msg: dict, calls: dict[str, _CallInfo]) -> str:
    role = msg.get("role")
    content = _clip(msg.get("content"), MAX_MESSAGE_CHARS)
    if role == "user":
        return f"### User\n{content}"
    if role == "assistant":
        parts = []
        if content.strip():
            parts.append(content)
        formatted_calls = _format_tool_calls(msg)
        if formatted_calls:
            parts.append(formatted_calls)
        return "### Assistant\n" + ("\n\n".join(parts) if parts else "(no text)")
    if role == "tool":
        call = calls.get(str(msg.get("tool_call_id") or ""))
        label = f"Tool result: {call.name}" if call else "Tool result"
        return f"### {label}\n```\n{content}\n```"
    return ""


def build_dissection_brief(
    messages: list[dict],
    focus: FailureFocus,
    *,
    source_conv_id: str,
    source_title: str = "",
    instruction: str = "",
) -> str:
    """Render the seeded first user message for a dissection conversation."""
    calls = _tool_calls_by_id(messages)
    start = focus.user_index if focus.user_index is not None else 0
    end = focus.tool_index if focus.tool_index is not None else len(messages) - 1

    timeline_blocks: list[str] = []
    chars = 0
    considered = 0
    for i in range(start, end + 1):
        msg = messages[i]
        if msg.get("role") not in _QUOTABLE_ROLES:
            continue
        # The failing result is rendered in full under Failure; keep the
        # timeline readable by not duplicating it there.
        if focus.tool_index is not None and i == focus.tool_index:
            continue
        block = _format_message(msg, calls)
        if not block:
            continue
        if considered >= MAX_TIMELINE_MESSAGES or chars + len(block) > MAX_TIMELINE_CHARS:
            timeline_blocks.append("_(earlier/later messages omitted to keep this brief bounded)_")
            break
        timeline_blocks.append(block)
        chars += len(block)
        considered += 1

    source_label = f"“{source_title}”" if source_title else source_conv_id
    lines = [
        "# Dissection request",
        "",
        "You are working in a fork created to investigate a failure without polluting",
        f"the parent conversation {source_label} (`{source_conv_id}`). Do the debugging",
        "work here: read source, create a git worktree, run tests, and open a PR.",
        "",
    ]

    if focus.has_error:
        lines += [
            "## What failed",
            "",
            f"- Tool: `{focus.tool_name or 'unknown'}`",
            f"- Tool call id: `{focus.tool_call_id or 'unknown'}`",
        ]
        if focus.timestamp:
            lines.append(f"- When: {focus.timestamp}")
        lines += [
            "",
            "### Arguments",
            "```json",
            _format_arguments(focus.arguments),
            "```",
            "",
            "### Exact error",
            "```",
            _clip(focus.error, MAX_FAILURE_CHARS),
            "```",
            "",
        ]
    else:
        lines += [
            "## What to investigate",
            "",
            "No failing tool result was recorded. The most recent exchange is",
            "included below; decide from the user's instruction what needs work.",
            "",
        ]

    if timeline_blocks:
        lines += [
            "## Conversation leading to the failure",
            "",
            "\n\n".join(timeline_blocks),
            "",
        ]

    lines += [
        "## Your task",
        "",
        instruction.strip()
        or (
            "Investigate this failure, identify the root cause, fix it, and verify"
            " the fix with the narrowest relevant test. Report what changed and any"
            " remaining risks. Do not modify the parent conversation."
        ),
        "",
        "## Working notes",
        "",
        "- Before investigating, activate the relevant skills when available: "
        "`project` (this repository's workflow) and `postmortem`.",
        "- The parent conversation archive remains on disk. If you need more"
        f" history, use `conversation_search` to search it (parent conv id:"
        f" `{source_conv_id}`).",
        "- Use an isolated worktree under `.claude/worktrees/` for code changes.",
        "",
    ]
    return "\n".join(lines)
