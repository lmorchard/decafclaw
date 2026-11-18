"""Out-of-band improvement scout extension (#1482).

Subscribes ``ON_TURN_SETTLE`` as a quiet background observer. A settled turn is
first screened by cheap regex heuristics; only a turn with a tool error or an
explicit user correction reaches the LLM. The evaluator turns friction into a
record appended to ``workspace/improvement_inbox.jsonl`` for later triage by
the companion ``improvement-inbox`` contrib skill.

The extension never writes to the conversation: it does not append messages,
publish events, or call ``ctx.publish``. Every failure path is logged and
swallowed so a broken evaluator cannot disturb a settled turn.
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from decafclaw import llm
from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.extensions import BaseExtension, register_extension_type
from decafclaw.improvement_inbox import append_item, new_item
from decafclaw.model_catalog import effective_default_model

log = logging.getLogger(__name__)

_TOOL_ERROR_RE = re.compile(r"\[error:\s*", re.IGNORECASE)
_EXIT_CODE_RE = re.compile(r"\[exit code:\s*(-?\d+)\]", re.IGNORECASE)
_USER_CORRECTION_RES = (
    re.compile(r"^\s*(?:stop|wait)\b", re.IGNORECASE),
    re.compile(r"\b(?:didn't work|did not work|that's wrong|that is wrong|try again)\b", re.IGNORECASE),
)

_SNIPPET_LIMIT = 500
_DEFAULT_MAX_TRANSCRIPT_CHARS = 12000
_DEFAULT_DEDUP_TTL_SECONDS = 3600
_DEDUP_MAX_ENTRIES = 256

_SYSTEM_PROMPT = """You are an out-of-band improvement scout for DecafClaw, an AI agent runtime.
You inspect one settled turn that showed signs of harness friction: a tool error or an
explicit user correction. Identify friction in the agent's tooling, prompts, configuration,
or workflow that DecafClaw's maintainers could fix. Ignore ordinary task-domain failures
where the user's own data, a remote service, or the task itself was at fault.

Return ONLY one JSON object, with no prose and no markdown fence:
{
  "title": "short specific title",
  "observed_friction": "what went wrong, concretely",
  "suggested_improvement": "a concrete change to DecafClaw or its tooling",
  "subsystem": "tool, extension, skill, prompt, config, or similar area",
  "severity": "low" | "medium" | "high"
}

If the turn shows no genuine harness friction, return {"skip": true} instead."""


class _EvaluationOutcome(enum.Enum):
    """An evaluation that completed without producing an item.

    ``SKIPPED`` means the model answered and declined, so the trigger is real
    but already handled. ``FAILED`` means no evaluation happened (no model, an
    empty transcript) or its output was unusable, so a later turn should be
    allowed to retry.
    """

    SKIPPED = "skipped"
    FAILED = "failed"


class _Unparseable:
    """Marker type for unusable model output, distinct from a model decline."""


_UNPARSEABLE = _Unparseable()


@dataclass
class _Trigger:
    kind: str
    snippet: str


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _signature(trigger: _Trigger) -> str:
    seed = f"{trigger.kind}:{_normalize(trigger.snippet)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _snippet(text: str, limit: int = _SNIPPET_LIMIT) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    return compact[:limit]


def _text_of(content: Any) -> str:
    """Flatten message content to text for heuristic and prompt use."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _is_tool_error(content: str) -> bool:
    if _TOOL_ERROR_RE.search(content):
        return True
    match = _EXIT_CODE_RE.search(content)
    return bool(match and int(match.group(1)) != 0)


def _detect_trigger(transcript: list[dict[str, Any]]) -> _Trigger | None:
    """Return the first tool error, else the first user correction."""
    for message in transcript:
        if not isinstance(message, dict):
            continue
        text = _text_of(message.get("content"))
        if message.get("role") == "tool" and _is_tool_error(text):
            return _Trigger(kind="tool_error", snippet=_snippet(text))
    for message in transcript:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _text_of(message.get("content"))
        if any(pattern.search(text) for pattern in _USER_CORRECTION_RES):
            return _Trigger(kind="user_correction", snippet=_snippet(text))
    return None


