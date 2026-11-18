"""Candidate final response contract for ``TurnLifecycle.CANDIDATE_RESPONSE``.

The turn loop dispatches these hooks when an LLM response has no tool calls and
is about to become the turn's final answer (#1508). Hooks run sequentially in
registration order and the first non-``None`` verdict settles the candidate:

- deliver the text unchanged,
- deliver a replacement text (and extra archive records), or
- reject it and make the runner iterate again with ``retry_messages``.

Delivery stays in core: the hook decides *whether* the candidate is acceptable,
and the runner owns transcript mutation, archival, compaction, and final
delivery. A raising hook is logged and skipped, so a broken critic cannot fail
a turn.

Hooks that accumulate per-turn state across retries (a retry budget, judge token
totals) keep it in ``CandidateResponse.state`` under a key they own. Core
creates a fresh dict for every turn.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .events import TurnLifecycle

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


@dataclass
class CandidateResponse:
    """A candidate final response plus the turn state a critic may need.

    ``history`` is the live transcript; hooks read it to summarize the turn and
    must not mutate it. The runner applies ``CandidateVerdict.retry_messages``
    itself so history, the outgoing message list, and the archive stay in step.
    """

    content: str
    history: list[dict]
    turn_start_index: int
    accumulated_text_parts: list[str]
    user_message: str
    attachments: list[dict]
    retrieved_context_text: str
    # Per-turn scratch for stateful hooks. Core resets it for every turn.
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateVerdict:
    """Settled outcome of a ``CANDIDATE_RESPONSE`` hook.

    ``reject=True`` makes the runner append the rejected candidate as an
    assistant message (with its thinking/replay metadata), then
    ``retry_messages``, to history, the outgoing message list, and the archive
    before running another iteration. ``archive_messages`` is applied only on
    delivery; reflection uses it for its visibility record once the retry loop
    settles.
    """

    reject: bool
    text: str | None = None
    retry_messages: tuple[dict, ...] = ()
    archive_messages: tuple[dict, ...] = ()


async def dispatch_candidate_response(ctx: "Context", candidate: CandidateResponse) -> CandidateVerdict | None:
    """Run candidate hooks until one returns a verdict; None means deliver as-is."""
    for hook in ctx.get_interceptors(TurnLifecycle.CANDIDATE_RESPONSE):
        try:
            verdict = hook(ctx, candidate)
            if inspect.iscoroutine(verdict):
                verdict = await verdict
        except Exception as exc:  # fail-open; CancelledError is a BaseException and still propagates
            log.warning("CANDIDATE_RESPONSE hook %s failed: %s", hook, exc)
            continue
        if verdict is not None:
            return verdict
    return None
