"""Proactive memory retrieval — automatically surface relevant context per turn."""

import logging
import re

from .embeddings import embed_text, search_similar_sync
from .util import estimate_tokens

log = logging.getLogger(__name__)

# Matches [[PageName]] and [[PageName|display text]] in vault page content
_WIKI_LINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')

# Source type labels for display
SOURCE_LABELS = {
    "page": "Agent page",
    "user": "User page",
    "journal": "Journal",
    "graph_expansion": "Linked page",
    # Legacy compat
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

        # Exclude legacy conversation embeddings — they add noise (see #133)
        results = [r for r in results if r.get("source_type") != "conversation"]

        # Filter by similarity threshold
        results = [r for r in results if r["similarity"] >= mc.similarity_threshold]

        # Trim to max_results
        results = results[:mc.max_results]

        # Enrich with file metadata for relevance scoring
        results = _enrich_results(config, results)

        # Expand via wiki-link graph traversal (one hop)
        relevance = getattr(config, "relevance", None)
        if relevance and relevance.graph_expansion_enabled:
            results = _expand_graph_links(
                config, results,
                similarity_discount=relevance.graph_expansion_similarity_discount,
            )

        # Token budget trimming is handled by the composer's dynamic budget
        # allocation (_compose_memory_context). We return all candidates here
        # so the composer can score and select with the full picture.
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


def _enrich_results(config, results: list[dict]) -> list[dict]:
    """Enrich retrieval results with file metadata for relevance scoring.

    Adds modified_at (ISO timestamp) and importance (float) to each result.
    Called after threshold filtering on the small candidate set.
    Fail-open: errors on individual results log a warning and use defaults.
    """
    import os
    from datetime import datetime

    from .frontmatter import get_frontmatter_field, parse_frontmatter

    vault_root = config.vault_root
    for result in results:
        try:
            file_path = result.get("file_path", "")
            source_type = result.get("source_type", "")

            # Default values
            result.setdefault("importance", 0.5)
            result.setdefault("modified_at", result.get("created_at", ""))

            # Try to get file modification time and frontmatter
            if file_path:
                full_path = vault_root / file_path
                if full_path.exists():
                    mtime = os.path.getmtime(full_path)
                    result["modified_at"] = datetime.fromtimestamp(mtime).isoformat()

                    # Parse frontmatter for importance (pages/user only, not journal)
                    if source_type in ("page", "user"):
                        text = full_path.read_text()
                        metadata, _ = parse_frontmatter(text)
                        if metadata:
                            result["importance"] = get_frontmatter_field(
                                metadata, "importance", 0.5,
                            )
        except Exception:
            log.warning("Failed to enrich result %s", result.get("file_path", "?"),
                        exc_info=True)

    return results


def _expand_graph_links(
    config, results: list[dict], similarity_discount: float = 0.7,
) -> list[dict]:
    """Expand retrieval results by following [[wiki-links]] one hop.

    For each result, parse wiki-links from the page content. Linked pages
    are added to the candidate pool with discounted similarity and marked
    as graph_expansion source type. Deduplicates by file_path.
    """
    import os
    from datetime import datetime

    from .frontmatter import build_composite_text, get_frontmatter_field, parse_frontmatter

    vault_root = config.vault_root.resolve()
    seen_paths = {r.get("file_path") for r in results}
    expanded: list[dict] = []

    for result in results:
        file_path = result.get("file_path", "")
        if not file_path:
            continue
        full_path = vault_root / file_path
        if not full_path.exists():
            continue

        try:
            text = full_path.read_text()
        except (OSError, UnicodeError):
            continue

        # Parse wiki-links from page content
        linked_pages = _WIKI_LINK_RE.findall(text)
        if not linked_pages:
            continue

        log.debug("Graph expansion: %s has %d wiki-links", file_path, len(linked_pages))

        parent_similarity = result.get("similarity", 0.5)
        parent_page = file_path
        added_count = 0

        for page_name in linked_pages:
            page_name = page_name.strip()
            if not page_name:
                continue

            # Resolve the linked page against vault root
            from .skills.vault.tools import resolve_page
            linked_path = resolve_page(config, page_name, from_page=file_path)
            if linked_path is None or not linked_path.exists():
                log.debug("Graph expansion: link [[%s]] from %s — not found", page_name, file_path)
                continue

            try:
                rel_path = str(linked_path.relative_to(vault_root))
            except ValueError:
                log.debug("Graph expansion: link [[%s]] — outside vault root", page_name)
                continue

            if rel_path in seen_paths:
                log.debug("Graph expansion: link [[%s]] → %s — already in candidates", page_name, rel_path)
                continue
            seen_paths.add(rel_path)

            # Read linked page for metadata
            linked_text = ""
            try:
                linked_text = linked_path.read_text()
                metadata, body = parse_frontmatter(linked_text)
                importance = get_frontmatter_field(metadata, "importance", 0.5)
                mtime = os.path.getmtime(linked_path)
                modified_at = datetime.fromtimestamp(mtime).isoformat()
                entry_text = build_composite_text(metadata, body)
            except Exception:
                importance = 0.5
                modified_at = ""
                entry_text = linked_text

            expanded.append({
                "entry_text": entry_text,
                "file_path": rel_path,
                "similarity": parent_similarity * similarity_discount,
                "source_type": "graph_expansion",
                "linked_from": parent_page,
                "importance": importance,
                "modified_at": modified_at,
            })
            added_count += 1

        if added_count:
            log.debug("Graph expansion from '%s': added %d new candidates", file_path, added_count)

    if expanded:
        log.info("Graph expansion: %d candidates added from %d source pages",
                 len(expanded), sum(1 for r in results if r.get("file_path")))
    return results + expanded


def format_memory_context(results: list[dict]) -> str:
    """Format retrieval results into a context message for injection."""
    parts = ["[Automatically retrieved context — not from the user]\n"]
    for r in results:
        label = SOURCE_LABELS.get(r["source_type"], r["source_type"])
        # Show composite score if available (from relevance scoring), else raw similarity
        score = r.get("composite_score", r.get("similarity", 0))
        parts.append(f"--- {label} (score: {score:.2f}) ---\n{r['entry_text']}")
    return "\n\n".join(parts)
