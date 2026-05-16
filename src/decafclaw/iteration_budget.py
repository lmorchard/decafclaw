"""Iteration budget for the agent loop with one grace turn at exhaustion."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IterationBudget:
    """Tracks remaining tool-call iterations with one-shot grace-turn semantics.

    The agent loop calls ``consume()`` at the top of each iteration. When it
    returns False, the budget is exhausted; the loop then calls
    ``grace_turn()`` once to gate a single no-tools final LLM call so the
    model gets a chance to wrap up instead of being cut off mid-conversation.

    ``refund()`` gives back one iteration for "free" retries — calls that
    produced nothing usable (e.g. an empty LLM response) and shouldn't count
    against the user-visible budget.
    """

    remaining: int
    _grace_used: bool = False

    def consume(self) -> bool:
        """Try to consume one iteration. Returns True if budget remained
        (and was decremented), False if already exhausted (no change)."""
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True

    def refund(self) -> None:
        """Give back one iteration. Always increments — caller decides
        whether a refund is warranted (e.g. empty-response retry)."""
        self.remaining += 1

    def grace_turn(self) -> bool:
        """Return True the first time it's called, False on every subsequent
        call. Used to gate the one-shot grace LLM call after ``consume()``
        returns False."""
        if self._grace_used:
            return False
        self._grace_used = True
        return True
