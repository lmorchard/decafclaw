"""Concurrent tool-call execution for the agent loop.

Given a list of tool calls from the LLM, run them concurrently under a
semaphore, process media items (save/upload, replace placeholders),
validate widget payloads, archive results, and surface end-turn signals
(plain True / EndTurnConfirm / WidgetInputPause).

This is the "internal to the agent loop" half of the agent.py split.
The registry half lives in `tool_definitions.py`.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re as _re
import time

from .archive import append_message
from .media import EndTurnConfirm, ToolResult, WidgetInputPause
from .tools import execute_tool

log = logging.getLogger(__name__)


# `_archive` and `_check_cancelled` are duplicated from `agent.py` rather
# than extracted to a third shared module — they're trivial (4 + 8 lines)
# and pulling them out would invert the dependency direction (agent.py
# would import them from this module, but this module's whole purpose is
# to be a leaf the agent loop calls into). The duplication is intentional
# and the helpers should change together if they change at all.


def _conv_id(ctx) -> str:
    """Get conversation ID from context."""
    return ctx.conv_id or ctx.channel_id or "unknown"


def _archive(ctx, msg) -> None:
    """Archive a message, logging errors but never raising."""
    if ctx.skip_archive:
        return
    try:
        append_message(ctx.config, _conv_id(ctx), msg)
    except Exception as e:
        log.error(f"Archive write failed: {e}")


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


@functools.lru_cache(maxsize=128)
def _media_placeholder_pattern(filename: str) -> _re.Pattern:
    """Build a regex to find the placeholder for a given filename."""
    return _re.compile(
        r"\[file attached: " + _re.escape(filename) + r"[^\]]*\]"
    )


async def process_tool_media(ctx, result: ToolResult) -> list[str]:
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


def resolve_widget(fn_name: str, result: ToolResult,
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


async def execute_single_tool(call_ctx, tc, semaphore):
    """Execute one tool call. Returns (tool_msg dict, end_turn flag).

    Designed to run concurrently — uses its own forked ctx so
    current_tool_call_id doesn't race with other calls.
    Media is processed per-tool-call via process_tool_media().
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
        started = time.perf_counter()
        try:
            await call_ctx.publish("tool_start", tool=fn_name, args=fn_args,
                                   tool_call_id=tool_call_id,
                                   conv_id=call_ctx.conv_id)
            result = await execute_tool(call_ctx, fn_name, fn_args)
            log.debug(f"Tool result [{fn_name}]: {result.text[:200]}...")

            # Process media per-tool-call (save/upload, replace placeholders)
            file_ids = await process_tool_media(call_ctx, result)
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
            widget_payload = resolve_widget(fn_name, result, tool_call_id)
            try:
                input_bytes = len(json.dumps(fn_args, default=str).encode("utf-8"))
            except (TypeError, ValueError):
                input_bytes = 0
            publish_kwargs = {
                "tool": fn_name,
                "result_text": result.text,
                "display_text": getattr(result, "display_text", None),
                "display_short_text": getattr(
                    result, "display_short_text", None),
                "media": result.media or [],
                "tool_call_id": tool_call_id,
                "conv_id": call_ctx.conv_id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "input_bytes": input_bytes,
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


async def execute_tool_calls(ctx, tool_calls, history, messages):
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
            execute_single_tool(call_ctx, tc, semaphore),
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
            # execute_single_tool normally handles errors internally,
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