class _DedupCache:
    """In-memory TTL cache of signatures already acted on.

    Every remembered signature keeps its own expiry. When the cache is full the
    entry with the smallest expiry is evicted, so expiry runs in insertion order
    (FIFO). It is not an LRU cache: reads never refresh an entry.
    """

    def __init__(self, ttl_seconds: float, *, max_entries: int = _DEDUP_MAX_ENTRIES, clock=time.monotonic):
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._clock = clock
        self._entries: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        expired = [key for key, expiry in self._entries.items() if expiry <= now]
        for key in expired:
            self._entries.pop(key, None)

    def is_suppressed(self, signature: str) -> bool:
        """True when ``signature`` is remembered and has not expired."""
        self._prune(self._clock())
        return signature in self._entries

    def remember(self, signature: str) -> None:
        """Remember ``signature`` for one TTL, evicting the earliest expiry when full."""
        now = self._clock()
        self._prune(now)
        if len(self._entries) >= self.max_entries:
            earliest = min(self._entries, key=self._entries.__getitem__)
            self._entries.pop(earliest, None)
        self._entries[signature] = now + self.ttl_seconds


def _transcript_text(transcript: list[dict[str, Any]], max_chars: int) -> str:
    """Render the transcript tail as plain text within a character budget."""
    lines: list[str] = []
    for message in transcript:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "unknown"
        text = _text_of(message.get("content")).strip()
        if text:
            lines.append(f"{role}: {text}")
    rendered = "\n\n".join(lines)
    if max_chars > 0 and len(rendered) > max_chars:
        rendered = "…(earlier messages trimmed)…\n" + rendered[-max_chars:]
    return rendered


def _resolve_model(config: Any, model: str | None) -> str:
    if model:
        return model
    return effective_default_model(config) or ""


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _parse_suggestion(content: str) -> dict[str, Any] | None | _Unparseable:
    """Parse model output into a suggestion.

    ``None`` means a well-formed response declining with ``{"skip": true}``;
    ``_UNPARSEABLE`` means unusable output, which callers treat as a failure.
    """
    text = _strip_fences(content)
    if not text:
        return _UNPARSEABLE
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return _UNPARSEABLE
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return _UNPARSEABLE
    if not isinstance(payload, dict):
        return _UNPARSEABLE
    if payload.get("skip"):
        return None

    def _field(name: str, default: str) -> str:
        value = payload.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else default

    severity = _field("severity", "medium").lower()
    if severity not in ("low", "medium", "high"):
        severity = "medium"
    return {
        "title": _field("title", "Unclassified harness friction"),
        "observed_friction": _field("observed_friction", ""),
        "suggested_improvement": _field("suggested_improvement", ""),
        "subsystem": _field("subsystem", "unknown").lower(),
        "severity": severity,
    }


async def evaluate_transcript(
    config: Any,
    transcript: list[dict[str, Any]],
    *,
    trigger: str,
    model: str | None = None,
    max_transcript_chars: int = _DEFAULT_MAX_TRANSCRIPT_CHARS,
) -> dict[str, Any] | _EvaluationOutcome:
    """Ask the evaluator model for one suggestion.

    Returns a suggestion dict, ``_EvaluationOutcome.SKIPPED`` when the model
    completed and declined, or ``_EvaluationOutcome.FAILED`` when no evaluation
    could be made (no model, empty transcript) or its output was unusable.
    Failures never raise; callers own the fail-open boundary.
    """
    model_name = _resolve_model(config, model)
    if not model_name:
        log.debug("Improvement scout skipped: no model configured")
        return _EvaluationOutcome.FAILED
    transcript_text = _transcript_text(transcript, max_transcript_chars)
    if not transcript_text:
        return _EvaluationOutcome.FAILED
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Trigger: {trigger}\n\nSettled turn transcript:\n\n{transcript_text}",
        },
    ]
    response = await llm.call_llm(config, messages, model_name=model_name)
    if not isinstance(response, dict):
        return _EvaluationOutcome.FAILED
    parsed = _parse_suggestion(_text_of(response.get("content")))
    if isinstance(parsed, _Unparseable):
        return _EvaluationOutcome.FAILED
    if parsed is None:
        return _EvaluationOutcome.SKIPPED
    return parsed


