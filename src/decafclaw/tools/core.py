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

    # Write system prompt as a separate readable file
    system_msg = next((m for m in messages if m.get("role") == "system"), None)
    if system_msg:
        prompt_path = workspace / "debug_system_prompt.md"
        prompt_path.write_text(system_msg.get("content", ""))

    # Also write the summary
    summary_path = workspace / "debug_context_summary.txt"
    summary_path.write_text(summary)

    summary_text = (
        f"{summary}\n\n"
        f"Full context written to workspace/debug_context.json ({dump_path.stat().st_size} bytes)\n"
        f"Summary written to workspace/debug_context_summary.txt"
    )

    # Return media attachments for the JSON and prompt files
    from ..media import ToolResult
    media = [
        {
            "type": "file",
            "filename": "debug_context.json",
            "data": dump_path.read_bytes(),
            "content_type": "application/json",
        },
    ]
    if system_msg:
        media.append({
            "type": "file",
            "filename": "debug_system_prompt.md",
            "data": (system_msg.get("content", "")).encode(),
            "content_type": "text/markdown",
        })

    return ToolResult(text=summary_text, media=media)


def tool_think(ctx, content: str) -> str:
    """Internal reasoning scratchpad — hidden from the user."""
    log.info(f"[tool:think] {content[:100]}...")
    return "OK"


def tool_current_time(ctx) -> str:
    """Return the current date and time."""
    from datetime import datetime
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S (%A)")


def tool_context_stats(ctx) -> str:
    """Report token budget statistics for the current conversation."""
    log.info("[tool:context_stats]")

    messages = getattr(ctx, "messages", [])
    config = ctx.config

    def _estimate_tokens(text):
        """Rough token estimate: ~4 chars per token for English."""
        return len(text) // 4 if text else 0

    # System prompt
    system_msg = next((m for m in messages if m.get("role") == "system"), None)
    system_chars = len(system_msg.get("content", "")) if system_msg else 0
    system_tokens = _estimate_tokens(system_msg.get("content", "") if system_msg else "")

    # Tool definitions
    from . import TOOL_DEFINITIONS
    extra_tool_defs = getattr(ctx, "extra_tool_definitions", [])
    from ..mcp_client import get_registry
    mcp_registry = get_registry()
    mcp_tool_defs = mcp_registry.get_tool_definitions() if mcp_registry else []
    all_tool_defs = TOOL_DEFINITIONS + extra_tool_defs + mcp_tool_defs
    tools_json = json.dumps(all_tool_defs)
    tools_tokens = _estimate_tokens(tools_json)

    # Messages by role
    role_counts = {}
    role_chars = {}
    for msg in messages:
        role = msg.get("role", "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        content = msg.get("content") or ""
        role_chars[role] = role_chars.get(role, 0) + len(content)
        # Include tool call JSON in assistant message size
        if msg.get("tool_calls"):
            role_chars[role] += len(json.dumps(msg["tool_calls"]))

    # History (everything except system prompt)
    history_chars = sum(role_chars.get(r, 0) for r in role_chars if r != "system")
    history_tokens = _estimate_tokens("x" * history_chars)

    # Totals
    total_estimated = system_tokens + tools_tokens + history_tokens
    prompt_tokens_actual = getattr(ctx, "total_prompt_tokens", 0)
    completion_tokens_actual = getattr(ctx, "total_completion_tokens", 0)
    compaction_max = config.compaction_max_tokens

    # Archive size
    from ..archive import archive_path
    conv_id = getattr(ctx, "conv_id", "unknown")
    archive_file = archive_path(config, conv_id)
    archive_size = archive_file.stat().st_size if archive_file.exists() else 0

    lines = [
        "## Context Stats\n",
        f"**Compaction budget:** {compaction_max:,} tokens",
        f"**Last prompt tokens (actual):** {prompt_tokens_actual:,}",
        f"**Total completion tokens:** {completion_tokens_actual:,}",
        "",
        "### Estimated breakdown (approx)",
        f"| Component | Chars | ~Tokens | % of budget |",
        f"|-----------|-------|---------|-------------|",
        f"| System prompt | {system_chars:,} | ~{system_tokens:,} | {system_tokens*100//compaction_max if compaction_max else 0}% |",
        f"| Tool definitions ({len(all_tool_defs)}) | {len(tools_json):,} | ~{tools_tokens:,} | {tools_tokens*100//compaction_max if compaction_max else 0}% |",
        f"| Conversation history | {history_chars:,} | ~{history_tokens:,} | {history_tokens*100//compaction_max if compaction_max else 0}% |",
        f"| **Total estimated** | | **~{total_estimated:,}** | **{total_estimated*100//compaction_max if compaction_max else 0}%** |",
        "",
        "### Messages by role",
    ]

    for role in ["system", "user", "assistant", "tool"]:
        count = role_counts.get(role, 0)
        chars = role_chars.get(role, 0)
        if count:
            lines.append(f"- **{role}**: {count} message(s), {chars:,} chars")

    lines.append(f"\n### Archive")
    lines.append(f"- **Conversation ID:** {conv_id}")
    lines.append(f"- **Archive file size:** {archive_size:,} bytes")

    # Activated skills
    activated = getattr(ctx, "activated_skills", set())
    if activated:
        lines.append(f"\n### Active skills: {', '.join(activated)}")

    return "\n".join(lines)


CORE_TOOLS = {
    "web_fetch": tool_web_fetch,
    "debug_context": tool_debug_context,
    "context_stats": tool_context_stats,
    "think": tool_think,
    "current_time": tool_current_time,
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
            "name": "context_stats",
            "description": "Show token budget statistics for the current conversation. Reports estimated breakdown of system prompt, tool definitions, and history versus the compaction budget. Use when asked about context size, token usage, or why the agent might be forgetting things.",
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
            "name": "current_time",
            "description": "Get the current date and time. Use this instead of shell commands like 'date'.",
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
