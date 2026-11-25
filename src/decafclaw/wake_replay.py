"""Silent wake replay — read the persisted suppression fact, never re-guess it.

A wake turn that ends with ``BACKGROUND_WAKE_OK`` is suppressed live. New
archives persist ``suppress_user_message: true`` on the assistant row and strip
the sentinel from its content, so replay renders a status row from a recorded
fact. Older archives predate the flag; for those, a sentinel is stripped only
when the surrounding turn is structurally a wake (a ``wake_trigger`` row opened
it). That fallback is for legacy data, not the primary decision.
"""

from __future__ import annotations

from .sentinels import response_starts_with_sentinel, strip_leading_sentinel

BACKGROUND_WAKE_SENTINEL = "BACKGROUND_WAKE_OK"
SILENT_WAKE_TITLE = "wake · silent"


def silent_wake_body(text: str | None) -> str | None:
    """Return the body after a leading ``BACKGROUND_WAKE_OK``, or ``None``.

    ``""`` is a valid body (the sentinel alone), so callers must compare against
    ``None`` rather than truthiness.
    """
    if not response_starts_with_sentinel(text, BACKGROUND_WAKE_SENTINEL):
        return None
    return strip_leading_sentinel(text, BACKGROUND_WAKE_SENTINEL)


def suppressed_wake_body(message: dict, *, legacy_wake_turn: bool = False) -> str | None:
    """Return the replay body when ``message`` is a silent wake assistant row.

    The persisted flag is authoritative. ``legacy_wake_turn`` enables the
    text fallback for archives written before the flag existed.
    """
    if message.get("role") != "assistant":
        return None
    if message.get("suppress_user_message"):
        return message.get("content") or ""
    if not legacy_wake_turn:
        return None
    return silent_wake_body(message.get("content"))


def normalize_suppressed_wakes(messages: list[dict]) -> list[dict]:
    """Annotate/strip silent wake rows in a transcript slice.

    New rows already carry the flag and a stripped body, so this is a no-op for
    them. Legacy rows are recognized only inside a structurally identified wake
    turn (opened by ``wake_trigger`` and not yet closed by a ``user`` row).
    """
    normalized: list[dict] = []
    wake_turn = False
    for message in messages:
        role = message.get("role")
        if role == "wake_trigger":
            wake_turn = True
        elif role == "user":
            wake_turn = False
        body = suppressed_wake_body(message, legacy_wake_turn=wake_turn)
        if body is None:
            normalized.append(message)
        else:
            normalized.append({**message, "content": body, "suppress_user_message": True})
    return normalized
