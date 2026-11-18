"""Reflection core extension (#1508).

Owns self-critique policy for candidate final responses: whether to judge, the
judge call and its retry budget, the critique injected on rejection, the
visibility record archived for the UI, and the per-turn ``reflection_turn``
telemetry (#409).

The turn loop dispatches ``TurnLifecycle.CANDIDATE_RESPONSE`` when an LLM
response with no tool calls is about to become the final answer (see
``decafclaw.candidate_response``). This extension returns a verdict: deliver the
text, or reject it with the messages that make the runner iterate again.

Gates:

- ``extensions.reflection.enabled: false`` yanks the hook entirely — candidate
  responses return with no reflection work at all.
- ``reflection.enabled`` keeps the hook registered but delivers every candidate
  unjudged, and publishes no telemetry.
- ``ctx.skip_reflection`` (child agents), cancelled turns, empty content, and an
  exhausted retry budget skip the judge and deliver as-is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from decafclaw import reflection as reflection_engine
from decafclaw.candidate_response import CandidateResponse, CandidateVerdict
from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.reflection import ReflectionResult
from decafclaw.reflection_metrics import classify_outcome, response_delta

from . import BaseExtension, register_extension_type

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

_STATE_KEY = "reflection"

_EXHAUSTED_SUFFIX = (
    "\n\n---\n*I'm not confident in this answer. Try switching to a more capable model in the web UI model picker.*"
)


@dataclass
class ReflectionState:
    """Per-turn reflection bookkeeping, carried in ``CandidateResponse.state``.

    ``retries`` drives the retry budget; the token counters and
    ``first_response`` accumulate across rounds for the ``reflection_turn``
    emit. ``exhausted`` survives the skip path nulling ``last_result``.
    """

    retries: int = 0
    last_result: ReflectionResult | None = None
    first_response: str | None = None
    judge_prompt_tokens: int = 0
    judge_completion_tokens: int = 0
    exhausted: bool = False
    last_critique: str = ""


def should_reflect(ctx: "Context", config: Any, content: str, retries: int) -> bool:
    """Return True when this candidate is eligible for a judge call."""
    if not config.reflection.enabled:
        return False
    if retries >= config.reflection.max_retries:
        return False
    if ctx.skip_reflection:
        return False
    if not content or not content.strip():
        return False
    if ctx.cancelled and ctx.cancelled.is_set():
        return False
    return True


class ReflectionExtension(BaseExtension):
    """Judge candidate final responses and request retries with critique."""

    def __init__(self, options: dict[str, Any], config: Any):
        super().__init__(name="reflection")
        self.options = options or {}
        self.config = config

    def enable(self, bus: EventBus) -> list[str]:
        self.subscription_ids = [bus.subscribe(TurnLifecycle.CANDIDATE_RESPONSE, self.on_candidate_response)]
        return self.subscription_ids

    async def on_candidate_response(self, ctx: "Context", candidate: CandidateResponse) -> CandidateVerdict | None:
        """Evaluate the candidate and settle it: deliver, or retry."""
        config = getattr(ctx, "config", None) or self.config
        state = candidate.state.get(_STATE_KEY)
        if not isinstance(state, ReflectionState):
            state = ReflectionState()
            candidate.state[_STATE_KEY] = state

        content = candidate.content
        log.debug(
            "Reflection check: enabled=%s, retries=%d/%d, skip=%s, has_content=%s",
            config.reflection.enabled,
            state.retries,
            config.reflection.max_retries,
            ctx.skip_reflection,
            bool(content),
        )

        if not should_reflect(ctx, config, content, state.retries):
            return await self._deliver_unjudged(ctx, config, state, content)

        result = await self._evaluate(ctx, config, candidate, state)
        state.last_result = result
        return await self._apply_verdict(ctx, config, state, content, result)

    async def _deliver_unjudged(
        self, ctx: "Context", config: Any, state: ReflectionState, content: str
    ) -> CandidateVerdict:
        """Skip-path verdict: append the escalation nudge when the budget is spent."""
        reflected_out = (
            state.retries >= config.reflection.max_retries
            and state.last_result is not None
            and not state.last_result.passed
        )
        # Telemetry (#409): persist the exhausted verdict before nulling
        # last_result, so the per-turn emit can bucket it as loop_exhausted.
        if reflected_out:
            state.exhausted = True
        state.last_result = None
        if reflected_out:
            content += _EXHAUSTED_SUFFIX
        await self._emit_metrics(ctx, config, state, content)
        return CandidateVerdict(reject=False, text=content)

    async def _evaluate(
        self, ctx: "Context", config: Any, candidate: CandidateResponse, state: ReflectionState
    ) -> ReflectionResult:
        """Build the judge inputs, call the judge, publish ``reflection_result``."""
        tool_summary = reflection_engine.build_tool_summary(
            candidate.history,
            candidate.turn_start_index,
            max_result_len=config.reflection.max_tool_result_len,
        )
        prior_turn_summary = reflection_engine.build_prior_turn_summary(
            candidate.history,
            candidate.turn_start_index - 1,
            max_turns=3,
            max_result_len=200,
        )
        judge_user_message = candidate.user_message
        if candidate.attachments:
            att_desc = ", ".join(f"{a.get('filename', '?')} ({a.get('mime_type', '?')})" for a in candidate.attachments)
            judge_user_message += f"\n\n[User attached files: {att_desc}]"
        judge_agent_response = (
            "\n\n".join(
                part for part in [*candidate.accumulated_text_parts, candidate.content] if part and part.strip()
            )
            or candidate.content
        )
        # Telemetry (#409): capture the first response the judge sees and
        # accumulate judge token cost across rounds.
        if state.first_response is None:
            state.first_response = candidate.content

        result = await reflection_engine.evaluate_response(
            config,
            judge_user_message,
            judge_agent_response,
            tool_summary,
            prior_turn_summary=prior_turn_summary,
            retrieved_context=candidate.retrieved_context_text,
        )
        state.judge_prompt_tokens += result.prompt_tokens
        state.judge_completion_tokens += result.completion_tokens
        if result.critique:
            state.last_critique = result.critique

        log.info(
            "Reflection result: passed=%s, critique=%s, error=%s",
            result.passed,
            result.critique[:200] if result.critique else "",
            result.error[:100] if result.error else "",
        )
        await ctx.publish(
            "reflection_result",
            passed=result.passed,
            critique=result.critique,
            raw_response=result.raw_response,
            retry_number=state.retries + 1,
            error=result.error,
        )
        return result

    async def _apply_verdict(
        self,
        ctx: "Context",
        config: Any,
        state: ReflectionState,
        content: str,
        result: ReflectionResult,
    ) -> CandidateVerdict:
        """Apply the judge's verdict. Fail-open errors are treated as PASS."""
        if not result.passed and not result.error:
            log.info(
                "Reflection failed (retry %d/%d): %s",
                state.retries + 1,
                config.reflection.max_retries,
                result.critique[:200],
            )
            critique_msg = {
                "role": "user",
                "source": "reflection_retry",
                "content": (
                    "[reflection] Your previous response may not fully "
                    "address the user's request.\n"
                    f"Feedback: {result.critique}\n"
                    "Please try again, addressing the feedback above."
                ),
            }
            state.retries += 1
            return CandidateVerdict(reject=True, retry_messages=(critique_msg,))

        await self._emit_metrics(ctx, config, state, content)
        return CandidateVerdict(
            reject=False,
            text=content,
            archive_messages=self._visibility_records(config, state),
        )

    def _visibility_records(self, config: Any, state: ReflectionState) -> tuple[dict, ...]:
        """Reflection rows for the transcript UI at the configured visibility."""
        result = state.last_result
        if result is None:
            return ()
        visibility = config.reflection.visibility
        should_archive = visibility == "debug" or (visibility == "visible" and not result.passed)
        if not should_archive:
            return ()
        detail = (
            result.raw_response or result.critique or ("Response passed evaluation" if result.passed else "No details")
        )
        label = "reflection: PASS" if result.passed else f"reflection: retry {state.retries}"
        return ({"role": "reflection", "tool": label, "content": detail},)

    async def _emit_metrics(self, ctx: "Context", config: Any, state: ReflectionState, final_content: str) -> None:
        """Publish one ``reflection_turn`` event per judge-eligible turn (#409).

        Fail-open — measurement only, never affects the turn.
        """
        try:
            if not config.reflection.enabled or ctx.skip_reflection:
                return  # disabled or child agent — not a reflection turn
            if ctx.cancelled and ctx.cancelled.is_set():
                return
            last_error = state.last_result.error if state.last_result else ""
            outcome = classify_outcome(
                first_response=state.first_response,
                last_error=last_error,
                retry_count=state.retries,
                exhausted=state.exhausted,
                final_content=final_content,
            )
            if outcome is None:
                return
            first = state.first_response or final_content
            char_delta, overlap = response_delta(first, final_content)
            await ctx.publish(
                "reflection_turn",
                conv_id=ctx.conv_id,
                outcome=outcome,
                retry_count=state.retries,
                judge_prompt_tokens=state.judge_prompt_tokens,
                judge_completion_tokens=state.judge_completion_tokens,
                char_delta=char_delta,
                overlap_ratio=overlap,
                critique_fingerprint=state.last_critique[:120],
            )
        except Exception as exc:  # fail-open
            log.debug("reflection metrics emit failed: %s", exc)


register_extension_type("reflection", ReflectionExtension, default_enabled=True)
