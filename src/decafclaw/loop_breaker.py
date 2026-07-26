"""Per-turn loop-breaker: detects autonomous tool-call thrash and escalates
through three rungs — a diagnostic nudge, then a redirect that demands a
diagnosis before another action, then a hard stop. Trips are watermarked per
fingerprint so a rung only advances on a genuinely fresh offense, not a
standing condition. Pure/deterministic — no agent or LLM imports; driven by
TurnRunner. See docs/loop-breaker.md (#598, #707)."""

import dataclasses
import enum
import hashlib
import json
from typing import NamedTuple


class LoopVerdict(enum.Enum):
    NONE = "none"
    NUDGE = "nudge"
    REDIRECT = "redirect"
    STOP = "stop"


_MAX_ARG_CHARS = 400
_MAX_ERROR_CHARS = 300


def _render_args(args) -> str:
    """Canonical, order-insensitive text form of a call's arguments."""
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(args)


def _truncate(text: str, limit: int) -> str:
    """One-line, length-capped rendering for injection into prompt text."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def fingerprint(tool_name: str, args) -> str:
    """Stable hash of a tool call's name + arguments (order-insensitive)."""
    return hashlib.sha1(f"{tool_name}\x00{_render_args(args)}".encode()).hexdigest()


def summarize_args(args) -> str:
    """Render a call's arguments as one truncated line for redirect text."""
    return _truncate(_render_args(args), _MAX_ARG_CHARS)


def summarize_error(text: str) -> str:
    """Render a tool-result error body as one truncated line."""
    return _truncate(text, _MAX_ERROR_CHARS)


class CallSignature(NamedTuple):
    """One tool call's loop-relevant record.

    `args_text` and `error_text` are retained (pre-truncated) so a redirect
    can name the actual offending call and its actual failure instead of
    giving generic advice — the detector used to hash the args away and keep
    only an is_error bool (#707).
    """
    tool_name: str
    fingerprint: str
    is_error: bool
    args_text: str = ""
    error_text: str = ""


@dataclasses.dataclass(frozen=True)
class Offense:
    """What tripped the breaker, in enough detail to name it in a redirect.

    `tool_name` and `args_text` are empty for an error-surge trip, which by
    definition has no single offending call.
    """
    reason: str = ""
    tool_name: str = ""
    args_text: str = ""
    error_text: str = ""


@dataclasses.dataclass
class _Offender:
    """Per-fingerprint tally, plus a watermark of where `count` stood the last
    time this fingerprint tripped the breaker.

    The watermark is what makes escalation event-shaped instead of
    state-shaped (#707): a fingerprint that has already tripped at count N
    only trips again once it reaches N+1, i.e. once the agent has repeated
    the call *again* after being told to stop.
    """
    tool_name: str
    count: int = 0
    last_tripped_count: int = 0
    args_text: str = ""
    error_text: str = ""


class LoopBreaker:
    """Detects tool-call thrash within a single turn and escalates.

    Trips on either signal:
    - the same (tool_name, args_fingerprint) seen >= repeat_threshold times
    - >= error_threshold of the last error_window tool results are errors

    Escalation is one-way per instance and advances only on a *fresh* offense
    (see verdict()): first trip returns NUDGE, second REDIRECT, third and
    later STOP. `enabled=False` always returns NONE. One LoopBreaker per turn
    — state is not meant to persist across turns.
    """

    def __init__(self, config):
        self._cfg = config
        self._counts: dict[str, _Offender] = {}
        self._recent_errors: list[bool] = []  # rolling is_error flags
        self._total_errors = 0                # monotonic; never trimmed
        self._errors_at_last_trip = 0         # watermark for the error signal
        self._trips = 0
        self._last_error_text = ""
        self._offense = Offense()

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    def record(self, calls) -> None:
        """Record one iteration's tool calls.

        calls: iterable of CallSignature.
        """
        for sig in calls:
            entry = self._counts.get(sig.fingerprint)
            if entry is None:
                entry = self._counts[sig.fingerprint] = _Offender(
                    tool_name=sig.tool_name,
                )
            entry.tool_name = sig.tool_name
            entry.count += 1
            entry.args_text = sig.args_text
            if sig.is_error:
                # Keep the latest error for this call — the one a redirect quotes.
                entry.error_text = sig.error_text
            self._recent_errors.append(bool(sig.is_error))
            if sig.is_error:
                self._total_errors += 1
                self._last_error_text = sig.error_text
        window = self._cfg.error_window
        if len(self._recent_errors) > window:
            self._recent_errors = self._recent_errors[-window:]

    def _fresh_repeat_offender(self) -> "_Offender | None":
        """The worst fingerprint that has grown since it last tripped."""
        worst = None
        for entry in self._counts.values():
            if entry.count < self._cfg.repeat_threshold:
                continue
            if entry.count <= entry.last_tripped_count:
                continue  # already tripped at this count — agent stopped repeating it
            if worst is None or entry.count > worst.count:
                worst = entry
        return worst

    def _fresh_error_surge(self) -> bool:
        """True when the window is over threshold AND new errors have landed
        since the last trip. The second half is the point: a rolling window
        can stay over threshold on stale errors alone."""
        if sum(self._recent_errors) < self._cfg.error_threshold:
            return False
        return self._total_errors > self._errors_at_last_trip

    def verdict(self) -> LoopVerdict:
        """Compute the verdict for the most recently recorded round.

        Mutates escalation state: a trip advances the rung counter and moves
        the tripping signal's watermark, so a later round only trips again on
        a genuinely new offense. Call exactly once per recorded round.
        """
        if not self._cfg.enabled:
            return LoopVerdict.NONE
        offender = self._fresh_repeat_offender()
        if offender is not None:
            offender.last_tripped_count = offender.count
            self._offense = Offense(
                reason=(f"called {offender.tool_name} {offender.count}× "
                        "with the same args"),
                tool_name=offender.tool_name,
                args_text=offender.args_text,
                error_text=offender.error_text,
            )
        elif self._fresh_error_surge():
            self._errors_at_last_trip = self._total_errors
            errs = sum(self._recent_errors)
            self._offense = Offense(
                reason=(f"{errs} of the last {len(self._recent_errors)} "
                        "tool results were errors"),
                error_text=self._last_error_text,
            )
        else:
            return LoopVerdict.NONE
        self._trips += 1
        if self._trips == 1:
            return LoopVerdict.NUDGE
        if self._trips == 2:
            return LoopVerdict.REDIRECT
        return LoopVerdict.STOP

    def offense(self) -> Offense:
        """The most recent trip's evidence. Empty-field Offense before any trip."""
        return self._offense
