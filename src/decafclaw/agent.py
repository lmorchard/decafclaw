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
import functools
import json
import logging
import re as _re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context_composer import ComposedContext
    from .reflection import ReflectionResult

from .archive import append_message
from .compaction import compact_history
from .context_cleanup import clear_old_tool_results
from .context_composer import ComposerMode, ContextComposer
from .llm import call_llm
from .media import EndTurnConfirm, ToolResult, WidgetInputPause, extract_workspace_media
from .persistence import read_skill_data, read_skills_state, write_skill_data, write_skills_state
from .tools import TOOL_DEFINITIONS, execute_tool
from .tools.search_tools import SEARCH_TOOL_DEFINITIONS
from .tools.tool_registry import (
    build_deferred_list_text,
    classify_tools,
    get_fetched_tools,
)

_TASK_MODE_TO_COMPOSER: dict[str, ComposerMode] = {
    "heartbeat": ComposerMode.HEARTBEAT,
    "scheduled": ComposerMode.SCHEDULED,
}

# Cache preloaded skill definitions by config id, avoiding Config mutation
_skill_def_cache: dict[int, list] = {}


def invalidate_skill_cache(config) -> None:
    """Clear the cached skill definitions for a config. Call after refresh_skills."""
    _skill_def_cache.pop(id(config), None)

log = logging.getLogger(__name__)

# Track background tasks to prevent GC and surface exceptions


def _conv_id(ctx) -> str:
    """Get conversation ID from context."""
    return ctx.conv_id or ctx.channel_id or "unknown"


def _resolve_attachments(config, message: dict) -> dict:
    """Transform a message with attachments into multimodal content for the LLM.

    Messages without attachments pass through unchanged. The archive stores
    plain text + attachment metadata; this builds the ephemeral content array.
    """
    atts = message.get("attachments")
    if not atts:
        return message

    from .attachments import read_attachment_base64

    content_parts: list[dict] = []
    text = message.get("content", "")
    if text:
        content_parts.append({"type": "text", "text": text})

    for att in atts:
        b64_data = read_attachment_base64(config, att)
        if b64_data is None:
            content_parts.append({
                "type": "text",
                "text": f"[attachment missing: {att.get('filename', '?')}]",
            })
            continue

        mime = att.get("mime_type", "application/octet-stream")
        # TODO(#137): MIME type is client-supplied — validate with magic bytes
        # server-side to prevent non-images from being base64-embedded
        if mime.startswith("image/"):
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64_data}"},
            })
        else:
            # Non-image: represent as a textual placeholder only
            # (binary data is not sent to the LLM)
            content_parts.append({
                "type": "text",
                "text": f"[file: {att.get('filename', '?')} ({mime})]",
            })

    # Return message with multimodal content, stripping attachments key
    result = {k: v for k, v in message.items() if k != "attachments"}
    result["content"] = content_parts
    return result


def _archive(ctx, msg) -> None:
    """Archive a message, logging errors but never raising."""
    if ctx.skip_archive:
        return
    try:
        append_message(ctx.config, _conv_id(ctx), msg)
    except Exception as e:
        log.error(f"Archive write failed: {e}")



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


# -- Agent turn helpers --------------------------------------------------------


def _check_cancelled(ctx, history):
    """Check if the agent turn has been cancelled. Returns ToolResult or None."""
    if ctx.cancelled and ctx.cancelled.is_set():
        log.info("Agent turn cancelled by user")
        msg = "[Agent turn cancelled by user]"
        final_msg = {"role": "assistant", "content": msg}
        history.append(final_msg)
        _archive(ctx, final_msg)
        return ToolResult(text=msg)
    return None


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


