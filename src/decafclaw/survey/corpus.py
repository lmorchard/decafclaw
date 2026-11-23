"""Settled turn boundaries from conversation archives (#1738).

A boundary replays one real transition: everything said up to the assistant's
last reply, and the user message that actually followed. Synthetic runtime
user messages are filtered with the same predicate auto-titling uses
(:func:`decafclaw.web.auto_titling.is_synthetic_user_message`).
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from ..archive import read_archive_file
from ..conversation_paths import conversations_root, iter_conversation_archives, sessions_root
from ..web.auto_titling import is_synthetic_user_message

log = logging.getLogger(__name__)

# Transcript tails are prompt input, so bound them. The per-message cap keeps
# one long assistant dump from crowding out the rest of the turn.
DEFAULT_TAIL_CHARS = 6000
DEFAULT_MESSAGE_CHARS = 2000

_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?(.+?)[ \t]*$", re.M)
_CHECKBOX_ITEM = re.compile(r"^[ \t]*[-*+][ \t]+\[[ xX]\][ \t]+(.+?)[ \t]*$", re.M)
_LABELED_ITEM = re.compile(r"^[ \t]*(?:Option[ \t]+)?([A-Z])[).:][ \t]+(.+?)[ \t]*$", re.M)
_INLINE_ALTERNATIVE = re.compile(r"\b([^.!?\n]{3,70}?)\s+or\s+([^.!?\n]{3,70}?)(?:[?.!]|$)", re.I)
_OPTIONS_HEADER = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:options?|choices?|next steps?|possible next steps?)[ \t]*:?[ \t]*$",
    re.I | re.M,
)
_CHOICE_CUE = re.compile(
    r"(would you like|want me to|should i|do you want|which (?:one|option|direction)|"
    r"please choose|choose one|shall i|let me know (?:which|if)|either .*? or )",
    re.I,
)

_STOP_PHRASES = {
    "thanks",
    "thank you",
    "thanks!",
    "ok",
    "okay",
    "got it",
    "done",
    "stop",
    "no",
    "nope",
    "no thanks",
    "that's all",
    "thats all",
    "that is all",
    "nothing else",
    "all set",
    "good night",
    "goodnight",
    "bye",
    "see you",
    "great",
    "perfect",
    "sounds good",
}
_STOP_PREFIXES = ("no thanks", "that's it", "thats it", "nothing else", "i'm done", "im done")


@dataclass(frozen=True)
class OfferAnalysis:
    """Choice-shaped content extracted from one assistant message."""

    options: tuple[str, ...]
    checkbox_options: tuple[str, ...]
    labeled_options: tuple[str, ...]
    inline_options: tuple[str, ...]
    offers_choice: bool
    ends_with_question: bool


@dataclass(frozen=True)
class TurnBoundary:
    """One archived (transcript tail -> actual next user message) sample."""

    conv_id: str
    turn_id: str
    boundary_index: int
    timestamp: str
    transcript_tail: str
    actual_next: str
    previous_user: str
    assistant_before: str
    active_model: str
    user_turns: int
    provenance_pages: tuple[str, ...] = ()
    offered: OfferAnalysis | None = None

    def as_devcase(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "conv_id": self.conv_id,
            "timestamp": self.timestamp,
            "source": "archive",
            "user_turns": self.user_turns,
            "active_model": self.active_model,
            "transcript_tail": self.transcript_tail,
            "actual_next_user_message": self.actual_next,
            "previous_user_message": self.previous_user,
            "assistant_before": self.assistant_before,
            "provenance_pages": list(self.provenance_pages),
        }


@dataclass
class CorpusScan:
    """Stage 0 corpus counts: boundaries plus per-conversation final turns."""

    boundaries: list[TurnBoundary] = field(default_factory=list)
    conversations: int = 0
    conversations_with_boundaries: int = 0
    final_user_turns: int = 0
    final_turns_that_stop: int = 0
    archive_roots: list[str] = field(default_factory=list)


def normalize_text(value: str) -> str:
    """Casefold, drop markup/punctuation, collapse whitespace."""
    text = str(value or "").casefold()
    text = re.sub(r"[`*_#>]", "", text)
    text = re.sub(r"[^\w\s'/-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .,!?;:")


def token_set(value: str) -> frozenset[str]:
    return frozenset(token for token in normalize_text(value).split() if token)


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def page_key(value: str) -> str:
    """Normalize a page path or wiki link to a comparable stem.

    Provenance and embeddings carry paths (``agent/pages/Foo.md``), wiki links
    and PageRank carry names (``Foo``); the survey compares page *sets*, so
    both forms collapse to the casefolded stem.
    """
    name = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if name.casefold().endswith(".md"):
        name = name[:-3]
    return name.strip().casefold()


def _dedupe(items: list[str], *, limit: int = 200) -> tuple[str, ...]:
    seen: set[str] = set()
    kept: list[str] = []
    for item in items:
        text = str(item or "").strip().strip("-*` ")
        if not text or len(text) > limit:
            continue
        key = normalize_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(text)
    return tuple(kept)


def analyze_offer(assistant_text: str) -> OfferAnalysis:
    """Extract choice-shaped content from an assistant message.

    ``offers_choice`` is deliberately conservative: a checkbox list, an
    explicit "Options/Next steps" header, or a choice cue plus a trailing
    question. Content bullets in ordinary prose do not qualify.
    """
    text = str(assistant_text or "")
    checkbox = _dedupe([m.group(1) for m in _CHECKBOX_ITEM.finditer(text)])
    labeled = _dedupe([m.group(2) for m in _LABELED_ITEM.finditer(text)])
    inline: list[str] = []
    for match in _INLINE_ALTERNATIVE.finditer(text):
        inline.extend([match.group(1).strip(" ,"), match.group(2).strip(" ,")])
    inline_options = _dedupe(inline)

    listed = _dedupe([m.group(1) for m in _LIST_ITEM.finditer(text)])
    options = _dedupe([*checkbox, *listed, *labeled, *inline_options])

    tail = text[-300:]
    ends_with_question = "?" in tail
    has_header = bool(_OPTIONS_HEADER.search(text))
    has_cue = bool(_CHOICE_CUE.search(text))
    offers_choice = bool(checkbox) or has_header or (has_cue and ends_with_question)
    return OfferAnalysis(
        options=options,
        checkbox_options=checkbox,
        labeled_options=labeled,
        inline_options=inline_options,
        offers_choice=offers_choice,
        ends_with_question=ends_with_question,
    )


def _matches_option(user: str, option: str) -> bool:
    user_norm = normalize_text(user)
    option_norm = normalize_text(option)
    if not user_norm or not option_norm:
        return False
    if user_norm == option_norm:
        return True
    if len(user_norm) >= 3 and user_norm in option_norm:
        return True
    if len(option_norm) >= 4 and option_norm in user_norm:
        return True
    return difflib.SequenceMatcher(None, user_norm, option_norm).ratio() >= 0.85


def recapitulation_kind(user_text: str, offer: OfferAnalysis | None) -> str | None:
    """How the next user message reuses the assistant's offered choice.

    Returns ``"checkbox"``, ``"option"``, or ``"inline"`` for a match, else
    ``None``. Whole-message repetition is a separate signal (see
    :func:`is_whole_repeat`) because retrying a command is not a choice.
    """
    if offer is None:
        return None
    user = str(user_text or "").strip()
    if not user:
        return None
    if any(_matches_option(user, option) for option in offer.checkbox_options):
        return "checkbox"
    if any(_matches_option(user, option) for option in offer.inline_options):
        return "inline"
    if offer.offers_choice and any(_matches_option(user, option) for option in offer.options):
        return "option"
    return None


def is_whole_repeat(user_text: str, assistant_text: str) -> bool:
    """True when the next user message is literally inside the prior reply."""
    user = normalize_text(user_text)
    assistant = normalize_text(assistant_text)
    if len(user) < 4:
        return False
    return user in assistant


def looks_like_stop(user_text: str) -> bool:
    """Heuristic: the final user message ends the conversation."""
    normalized = normalize_text(user_text)
    if not normalized:
        return False
    if normalized in _STOP_PHRASES:
        return True
    return any(normalized.startswith(prefix) for prefix in _STOP_PREFIXES)


def message_text(message: dict) -> str:
    """Render the human-visible text of an assistant/user message."""
    if message.get("role") not in ("user", "assistant"):
        return ""
    content = message.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        content = str(content)
    text = content.strip()
    if not text or text == "None":
        return ""
    return text


def render_transcript_tail(
    messages: list[dict],
    end_index: int,
    *,
    max_chars: int = DEFAULT_TAIL_CHARS,
    max_message_chars: int = DEFAULT_MESSAGE_CHARS,
) -> str:
    """Render user/assistant text before ``end_index`` as a prompt tail."""
    parts: list[str] = []
    for message in messages[:end_index]:
        role = message.get("role")
        if role == "user" and is_synthetic_user_message(message):
            continue
        text = message_text(message)
        if not text:
            continue
        if len(text) > max_message_chars:
            text = text[:max_message_chars].rstrip() + " …"
        label = "Human" if role == "user" else "Assistant"
        parts.append(f"{label}: {text}")
    tail = "\n\n".join(parts)
    if len(tail) > max_chars:
        keep = max(0, max_chars - 2)
        tail = "…\n" + tail[-keep:].lstrip("\n") if keep else ""
    return tail


def _provenance_pages(messages: list[dict], start: int, end: int) -> tuple[str, ...]:
    pages: list[str] = []
    seen: set[str] = set()
    for message in messages[start:end]:
        if message.get("role") != "assistant":
            continue
        provenance = message.get("provenance") or {}
        vault = provenance.get("vault") if isinstance(provenance, dict) else None
        for entry in vault or []:
            if not isinstance(entry, dict):
                continue
            page = str(entry.get("page") or "").strip()
            key = page_key(page)
            if not page or key in seen:
                continue
            seen.add(key)
            pages.append(page)
    return tuple(pages)


def _active_model(messages: list[dict], end_index: int, default_model: str) -> str:
    for message in reversed(messages[:end_index]):
        if message.get("role") != "model":
            continue
        name = str(message.get("content") or "").strip()
        if name:
            return name
    return default_model


def boundaries_for_conversation(
    conv_id: str,
    messages: list[dict],
    *,
    default_model: str,
    tail_chars: int = DEFAULT_TAIL_CHARS,
    message_chars: int = DEFAULT_MESSAGE_CHARS,
) -> list[TurnBoundary]:
    """Build every settled boundary in one archive.

    Synthetic user messages are skipped for both ends: they neither start a
    turn nor count as the actual next message.
    """
    real_user_indices = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "user" and not is_synthetic_user_message(message)
    ]
    boundaries: list[TurnBoundary] = []
    for position in range(1, len(real_user_indices)):
        next_index = real_user_indices[position]
        actual_next = message_text(messages[next_index])
        if not actual_next:
            continue
        previous_user = message_text(messages[real_user_indices[position - 1]])
        assistant_before = ""
        for message in reversed(messages[:next_index]):
            if message.get("role") == "assistant" and message_text(message):
                assistant_before = message_text(message)
                break
        if not assistant_before:
            continue
        next_boundary = real_user_indices[position + 1] if position + 1 < len(real_user_indices) else len(messages)
        boundaries.append(
            TurnBoundary(
                conv_id=conv_id,
                turn_id=f"{conv_id}#u{position + 1}",
                boundary_index=position + 1,
                timestamp=str(messages[next_index].get("timestamp") or ""),
                transcript_tail=render_transcript_tail(
                    messages,
                    next_index,
                    max_chars=tail_chars,
                    max_message_chars=message_chars,
                ),
                actual_next=actual_next,
                previous_user=previous_user,
                assistant_before=assistant_before,
                active_model=_active_model(messages, next_index, default_model),
                user_turns=len(real_user_indices),
                provenance_pages=_provenance_pages(messages, next_index + 1, next_boundary),
                offered=analyze_offer(assistant_before),
            )
        )
    return boundaries


def scan_corpus(
    config,
    *,
    default_model: str = "",
    tail_chars: int = DEFAULT_TAIL_CHARS,
    message_chars: int = DEFAULT_MESSAGE_CHARS,
    limit: int | None = None,
) -> CorpusScan:
    """Scan every archived conversation for Stage 0 counts and dev samples."""
    scan = CorpusScan()
    roots = {str(conversations_root(config)), str(sessions_root(config))}
    for conv_id, path in iter_conversation_archives(config):
        if limit is not None and scan.conversations >= limit:
            break
        scan.conversations += 1
        messages = read_archive_file(path)
        real_user_indices = [
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user" and not is_synthetic_user_message(message)
        ]
        if real_user_indices:
            final_text = message_text(messages[real_user_indices[-1]])
            scan.final_user_turns += 1
            if looks_like_stop(final_text):
                scan.final_turns_that_stop += 1
        boundaries = boundaries_for_conversation(
            conv_id,
            messages,
            default_model=default_model,
            tail_chars=tail_chars,
            message_chars=message_chars,
        )
        if boundaries:
            scan.conversations_with_boundaries += 1
            scan.boundaries.extend(boundaries)
    scan.archive_roots = sorted(roots)
    return scan


def parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed
