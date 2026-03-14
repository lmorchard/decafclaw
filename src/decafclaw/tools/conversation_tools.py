"""Conversation tools — search and compact conversations."""

import logging
from .. import embeddings

log = logging.getLogger(__name__)


async def tool_conversation_search(ctx, query: str) -> str:
    """Search across conversation archives."""
    log.info(f"[tool:conversation_search] query={query}")

    results = await embeddings.search_similar(
        ctx.config, query, top_k=5, source_type="conversation"
    )

    if results:
        lines = [f"Found {len(results)} matching conversation entries:\n"]
        for i, r in enumerate(results):
            sim = f"{r['similarity']:.2f}"
            lines.append(f"--- Result {i+1} (relevance: {sim}, conv: {r['file_path']}) ---\n{r['entry_text']}")
        return "\n\n".join(lines)

    return f"No conversation history found matching '{query}'"


async def tool_conversation_compact(ctx) -> str:
    """Manually trigger conversation compaction."""
    log.info("[tool:conversation_compact]")
    from ..compaction import compact_history
    history = getattr(ctx, "history", None)
    if history is None:
        return "[error: no conversation history available]"
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
        "function": {
            "name": "conversation_search",
            "description": (
                "Search across past conversation history. Use this to find things "
                "discussed in previous conversations that may not be in your current "
                "context. This searches the full uncompacted conversation archives. "
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