def _refresh_dynamic_tools(ctx) -> None:
    """Call dynamic tool providers to refresh skill tools for this turn.

    Skills that export get_tools(ctx) have their tools and definitions
    replaced each turn based on current state (e.g., project phase).
    Collects all possible tool names from providers, removes stale entries,
    then re-adds the current set.
    """
    providers = ctx.tools.dynamic_providers
    if not providers:
        return

    # Collect names from previous turn + this turn so we can remove stale entries
    names_to_remove: set[str] = set()
    for skill_name in providers:
        names_to_remove.update(ctx.tools.dynamic_provider_names.get(skill_name, set()))

    # Call each provider for this turn's tools
    provider_results: list[tuple[str, dict, list]] = []
    for skill_name, get_tools_fn in providers.items():
        try:
            tools, tool_defs = get_tools_fn(ctx)
            names_to_remove.update(tools.keys())
            ctx.tools.dynamic_provider_names[skill_name] = set(tools.keys())
            provider_results.append((skill_name, tools, tool_defs))
        except Exception as e:
            # Fail-open: remove this provider's stale tools. If the model
            # tries to call a removed tool, it gets a "tool not found" error.
            log.warning(f"Dynamic tool provider for '{skill_name}' failed: {e}")
            ctx.tools.dynamic_provider_names[skill_name] = set()

    # Remove all dynamic-provider tools (old + new names) from extra
    ctx.tools.extra = {
        name: fn for name, fn in ctx.tools.extra.items()
        if name not in names_to_remove
    }
    ctx.tools.extra_definitions = [
        td for td in ctx.tools.extra_definitions
        if td.get("function", {}).get("name") not in names_to_remove
    ]

    # Re-add the current turn's tools from each provider
    for skill_name, tools, tool_defs in provider_results:
        ctx.tools.extra.update(tools)
        ctx.tools.extra_definitions.extend(tool_defs)


def _collect_all_tool_defs(ctx) -> list:
    """Gather all available tool definitions (core + skill + MCP + extra).

    Does NOT apply allowed_tools filter — returns the full unfiltered set
    so classification can see everything before deciding what to defer.
    """
    # Skill tools first — activated skill tools get priority positioning
    # so the model sees them before the long tail of core tools
    all_tools = list(ctx.tools.extra_definitions) + list(TOOL_DEFINITIONS)

    # Pre-load tool definitions from discovered skills (stable tool list).
    # Cached by config id to avoid re-executing tools.py every iteration.
    config_id = id(ctx.config)
    _cached = _skill_def_cache.get(config_id)
    if _cached is None:
        _cached = []
        for skill_info in ctx.config.discovered_skills:
            if skill_info.has_native_tools:
                try:
                    from .tools.skill_tools import _load_native_tools
                    _, tool_defs, _ = _load_native_tools(skill_info)
                    _cached.extend(tool_defs)
                except Exception as e:
                    log.warning(f"Failed to pre-load skill '{skill_info.name}' tools: {e}")
        _skill_def_cache[config_id] = _cached

    preloaded_names = {t.get("function", {}).get("name") for t in all_tools}
    for td in _cached:
        name = td.get("function", {}).get("name")
        if name and name not in preloaded_names:
            all_tools.append(td)
            preloaded_names.add(name)

    from .mcp_client import get_registry
    mcp_registry = get_registry()
    if mcp_registry:
        all_tools = all_tools + mcp_registry.get_tool_definitions()

    return all_tools


def _build_tool_list(ctx) -> tuple[list, str | None]:
    """Build the tool list, with optional deferred mode.

    Returns (tool_definitions, deferred_text) where deferred_text is
    None if all tools fit in the budget, or a system prompt block
    listing deferred tools when the budget is exceeded.
    """
    all_defs = _collect_all_tool_defs(ctx)
    fetched = get_fetched_tools(ctx)
    # Skill tools (from activated skills) should never be deferred
    skill_tool_names = {
        td.get("function", {}).get("name", "")
        for td in ctx.tools.extra_definitions
    }
    # Pre-emptive matches populated by ContextComposer at turn start;
    # reused across iterations so mid-turn reclassification stays consistent.
    active, deferred = classify_tools(
        all_defs, ctx.config, fetched, skill_tool_names,
        preempt_matches=ctx.tools.preempt_matches,
    )

    # Apply allowed_tools filter to the active set only
    allowed = ctx.tools.allowed
    if allowed is not None:
        active = [
            t for t in active
            if t.get("function", {}).get("name") in allowed
        ]

    if not deferred:
        return active, None

    # Deferred mode: set the pool on ctx and add tool_search
    ctx.tools.deferred_pool = deferred
    active = active + SEARCH_TOOL_DEFINITIONS

    # Build deferred list text for system prompt
    core_names = {td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS}
    deferred_text = build_deferred_list_text(deferred, core_names=core_names)

    return active, deferred_text


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


