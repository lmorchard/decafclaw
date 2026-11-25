"""Start-anchored response sentinels (``HEARTBEAT_OK``, ``BACKGROUND_WAKE_OK``).

Kept in a small module with no project imports so history/replay code can use
the matcher without pulling in the heartbeat runtime.
"""

import functools
import re


def response_starts_with_sentinel(response: str | None, sentinel: str) -> bool:
    """True when `response` begins with `sentinel`, ignoring leading whitespace.

    THE matcher for every response sentinel. Start-anchored on purpose: the
    original HEARTBEAT_OK check matched anywhere in the first 300 characters, so
    a response that merely *mentioned* the sentinel counted as a quiet cycle.
    That already forced unrelated code to work around it — `agent.py` orders the
    loop-breaker note ahead of accumulated preambles precisely so a preamble
    mentioning HEARTBEAT_OK can't land in the window and bury the alert.
    BACKGROUND_WAKE_OK was written start-anchored to dodge the same trap; this is
    that rule, shared instead of duplicated (#450).

    Safe to tighten because `polling.py:build_task_preamble` — the only producer
    of these instructions — already asks for the marker first: "respond with
    HEARTBEAT_OK" (heartbeat) and "begin your summary with HEARTBEAT_OK on its
    own line" (scheduled, worded that way in #362).

    A trailing word boundary is applied only when the sentinel ends in a word
    character, so HEARTBEAT_OKAY doesn't match HEARTBEAT_OK. It cannot be applied
    to a sentinel ending in punctuation — `\\b` after `]` asserts that a word
    character follows, which would make "[SILENT]" alone fail while
    "[SILENT] x" passed.
    """
    if not response or not sentinel.strip():
        # An empty sentinel compiles to `^\\s*`, which matches every response —
        # a config-driven empty value would silently suppress everything.
        return False
    return _sentinel_re(sentinel).match(response) is not None


def strip_leading_sentinel(response: str | None, sentinel: str) -> str:
    """Return ``response`` with a leading sentinel and its separator removed.

    Used when archiving a silent wake: the flag records the decision, and the
    body stays readable on replay. A response that does not start with the
    sentinel is returned unchanged.
    """
    if not response or not sentinel.strip():
        return response or ""
    match = _sentinel_re(sentinel).match(response)
    if match is None:
        return response
    rest = response[match.end() :]
    return rest.lstrip(" \t\r\n:—-–,").strip()


@functools.lru_cache(maxsize=None)
def _sentinel_re(sentinel: str) -> re.Pattern[str]:
    """Compiled start-anchored matcher for `sentinel`. Cached — the set of
    sentinels is tiny and fixed at import time in practice."""
    boundary = r"\b" if sentinel[-1:].isalnum() or sentinel.endswith("_") else ""
    return re.compile(rf"^\s*{re.escape(sentinel)}{boundary}", re.IGNORECASE)
