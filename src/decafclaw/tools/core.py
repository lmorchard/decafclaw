"""Core tools — web fetch, debug, think, compaction."""

import httpx
import json
import logging

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

    # Summarize each message
    lines = [f"Total messages: {len(messages)}\n"]
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")

        # Truncate long content
        preview = content[:200] + "..." if len(content) > 200 else content

        if role == "system":
            lines.append(f"[{i}] system: {preview}")
        elif role == "user":
            lines.append(f"[{i}] user: {preview}")
        elif role == "assistant":
            if tool_calls:
                names = [tc["function"]["name"] for tc in tool_calls]
                lines.append(f"[{i}] assistant: (tool calls: {', '.join(names)})")
            else:
                lines.append(f"[{i}] assistant: {preview}")
        elif role == "tool":
            lines.append(f"[{i}] tool [{tool_call_id}]: {preview}")
        else:
            lines.append(f"[{i}] {role}: {preview}")

    return "\n".join(lines)


def tool_think(ctx, content: str) -> str:
    """Internal reasoning scratchpad — hidden from the user."""
    log.info(f"[tool:think] {content[:100]}...")
    return "OK"


async def tool_compact_conversation(ctx) -> str:
    """Manually trigger conversation compaction."""
    log.info("[tool:compact_conversation]")
    from ..compaction import compact_history
    history = getattr(ctx, "history", None)
    if history is None:
        return "[error: no conversation history available]"
    result = await compact_history(ctx, history)
    if result:
        return f"Conversation compacted. History now has {len(history)} messages."
    else:
        return "No compaction needed (not enough turns to compact)."


CORE_TOOLS = {
    "web_fetch": tool_web_fetch,
    "debug_context": tool_debug_context,
    "think": tool_think,
    "compact_conversation": tool_compact_conversation,
}

CORE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch raw HTML from a URL via HTTP GET. Use this when you need the original markup or headers. For clean readable content from articles or PDFs, prefer tabstack_extract_markdown instead.",
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
            "description": "Dump the current conversation context for debugging. Shows all messages the LLM can see, including system prompt, user messages, assistant responses, and tool results. Use when asked to inspect or describe your context. IMPORTANT: Always paste the full output of this tool verbatim in your response — do not summarize or paraphrase it.",
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
    {
        "type": "function",
        "function": {
            "name": "compact_conversation",
            "description": "Manually compact the conversation history into a summary. Use when the conversation is getting long or when you want to consolidate context. This triggers the same compaction that happens automatically when the token budget is exceeded.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