@functools.lru_cache(maxsize=128)
def _media_placeholder_pattern(filename: str) -> _re.Pattern:
    """Build a regex to find the placeholder for a given filename."""
    return _re.compile(
        r"\[file attached: " + _re.escape(filename) + r"[^\]]*\]"
    )


async def _process_tool_media(ctx, result: ToolResult) -> list[str]:
    """Process media items on a tool result — save/upload and replace placeholders.

    For handlers returning workspace_ref: replaces placeholder text with markdown refs.
    For handlers returning file_id: collects file_ids for caller to attach.

    Returns list of file_ids (for Mattermost attachment), empty for other channels.
    Clears result.media after processing.
    """
    if not result.media:
        return []

    handler = ctx.media_handler
    if handler is None:
        log.warning(f"No media handler — {len(result.media)} media item(s) not delivered")
        result.media.clear()
        return []

    conv_id = ctx.conv_id or ctx.channel_id or "unknown"
    file_ids = []

    for item in result.media:
        filename = item.get("filename", "unknown")
        content_type = item.get("content_type", "application/octet-stream")
        data = item.get("data", b"")

        try:
            save_result = await handler.save_media(conv_id, filename, data, content_type)
        except Exception as e:
            log.warning(f"Failed to save media {filename}: {e}")
            continue

        if save_result.workspace_ref:
            pattern = _media_placeholder_pattern(filename)
            if content_type.startswith("image/"):
                replacement = f"![{filename}]({save_result.workspace_ref})"
            else:
                replacement = f"[{filename}]({save_result.workspace_ref})"
            new_text, count = pattern.subn(replacement, result.text, count=1)
            if count > 0:
                result.text = new_text
            else:
                # No placeholder — append ref so the media is discoverable
                result.text = result.text.rstrip() + "\n" + replacement
        if save_result.file_id:
            file_ids.append(save_result.file_id)

    result.media.clear()
    return file_ids


async def _execute_single_tool(call_ctx, tc, semaphore):
    """Execute one tool call. Returns (tool_msg dict, end_turn flag).

    Designed to run concurrently — uses its own forked ctx so
    current_tool_call_id doesn't race with other calls.
    Media is processed per-tool-call via _process_tool_media().
    """
    tool_call_id = tc["id"]
    fn_name = tc["function"]["name"]
    try:
        fn_args = json.loads(tc["function"]["arguments"])
    except json.JSONDecodeError as e:
        log.error(f"Malformed tool call arguments for {fn_name}: {e}")
        fn_args = {}

    log.info(f"Tool call: {fn_name}({fn_args})")

    result = ToolResult(text=f"[error: {fn_name} did not complete]")
    async with semaphore:
        try:
            await call_ctx.publish("tool_start", tool=fn_name, args=fn_args,
                                   tool_call_id=tool_call_id)
            result = await execute_tool(call_ctx, fn_name, fn_args)
            log.debug(f"Tool result [{fn_name}]: {result.text[:200]}...")

            # Process media per-tool-call (save/upload, replace placeholders)
            file_ids = await _process_tool_media(call_ctx, result)
            if file_ids:
                await call_ctx.publish("tool_media_uploaded",
                                       tool=fn_name,
                                       file_ids=file_ids,
                                       tool_call_id=tool_call_id)
        except asyncio.CancelledError:
            result = ToolResult(text=f"[cancelled: {fn_name}]")
        except Exception as e:
            log.error(f"Tool call {fn_name} failed: {e}", exc_info=True)
            result = ToolResult(text=f"[error executing {fn_name}: {e}]")
        finally:
            widget_payload = _resolve_widget(fn_name, result, tool_call_id)
            publish_kwargs = {
                "tool": fn_name,
                "result_text": result.text,
                "display_text": getattr(result, "display_text", None),
                "display_short_text": getattr(
                    result, "display_short_text", None),
                "media": result.media or [],
                "tool_call_id": tool_call_id,
            }
            if widget_payload is not None:
                publish_kwargs["widget"] = widget_payload
            await call_ctx.publish("tool_end", **publish_kwargs)

    content = result.text
    if result.data is not None:
        try:
            content += "\n\n```json\n" + json.dumps(result.data, indent=2) + "\n```"
        except (TypeError, ValueError) as e:
            log.warning(f"Failed to serialize ToolResult.data for {fn_name}: {e}")
            content += "\n\n[structured data omitted: serialization error]"
    tool_msg = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }
    if result.display_short_text:
        tool_msg["display_short_text"] = result.display_short_text
    if widget_payload is not None:
        tool_msg["widget"] = widget_payload
    _archive(call_ctx, tool_msg)
    return tool_msg, result.end_turn


