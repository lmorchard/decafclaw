"""Memory tools — save, search, and recall memories."""

import logging
from .. import memory

log = logging.getLogger(__name__)


async def tool_memory_save(ctx, tags: list[str], content: str) -> str:
    """Save a memory."""
    log.info(f"[tool:memory_save] tags={tags}")
    result = memory.save_entry(
        config=ctx.config,
        channel_name=getattr(ctx, "channel_name", ""),
        channel_id=getattr(ctx, "channel_id", ""),
        thread_id=getattr(ctx, "thread_id", ""),
        tags=tags,
        content=content,
    )
    # Index for semantic search if enabled
    if ctx.config.memory_search_strategy == "semantic":
        try:
            from .. import embeddings
            from datetime import datetime
            tag_str = ", ".join(tags)
            channel_name = getattr(ctx, "channel_name", "")
            channel_id = getattr(ctx, "channel_id", "")
            thread_id = getattr(ctx, "thread_id", "")
            # Format like the actual markdown entry for consistent embeddings
            entry_text = f"## {datetime.now():%Y-%m-%d %H:%M}\n\n"
            if channel_name or channel_id:
                entry_text += f"- **channel:** {channel_name} ({channel_id})\n"
            if thread_id:
                entry_text += f"- **thread:** {thread_id}\n"
            entry_text += f"- **tags:** {tag_str}\n\n{content}"
            await embeddings.index_entry(ctx.config, "live", entry_text)
        except Exception as e:
            log.error(f"Failed to index memory for semantic search: {e}")
    return result


async def tool_memory_search(ctx, query: str) -> str:
    """Search memories."""
    log.info(f"[tool:memory_search] query={query} strategy={ctx.config.memory_search_strategy}")

    if ctx.config.memory_search_strategy == "semantic":
        try:
            from .. import embeddings
            # Ensure index exists (reindex on first search if needed)
            results = await embeddings.search_similar(ctx.config, query, top_k=5)
            if results:
                lines = [f"Found {len(results)} matching memories (ranked by relevance):\n"]
                for i, r in enumerate(results):
                    sim = f"{r['similarity']:.2f}"
                    lines.append(f"--- Result {i+1} (relevance: {sim}) ---\n{r['entry_text']}")
                return "\n\n".join(lines)
            # Fall through to substring if semantic returns nothing
            log.info("Semantic search returned no results, falling back to substring")
        except Exception as e:
            log.error(f"Semantic search failed, falling back to substring: {e}")

    return memory.search_entries(
        config=ctx.config,
        query=query,
    )


def tool_memory_recent(ctx, n: int = 5) -> str:
    """Recall recent memories."""
    log.info(f"[tool:memory_recent] n={n}")
    return memory.recent_entries(
        config=ctx.config,
        n=n,
    )


MEMORY_TOOLS = {
    "memory_save": tool_memory_save,
    "memory_search": tool_memory_search,
    "memory_recent": tool_memory_recent,
}

MEMORY_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": (
                "Save a persistent memory. Use this to store any information worth "
                "remembering: user preferences, facts, project details, architectural "
                "decisions, your own operational characteristics, or conversation context. "
                "Memories persist across restarts and are searched via substring match, so "
                "rich tagging at save time is critical for future retrieval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Free-form tags to categorize this memory. Be GENEROUS with tags — "
                            "include the specific term, broader categories, synonyms, and related "
                            "concepts. Example: for a favorite cocktail, use tags like "
                            "['preference', 'cocktail', 'drink', 'beverage', 'alcohol', 'favorite']. "
                            "More tags = more searchable. Tags are the primary way memories are "
                            "found later, so think about what words someone might search for."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "The memory content to save",
                    },
                },
                "required": ["tags", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "Search your persistent memories. ALWAYS try this BEFORE web search or "
                "other external tools — your memories may already have the answer. Use "
                "this for personal details, preferences, project context, facts from "
                "prior conversations, or any stored knowledge.\n\n"
                "The search understands meaning — use natural language queries. "
                "'What drinks do I like?' will find memories about cocktails.\n\n"
                "**IMPORTANT:** When this tool returns results, you MUST use them — "
                "either in your thinking or presented to the user. Do NOT ignore search "
                "results or say you have no information when results were returned. The "
                "first result is the most relevant. If no results after a few queries, "
                "then say so.\n\n"
                "If initial search returns nothing, try rephrasing: synonyms, related "
                "terms, broader categories. Try at least 3 variations before giving up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for (case-insensitive substring match)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_recent",
            "description": "Recall your most recent memories. Use at the start of a conversation to refresh your context about what you know and what's been discussed before.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Number of recent memories to return (default 5)",
                    },
                },
                "required": [],
            },
        },
    },
]
