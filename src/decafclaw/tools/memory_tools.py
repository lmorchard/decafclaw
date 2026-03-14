"""Memory tools — save, search, and recall memories."""

import logging
from .. import memory

log = logging.getLogger(__name__)


def tool_memory_save(ctx, tags: list[str], content: str) -> str:
    """Save a memory."""
    log.info(f"[tool:memory_save] tags={tags}")
    return memory.save_entry(
        config=ctx.config,
        channel_name=getattr(ctx, "channel_name", ""),
        channel_id=getattr(ctx, "channel_id", ""),
        thread_id=getattr(ctx, "thread_id", ""),
        tags=tags,
        content=content,
    )


def tool_memory_search(ctx, query: str) -> str:
    """Search memories."""
    log.info(f"[tool:memory_search] query={query}")
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
                "Search your persistent memories. Use this when you need information "
                "from a prior conversation, a project detail, or a fact you don't have "
                "in your immediate context. Returns whole matching entries.\n\n"
                "**IMPORTANT — SUBSTRING MATCH ONLY. Follow this checklist on every search:**\n"
                "This tool does NOT understand meaning — it matches exact substrings. "
                "You MUST work through this checklist in order:\n"
                "1. Rephrase idioms into concrete terms before searching "
                "(e.g., 'what do I do for a living' -> search for 'job', 'work', 'career').\n"
                "2. Try the most specific concrete term.\n"
                "3. No results? Try the SINGULAR form (cocktails -> cocktail) or PLURAL form.\n"
                "4. Still nothing? Try the ROOT WORD (e.g., 'drinking' -> 'drink').\n"
                "5. Still nothing? Try SYNONYMS (e.g., 'drinks' -> 'beverages', 'tool' -> 'capability').\n"
                "6. Still nothing? Try BROADER categories (e.g., 'cocktail' -> 'drink', 'food').\n"
                "7. Still nothing? Try likely TAGS (memories are saved with tags like "
                "'preference', 'fact', 'project', 'agent', 'architecture' — search for the tag word itself).\n"
                "Do NOT stop after one or two failed searches. Work the full checklist."
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