def _resolve_widget(fn_name: str, result: ToolResult,
                    tool_call_id: str = "") -> dict | None:
    """Validate result.widget against the registry and return a
    serializable payload, or None if no widget / validation fails.

    Phase-2 side effects for input widgets (``accepts_input=True``):

    - If ``end_turn`` is falsy, strip the widget (input widgets require
      the turn to pause).
    - If ``end_turn`` is an ``EndTurnConfirm``, drop the confirm (widget
      pause wins) and set ``end_turn=True``.
    - Register the ``on_response`` callback in
      ``widget_input.pending_callbacks`` keyed by ``tool_call_id``.
    - Promote ``result.end_turn`` to a ``WidgetInputPause`` sentinel
      that the agent loop detects and routes to the pause path.
    """
    widget = getattr(result, "widget", None)
    if widget is None:
        return None
    from .widgets import get_widget_registry
    registry = get_widget_registry()
    if registry is None:
        log.warning(
            "tool %s returned a widget but widget registry is not "
            "initialized; stripping", fn_name)
        result.widget = None
        return None
    ok, err = registry.validate(widget.widget_type, widget.data)
    if not ok:
        log.warning(
            "tool %s widget %r failed validation: %s — stripping",
            fn_name, widget.widget_type, err)
        result.widget = None
        return None
    desc = registry.get(widget.widget_type)
    target = widget.target
    if target not in ("inline", "canvas"):
        log.warning(
            "tool %s widget %r has unknown target %r — stripping",
            fn_name, widget.widget_type, target)
        result.widget = None
        return None
    if desc is not None and target not in desc.modes:
        log.warning(
            "tool %s widget %r used target %r not in declared modes %r"
            " — stripping",
            fn_name, widget.widget_type, target, desc.modes)
        result.widget = None
        return None
    # Apply per-widget server-side normalization (e.g. iframe_sandbox CSP
    # wrapping). Mutate widget.data so downstream consumers — archive,
    # canvas state, WS event — all see the same normalized shape.
    normalized = registry.normalize(widget.widget_type, widget.data)
    widget.data = normalized
    payload = {
        "widget_type": widget.widget_type,
        "target": target,
        "data": normalized,
    }

    # Phase 2: input-widget enforcement + pause-signal promotion.
    if desc is not None and desc.accepts_input:
        # Rule: input widget requires end_turn truthy (True or
        # EndTurnConfirm; widget-pause wins over EndTurnConfirm).
        if not result.end_turn:
            log.warning(
                "tool %s emitted input widget %r without end_turn=True "
                "— stripping (input widgets must pause the turn)",
                fn_name, widget.widget_type)
            result.widget = None
            return None
        if isinstance(result.end_turn, EndTurnConfirm):
            log.warning(
                "tool %s emitted input widget %r alongside EndTurnConfirm "
                "— dropping EndTurnConfirm (widget-pause takes priority)",
                fn_name, widget.widget_type)
        # Register the callback for live-path pickup.
        if widget.on_response is not None and tool_call_id:
            from .widget_input import pending_callbacks
            pending_callbacks[tool_call_id] = widget.on_response
        # Promote end_turn to the pause sentinel.
        result.end_turn = WidgetInputPause(
            tool_call_id=tool_call_id,
            widget_payload=payload,
        )

    return payload


