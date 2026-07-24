"""Per-turn loop-breaker: detects autonomous tool-call thrash and escalates
a diagnostic nudge, then a hard stop. Pure/deterministic — no agent or LLM
imports; driven by TurnRunner. See docs/loop-breaker.md (#598)."""

import enum
import hashlib
import json


class LoopVerdict(enum.Enum):
    NONE = "none"
    NUDGE = "nudge"
    STOP = "stop"


def fingerprint(tool_name: str, args) -> str:
    """Stable hash of a tool call's name + arguments (order-insensitive)."""
    try:
        arg_repr = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        arg_repr = repr(args)
    return hashlib.sha1(f"{tool_name}\x00{arg_repr}".encode()).hexdigest()


class LoopBreaker:
    """Detects tool-call thrash within a single turn and escalates.

    Trips on either signal:
    - the same (tool_name, args_fingerprint) seen >= repeat_threshold times
    - >= error_threshold of the last error_window tool results are errors

    Escalation is one-way per instance: the first trip returns NUDGE; any
    subsequent trip after that returns STOP. `enabled=False` always returns
    NONE. One LoopBreaker per turn — state is not meant to persist across
    turns.
    """

    def __init__(self, config):
        self._cfg = config
        # fingerprint -> [tool_name, count]. Tracks the name alongside the
        # count so last_signal() can name the offending tool.
        self._counts: dict[str, list] = {}
        self._recent_errors: list[bool] = []  # rolling is_error flags
        self._nudged = False
        self._last_signal = ""

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    def record(self, calls) -> None:
        """Record one iteration's tool calls.

        calls: iterable of (tool_name, fingerprint, is_error).
        """
        for tool_name, fp, is_error in calls:
            entry = self._counts.setdefault(fp, [tool_name, 0])
            entry[0] = tool_name
            entry[1] += 1
            self._recent_errors.append(bool(is_error))
        # Trim to a rolling window of the last N results.
        window = self._cfg.error_window
        if len(self._recent_errors) > window:
            self._recent_errors = self._recent_errors[-window:]

    def _tripped_reason(self) -> str | None:
        # Repeated identical call?
        top_name, top_n = None, 0
        for name, n in self._counts.values():
            if n > top_n:
                top_name, top_n = name, n
        if top_n >= self._cfg.repeat_threshold:
            return f"called {top_name} {top_n}× with the same args"
        # Repeated errors in the window?
        errs = sum(self._recent_errors)
        if errs >= self._cfg.error_threshold:
            return f"{errs} of the last {len(self._recent_errors)} tool results were errors"
        return None

    def verdict(self) -> LoopVerdict:
        """Compute the verdict for the most recently recorded round.

        Mutates escalation state: a NUDGE verdict flips the one-way "already
        nudged" flag, so a second call without an intervening `record()`
        will escalate NUDGE -> STOP. Call exactly once per recorded round.
        """
        if not self._cfg.enabled:
            return LoopVerdict.NONE
        reason = self._tripped_reason()
        if reason is None:
            return LoopVerdict.NONE
        self._last_signal = reason
        if not self._nudged:
            self._nudged = True
            return LoopVerdict.NUDGE
        return LoopVerdict.STOP

    def last_signal(self) -> str:
        return self._last_signal
