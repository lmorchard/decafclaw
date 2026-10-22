"""Periodic auto-titling of web UI conversations (#1072).

Sidebar tabs start with the first 100 characters of the opening message
(websocket.py), which is often scaffolding. After a completed agent turn the
caller may fire ``maybe_auto_title`` as a background task; when the turn count
hits a cadence point the title is refined with a short LLM-generated title.

Guards:

- Conversations the user renamed (``custom_title``) are never overwritten —
  the index refuses ``manual=False`` renames for them, and the check here
  saves the LLM call.
- Each cadence point is attempted at most once (module-level dedup), so
  multiple open tabs of one conversation do not multiply LLM calls.
- The caller must not await this on the turn-completion path; it is designed
  to run as a fire-and-forget background task so titling adds no latency to
  the main turn.
"""

from __future__ import annotations

import logging

from .. import llm
from ..archive import read_archive
from .conversations import ConversationIndex

log = logging.getLogger(__name__)

# Turn numbers at which a retitle is attempted (#1072: at turn 2, then every
# 5 turns, up to and including turn 20).
TITLING_TURNS = (2, 5, 10, 15, 20)

TITLE_PROMPT = (
    "Based on the conversation history below, generate a clear, descriptive "
    "title between 3 and 6 words. Return ONLY the title with no quotes or "
    "punctuation."
)

# History sent to the model: only the most recent messages, each truncated,
# to keep the call light.
MAX_HISTORY_MESSAGES = 40
MAX_MESSAGE_CHARS = 400

# In-memory dedup for one process: conv_id -> cadence points for which an
# attempt already started or completed. The check-and-reserve below is sync
# (no await), so it is atomic within the event loop.
_attempted: dict[str, set[int]] = {}


def count_user_turns(config, conv_id: str) -> int:
    """Number of user turns in the conversation archive."""
    return sum(1 for m in read_archive(config, conv_id) if m.get("role") == "user")


def _history_text(config, conv_id: str) -> str:
    """Render the recent user/assistant history as a flat text block."""
    lines = []
    for m in read_archive(config, conv_id)[-MAX_HISTORY_MESSAGES:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if len(content) > MAX_MESSAGE_CHARS:
            content = content[:MAX_MESSAGE_CHARS] + "…"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _clean_title(raw: str) -> str:
    """Normalize raw model output to a plain title string."""
    title = raw.strip().strip('"').strip("'").strip()
    title = title.rstrip(".!?…")
    return " ".join(title.split())


def _differs(existing: str, new: str) -> bool:
    """Whether ``new`` is a material change to ``existing``."""
    return new.casefold() != existing.casefold()


async def _generate_title(config, conv_id: str) -> str:
    history = _history_text(config, conv_id)
    if not history:
        return ""
    response = await llm.call_llm(
        config,
        [
            {"role": "system", "content": TITLE_PROMPT},
            {"role": "user", "content": history},
        ],
    )
    return _clean_title(response.get("content") or "")


async def maybe_auto_title(config, conv_id: str, event_bus) -> None:
    """Run one auto-titling pass if this conversation is due.

    No-ops (silently) when the conversation is unknown or user-renamed, when
    the turn count is not a cadence point, or when that point was already
    attempted. On success it renames through the index (``manual=False``
    guard) and publishes a ``conv_renamed`` event so connected clients can
    refresh their sidebar in real time.

    Fail-open: any error is logged and swallowed — titling must never break
    the turn that triggered it.
    """
    try:
        turns = count_user_turns(config, conv_id)
        if turns not in TITLING_TURNS:
            return
        attempted = _attempted.setdefault(conv_id, set())
        if turns in attempted:
            return
        attempted.add(turns)

        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if conv is None or conv.custom_title:
            return

        title = await _generate_title(config, conv_id)
        if not title or not _differs(conv.title, title):
            return

        updated = index.rename(conv_id, title, manual=False)
        if updated is None:  # user renamed between the check and the rename
            return
        await event_bus.publish(
            {
                "type": "conv_renamed",
                "conv_id": conv_id,
                "title": updated.title,
                "updated_at": updated.updated_at,
            }
        )
    except Exception:
        log.debug("Auto-titling skipped for conv %s", conv_id[:8], exc_info=True)
