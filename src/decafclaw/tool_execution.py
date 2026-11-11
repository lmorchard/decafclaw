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
from typing import TYPE_CHECKING

from .archive import append_message
from .conversation_paths import conversation_dir
from .media import EndTurnConfirm, ToolResult, WidgetInputPause
from .telemetry import get_tracer
from .tools import TOOL_DEFINITIONS, execute_tool

_tracer = get_tracer(__name__)

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# Batch execution policies (issue #1124). Within a single batch of tool calls:
#   - `shared`: calls may overlap with each other, subject to the global
#     semaphore / `max_concurrent_tools` budget.
#   - `exclusive`: the call waits for preceding calls in the batch and blocks
#     later calls until it completes.
# When policy resolution fails for a call, the batch falls back to serial
# execution (every call effectively exclusive, in order).
EXEC_SHARED = "shared"
EXEC_EXCLUSIVE = "exclusive"

# Tools that may be interrupted mid-flight when a steering message arrives
# (issue #1125, Slice 1 — pure waits). `wait` sleeps on a bare `asyncio.sleep`
# with no cooperative cancellation, so it is the one pure-wait tool an
# interrupt can cleanly take; everything else runs to completion within the
# batch. Mirrors how `loop_breaker.py` manages its `exempt_tools` set.
INTERRUPTIBLE_TOOLS = frozenset({"wait"})


# `_archive` and `_check_cancelled` are duplicated from `agent.py` rather
# than extracted to a third shared module — they're trivial (4 + 8 lines)
# and pulling them out would invert the dependency direction (agent.py
# would import them from this module, but this module's whole purpose is
# to be a leaf the agent loop calls into). The duplication is intentional
# and the helpers should change together if they change at all.


def _conv_id(ctx: "Context") -> str:
    """Get conversation ID from context."""
    return ctx.conv_id or ctx.channel_id or "unknown"


def _archive(ctx: "Context", msg) -> None:
    """Archive a message, logging errors but never raising."""
    if ctx.skip_archive:
        return
    try:
        append_message(ctx.config, _conv_id(ctx), msg)
    except Exception as e:
        log.error(f"Archive write failed: {e}")


def _check_cancelled(ctx: "Context", history):
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
    return _re.compile(r"\[file attached: " + _re.escape(filename) + r"[^\]]*\]")


async def process_tool_media(ctx: "Context", result: ToolResult) -> list[str]:
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


def resolve_widget(fn_name: str, result: ToolResult, tool_call_id: str = "") -> dict | None:
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
        log.warning("tool %s returned a widget but widget registry is not initialized; stripping", fn_name)
        result.widget = None
        return None
    ok, err = registry.validate(widget.widget_type, widget.data)
    if not ok:
        log.warning("tool %s widget %r failed validation: %s — stripping", fn_name, widget.widget_type, err)
        result.widget = None
        return None
    desc = registry.get(widget.widget_type)
    target = widget.target
    if target not in ("inline", "canvas"):
        log.warning("tool %s widget %r has unknown target %r — stripping", fn_name, widget.widget_type, target)
        result.widget = None
        return None
    if desc is not None and target not in desc.modes:
        log.warning(
            "tool %s widget %r used target %r not in declared modes %r — stripping",
            fn_name,
            widget.widget_type,
            target,
            desc.modes,
        )
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
                "tool %s emitted input widget %r without end_turn=True — stripping (input widgets must pause the turn)",
                fn_name,
                widget.widget_type,
            )
            result.widget = None
            return None
        if isinstance(result.end_turn, EndTurnConfirm):
            log.warning(
                "tool %s emitted input widget %r alongside EndTurnConfirm "
                "— dropping EndTurnConfirm (widget-pause takes priority)",
                fn_name,
                widget.widget_type,
            )
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
    with _tracer.start_as_current_span("execute_single_tool") as span:
        span.set_attribute("tool.name", tc["function"]["name"])
        return await _execute_single_tool_impl(call_ctx, tc, semaphore)


