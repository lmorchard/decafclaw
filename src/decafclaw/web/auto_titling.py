"""Periodic auto-titling of web UI conversations (#1072).

Sidebar tabs start with the first 100 characters of the opening message
(websocket.py), which is often scaffolding. After a completed agent turn the
caller may fire ``maybe_auto_title`` as a background task; when the turn count
hits a cadence point the title is refined with a short LLM-generated title.

Guards, in cost order (cheap index lookups before any archive read):

- Conversations the user renamed (``custom_title``) are never overwritten —
  the index refuses ``manual=False`` renames for them, and the check here
  saves the LLM call and the archive read.
- Each cadence point is attempted at most once (module-level dedup), so
  multiple open tabs of one conversation do not multiply LLM calls.
- Once the last cadence point has been attempted, the conversation is
  marked exhausted in memory: later turns skip the turn count entirely
  (the sentinel is per-process and cleared on restart).
- The caller must not await this on the turn-completion path; it is
  designed to run as a fire-and-forget background task so titling adds no
  latency to the main turn.
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
# Highest cadence point completed for each conversation. Out-of-order
# results from older cadence attempts are discarded (#1132).
_latest_cadence: dict[str, int] = {}
# conv_ids whose turn count has passed the last cadence point: no later
# turn can ever be due, so skip the archive read entirely. Per-process;
# a restart clears the sentinel, which bounds the residual cost.
_exhausted: set[str] = set()

_SYNTHETIC_SOURCES = {"widget_response", "reflection_retry", "system_update"}
_SYNTHETIC_PREFIXES = ("[reflection]", "[System Update:")


def is_synthetic_user_message(msg: dict) -> bool:
    """Return True if ``msg`` is a synthetic user-role message from the runtime."""
    if msg.get("source") in _SYNTHETIC_SOURCES:
        return True
    content = str(msg.get("content", "")).strip()
    return content.startswith(_SYNTHETIC_PREFIXES)


def count_user_turns(config, conv_id: str) -> int:
    """Number of actual user turns in the conversation archive.

    Synthetic user-role messages injected by the runtime (widget responses,
    reflection retries, mid-conversation system updates) are excluded so the
    cadence stays aligned with actual user turns (#1072, #1132).
    """
    return sum(1 for m in read_archive(config, conv_id) if m.get("role") == "user" and not is_synthetic_user_message(m))


def _history_text(config, conv_id: str) -> str:
    """Render the recent user/assistant history as a flat text block."""
    lines = []
    for m in read_archive(config, conv_id)[-MAX_HISTORY_MESSAGES:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        if role == "user" and is_synthetic_user_message(m):
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


async def retitle_conversation(config, conv_id: str, event_bus) -> tuple[bool, str]:
    """Run an on-demand retitle pass for this conversation (#1156).

    Bypasses cadence and dedup guards (TITLING_TURNS, _attempted, _exhausted).
    On success:
      - generates a title from recent conversation history via _generate_title.
      - renames through ConversationIndex(config) with manual=False and clear_custom=True,
        so custom_title is cleared and subsequent periodic auto-titling can continue.
      - publishes a 'conv_renamed' event on the event_bus so every open tab updates.
    Returns (True, new_title) on success, or (False, error_message) on failure.
    Fails open: on error, title is unchanged and returns (False, err).
    """
    try:
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if conv is None:
            return False, "Conversation not found."

        title = await _generate_title(config, conv_id)
        if not title:
            return False, "Not enough conversation history to generate a title."

        updated = index.rename(conv_id, title, manual=False, clear_custom=True)
        if updated is None:
            return False, "Could not update conversation title."

        await event_bus.publish(
            {
                "type": "conv_renamed",
                "conv_id": conv_id,
                "title": updated.title,
                "updated_at": updated.updated_at,
            }
        )
        return True, updated.title
    except Exception as exc:
        log.warning("On-demand retitle failed for conv %s: %s", conv_id[:8], exc, exc_info=True)
        return False, "Failed to generate title."


async def maybe_auto_title(config, conv_id: str, event_bus) -> None:
    """Run one auto-titling pass if this conversation is due.

    No-ops (silently) when the conversation is unknown or user-renamed, when
    the turn count is not a cadence point, or when that point was already
    attempted. On success it renames through the index (``manual=False``
    guard) and publishes a ``conv_renamed`` event so connected clients can
    refresh their sidebar in real time.

    The cheap index guards run before any archive read: the turn count is
    only taken for indexed conversations without a custom title that have
    not exhausted the cadence, so renamed, system, and long conversations
    cost one small index lookup per turn, not a full archive parse.

    Fail-open: any error is logged and swallowed — titling must never break
    the turn that triggered it.
    """
    try:
        if conv_id in _exhausted:
            return
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if conv is None or conv.custom_title:
            return

        turns = count_user_turns(config, conv_id)
        if turns > TITLING_TURNS[-1]:
            _exhausted.add(conv_id)
            return
        if turns not in TITLING_TURNS:
            return
        attempted = _attempted.setdefault(conv_id, set())
        if turns in attempted:
            return
        attempted.add(turns)
        if turns == TITLING_TURNS[-1]:
            # The last cadence point has now been attempted: no later turn
            # can ever be due, so subsequent turns skip the count.
            _exhausted.add(conv_id)

        title = await _generate_title(config, conv_id)
        if turns < _latest_cadence.get(conv_id, 0):
            # Superseded by a newer cadence that already resolved (#1132).
            return
        _latest_cadence[conv_id] = max(_latest_cadence.get(conv_id, 0), turns)

        conv = index.get(conv_id)
        if conv is None or conv.custom_title:
            return
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
