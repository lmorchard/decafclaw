"""Pre-emptive keyword-match against the current user message to promote
relevant deferred tools into the active set for the turn.

This is a v1 implementation — pure keyword overlap, no TF-IDF or
embeddings. See docs/preemptive-tool-search.md for design rationale
and the follow-up plan for more sophisticated matching.

Pure-library module — no integration with the turn lifecycle. Callers
are in :mod:`decafclaw.context_composer` (match computation) and
:mod:`decafclaw.agent` (wiring the result into classify_tools).
"""

from __future__ import annotations

import re

# Minimal English stopword list. Covers the most common grammatical
# words that contribute noise rather than signal to keyword matching.
#
# TODO(followup): evaluate swapping this for a maintained library such
# as the `stop-words` PyPI package (small, no extra deps), or lifting
# the list from scikit-learn / spaCy. A hardcoded list rots; a library
# stays current. Keeping inline for v1 because (a) zero additional
# runtime deps, (b) this list is intentionally minimal and isn't
# expected to change often.
STOPWORDS: frozenset[str] = frozenset({
    "about", "also", "all", "and", "any", "are", "been", "but", "can",
    "could", "day", "every", "first", "for", "from", "get", "has",
    "have", "her", "him", "his", "how", "into", "its", "just", "like",
    "make", "man", "may", "maybe", "need", "new", "not", "now", "old",
    "one", "only", "other", "our", "out", "please", "really", "say",
    "see", "she", "should", "some", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "thing", "this", "those",
    "two", "use", "want", "was", "way", "were", "what", "when", "where",
    "which", "who", "will", "with", "would", "you", "your",
})

# Minimum token length. Two-character tokens are usually grammatical
# noise ("is", "of", "at", "by") or too generic to match meaningfully.
_MIN_TOKEN_LENGTH = 3

# Non-alphanumeric chars are separators. Preserves underscores and
# hyphens as boundaries too — we want `mcp__server-name__tool` to
# tokenize as {mcp, server, name, tool}, not {mcp__server, name__tool}.
_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alphanumeric runs, drop short tokens and stopwords.

    Returns a set of unique tokens >= _MIN_TOKEN_LENGTH characters long,
    excluding stopwords. Idempotent and deterministic.
    """
    if not text:
        return set()
    lowered = text.lower()
    raw_tokens = _SPLIT_RE.split(lowered)
    return {
        t for t in raw_tokens
        if len(t) >= _MIN_TOKEN_LENGTH and t not in STOPWORDS
    }


def match_tools(
    input_tokens: set[str],
    candidates: list[dict],
    max_matches: int,
) -> list[dict]:
    """Score each candidate tool against the input tokens and return the top matches.

    For each candidate, scores = ``|input_tokens ∩ tokenize(name + description)|``.
    Tools with score >= 1 are considered matches. Returns up to ``max_matches``
    results, sorted by (-score, name) for deterministic tie-breaking.

    Args:
        input_tokens: the user message + prior assistant tokens (already tokenized).
        candidates: tool definitions in the OpenAI-style shape
            ``{"function": {"name": str, "description": str, ...}, ...}``.
        max_matches: safety cap on the number of returned tools.

    Returns:
        A list of match entries, each of the form::

            {"name": str, "score": int, "matched_tokens": list[str]}

        ``matched_tokens`` is sorted for stable output (useful in logs/diagnostics).
    """
    if not input_tokens or not candidates or max_matches <= 0:
        return []

    scored: list[dict] = []
    for td in candidates:
        fn = td.get("function", {})
        name = fn.get("name", "")
        if not name:
            continue
        description = fn.get("description", "")
        tool_tokens = tokenize(f"{name} {description}")
        overlap = input_tokens & tool_tokens
        if overlap:
            scored.append({
                "name": name,
                "score": len(overlap),
                "matched_tokens": sorted(overlap),
            })

    # Sort by -score, then alphabetical name for determinism.
    scored.sort(key=lambda e: (-e["score"], e["name"]))
    return scored[:max_matches]


def extract_last_assistant_text(history: list[dict]) -> str:
    """Find the most recent assistant-role message with non-empty content.

    Skips tool results, confirmation requests/responses, vault retrievals,
    cancelled markers (``"[cancelled]"``), and any non-assistant roles.
    Returns the content text, or an empty string if no prior assistant
    message exists.

    Used by the pre-emptive search to carry topical continuity from the
    previous turn's final assistant response into the current turn's
    match input.
    """
    if not history:
        return ""
    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not content or not isinstance(content, str):
            continue
        stripped = content.strip()
        if not stripped or stripped == "[cancelled]":
            continue
        return stripped
    return ""