async def _execute_single_tool_impl(call_ctx, tc, semaphore):
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
            await call_ctx.publish(
                "tool_start", tool=fn_name, args=fn_args, tool_call_id=tool_call_id, conv_id=call_ctx.conv_id
            )
            result = await execute_tool(call_ctx, fn_name, fn_args)
            log.debug(f"Tool result [{fn_name}]: {result.text[:200]}...")

            # Process media per-tool-call (save/upload, replace placeholders)
            file_ids = await process_tool_media(call_ctx, result)
            if file_ids:
                await call_ctx.publish(
                    "tool_media_uploaded", tool=fn_name, file_ids=file_ids, tool_call_id=tool_call_id
                )
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
                "args": fn_args,
                "result_text": result.text,
                "display_text": getattr(result, "display_text", None),
                "display_short_text": getattr(result, "display_short_text", None),
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

    max_bytes = call_ctx.config.max_tool_output_bytes
    if max_bytes is not None:
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > max_bytes:
            tool_outputs_dir = conversation_dir(call_ctx.config, call_ctx.conv_id, create=True) / "tool_outputs"
            tool_outputs_dir.mkdir(parents=True, exist_ok=True)
            filepath = tool_outputs_dir / f"{tool_call_id}.txt"
            filepath.write_bytes(content_bytes)

            truncated_bytes = content_bytes[:max_bytes]
            content = truncated_bytes.decode("utf-8", errors="replace")
            content += f"\n\n[Output truncated. Full output saved to {filepath}. Use the 'read' tool to inspect it.]"

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


def _resolve_execution_policy(ctx: "Context", fn_name: str) -> str | None:
    """Resolve the batch execution policy for one tool call (issue #1124).

    Sources are checked in the same order ``execute_tool`` dispatches its
    handlers: skill-provided extras, the global tool registry, then MCP (or the
    MCP registry). Returns the policy for the first resolvable definition, or
    ``None`` when nothing resolves for the name.

    Precedence:
      1. Explicit ``execution`` key on the tool def (``"shared"`` / ``"exclusive"``).
      2. Derived from the ``writes`` flag — read-only (``False``) → shared,
         mutating (``True``) → exclusive.
      3. Default → shared.

    An unresolvable name returns ``None``; the batch runner treats that as a
    policy-resolution failure and falls back to serial execution.
    """
    sources: list[list] = []
    sources.append(list(ctx.tools.extra_definitions or []))
    sources.append(list(TOOL_DEFINITIONS))
    if ctx.tools.mcp_definitions is not None:
        sources.append(list(ctx.tools.mcp_definitions))
    else:
        from .mcp_client import get_registry

        registry = get_registry()
        sources.append(list(registry.get_tool_definitions()) if registry else [])
    for source in sources:
        for td in source:
            fn = td.get("function") or {}
            if fn.get("name") != fn_name:
                continue
            exec_mode = str(td.get("execution") or "").strip().lower()
            if exec_mode in (EXEC_SHARED, EXEC_EXCLUSIVE):
                return exec_mode
            writes = td.get("writes")
            if writes is False:
                return EXEC_SHARED
            if writes is True:
                return EXEC_EXCLUSIVE
            return EXEC_SHARED
    return None


