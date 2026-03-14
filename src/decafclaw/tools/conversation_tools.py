"""Conversation search tools — search across archived conversations."""

import logging
from .. import embeddings
from ..archive import read_archive

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


CONVERSATION_TOOLS = {
    "conversation_search": tool_conversation_search,
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
]
