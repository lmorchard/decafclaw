"""Per-turn loop-breaker: detects autonomous tool-call thrash and escalates
a diagnostic nudge, then a hard stop. Pure/deterministic — no agent or LLM
imports; driven by TurnRunner. See docs/loop-breaker.md (#598)."""

import dataclasses
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
        self._counts: dict[str, _Offender] = {}
        self._recent_errors: list[bool] = []  # rolling is_error flags
        self._total_errors = 0                # monotonic; never trimmed
        self._errors_at_last_trip = 0         # watermark for the error signal
        self._trips = 0
        self._last_signal = ""

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    def record(self, calls) -> None:
        """Record one iteration's tool calls.

        calls: iterable of (tool_name, fingerprint, is_error).
        """
        for tool_name, fp, is_error in calls:
            entry = self._counts.get(fp)
            if entry is None:
                entry = self._counts[fp] = _Offender(tool_name=tool_name)
            entry.tool_name = tool_name
            entry.count += 1
            self._recent_errors.append(bool(is_error))
            if is_error:
                self._total_errors += 1
        # Trim to a rolling window of the last N results.
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
            self._last_signal = (
                f"called {offender.tool_name} {offender.count}× with the same args"
            )
        elif self._fresh_error_surge():
            self._errors_at_last_trip = self._total_errors
            errs = sum(self._recent_errors)
            self._last_signal = (
                f"{errs} of the last {len(self._recent_errors)} tool results were errors"
            )
        else:
            return LoopVerdict.NONE
        self._trips += 1
        return LoopVerdict.NUDGE if self._trips == 1 else LoopVerdict.STOP

    def last_signal(self) -> str:
        return self._last_signal
