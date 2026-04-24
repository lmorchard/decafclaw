"""Conversation tools — search and compact conversations."""

import json
import logging

from ..media import ToolResult

log = logging.getLogger(__name__)


def tool_conversation_search(ctx, query: str) -> str:
    """Search across conversation archives using substring matching."""
    log.info(f"[tool:conversation_search] query={query}")

    conv_dir = ctx.config.workspace_path / "conversations"
    if not conv_dir.exists():
        return f"No conversation history found matching '{query}'"

    query_lower = query.lower()
    results: list[str] = []
    max_results = 10

    for filepath in sorted(conv_dir.glob("*.jsonl"), reverse=True):
        conv_id = filepath.stem
        with filepath.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("role") not in ("user", "assistant"):
                    continue
                content = msg.get("content", "")
                if not content or query_lower not in content.lower():
                    continue
                role = msg.get("role", "unknown")
                excerpt = content[:500] + ("..." if len(content) > 500 else "")
                results.append(f"--- [{conv_id}] {role} ---\n{excerpt}")
                if len(results) >= max_results:
                    break
        if len(results) >= max_results:
            break

    if not results:
        return f"No conversation history found matching '{query}'"

    return f"Found {len(results)} matching conversation entries:\n\n" + "\n\n".join(results)


async def tool_conversation_compact(ctx) -> str | ToolResult:
    """Manually trigger conversation compaction."""
    log.info("[tool:conversation_compact]")
    from ..compaction import compact_history
    history = ctx.history
    if history is None:
        return ToolResult(text="[error: no conversation history available]")
    result = await compact_history(ctx, history)
    if result:
        return f"Conversation compacted. History now has {len(history)} messages."
    else:
        return "No compaction needed (not enough turns to compact)."


CONVERSATION_TOOLS = {
    "conversation_search": tool_conversation_search,
    "conversation_compact": tool_conversation_compact,
}

CONVERSATION_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "conversation_search",
            "description": (
                "Search across past conversation history using substring matching. "
                "Use this to find things discussed in previous conversations. "
                "Searches the full uncompacted JSONL archives. "
                "Useful for: 'when did we discuss X?', 'what did I say about Y?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in past conversations",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        # Triggers an LLM summarization call bounded by its own model timeout
        # (300s default), which can exceed the default tool wrapper.
        "timeout": None,
        "function": {
            "name": "conversation_compact",
            "description": "Manually compact the conversation history into a summary. Use when the conversation is getting long or when you want to consolidate context. This triggers the same compaction that happens automatically when the token budget is exceeded.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
