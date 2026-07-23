"""Conversation tools — search and compact conversations."""

import json
import logging

import snowballstemmer

from ..conversation_paths import iter_conversation_archives
from ..media import ToolResult
from ..preempt_search import tokenize

log = logging.getLogger(__name__)

# Ranking: a raw-substring hit is a much stronger signal than incidental
# token overlap (it's the caller's exact phrase, mid-word matches included),
# so it dominates the score. Token overlap then orders the rest and breaks
# ties among substring hits.
_SUBSTRING_BOOST = 1000
_MAX_RESULTS = 10

# One shared English stemmer (Porter2). Stemming both query and content
# tokens lets plural/inflected queries match singular text and vice versa
# ("colors" ~ "color", "providers" ~ "provider") — the failure mode in #535.
_STEMMER = snowballstemmer.stemmer("english")


def _stemmed_tokens(text: str) -> set[str]:
    """Tokenize (stopword-filtered, >=3 chars) then stem to word roots."""
    return {_STEMMER.stemWord(t) for t in tokenize(text)}


def tool_conversation_search(ctx, query: str) -> str:
    """Search conversation archives by stemmed-token overlap plus substring."""
    log.info(f"[tool:conversation_search] query={query}")

    archives = sorted(
        iter_conversation_archives(ctx.config), key=lambda t: t[0], reverse=True)
    if not archives:
        return f"No conversation history found matching '{query}'"

    query_lower = query.lower()
    query_stems = _stemmed_tokens(query)

    # Collect every match with a relevance score, then rank — a message that
    # overlaps more query tokens should outrank one that overlaps fewer.
    scored: list[tuple[int, int, str]] = []
    order = 0  # preserves archive iteration order for deterministic ties

    for conv_id, filepath in archives:
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
                if not content:
                    continue

                score = 0
                if query_lower in content.lower():
                    score += _SUBSTRING_BOOST
                if query_stems:
                    score += len(query_stems & _stemmed_tokens(content))
                if score == 0:
                    continue

                role = msg.get("role", "unknown")
                excerpt = content[:500] + ("..." if len(content) > 500 else "")
                scored.append((score, order, f"--- [{conv_id}] {role} ---\n{excerpt}"))
                order += 1

    if not scored:
        return f"No conversation history found matching '{query}'"

    # Highest score first; ties keep archive order (order asc).
    scored.sort(key=lambda t: (-t[0], t[1]))
    results = [entry for _score, _order, entry in scored[:_MAX_RESULTS]]

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
                "Search the **raw chat history** of past conversations. "
                "Matches by word overlap (stemmed, so plural/inflected words "
                "match: 'providers' finds 'provider') and by substring, with "
                "results ranked by relevance. Use this when the user is asking "
                "for a verbatim reference — the literal exchange — and only when "
                "the answer is not better-suited to the vault. **Resolved "
                "decisions, design choices, and curated knowledge live in the "
                "vault — for those, prefer vault_search even when the user "
                "phrases it as 'what did we decide' or 'we talked about'.** "
                "Searches the full uncompacted JSONL archives. "
                "Useful for: pinning down the exact wording of a prior exchange."
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
