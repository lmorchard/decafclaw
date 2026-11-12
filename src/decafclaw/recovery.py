"""Bounded recovery policy for LLM turn failures (#1144).

Chooses the next agent-loop action after a recoverable LLM failure — retry
the bounded attempt, continue from partial output, or stop with an explicit
uncertain outcome — without ever blindly replaying a request whose output
may already be committed.
"""

from dataclasses import dataclass

RECOVERY_RETRY = "retry"
RECOVERY_CONTINUE = "continue"
RECOVERY_STOP = "stop"


@dataclass(frozen=True)
class RecoveryState:
    """Recovery-relevant state for one failed LLM turn (#1144).

    ``phase`` classifies where the failure happened:

      - ``pre_output`` — nothing committed yet; a retry is safe.
      - ``partial_text`` — visible partial text committed; continuation is
        preferred over a blind replay.
      - ``completed_tools`` — tool calls completed; completed tool results
        must be preserved, only provably-unexecuted calls may be retried.
      - ``uncertain`` — execution state could not be proven; stop with an
        explicit uncertain outcome instead of guessing.
    """

    phase: str = "pre_output"
    committed_output: bool = False
    executed_tools: bool = False
    retry_exhausted: bool = False


def decide_recovery_retry(state: RecoveryState, *, cancelled: bool = False) -> str:
    """Return the bounded recovery action for a failed LLM turn.

    Precedence: user cancellation > uncertain outcome > retry (pre-output,
    nothing committed, retry budget not yet exhausted) > continue (partial
    text committed). When the provider adapter's bounded retry budget is
    already spent with nothing committed, the policy stops as an explicit
    uncertain outcome instead of re-entering the retry path. Completed tool
    runs are preserved; only provably-unexecuted calls may be retried.
    """
    if cancelled or state.phase == "uncertain":
        return RECOVERY_STOP
    if state.phase == "partial_text" and state.committed_output:
        return RECOVERY_CONTINUE
    if state.phase == "pre_output" and not state.committed_output:
        if state.retry_exhausted:
            # Bounded retry budget spent with nothing committed — stop as an
            # explicit uncertain outcome rather than re-entering the retry path
            # on a turn that already burned its attempts.
            return RECOVERY_STOP
        return RECOVERY_RETRY
    # ``completed_tools``, partial-without-commit, and anything else: preserve
    # committed state and do not guess at unexecuted-call safety.
    return RECOVERY_STOP
