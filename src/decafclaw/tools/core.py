"""Core tools — web fetch, debug, think, compaction."""

import httpx
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def tool_web_fetch(ctx, url: str) -> str:
    """Fetch a URL and return the raw response body as text."""
    log.info(f"[tool:web_fetch] {url}")
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text
        if len(text) > 50000:
            text = text[:50000] + "\n\n[truncated at 50000 chars]"
        return text
    except httpx.HTTPError as e:
        return f"[error: {e}]"


def tool_debug_context(ctx) -> str:
    """Dump the current conversation context for debugging."""
    log.info("[tool:debug_context]")
    messages = getattr(ctx, "messages", None)
    if messages is None:
        return "[no context available]"

    # Build full dump with message details
    lines = [f"Total messages: {len(messages)}\n"]
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")

        if role == "system":
            lines.append(f"[{i}] system: ({len(content)} chars)")
        elif role == "user":
            lines.append(f"[{i}] user: {content[:200]}{'...' if len(content) > 200 else ''}")
        elif role == "assistant":
            if tool_calls:
                names = [tc["function"]["name"] for tc in tool_calls]
                lines.append(f"[{i}] assistant: (tool calls: {', '.join(names)})")
            else:
                lines.append(f"[{i}] assistant: {content[:200]}{'...' if len(content) > 200 else ''}")
        elif role == "tool":
            lines.append(f"[{i}] tool [{tool_call_id}]: ({len(content)} chars)")
        else:
            lines.append(f"[{i}] {role}: {content[:200]}{'...' if len(content) > 200 else ''}")

    # Build the full LLM context: messages + tool definitions
    from . import TOOL_DEFINITIONS
    extra_tool_defs = getattr(ctx, "extra_tool_definitions", [])
    all_tool_defs = TOOL_DEFINITIONS + extra_tool_defs

    tool_names = [t["function"]["name"] for t in all_tool_defs]
    lines.append(f"\nTool definitions ({len(all_tool_defs)}): {', '.join(tool_names)}")
    summary = "\n".join(lines)

    full_context = {
        "messages": messages,
        "tools": all_tool_defs,
    }

    # Write full context (no truncation) to workspace file
    workspace = ctx.config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    dump_path = workspace / "debug_context.json"
    dump_path.write_text(json.dumps(full_context, indent=2, default=str))

    # Also write the summary
    summary_path = workspace / "debug_context_summary.txt"
    summary_path.write_text(summary)

    return (
        f"{summary}\n\n"
        f"Full context written to workspace/debug_context.json ({dump_path.stat().st_size} bytes)\n"
        f"Summary written to workspace/debug_context_summary.txt"
    )


def tool_think(ctx, content: str) -> str:
    """Internal reasoning scratchpad — hidden from the user."""
    log.info(f"[tool:think] {content[:100]}...")
    return "OK"


CORE_TOOLS = {
    "web_fetch": tool_web_fetch,
    "debug_context": tool_debug_context,
    "think": tool_think,
}

CORE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch raw HTML from a URL via HTTP GET. Use this when you need the original markup or headers. If the tabstack skill is activated, prefer tabstack_extract_markdown for clean readable content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "debug_context",
            "description": "Dump the current conversation context for debugging. Writes full context as JSON to workspace/debug_context.json and a summary to workspace/debug_context_summary.txt. Returns a brief summary in the response. Use when asked to inspect or describe your context.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Use this tool for internal reasoning and planning that should NOT be "
                "shown to the user. Think through your approach before acting: plan "
                "multi-step work, evaluate options, reason about what tools to use, or "
                "work through logic. The content is logged for debugging but hidden from "
                "the conversation. Use this INSTEAD of narrating your process in the chat "
                "(e.g., instead of saying 'Let me search for that...', use think to plan, "
                "then just do it)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Your internal reasoning or planning",
                    },
                },
                "required": ["content"],
            },
        },
    },
]
