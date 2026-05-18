"""Render conversation archives to human-readable markdown.

Pure transformation: ``render_markdown(messages, conv_id)`` takes the raw
archive list-of-dicts (as produced by :func:`decafclaw.archive.read_archive`)
and emits a markdown string suitable for pasting into a vault, share, or PR.

We export only conversation-flow roles (``user``, ``assistant``, ``tool``,
``background_event``); metadata-only roles (``system``, ``model``,
``reflection``, ``confirmation_*``, ``cancel_marker``, ``wake_trigger``) are
skipped. Auto-injected composer roles (``vault_retrieval``,
``vault_references``, ``conversation_notes``) never reach the archive in the
first place.

The markdown is opinionated and not configurable in v1 — see issue #519.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

# Roles we render. Everything else is silently skipped.
EXPORTED_ROLES = frozenset({"user", "assistant", "tool", "background_event"})

# Truncate any single fenced body over this many bytes. Single mega tool
# results otherwise dominate exports.
MAX_FENCED_BYTES = 16 * 1024


def render_markdown(messages: list[dict], conv_id: str) -> str:
    """Render an archive message list as a markdown transcript."""
    parts: list[str] = [
        f"# Conversation {conv_id}",
        "",
        f"Exported {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]

    # tool_call_id → function name, populated as we walk assistant turns
    tool_names: dict[str, str] = {}

    for msg in messages:
        role = msg.get("role")
        if role not in EXPORTED_ROLES:
            continue
        if role == "user":
            parts.extend(_render_user(msg))
        elif role == "assistant":
            parts.extend(_render_assistant(msg, tool_names))
        elif role == "tool":
            parts.extend(_render_tool(msg, tool_names))
        elif role == "background_event":
            parts.extend(_render_background_event(msg))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _render_user(msg: dict) -> list[str]:
    out = ["## User", "", _content_text(msg)]
    out.extend(_attachment_refs(msg))
    return out


def _render_assistant(msg: dict, tool_names: dict[str, str]) -> list[str]:
    out = ["## Assistant", ""]
    text = _content_text(msg)
    if text:
        out.extend([text, ""])

    widget = msg.get("widget")
    if isinstance(widget, dict):
        out.extend(_render_widget(widget))

    out.extend(_attachment_refs(msg))

    tool_calls = msg.get("tool_calls") or []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name")
        if not name:
            # Skip thinking-placeholder or otherwise nameless entries.
            continue
        call_id = tc.get("id") or ""
        if call_id:
            tool_names[call_id] = name
        out.append(f"### Tool call: {name}")
        out.append("")
        args_text = _format_args(fn.get("arguments"))
        out.extend(_fence(args_text, lang="json"))
        out.append("")
    return out


def _render_tool(msg: dict, tool_names: dict[str, str]) -> list[str]:
    call_id = msg.get("tool_call_id") or ""
    name = tool_names.get(call_id) or msg.get("tool") or "(unknown)"
    out = [f"### Tool result: {name}", ""]
    body = _content_text(msg)
    if not body:
        data = msg.get("data")
        if data is not None:
            try:
                body = json.dumps(data, indent=2)
            except (TypeError, ValueError):
                body = str(data)
    out.extend(_fence(_truncate(body)))
    return out


def _render_background_event(msg: dict) -> list[str]:
    kind = msg.get("kind") or msg.get("event_type") or "wake"
    text = _content_text(msg)
    summary = text.splitlines()[0] if text else kind
    if not summary:
        summary = kind
    return [f"> [background event] {summary}"]


def _render_widget(widget: dict) -> list[str]:
    if widget.get("widget_type") != "code_block":
        return []
    data = widget.get("data") or {}
    code = data.get("code") or data.get("content") or ""
    if not code:
        return []
    lang = data.get("language") or ""
    return [*_fence(_truncate(str(code)), lang=lang), ""]


def _attachment_refs(msg: dict) -> list[str]:
    attachments = msg.get("attachments") or []
    refs: list[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        ref = att.get("path") or att.get("url") or att.get("file_id") or att.get("id")
        if ref:
            refs.append(f"![]({_escape_link_destination(str(ref))})")
    if refs:
        refs.append("")
    return refs


def _escape_link_destination(dest: str) -> str:
    """Render ``dest`` as a CommonMark link destination, safe for inclusion
    in ``![](...)``.

    Attachment paths and ids are user-controlled and may contain spaces,
    parentheses, or angle brackets that would otherwise terminate the
    link destination prematurely or be interpreted as markup. We wrap
    anything containing whitespace or special punctuation in angle
    brackets (the CommonMark ``<...>`` form), escaping ``<``, ``>``, and
    ``\\`` inside. Simple paths pass through untouched so common cases
    stay readable.
    """
    if dest and not re.search(r"[\s()<>]", dest):
        return dest
    escaped = dest.replace("\\", "\\\\").replace("<", "\\<").replace(">", "\\>")
    return f"<{escaped}>"


def _content_text(msg: dict) -> str:
    content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip("\n")
    # Some providers represent content as a list of parts; serialize them.
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if text:
                    chunks.append(str(text))
            elif isinstance(part, str):
                chunks.append(part)
        return "\n".join(chunks).strip("\n")
    return str(content)


def _format_args(args) -> str:
    if args is None:
        return ""
    if isinstance(args, str):
        # OpenAI ships arguments as a JSON-encoded string; pretty-print if
        # parseable, else leave as-is.
        try:
            return json.dumps(json.loads(args), indent=2)
        except (TypeError, ValueError, json.JSONDecodeError):
            return args
    try:
        return json.dumps(args, indent=2)
    except (TypeError, ValueError):
        return str(args)


def _fence(body: str, lang: str = "") -> list[str]:
    """Wrap ``body`` in a fenced code block, escaping triple-backtick runs.

    The fence is one backtick longer than the longest run of backticks in
    ``body`` (minimum three).
    """
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{lang}", body, fence]


def _truncate(body: str) -> str:
    encoded = body.encode("utf-8")
    if len(encoded) <= MAX_FENCED_BYTES:
        return body
    head = encoded[:MAX_FENCED_BYTES].decode("utf-8", errors="ignore")
    return f"{head}\n... [truncated, original was {len(encoded)} bytes]"
