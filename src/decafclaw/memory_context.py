"""Proactive memory retrieval — automatically surface relevant context per turn."""

import logging

from .embeddings import embed_text, search_similar_sync
from .util import estimate_tokens

log = logging.getLogger(__name__)

# Source type labels for display
SOURCE_LABELS = {
    "wiki": "Wiki",
    "memory": "Memory",
    "conversation": "Conversation",
}


async def retrieve_memory_context(config, user_message: str) -> list[dict]:
    """Retrieve relevant memory/wiki/conversation entries for a user message.

    Returns a list of result dicts with entry_text, source_type, similarity.
    Fail-open: any error logs a warning and returns empty list.
    """
    try:
        mc = config.memory_context
        if not mc.enabled:
            return []
        if not config.embedding.model:
            return []

        # Embed the query — skip reindexing (hot path, avoid latency)
        query_embedding = await embed_text(config, user_message)
        if not query_embedding:
            return []

        # Search all source types, fetch extra to allow for threshold filtering.
        # Over-fetch to allow for deduplication and token budget filtering.
        results = search_similar_sync(
            config, query_embedding, top_k=mc.max_results * 2
        )

        # Exclude conversation entries — they add noise (see #133)
        results = [r for r in results if r["source_type"] != "conversation"]

        # Filter by similarity threshold
        results = [r for r in results if r["similarity"] >= mc.similarity_threshold]

        # Trim to max_results
        results = results[:mc.max_results]

        # Trim to token budget
        results = _trim_to_token_budget(results, mc.max_tokens)

        return results

    except Exception:
        log.warning("Memory context retrieval failed", exc_info=True)
        return []


def _trim_to_token_budget(results: list[dict], max_tokens: int) -> list[dict]:
    """Trim results to fit within a token budget (len // 4 estimate)."""
    trimmed = []
    total_tokens = 0
    for r in results:
        entry_tokens = estimate_tokens(r["entry_text"])
        if total_tokens + entry_tokens > max_tokens and trimmed:
            break
        trimmed.append(r)
        total_tokens += entry_tokens
    return trimmed


def format_memory_context(results: list[dict]) -> str:
    """Format retrieval results into a context message for injection."""
    parts = ["[Automatically retrieved context — not from the user]\n"]
    for r in results:
        label = SOURCE_LABELS.get(r["source_type"], r["source_type"])
        sim = f"{r['similarity']:.2f}"
        parts.append(f"--- {label} (relevance: {sim}) ---\n{r['entry_text']}")
    return "\n\n".join(parts)