async def _execute_tool_calls(ctx, tool_calls, history, messages):
    """Execute tool calls concurrently, add results to history.

    Returns (ToolResult, False) if cancelled, (None, end_turn_signal) otherwise.
    end_turn_signal is False, True, or an EndTurnConfirm action.
    """
    cancelled = _check_cancelled(ctx, history)
    if cancelled:
        return cancelled, False

    semaphore = asyncio.Semaphore(ctx.config.agent.max_concurrent_tools)

    # Fork ctx per tool call so concurrent tools don't race on current_tool_call_id
    tasks = []
    for tc in tool_calls:
        call_ctx = ctx.fork_for_tool_call(tc["id"])
        task = asyncio.create_task(
            _execute_single_tool(call_ctx, tc, semaphore),
            name=f"tool-{tc['function']['name']}-{tc['id'][:8]}",
        )
        tasks.append(task)

    # Cancel watcher: if the cancel event fires, cancel all in-flight tasks
    cancel_event = ctx.cancelled

    async def _cancel_watcher():
        if cancel_event:
            await cancel_event.wait()
            for t in tasks:
                t.cancel()

    watcher = asyncio.create_task(_cancel_watcher()) if cancel_event else None

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if watcher:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass

    # Check if we were cancelled during execution
    cancelled = _check_cancelled(ctx, history)
    if cancelled:
        return cancelled, False

    # Collect results in original call order (gather preserves order).
    # Priority among end-turn signals in a single batch:
    #   WidgetInputPause > EndTurnConfirm > True
    # Only one pause/end signal wins per batch.
    end_turn_signal: bool | EndTurnConfirm | WidgetInputPause = False
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            # Task was cancelled or failed — gather with return_exceptions.
            # _execute_single_tool normally handles errors internally,
            # so this only fires for unexpected failures (e.g. CancelledError
            # from the cancel watcher).
            err_type = type(result).__name__
            err_text = str(result) or err_type
            tool_msg = {
                "role": "tool",
                "tool_call_id": tool_calls[i]["id"],
                "content": f"[error: {err_text}]",
            }
            history.append(tool_msg)
            messages.append(tool_msg)
            _archive(ctx, tool_msg)
        else:
            tool_msg, end_turn = result
            if isinstance(end_turn, WidgetInputPause):
                end_turn_signal = end_turn  # Widget pause wins over all
            elif isinstance(end_turn, EndTurnConfirm):
                if not isinstance(end_turn_signal, WidgetInputPause):
                    end_turn_signal = end_turn
            elif end_turn and not isinstance(
                    end_turn_signal, (EndTurnConfirm, WidgetInputPause)):
                end_turn_signal = True
            history.append(tool_msg)
            messages.append(tool_msg)

    return None, end_turn_signal


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

    # Auto-activate always-loaded skills (bundled only — trust boundary)
    from .skills import _BUNDLED_SKILLS_DIR
    bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
    discovered = config.discovered_skills
    for skill_info in discovered:
        if not skill_info.always_loaded or skill_info.name in ctx.skills.activated:
            continue
        if not Path(skill_info.location).resolve().is_relative_to(bundled_dir):
            continue  # only bundled skills can be always-loaded
        from .tools.skill_tools import activate_skill_internal
        try:
            await activate_skill_internal(ctx, skill_info)
            log.debug(f"Auto-activated always-loaded skill '{skill_info.name}'")
        except Exception as e:
            log.error(f"Failed to auto-activate skill '{skill_info.name}': {e}")

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