async def execute_tool_calls(ctx: "Context", tool_calls, history, messages):
    """Execute tool calls concurrently, add results to history.

    Returns (ToolResult, False) if cancelled, (None, end_turn_signal) otherwise.
    end_turn_signal is False, True, or an EndTurnConfirm action.
    """
    cancelled = _check_cancelled(ctx, history)
    if cancelled:
        return cancelled, False

    semaphore = asyncio.Semaphore(ctx.config.agent.max_concurrent_tools)

    # Resolve per-call batch execution policies (issue #1124). A `shared` call
    # may overlap with other shared calls within the global semaphore budget; an
    # `exclusive` call waits for preceding calls and blocks later calls until it
    # completes. When policy resolution fails for a call, fall back to serial
    # execution for the whole batch (fail safe).
    policies: list[str | None] = []
    for tc in tool_calls:
        policies.append(_resolve_execution_policy(ctx, tc.get("function", {}).get("name") or ""))
    if any(p is None for p in policies):
        policies = [EXEC_EXCLUSIVE] * len(tool_calls)

    count = len(tool_calls)
    start_events: list[asyncio.Event] = [asyncio.Event() for _ in range(count)]
    done_events: list[asyncio.Event] = [asyncio.Event() for _ in range(count)]
    successors: list[list[int]] = [[] for _ in range(count)]
    preds: list[list[int]] = [[] for _ in range(count)]
    for j in range(count):
        for i in range(j + 1, count):
            # Exclusive calls wait for all preceding calls and block later
            # calls until completion; shared calls only wait for preceding
            # exclusive calls.
            if policies[j] == EXEC_EXCLUSIVE or policies[i] == EXEC_EXCLUSIVE:
                successors[j].append(i)
                preds[i].append(j)
    for i in range(count):
        if not preds[i]:
            # No preceding calls to wait for — start immediately.
            start_events[i].set()

    call_ctxs = [ctx.fork_for_tool_call(tc["id"]) for tc in tool_calls]

    async def _run_batch_call(i: int):
        try:
            await start_events[i].wait()
            return await execute_single_tool(call_ctxs[i], tool_calls[i], semaphore)
        finally:
            done_events[i].set()
            for succ in successors[i]:
                start_events[succ].set()

    tasks = [
        asyncio.create_task(
            _run_batch_call(i),
            name=f"tool-{tool_calls[i]['function']['name']}-{tool_calls[i]['id'][:8]}",
        )
        for i in range(count)
    ]

    # Steering watcher (issue #1125): when a steering message arrives while the
    # batch is mid-flight, cancel in-flight tasks for interruptible tools and
    # skip pending interruptible tasks. Non-interruptible calls are left alone
    # and run to completion within the batch.
    steer_event = ctx.steer_event
    steered: set[int] = set()

    async def _steer_watcher():
        if steer_event is None:
            return
        await steer_event.wait()
        for i, tc in enumerate(tool_calls):
            fn_name = (tc.get("function") or {}).get("name") or ""
            if fn_name not in INTERRUPTIBLE_TOOLS:
                continue
            task = tasks[i]
            if task.done():
                continue
            task.cancel()
            steered.add(i)

    # Cancel watcher: if the cancel event fires, cancel all in-flight tasks
    cancel_event = ctx.cancelled

    async def _cancel_watcher():
        if cancel_event:
            await cancel_event.wait()
            for t in tasks:
                t.cancel()

    watcher = asyncio.create_task(_cancel_watcher()) if cancel_event else None
    steer_watcher = asyncio.create_task(_steer_watcher()) if steer_event else None

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for w in (watcher, steer_watcher):
            if w is None:
                continue
            w.cancel()
            try:
                await w
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
            content = f"[error: {err_text}]"
            if i in steered:
                # Steering arrived while this interruptible call was still
                # pending (queued behind a blocking exclusive call) — it was
                # skipped rather than executed.
                content = "[skipped: interrupted by steering message]"
            tool_msg = {
                "role": "tool",
                "tool_call_id": tool_calls[i]["id"],
                "content": content,
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
            elif end_turn and not isinstance(end_turn_signal, (EndTurnConfirm, WidgetInputPause)):
                end_turn_signal = True
            if i in steered:
                # Steering cancelled an in-flight interruptible call (e.g. the
                # `wait` sleeping in asyncio.sleep); replace the cancelled
                # placeholder with the steering-interrupted synthetic result.
                tool_msg["content"] = "[interrupted by steering message]"
            history.append(tool_msg)
            messages.append(tool_msg)

    return None, end_turn_signal
