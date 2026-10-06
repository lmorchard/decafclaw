"""Shared utility functions."""

import difflib
from collections.abc import Iterable


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return len(text) // 4 if text else 0


def find_fuzzy_matches(target: str, candidates: Iterable[str], n: int = 3, cutoff: float = 0.6) -> list[str]:
    """Return up to ``n`` candidates close to ``target``, best match first.

    Ranking uses ``difflib.get_close_matches``. Duplicate candidates are
    dropped. Callers use the result only for "Did you mean" suggestions;
    nothing here resolves or redirects a lookup.
    """
    unique = list(dict.fromkeys(candidates))
    return difflib.get_close_matches(target, unique, n=n, cutoff=cutoff)