class ImprovementScoutExtension(BaseExtension):
    """Observe settled turns and buffer actionable friction in a JSONL inbox."""

    def __init__(self, options: dict[str, Any], config: Any, *, clock=time.monotonic):
        super().__init__(name="improvement_scout")
        self.options = options or {}
        self.config = config
        self.model = self.options.get("model") or None
        try:
            self.max_transcript_chars = int(self.options.get("max_transcript_chars", _DEFAULT_MAX_TRANSCRIPT_CHARS))
        except (TypeError, ValueError):
            self.max_transcript_chars = _DEFAULT_MAX_TRANSCRIPT_CHARS
        try:
            dedup_ttl = float(self.options.get("dedup_ttl_seconds", _DEFAULT_DEDUP_TTL_SECONDS))
        except (TypeError, ValueError):
            dedup_ttl = _DEFAULT_DEDUP_TTL_SECONDS
        self._dedup = _DedupCache(dedup_ttl, clock=clock)

    def enable(self, bus: EventBus) -> list[str]:
        self.disable(bus)
        self.subscription_ids = [bus.subscribe(TurnLifecycle.ON_TURN_SETTLE, self.on_turn_settle_hook)]
        return self.subscription_ids

    async def on_turn_settle_hook(self, ctx: Any, transcript: list[dict[str, Any]]) -> None:
        """Evaluate one settled turn; never raise into the turn dispatch."""
        try:
            await self._handle(ctx, transcript or [])
        except Exception as e:  # noqa: BLE001 - an observer must fail open
            log.warning("Improvement scout failed to evaluate settled turn: %s", e)

    async def _handle(self, ctx: Any, transcript: list[dict[str, Any]]) -> None:
        trigger = _detect_trigger(transcript)
        if trigger is None:
            return
        signature = _signature(trigger)
        if self._dedup.is_suppressed(signature):
            log.debug("Improvement scout suppressed repeated %s", trigger.kind)
            return

        # Suppress this exact trigger once the evaluator has answered: either an
        # item was buffered, or the model completed and declined. A failed
        # evaluation (raised, no model, unusable output) records nothing, so a
        # transient problem does not suppress real friction for the TTL.
        # Exceptions propagate to the hook, which logs and swallows them.
        outcome = await evaluate_transcript(
            self.config,
            transcript,
            trigger=trigger.kind,
            model=self.model,
            max_transcript_chars=self.max_transcript_chars,
        )
        if isinstance(outcome, _EvaluationOutcome):
            if outcome is _EvaluationOutcome.SKIPPED:
                self._dedup.remember(signature)
                log.debug("Improvement scout recorded skip for repeated %s", trigger.kind)
            return

        item = new_item(
            conversation_id=str(getattr(ctx, "conv_id", "") or ""),
            trigger=trigger.kind,
            title=outcome["title"],
            observed_friction=outcome["observed_friction"],
            suggested_improvement=outcome["suggested_improvement"],
            subsystem=outcome["subsystem"],
            severity=outcome["severity"],
        )
        append_item(self.config, item)
        self._dedup.remember(signature)
        log.debug("Improvement scout buffered %s (%s)", item["id"], trigger.kind)


register_extension_type("improvement_scout", ImprovementScoutExtension)
