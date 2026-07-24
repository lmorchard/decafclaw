"""The agent loop — the core of DecafClaw.

This is where the interesting stuff happens. The loop:
1. Receives a message (from stdin or Mattermost)
2. Builds a prompt with system prompt + history + tools
3. Calls the LLM
4. If the LLM wants to use tools, executes them and loops
5. Returns the final text response
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re as _re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context_composer import ComposedContext
    from .reflection import ReflectionResult

from .archive import append_message
from .compaction import compact_history
from .config_types import LoopBreakerConfig
from .context_cleanup import clear_old_tool_results
from .context_composer import ComposerMode, ContextComposer
from .iteration_budget import IterationBudget
from .llm import call_llm
from .loop_breaker import LoopBreaker, LoopVerdict, fingerprint
from .media import EndTurnConfirm, ToolResult, WidgetInputPause, extract_workspace_media
from .persistence import read_skill_data, read_skills_state, write_skill_data, write_skills_state
from .reflection_metrics import classify_outcome, response_delta
from .skills import activate_always_loaded
from .tool_definitions import build_tool_list, refresh_dynamic_tools
from .tool_execution import execute_tool_calls

_TASK_MODE_TO_COMPOSER: dict[str, ComposerMode] = {
    "heartbeat": ComposerMode.HEARTBEAT,
    "scheduled": ComposerMode.SCHEDULED,
}

log = logging.getLogger(__name__)

# Track background tasks to prevent GC and surface exceptions


def _conv_id(ctx) -> str:
    """Get conversation ID from context."""
    return ctx.conv_id or ctx.channel_id or "unknown"


def _archive(ctx, msg) -> None:
    """Archive a message, logging errors but never raising."""
    if ctx.skip_archive:
        return
    wrote = False
    try:
        append_message(ctx.config, _conv_id(ctx), msg)
        wrote = True
    except Exception as e:
        log.error(f"Archive write failed: {e}")
    # Mark the conversation so a subsequent CancelledError handler
    # doesn't double-archive the partial body (issue #491). Only fires
    # for assistant messages with actual body content that actually
    # landed in the archive — tool_call-only assistant rows don't count
    # as "delivered text" for cancel bookkeeping, and we don't want a
    # failed write to make the manager think the partial is durable.
    if (wrote
            and msg.get("role") == "assistant"
            and msg.get("content")
            and ctx.manager is not None):
        try:
            ctx.manager.note_partial_assistant_archived(_conv_id(ctx))
        except Exception as exc:
            log.debug("note_partial_assistant_archived failed: %s", exc)



async def _maybe_compact(ctx, config, history, prompt_tokens) -> None:
    """Run the lightweight tool-result clear pass, then trigger
    full compaction if the token budget is exceeded.

    The clear pass is cheap (in-memory string surgery, no LLM call)
    so it runs every iteration. Full compaction only fires when
    history is large enough to justify the LLM summarization cost.
    """
    # Lightweight tier first — see #298 and docs/context-composer.md.
    try:
        delta = clear_old_tool_results(history, config)
        ctx.composer.cleanup_cleared_count += delta.cleared_count
        ctx.composer.cleanup_cleared_bytes += delta.cleared_bytes
        if delta.cleared_count:
            log.info(
                "Tool-result clear: %d message(s), %d bytes reclaimed",
                delta.cleared_count, delta.cleared_bytes,
            )
    except Exception:
        # Cleanup is fail-open. Use log.exception so the traceback
        # actually surfaces in logs — without it we lose the diagnostic
        # signal we'd need to fix recurring failures.
        log.exception("Tool-result clearing failed")

    log.info(f"Compaction check: prompt_tokens={prompt_tokens}, "
             f"threshold={config.compaction.max_tokens}")
    if prompt_tokens and prompt_tokens > config.compaction.max_tokens:
        log.info(f"Token budget exceeded ({prompt_tokens} > {config.compaction.max_tokens}), "
                 f"triggering compaction")
        try:
            await compact_history(ctx, history)
            # After compaction, summarized content replaces originals —
            # allow previously-injected pages to be re-injected if relevant,
            # and reset cleanup accounting since the new in-memory view
            # supersedes the previous one.
            ctx.composer.injected_paths.clear()
            ctx.composer.cleanup_cleared_count = 0
            ctx.composer.cleanup_cleared_bytes = 0
        except Exception as e:
            log.error(f"Compaction failed: {e}")


def _extract_call_signatures(tool_calls, messages):
    """Map each tool_call to (tool_name, fingerprint, is_error) using the
    tool-result messages just appended by execute_tool_calls. Errors are
    tool-role messages whose content starts with '[error' (see
    tool_execution.py:ToolResult(text="[error: ...]")).
    """
    results_by_id = {
        m.get("tool_call_id"): (m.get("content") or "")
        for m in messages if m.get("role") == "tool"
    }
    out = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name") or tc.get("name", "")
        raw_args = fn.get("arguments", tc.get("arguments", ""))
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (TypeError, ValueError):
            args = raw_args
        content = results_by_id.get(tc.get("id"), "")
        is_error = content.lstrip().startswith("[error")
        out.append((name, fingerprint(name, args), is_error))
    return out


# -- Agent turn helpers --------------------------------------------------------


def _check_cancelled(ctx, history):
    """Check if the agent turn has been cancelled. Returns ToolResult or None.

    Appends an in-memory marker to history so the iteration loop's
    accumulated-text / reflection logic sees the cancel notice, but does
    NOT write to the archive. The canonical cancel-archive write happens
    in ConversationManager.run() once the loop unwinds. Notifies the
    manager that the agent observed the cancellation so the normal-
    completion path persists the marker even when this helper returns
    cleanly via _Final (issue #491).
    """
    if ctx.cancelled and ctx.cancelled.is_set():
        log.info("Agent turn cancelled by user")
        msg = "[Agent turn cancelled by user]"
        final_msg = {"role": "assistant", "content": msg}
        history.append(final_msg)
        _note_cancel_observed(ctx)
        return ToolResult(text=msg)
    return None


def _note_cancel_observed(ctx) -> None:
    """Tell the manager the agent observed the cancel signal cleanly,
    so the normal-completion path persists the marker. No-op when no
    manager is attached (e.g. unit tests calling the helper directly).
    """
    if ctx.manager is None:
        return
    try:
        ctx.manager.note_cancel_observed_by_agent(_conv_id(ctx))
    except Exception as exc:
        log.debug("note_cancel_observed_by_agent failed: %s", exc)


@dataclass(frozen=True)
class ReflectionOutcome:
    """Result of evaluating a candidate final response.

    Replaces the 4-tuple return shape of the old _handle_reflection.
    `text` is None when the caller should retry (critique already
    injected into history/messages by the helper). When `should_retry`
    is False, `text` is the response to deliver (with optional
    exhaustion-escalation suffix appended in the skip path).

    `reflection_retries` and `last_reflection` are mutated on the call
    sites' state directly — they don't appear in this return type.
    """
    text: str | None
    should_retry: bool


def _should_reflect(ctx, config, content: str, reflection_retries: int) -> bool:
    """Check whether reflection should run on this response."""
    if not config.reflection.enabled:
        return False
    if reflection_retries >= config.reflection.max_retries:
        return False
    if ctx.skip_reflection:
        return False
    if not content or not content.strip():
        return False
    if ctx.cancelled and ctx.cancelled.is_set():
        return False
    return True


async def _handle_widget_input_pause(ctx, signal: WidgetInputPause
                                     ) -> str | None:
    """Pause the agent turn on an input widget and resume with the
    user's answer formatted as a synthetic user-message string.

    Returns the inject-string to append to history. Returns None if
    the pause infra isn't available, OR if the user cancels the turn
    while the widget is pending — both cases route the outer loop to
    end the turn cleanly without injecting a synthetic answer.
    """
    from .confirmations import (
        ConfirmationAction,
        ConfirmationRequest,
    )
    from .widget_input import (
        default_inject_message,
        pending_callbacks,
    )

    request_confirmation = ctx.request_confirmation
    if request_confirmation is None:
        log.warning(
            "WidgetInputPause received but ctx has no request_confirmation "
            "— cannot pause, ending turn gracefully")
        # Drop the registered callback so it can't leak across turns.
        pending_callbacks.pop(signal.tool_call_id, None)
        return None

    request = ConfirmationRequest(
        action_type=ConfirmationAction.WIDGET_RESPONSE,
        action_data=signal.widget_payload,
        tool_call_id=signal.tool_call_id,
        timeout=None,  # widget responses have no deadline
    )

    # Race the confirmation await against ctx.cancelled so a "stop turn"
    # click while the widget is pending unblocks the loop. The
    # confirmation infra's await on confirmation_event doesn't observe
    # cancel_event natively.
    cancel_event = ctx.cancelled
    confirm_task = asyncio.create_task(request_confirmation(request))
    response = None
    cancelled_during_pause = False
    try:
        if cancel_event is not None:
            cancel_task = asyncio.create_task(cancel_event.wait())
            done, _pending = await asyncio.wait(
                [confirm_task, cancel_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            cancel_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await cancel_task
            if confirm_task in done:
                response = confirm_task.result()
            else:
                # Cancel won. Tear down the pause cleanly: cancel the
                # confirm await and clear the pending confirmation in
                # the manager so the archive doesn't keep showing a
                # stale pending widget after the cancelled turn. We
                # use cancel_pending_confirmation rather than
                # respond_to_confirmation because the latter would
                # dispatch the recovery handler and inject a synthetic
                # "user responded with: ..." message — but the user
                # didn't respond, they cancelled.
                cancelled_during_pause = True
                confirm_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await confirm_task
                manager = ctx.manager
                conv_id = ctx.conv_id
                if manager and conv_id:
                    try:
                        await manager.cancel_pending_confirmation(conv_id)
                    except Exception as exc:
                        log.debug(
                            "Failed to clear pending widget confirmation "
                            "on cancel for %s: %s", conv_id, exc)
        else:
            response = await confirm_task
    finally:
        # Always remove the callback entry so a crashed await doesn't
        # leak it across later turns. pop() with a default is a no-op
        # if the entry was already consumed.
        callback = pending_callbacks.pop(signal.tool_call_id, None)

    if cancelled_during_pause or response is None:
        return None

    if callback is not None:
        try:
            return callback(response.data)
        except Exception as exc:
            log.warning(
                "widget on_response callback raised for %s: %s",
                signal.tool_call_id, exc)
            return default_inject_message(response.data)
    return default_inject_message(response.data)


async def _handle_end_turn_confirm(ctx, action: EndTurnConfirm) -> bool:
    """Handle an EndTurnConfirm action via the event bus.

    Publishes a confirmation request and waits for the user to click
    Approve or Deny. Returns True if approved, False if denied.
    Uses the same event pattern as request_confirmation in
    tools/confirmation.py.
    """
    from .tools.confirmation import request_confirmation
    result = await request_confirmation(
        ctx,
        tool_name="end_turn_confirm",
        command=action.message or "Review",
        message=action.message,
        timeout=300,
        approve_label=action.approve_label,
        deny_label=action.deny_label,
    )
    return result.get("approved", False)


async def _call_llm_with_events(ctx, config, messages, tools,
                                model_name=None,
                                llm_url=None, llm_model=None,
                                llm_api_key=None) -> dict:
    """Call the LLM with event publishing for progress tracking.

    Accepts model_name (new path) or llm_url/llm_model/llm_api_key (legacy).
    """
    llm_kwargs: dict = {}
    if model_name:
        llm_kwargs["model_name"] = model_name
    if llm_url:
        llm_kwargs["llm_url"] = llm_url
    if llm_model:
        llm_kwargs["llm_model"] = llm_model
    if llm_api_key:
        llm_kwargs["llm_api_key"] = llm_api_key

    iteration = ctx._current_iteration
    await ctx.publish("llm_start", iteration=iteration)
    from .config import resolve_streaming
    if resolve_streaming(config, ctx.active_model):
        from .llm import call_llm_streaming
        on_chunk = ctx.on_stream_chunk
        cancel_event = ctx.cancelled
        response = await call_llm_streaming(
            config, messages, tools=tools, on_chunk=on_chunk,
            cancel_event=cancel_event, **llm_kwargs
        )
    else:
        cancel_event = ctx.cancelled
        if cancel_event:
            llm_task = asyncio.create_task(
                call_llm(config, messages, tools=tools, **llm_kwargs))
            cancel_task = asyncio.create_task(cancel_event.wait())
            done, _ = await asyncio.wait(
                [llm_task, cancel_task], return_when=asyncio.FIRST_COMPLETED
            )
            cancel_task.cancel()
            if llm_task not in done:
                llm_task.cancel()
                try:
                    await llm_task
                except (asyncio.CancelledError, Exception):
                    pass
                response = {"content": "", "tool_calls": None, "role": "assistant", "usage": {}}
            else:
                response = llm_task.result()
        else:
            response = await call_llm(config, messages, tools=tools, **llm_kwargs)
    await ctx.publish("llm_end", iteration=iteration,
                      content=response.get("content"),
                      has_tool_calls=bool(response.get("tool_calls")))
    return response


# -- Turn setup helpers ---------------------------------------------------------


async def _setup_turn_state(ctx, config, history) -> dict[str, str]:
    """Restore persisted skill/model state and resolve model overrides.

    Handles:
    - Skill restoration from sidecar (persisted activated skills + skill_data)
    - Auto-activation of always-loaded bundled skills
    - Active model restoration from archive
    - Model resolution to LLM config overrides

    Returns model_override dict (may be empty if no override needed).
    """
    from .tools.skill_tools import restore_skills  # deferred: circular dep

    # Restore previously-activated skills from the sidecar (survives restarts).
    # Merge with any skills already on ctx (e.g. set by Mattermost in-session state).
    conv_id = ctx.conv_id or ctx.channel_id
    if conv_id:
        persisted = read_skills_state(config, conv_id)
        existing = set(ctx.skills.activated)
        if persisted - existing:
            ctx.skills.activated = existing | persisted
        # Restore skill_data (e.g. vault base path) from sidecar
        persisted_data = read_skill_data(config, conv_id)
        existing_data = ctx.skills.data
        ctx.skills.data = {**persisted_data, **existing_data}
    await restore_skills(ctx)

    # Auto-activate always-loaded skills. Trusted tiers (bundled /
    # admin / extra) are eligible; workspace skills already had the
    # always-loaded flag stripped at discovery, so the helper also
    # defends against a workspace skill that somehow slipped through.
    await activate_always_loaded(ctx)

    # Restore active model from archive (scan reverse for last valid model message).
    if not ctx.active_model and conv_id:
        from .archive import read_archive
        for msg in reversed(read_archive(config, conv_id)):
            if msg.get("role") == "model":
                name = msg.get("content", "")
                if name and name in config.model_configs:
                    ctx.active_model = name
                    break

    # Build model override for the LLM call
    model_override: dict[str, str] = {}
    if ctx.active_model and ctx.active_model in config.model_configs:
        model_override = {"model_name": ctx.active_model}
        log.info("Agent turn: model=%s", ctx.active_model)
    elif config.default_model:
        model_override = {"model_name": config.default_model}
        log.info("Agent turn: model=%s (default)", config.default_model)
    else:
        log.info("Agent turn: model=%s (legacy config.llm)", config.llm.model)

    return model_override


class IterationOutcome:
    """Tagged-union return type from TurnRunner._run_iteration.

    Two variants: _Continue (loop again) and _Final (return this
    ToolResult from the turn). Cancellation collapses into _Final
    since the outer loop treats it identically.
    """


@dataclass(frozen=True)
class _Continue(IterationOutcome):
    """Loop again — used for tool-call iterations, retries, widget
    injection, EndTurnConfirm-approved continuation."""


@dataclass(frozen=True)
class _Final(IterationOutcome):
    """Turn is done; return this ToolResult."""
    result: "ToolResult"


# -- Main agent turn -----------------------------------------------------------


@dataclass
class TurnRunner:
    """Owns the mutable state of a single agent turn.

    State that was local variables in the original run_agent_turn
    becomes fields here. State written through ctx helpers
    (ctx.tokens, ctx.skills, ctx.history) stays on ctx.

    Single-use: do not call run() twice on the same instance.
    """
    ctx: Any
    config: Any
    history: list
    user_message: str
    archive_text: str
    attachments: list[dict] | None

    messages: list = field(default_factory=list)
    deferred_msg: dict | None = None
    prompt_tokens: int = 0
    empty_retries: int = 0
    reflection_retries: int = 0
    last_reflection: "ReflectionResult | None" = None
    # Reflection telemetry (#409) — captured across rounds for the per-turn
    # metrics emit. first_response is the round-0 response the judge saw;
    # exhausted survives _reflection_skip nulling last_reflection.
    reflection_first_response: "str | None" = None
    reflection_judge_prompt_tokens: int = 0
    reflection_judge_completion_tokens: int = 0
    reflection_exhausted: bool = False
    reflection_last_critique: str = ""
    turn_start_index: int = 0
    accumulated_text_parts: list[str] = field(default_factory=list)
    model_override: dict[str, str] = field(default_factory=dict)
    retrieved_context_text: str = ""
    composed: "ComposedContext | None" = None
    composer: "ContextComposer | None" = None
    budget: IterationBudget = field(
        default_factory=lambda: IterationBudget(remaining=0),
    )
    loop_breaker: LoopBreaker = field(
        default_factory=lambda: LoopBreaker(LoopBreakerConfig()),
    )

    async def run(self) -> "ToolResult":
        """Process a single user message through the agent loop."""
        self.ctx.history = self.history

        self.model_override = await _setup_turn_state(self.ctx, self.config, self.history)

        try:
            await self._compose()

            self.prompt_tokens = 0
            self.empty_retries = 0
            self.reflection_retries = 0
            self.last_reflection = None  # last ReflectionResult, for archiving after final response
            self.reflection_first_response = None
            self.reflection_judge_prompt_tokens = 0
            self.reflection_judge_completion_tokens = 0
            self.reflection_exhausted = False
            self.reflection_last_critique = ""

            self.accumulated_text_parts = []  # text from iterations that also had tool calls
            self.budget = IterationBudget(
                remaining=self.config.agent.max_tool_iterations,
            )
            self.loop_breaker = LoopBreaker(self.config.loop_breaker)

            iteration = 0
            while self.budget.consume():
                outcome = await self._run_iteration(iteration)
                if isinstance(outcome, _Final):
                    return outcome.result
                iteration += 1

            # Budget exhausted — try one grace turn before giving up.
            if self.budget.grace_turn():
                grace_result = await self._run_grace_turn()
                if grace_result is not None:
                    return grace_result
            return await self._finalize_max_iterations()

        finally:
            await self._write_diagnostics()

    async def _compose(self) -> None:
        """Build the composed context, archive composer-added messages,
        initialize message-tracking state on self.

        Note: ``skip_vault_retrieval`` is handled directly by the composer,
        not via mode — HEARTBEAT/SCHEDULED skip wiki too, which isn't
        always desired when only memory should be skipped.
        """
        if self.ctx.is_child:
            composer_mode = ComposerMode.CHILD_AGENT
        elif self.ctx.task_mode in _TASK_MODE_TO_COMPOSER:
            composer_mode = _TASK_MODE_TO_COMPOSER[self.ctx.task_mode]
        else:
            composer_mode = ComposerMode.INTERACTIVE

        self.composer = ContextComposer(state=self.ctx.composer)
        self.composed = await self.composer.compose(
            self.ctx, self.user_message, self.history,
            mode=composer_mode, attachments=self.attachments,
        )
        self.messages = self.composed.messages
        self.ctx.messages = self.messages
        self.retrieved_context_text = self.composed.retrieved_context_text

        for msg in self.composed.messages_to_archive:
            if self.ctx.task_mode == "background_wake" and msg.get("role") == "user":
                archive_msg: dict = {
                    "role": "wake_trigger",
                    "content": msg.get("content", ""),
                }
                _archive(self.ctx, archive_msg)
            elif self.archive_text and msg.get("role") == "user":
                archive_msg = {"role": "user", "content": self.archive_text}
                if msg.get("attachments"):
                    archive_msg["attachments"] = msg["attachments"]
                _archive(self.ctx, archive_msg)
            else:
                _archive(self.ctx, msg)

        if (len(self.messages) > 1
                and self.messages[1].get("role") == "system"
                and self.composed.deferred_tools):
            self.deferred_msg = self.messages[1]
        else:
            self.deferred_msg = None

        self.turn_start_index = len(self.history)

    async def _run_iteration(self, iteration: int) -> IterationOutcome:
        """Run one LLM iteration: cancel-check, tool refresh, deferred-list
        injection, the LLM call, and dispatch to tool-calls or no-tool-calls
        handler. Returns _Continue or _Final."""
        cancelled = _check_cancelled(self.ctx, self.history)
        if cancelled:
            return _Final(result=cancelled)

        log.debug(f"Agent iteration {iteration + 1}")
        self.ctx._current_iteration = iteration + 1

        refresh_dynamic_tools(self.ctx)
        all_tools, deferred_text = build_tool_list(self.ctx)

        if deferred_text:
            new_msg = {"role": "system", "content": deferred_text}
            if self.deferred_msg is not None and self.deferred_msg in self.messages:
                idx = self.messages.index(self.deferred_msg)
                self.messages[idx] = new_msg
            else:
                self.messages.insert(1, new_msg)
            self.deferred_msg = new_msg
        elif self.deferred_msg is not None and self.deferred_msg in self.messages:
            self.messages.remove(self.deferred_msg)
            self.deferred_msg = None

        response = await _call_llm_with_events(
            self.ctx, self.config, self.messages, all_tools,
            **self.model_override,
        )

        usage = response.get("usage")
        if usage:
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            cached_tokens = usage.get("cached_tokens", 0)
            self.ctx.tokens.total_prompt += prompt_tokens
            self.ctx.tokens.total_completion += completion_tokens
            self.ctx.tokens.last_prompt = prompt_tokens
            self.ctx.tokens.total_cached_prompt += cached_tokens
            self.ctx.tokens.last_cached_prompt = cached_tokens
            self.prompt_tokens = prompt_tokens
            log.debug("Turn token usage: prompt=%s, cached=%s, completion=%s",
                      prompt_tokens, cached_tokens, completion_tokens)
            # composer is set by _compose() before the loop; guard is for the type checker
            if self.composer is not None:
                self.composer.record_actuals(prompt_tokens, completion_tokens,
                                             cached_tokens)

        tool_calls = response.get("tool_calls")
        if tool_calls:
            return await self._handle_tool_calls(response, tool_calls)

        return await self._handle_no_tool_calls(response)

    async def _handle_tool_calls(
        self, response: dict, tool_calls: list,
    ) -> IterationOutcome:
        """Append assistant tool-call message, execute tools, dispatch
        on end-turn signals (widget pause, EndTurnConfirm, end_turn=True).

        Returns _Continue to loop again, or _Final(result) to end the turn.
        """
        iter_content = response.get("content")
        assistant_msg = {"role": "assistant", "content": iter_content}
        assistant_msg["tool_calls"] = tool_calls
        self.history.append(assistant_msg)
        self.messages.append(assistant_msg)
        _archive(self.ctx, assistant_msg)

        if iter_content:
            self.accumulated_text_parts.append(iter_content)
            await self.ctx.publish("text_before_tools", text=iter_content)

        cancelled, end_turn_signal = await execute_tool_calls(
            self.ctx, tool_calls, self.history, self.messages,
        )
        if cancelled:
            return _Final(result=cancelled)

        if isinstance(end_turn_signal, WidgetInputPause):
            inject_content = await _handle_widget_input_pause(
                self.ctx, end_turn_signal,
            )
            if inject_content is None:
                end_turn_signal = True
            else:
                synthetic = {
                    "role": "user",
                    "source": "widget_response",
                    "content": inject_content,
                }
                self.history.append(synthetic)
                self.messages.append(synthetic)
                _archive(self.ctx, synthetic)
                return _Continue()

        if isinstance(end_turn_signal, EndTurnConfirm):
            log.info("EndTurnConfirm — making presentation LLM call before confirmation")
            present_response = await _call_llm_with_events(
                self.ctx, self.config, self.messages, [],
                **self.model_override,
            )
            present_content = present_response.get("content") or ""
            present_msg = {"role": "assistant", "content": present_content}
            self.history.append(present_msg)
            self.messages.append(present_msg)
            _archive(self.ctx, present_msg)

            log.info("EndTurnConfirm — requesting confirmation")
            approved = await _handle_end_turn_confirm(self.ctx, end_turn_signal)
            if approved:
                log.info("EndTurnConfirm approved — continuing agent loop")
                if end_turn_signal.on_approve:
                    if asyncio.iscoroutinefunction(end_turn_signal.on_approve):
                        await end_turn_signal.on_approve()
                    else:
                        end_turn_signal.on_approve()
                note = f"[User approved: {end_turn_signal.message or 'review'}]"
                self.history.append({"role": "user", "content": note})
                self.messages.append({"role": "user", "content": note})
                return _Continue()
            else:
                log.info("EndTurnConfirm denied — ending turn")
                if end_turn_signal.on_deny:
                    if asyncio.iscoroutinefunction(end_turn_signal.on_deny):
                        await end_turn_signal.on_deny()
                    else:
                        end_turn_signal.on_deny()
                deny_label = end_turn_signal.deny_label or "denied"
                note = f"[User selected '{deny_label}'. Ask what they'd like changed.]"
                self.history.append({"role": "user", "content": note})
                self.messages.append({"role": "user", "content": note})
                end_turn_signal = True

        if end_turn_signal:
            log.info("Tool signalled end_turn — making final no-tools LLM call")
            final_response = await _call_llm_with_events(
                self.ctx, self.config, self.messages, [],
                **self.model_override,
            )
            content = final_response.get("content") or ""
            final_msg = {"role": "assistant", "content": content}
            self.history.append(final_msg)
            _archive(self.ctx, final_msg)

            return _Final(result=self._extract_workspace_media(content))

        # Loop-breaker: only checked on the normal continue path — a genuine
        # end-turn signal (widget pause / EndTurnConfirm / end_turn=True)
        # already returned above and takes precedence over the breaker.
        # Gated on `enabled` so a disabled breaker is a true no-op — nothing
        # is extracted or recorded, not just the verdict skipped.
        if self.loop_breaker.enabled:
            sigs = _extract_call_signatures(tool_calls, self.messages)
            self.loop_breaker.record(sigs)
            verdict = self.loop_breaker.verdict()
            if verdict is LoopVerdict.NUDGE:
                # Role "user", not "system": models weight user-role directives
                # more heavily than system-role for mid-turn corrections, and a
                # weakly-followed "STOP and diagnose" defeats the point of the
                # nudge. Matches _run_grace_turn's deliberate user-role choice.
                #
                # Ephemeral — appended to self.messages only, never archived or
                # added to self.history. Archiving would re-surface it via
                # restore_history on a restart/reload (role "user" is equally an
                # LLM role that gets resurrected), permanently polluting context
                # on all later turns.
                nudge = {
                    "role": "user",
                    "content": (
                        f"[loop-breaker] You {self.loop_breaker.last_signal()} without "
                        "progress. STOP repeating it. Switch to root-cause diagnosis: "
                        "read the relevant logs, build a minimal repro, and re-check the "
                        "contract/interface before any further edits."
                    ),
                }
                self.messages.append(nudge)
                await self.ctx.publish("loop_breaker", action="nudge",
                                       reason=self.loop_breaker.last_signal())
            elif verdict is LoopVerdict.STOP:
                await self.ctx.publish("loop_breaker", action="stop",
                                       reason=self.loop_breaker.last_signal())
                return _Final(result=await self._finalize_loop_break())

        return _Continue()

    async def _handle_no_tool_calls(self, response: dict) -> IterationOutcome:
        """Process a no-tool-calls LLM response. Handles empty-retry,
        reflection, archive of last_reflection, and compaction trigger.
        Returns _Continue (retry) or _Final(result) (deliver response)."""
        content = response.get("content") or ""
        # If cancellation fired mid-stream and the provider returned an
        # empty body, don't archive an empty assistant message — the
        # cancel propagates to the manager which writes the canonical
        # marker (plus any partial accumulated server-side). Issue #491.
        if (not content and self.ctx.cancelled
                and self.ctx.cancelled.is_set()):
            _note_cancel_observed(self.ctx)
            return _Final(result=ToolResult(text=""))
        if not content:
            if self.empty_retries < 1:
                self.empty_retries += 1
                # Empty response — refund the iteration so the retry doesn't
                # eat budget (the LLM produced nothing usable).
                self.budget.refund()
                log.warning("LLM returned empty response, retrying")
                return _Continue()
            log.warning("LLM returned empty content with no tool calls (after retry)")

        log.debug("Reflection check: enabled=%s, retries=%d/%d, skip=%s, has_content=%s",
                   self.config.reflection.enabled, self.reflection_retries,
                   self.config.reflection.max_retries, self.ctx.skip_reflection, bool(content))
        outcome = await self._handle_reflection(content)
        if outcome.should_retry:
            return _Continue()
        assert outcome.text is not None  # invariant: should_retry=False implies text is not None
        content = outcome.text

        final_msg = {"role": "assistant", "content": content}
        self.history.append(final_msg)
        _archive(self.ctx, final_msg)

        if self.last_reflection is not None:
            visibility = self.config.reflection.visibility
            r = self.last_reflection
            should_archive = (
                visibility == "debug"
                or (visibility == "visible" and not r.passed)
            )
            if should_archive:
                detail = r.raw_response or r.critique or (
                    "Response passed evaluation" if r.passed else "No details")
                label = ("reflection: PASS" if r.passed
                         else f"reflection: retry {self.reflection_retries}")
                _archive(self.ctx, {"role": "reflection", "tool": label,
                                    "content": detail})

        await self._emit_reflection_metrics(content)
        await _maybe_compact(self.ctx, self.config, self.history, self.prompt_tokens)
        return _Final(result=self._extract_workspace_media(content))

    async def _emit_reflection_metrics(self, final_content: str) -> None:
        """Publish one reflection_turn telemetry event per judge-eligible turn
        (#409). Fail-open — measurement only, never affects the turn."""
        try:
            config = self.config
            if not config.reflection.enabled or self.ctx.skip_reflection:
                return  # disabled or child agent — not a reflection turn
            if self.ctx.cancelled and self.ctx.cancelled.is_set():
                return
            last_error = self.last_reflection.error if self.last_reflection else ""
            outcome = classify_outcome(
                first_response=self.reflection_first_response,
                last_error=last_error,
                retry_count=self.reflection_retries,
                exhausted=self.reflection_exhausted,
                final_content=final_content,
            )
            if outcome is None:
                return
            first = self.reflection_first_response or final_content
            char_delta, overlap = response_delta(first, final_content)
            await self.ctx.publish(
                "reflection_turn",
                conv_id=self.ctx.conv_id,
                outcome=outcome,
                retry_count=self.reflection_retries,
                judge_prompt_tokens=self.reflection_judge_prompt_tokens,
                judge_completion_tokens=self.reflection_judge_completion_tokens,
                char_delta=char_delta,
                overlap_ratio=overlap,
                critique_fingerprint=self.reflection_last_critique[:120],
            )
        except Exception as exc:  # fail-open
            log.debug("reflection metrics emit failed: %s", exc)

    async def _handle_reflection(self, content: str) -> ReflectionOutcome:
        """Thin orchestrator. Mutates self.reflection_retries and
        self.last_reflection; returns ReflectionOutcome."""
        if not _should_reflect(
            self.ctx, self.config, content, self.reflection_retries,
        ):
            return self._reflection_skip(content)
        result = await self._reflection_evaluate(content)
        self.last_reflection = result
        return self._reflection_apply_verdict(content, result)

    def _reflection_skip(self, content: str) -> ReflectionOutcome:
        """Reflection-did-not-run branch. Appends escalation suffix
        on exhaustion; clears self.last_reflection."""
        reflection_exhausted = (
            self.reflection_retries >= self.config.reflection.max_retries
            and self.last_reflection is not None
            and not self.last_reflection.passed
        )
        # Telemetry (#409): persist the exhausted verdict before nulling
        # last_reflection, so the per-turn emit can bucket it as loop_exhausted.
        if reflection_exhausted:
            self.reflection_exhausted = True
        self.last_reflection = None
        if reflection_exhausted:
            content += (
                "\n\n---\n*I'm not confident in this answer. "
                "Try switching to a more capable model in the web UI model picker.*"
            )
        return ReflectionOutcome(text=content, should_retry=False)

    async def _reflection_evaluate(self, content: str) -> "ReflectionResult":
        """Build summaries + attachment annotation + accumulated-text
        concat, call evaluate_response, publish reflection_result event."""
        from .reflection import (
            build_prior_turn_summary,
            build_tool_summary,
            evaluate_response,
        )

        tool_summary = build_tool_summary(
            self.history, self.turn_start_index,
            max_result_len=self.config.reflection.max_tool_result_len,
        )
        prior_turn_summary = build_prior_turn_summary(
            self.history, self.turn_start_index - 1,
            max_turns=3,
            max_result_len=200,
        )
        judge_user_message = self.user_message
        if self.attachments:
            att_desc = ", ".join(
                f"{a.get('filename', '?')} ({a.get('mime_type', '?')})"
                for a in self.attachments
            )
            judge_user_message += f"\n\n[User attached files: {att_desc}]"
        judge_agent_response = "\n\n".join(
            part for part in [*self.accumulated_text_parts, content]
            if part and part.strip()
        ) or content
        # Telemetry (#409): capture the first response the judge sees and
        # accumulate judge token cost across rounds.
        if self.reflection_first_response is None:
            self.reflection_first_response = content

        result = await evaluate_response(
            self.config, judge_user_message, judge_agent_response, tool_summary,
            prior_turn_summary=prior_turn_summary,
            retrieved_context=self.retrieved_context_text,
        )
        self.reflection_judge_prompt_tokens += result.prompt_tokens
        self.reflection_judge_completion_tokens += result.completion_tokens
        if result.critique:
            self.reflection_last_critique = result.critique

        log.info("Reflection result: passed=%s, critique=%s, error=%s",
                 result.passed, result.critique[:200] if result.critique else "",
                 result.error[:100] if result.error else "")
        await self.ctx.publish("reflection_result",
            passed=result.passed,
            critique=result.critique,
            raw_response=result.raw_response,
            retry_number=self.reflection_retries + 1,
            error=result.error)
        return result

    def _reflection_apply_verdict(
        self, content: str, result: "ReflectionResult",
    ) -> ReflectionOutcome:
        """Apply the judge's verdict. On fail-with-real-critique,
        append failed_msg + critique_msg, archive, bump retries.
        Fail-open errors treated as PASS."""
        if not result.passed and not result.error:
            log.info("Reflection failed (retry %d/%d): %s",
                     self.reflection_retries + 1,
                     self.config.reflection.max_retries,
                     result.critique[:200])
            failed_msg = {"role": "assistant", "content": content}
            self.history.append(failed_msg)
            self.messages.append(failed_msg)
            _archive(self.ctx, failed_msg)

            critique_msg = {
                "role": "user",
                "content": (
                    "[reflection] Your previous response may not fully "
                    "address the user's request.\n"
                    f"Feedback: {result.critique}\n"
                    "Please try again, addressing the feedback above."
                ),
            }
            self.history.append(critique_msg)
            self.messages.append(critique_msg)
            _archive(self.ctx, critique_msg)

            self.reflection_retries += 1
            return ReflectionOutcome(text=None, should_retry=True)

        return ReflectionOutcome(text=content, should_retry=False)

    async def _run_grace_turn(self) -> "ToolResult | None":
        """One-shot final LLM call after budget exhaustion. Appends a
        directive user-role nudge, calls the LLM with tools=[], archives
        the result. (User-role chosen over system-role because models
        weight user messages more heavily mid-turn — matches the
        reflection-critique pattern.)

        Returns the final ToolResult on success, or None on exception so the
        caller falls back to ``_finalize_max_iterations`` (always-something
        contract — the user never sees a turn that produces no output)."""
        log.info("Iteration budget exhausted — making grace-turn LLM call")
        # Bump _current_iteration so llm_start/llm_end events for the grace
        # call don't reuse the last tool-enabled iteration's number — event
        # consumers (UI iteration counters, diagnostics) key off this.
        self.ctx._current_iteration += 1
        # User-role nudge (models weight user-role > system in mid-turn
        # context, matching the reflection critique pattern). Directive
        # phrasing — without "STOP" / "Do not continue", models keep trying
        # to do the original task and bail mid-stream when they realize they
        # can't call a tool.
        grace_note = {
            "role": "user",
            "content": (
                "[iteration_limit] You have reached your tool iteration "
                "budget. STOP. Do not continue the task you were given — "
                "no more tool calls are available. Reply with a brief "
                "closing message (1-3 sentences) that summarizes what you "
                "accomplished so far and tells the user you've hit your "
                "iteration limit."
            ),
        }
        self.messages.append(grace_note)
        try:
            response = await _call_llm_with_events(
                self.ctx, self.config, self.messages, [],
                **self.model_override,
            )
        except Exception as exc:
            log.warning(
                f"Grace-turn LLM call failed: {exc!r} — falling back to notice",
            )
            return None

        content = response.get("content") or ""
        if not content:
            # Grace turn produced no usable output — fall back to the notice
            # path so the user always sees *something* (always-something
            # contract). _finalize_max_iterations will archive the notice.
            log.warning("Grace-turn LLM call returned empty content — falling back to notice")
            return None
        final_msg = {"role": "assistant", "content": content}
        self.history.append(final_msg)
        _archive(self.ctx, final_msg)
        await _maybe_compact(
            self.ctx, self.config, self.history, self.prompt_tokens,
        )
        return self._extract_workspace_media(content)

    async def _finalize_max_iterations(self) -> "ToolResult":
        """Hit max iterations without a final response. Preserve any
        accumulated text from tool-call iterations and append a notice."""
        limit_note = (
            f"\n\n[Agent reached max tool iterations "
            f"({self.config.agent.max_tool_iterations}) without a final response]"
        )
        accumulated = "\n\n".join(self.accumulated_text_parts)
        msg = accumulated + limit_note if accumulated else limit_note.strip()
        final_msg = {"role": "assistant", "content": msg}
        self.history.append(final_msg)
        _archive(self.ctx, final_msg)
        await _maybe_compact(
            self.ctx, self.config, self.history, self.prompt_tokens,
        )
        return ToolResult(text=msg)

    async def _finalize_loop_break(self) -> "ToolResult":
        """Loop-breaker hard-stop: end the turn with a summary of the thrash
        and a diagnostic next step, preserving any accumulated text."""
        note = (
            f"\n\n[loop-breaker] Stopped: you {self.loop_breaker.last_signal()} "
            "without progress. Next: read the relevant logs, build a minimal "
            "repro, and re-check the contract before retrying."
        )
        accumulated = "\n\n".join(self.accumulated_text_parts)
        msg = accumulated + note if accumulated else note.strip()
        final_msg = {"role": "assistant", "content": msg}
        self.history.append(final_msg)
        _archive(self.ctx, final_msg)
        await _maybe_compact(
            self.ctx, self.config, self.history, self.prompt_tokens,
        )
        return ToolResult(text=msg)

    def _extract_workspace_media(self, content: str) -> "ToolResult":
        """Extract workspace:// refs only for channels that need it.

        Mattermost strips refs and uploads files; web/terminal render them
        in-place. Returns ToolResult with media when extraction applies,
        otherwise just text.
        """
        handler = self.ctx.media_handler
        should_extract = (handler is None or handler.strips_workspace_refs)
        if should_extract:
            cleaned_text, workspace_media = extract_workspace_media(
                content or "", self.config.workspace_path,
            )
            if workspace_media:
                return ToolResult(text=cleaned_text, media=workspace_media)
        return ToolResult(text=content or "")

    async def _write_diagnostics(self) -> None:
        """Persist context diagnostics + skill state on any turn-exit path.

        Fail-open: any failure logs at DEBUG and is swallowed so the
        finally block never raises through to callers.
        """
        conv_id = self.ctx.conv_id or self.ctx.channel_id

        if self.composed is not None and self.composer is not None and conv_id:
            try:
                from .context_composer import write_context_sidecar
                diagnostics = self.composer.build_diagnostics(self.config, self.composed)
                write_context_sidecar(self.config, conv_id, diagnostics)
            except Exception as exc:
                log.debug("context sidecar write failed for %s: %s", conv_id, exc)

        if conv_id:
            try:
                activated = self.ctx.skills.activated
                if activated:
                    write_skills_state(self.config, conv_id, activated)
                skill_data = self.ctx.skills.data
                if skill_data:
                    write_skill_data(self.config, conv_id, skill_data)
            except Exception as exc:
                log.debug("skill state persistence failed for %s: %s", conv_id, exc)


async def run_agent_turn(ctx, user_message: str, history: list,
                         archive_text: str = "",
                         attachments: list[dict] | None = None) -> "ToolResult":
    """Process a single user message through the agent loop.

    Public entry point. Constructs a TurnRunner and runs it.
    """
    runner = TurnRunner(
        ctx=ctx, config=ctx.config, history=history,
        user_message=user_message, archive_text=archive_text,
        attachments=attachments,
    )
    return await runner.run()