# -- Wiki context helpers ------------------------------------------------------

_WIKI_MENTION_RE = _re.compile(r'@\[\[([^\]]+)\]\]')


def _parse_wiki_references(
    user_message: str, wiki_page: str | None = None,
) -> list[dict]:
    """Parse @[[PageName]] mentions and optional open wiki page.

    Returns a list of dicts: {"page": name, "source": "mention"|"open_page"}.
    Does NOT resolve or read pages — caller filters against already-injected
    pages first, then resolves only the ones needed.
    """
    seen: set[str] = set()
    results: list[dict] = []

    # Parse @[[...]] mentions from message text
    for match in _WIKI_MENTION_RE.finditer(user_message):
        raw = match.group(1).strip()
        # Handle @[[target|display]] — extract target before pipe
        page_name = raw.split("|")[0].strip()
        if page_name and page_name not in seen:
            seen.add(page_name)
            results.append({"page": page_name, "source": "mention"})

    # Add open wiki page from web UI (if not already mentioned)
    if wiki_page and wiki_page not in seen:
        results.append({"page": wiki_page, "source": "open_page"})

    return results


def _read_wiki_page(config, page_name: str) -> str | None:
    """Resolve and read a wiki page. Returns content or None. Fail-open."""
    from .skills.vault.tools import resolve_page

    resolved = resolve_page(config, page_name)
    if not resolved:
        return None
    try:
        return resolved.read_text()
    except (OSError, UnicodeError):
        log.warning("Failed to read wiki page %s at %s", page_name, resolved,
                     exc_info=True)
        return None


def _get_already_injected_pages(history: list) -> set[str]:
    """Scan history for vault_references messages and return set of page names."""
    pages: set[str] = set()
    for msg in history:
        if msg.get("role") == "vault_references":
            page = msg.get("wiki_page")
            if page:
                pages.add(page)
    return pages


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
    turn_start_index: int = 0
    accumulated_text_parts: list[str] = field(default_factory=list)
    model_override: dict[str, str] = field(default_factory=dict)
    retrieved_context_text: str = ""
    composed: "ComposedContext | None" = None
    composer: "ContextComposer | None" = None

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

            self.accumulated_text_parts = []  # text from iterations that also had tool calls

            for iteration in range(self.config.agent.max_tool_iterations):
                outcome = await self._run_iteration(iteration)
                if isinstance(outcome, _Final):
                    return outcome.result
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

        _refresh_dynamic_tools(self.ctx)
        all_tools, deferred_text = _build_tool_list(self.ctx)

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
            self.ctx.tokens.total_prompt += prompt_tokens
            self.ctx.tokens.total_completion += completion_tokens
            self.ctx.tokens.last_prompt = prompt_tokens
            self.prompt_tokens = prompt_tokens
            # composer is set by _compose() before the loop; guard is for the type checker
            if self.composer is not None:
                self.composer.record_actuals(prompt_tokens, completion_tokens)

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

        cancelled, end_turn_signal = await _execute_tool_calls(
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

        return _Continue()

    async def _handle_no_tool_calls(self, response: dict) -> IterationOutcome:
        """Process a no-tool-calls LLM response. Handles empty-retry,
        reflection, archive of last_reflection, and compaction trigger.
        Returns _Continue (retry) or _Final(result) (deliver response)."""
        content = response.get("content") or ""
        if not content:
            if self.empty_retries < 1:
                self.empty_retries += 1
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

        await _maybe_compact(self.ctx, self.config, self.history, self.prompt_tokens)
        return _Final(result=self._extract_workspace_media(content))

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
        result = await evaluate_response(
            self.config, judge_user_message, judge_agent_response, tool_summary,
            prior_turn_summary=prior_turn_summary,
            retrieved_context=self.retrieved_context_text,
        )

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

