"""Session search shared by the agent tool and the web UI (#1630, #1451).

Searches only the user's indexed conversations, never the system/heartbeat
archive sweep that ``conversation_search`` performs. Blocking JSONL reads stay
in this module so callers run it through ``asyncio.to_thread``.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from ..archive import iter_archive_messages
from ..preempt_search import tokenize
from .conversations import ConversationIndex, ConversationMeta

# Title matches stay authoritative, the user's own phrasing outranks
# assistant text, and assistant matches still register so a detail only the
# reply mentions is findable. Substring hits beat token overlap.
_TITLE_SUBSTRING = 10
_TITLE_TOKEN = 3
_USER_SUBSTRING = 8
_USER_TOKEN = 2
_ASSISTANT_SUBSTRING = 4
_ASSISTANT_TOKEN = 1
# Recency is a bounded multiplier, not an additive score: it reorders sessions
# with similar evidence without letting a fresh weak match outrank a strong one.
_RECENCY_BOOST = 0.2
_RECENCY_WINDOW_DAYS = 30
_SNIPPET_RADIUS = 70
_SNIPPET_MAX = 180
_WHITESPACE_RE = re.compile(r"\s+")

MAX_LIMIT = 50


@dataclass(frozen=True)
class SessionSearchHit:
    """One matching conversation with its best excerpt."""

    meta: ConversationMeta
    score: float
    matched_role: str | None
    snippet: str | None


def _text_score(
    text_lower: str,
    text_tokens: set[str],
    query_lower: str,
    query_tokens: set[str],
    substring_weight: int,
    token_weight: int,
) -> int:
    score = 0
    if query_lower in text_lower:
        score += substring_weight
    score += len(query_tokens & text_tokens) * token_weight
    return score


def _recency_weight(updated_at: str, now: datetime) -> float:
    """Multiplier from 1.0 to 1.2 for sessions updated within the window."""
    try:
        updated = datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        return 1.0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - updated).total_seconds() / 86400)
    if age_days >= _RECENCY_WINDOW_DAYS:
        return 1.0
    return 1.0 + _RECENCY_BOOST * (1.0 - age_days / _RECENCY_WINDOW_DAYS)


def _matched_snippet(content: str, query_lower: str, query_tokens: set[str]) -> str | None:
    """Extract a short excerpt around the first substring or token hit."""
    lowered = content.lower()
    index = lowered.find(query_lower) if query_lower else -1
    match_len = len(query_lower)
    if index < 0:
        for token in sorted(query_tokens):
            index = lowered.find(token)
            if index >= 0:
                match_len = len(token)
                break
    if index < 0:
        return None

    start = max(0, index - _SNIPPET_RADIUS)
    end = min(len(content), index + match_len + _SNIPPET_RADIUS)
    snippet = _WHITESPACE_RE.sub(" ", content[start:end]).strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet += "…"
    if len(snippet) > _SNIPPET_MAX:
        snippet = snippet[:_SNIPPET_MAX].rstrip() + "…"
    return snippet


def search_sessions(
    config,
    username: str,
    query: str,
    *,
    limit: int = 5,
    include_archived: bool = False,
) -> list[SessionSearchHit]:
    """Rank the user's sessions against a query; blocking, so use a thread.

    Returns at most ``limit`` hits, best first. An empty or whitespace-only
    query returns no hits.
    """
    if not query or not query.strip():
        return []

    index = ConversationIndex(config)
    convs = index.list_for_user(username, include_archived=include_archived)
    if not convs:
        return []

    query_lower = query.lower().strip()
    query_tokens = set(tokenize(query_lower))
    now = datetime.now(timezone.utc)

    scored = []
    for c in convs:
        title_lower = c.title.lower()
        title_score = _text_score(
            title_lower,
            set(tokenize(title_lower)),
            query_lower,
            query_tokens,
            _TITLE_SUBSTRING,
            _TITLE_TOKEN,
        )

        # Only user and assistant text counts: tool output and metadata dumps
        # would match incidental tokens without reflecting what was discussed.
        best_user: tuple[int, str] | None = None
        best_assistant: tuple[int, str] | None = None
        for msg in iter_archive_messages(config, c.conv_id):
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content:
                continue
            content_lower = content.lower()
            if role == "user":
                score = _text_score(
                    content_lower,
                    set(tokenize(content_lower)),
                    query_lower,
                    query_tokens,
                    _USER_SUBSTRING,
                    _USER_TOKEN,
                )
                if score > 0 and (best_user is None or score > best_user[0]):
                    best_user = (score, content)
            else:
                score = _text_score(
                    content_lower,
                    set(tokenize(content_lower)),
                    query_lower,
                    query_tokens,
                    _ASSISTANT_SUBSTRING,
                    _ASSISTANT_TOKEN,
                )
                if score > 0 and (best_assistant is None or score > best_assistant[0]):
                    best_assistant = (score, content)

        content_score = (best_user[0] if best_user else 0) + (best_assistant[0] if best_assistant else 0)
        if title_score + content_score == 0:
            continue

        # Prefer the user's phrasing for the excerpt; fall back to the reply.
        matched_role: str | None = None
        snippet: str | None = None
        if best_user is not None:
            snippet = _matched_snippet(best_user[1], query_lower, query_tokens)
            if snippet:
                matched_role = "user"
        if snippet is None and best_assistant is not None:
            snippet = _matched_snippet(best_assistant[1], query_lower, query_tokens)
            if snippet:
                matched_role = "assistant"

        score = (title_score + content_score) * _recency_weight(c.updated_at, now)
        scored.append((score, c, matched_role, snippet))

    scored.sort(key=lambda x: (x[0], x[1].updated_at), reverse=True)
    limit = max(1, min(int(limit), MAX_LIMIT))
    return [
        SessionSearchHit(meta=c, score=score, matched_role=matched_role, snippet=snippet)
        for score, c, matched_role, snippet in scored[:limit]
    ]
