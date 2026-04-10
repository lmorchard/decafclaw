"""WebSocket handler for web gateway chat."""

import asyncio
import json
import logging
import re

from starlette.websockets import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

# conv_id must be safe for use as a filename — alphanumeric, hyphens, dots, underscores only
_SAFE_CONV_ID = re.compile(r"^[a-zA-Z0-9._-]+$")


def _is_safe_conv_id(conv_id: str) -> bool:
    """Reject conv_ids that could escape the conversations directory."""
    return bool(conv_id and _SAFE_CONV_ID.match(conv_id))


# -- WebSocket message handlers ------------------------------------------------



async def _handle_select_conv(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    if not _is_safe_conv_id(conv_id):
        await ws_send({"type": "error", "message": "Invalid conversation ID"})
        return
    conv = index.get(conv_id)
    if conv and conv.user_id == username:
        await ws_send({"type": "conv_selected", "conv_id": conv_id})
    else:
        # Check if it's a system conversation (archive exists on disk)
        # Reject other users' web conversations
        if conv_id.startswith("web-") and "--child-" not in conv_id:
            await ws_send({"type": "error",
                           "message": f"Conversation not found: {conv_id}"})
            return
        from ..archive import archive_path
        if archive_path(state["config"], conv_id).exists():
            await ws_send({"type": "conv_selected", "conv_id": conv_id,
                           "read_only": True})
        else:
            await ws_send({"type": "error",
                           "message": f"Conversation not found: {conv_id}"})


async def _handle_load_history(ws_send, index, username, msg, state):
    config = state["config"]
    conv_id = msg.get("conv_id", "")
    if not _is_safe_conv_id(conv_id):
        await ws_send({"type": "error", "message": "Invalid conversation ID"})
        return
    conv = index.get(conv_id)
    is_owner = conv and conv.user_id == username
    if not is_owner:
        # Reject other users' web conversations
        if conv_id.startswith("web-") and "--child-" not in conv_id:
            await ws_send({"type": "error", "message": "Conversation not found"})
            return
        # Allow read-only access if archive exists on disk (system conversations)
        from ..archive import archive_path
        if not archive_path(config, conv_id).exists():
            await ws_send({"type": "error", "message": "Conversation not found"})
            return
    try:
        limit = min(max(1, int(msg.get("limit", 50))), 500)
    except (TypeError, ValueError):
        limit = 50
    before = msg.get("before", "")
    # Metadata roles that should not be rendered as chat messages
    _HIDDEN_ROLES = {"effort", "model"}

    # Read archive once and reuse for history, token estimation, and model scan
    from ..archive import read_archive as _read_archive
    from ..archive import read_compacted_history

    all_msgs = _read_archive(config, conv_id)

    # Paginate: filter by timestamp, take last N
    filtered = all_msgs
    if before:
        filtered = [m for m in all_msgs if m.get("timestamp", "") < before]
    has_more = len(filtered) > limit
    messages = filtered[-limit:] if has_more else filtered
    messages = [m for m in messages if m.get("role") not in _HIDDEN_ROLES]

    estimated_tokens = None
    current_model = None
    if not before:
        from ..compaction import estimate_tokens, flatten_messages
        working = read_compacted_history(config, conv_id) or all_msgs
        if working:
            estimated_tokens = estimate_tokens(flatten_messages(working))

        # Extract current model (scan reverse for last valid model message).
        for m in reversed(all_msgs):
            if m.get("role") == "model":
                name = m.get("content", "")
                if name and name in config.model_configs:
                    current_model = name
                    break

    response = {
        "type": "conv_history", "conv_id": conv_id,
        "messages": messages, "has_more": has_more,
        "context_limit": config.compaction.max_tokens,
    }
    if not is_owner:
        response["read_only"] = True
    if estimated_tokens is not None:
        response["estimated_tokens"] = estimated_tokens
    if current_model is not None:
        response["active_model"] = current_model
    # Send available model configs so the UI can offer a picker
    if config.model_configs:
        response["available_models"] = sorted(config.model_configs.keys())
        response["default_model"] = config.default_model

    # Register this WebSocket as a viewer of this conversation so it
    # receives live events (including for in-progress turns after reload).
    conv_viewers = state.setdefault("conv_viewers", {})
    conv_viewers.setdefault(conv_id, set()).add(state["websocket"])
    busy_convs = state.get("busy_convs", set())
    if conv_id in busy_convs:
        response["turn_active"] = True

    await ws_send(response)



async def _handle_send(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    text = msg.get("text", "").strip()
    attachments = msg.get("attachments") or None
    wiki_page = msg.get("wiki_page") or None
    if not conv_id or (not text and not attachments):
        await ws_send({"type": "error", "message": "conv_id and text (or attachments) required"})
        return
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        await ws_send({"type": "error", "message": "Conversation not found"})
        return

    # -- Command dispatch (centralized in commands.py) --
    from ..commands import dispatch_command
    from ..context import Context

    cmd_ctx = Context(config=state["config"], event_bus=state["event_bus"])
    cmd_ctx.user_id = username
    cmd_ctx.conv_id = conv_id
    cmd_result = await dispatch_command(cmd_ctx, text)

    if cmd_result.mode in ("help", "unknown", "error"):
        await ws_send({
            "type": "message_complete", "conv_id": conv_id,
            "role": "assistant", "text": cmd_result.text, "final": True,
        })
        return

    if cmd_result.mode == "fork":
        await ws_send({
            "type": "message_complete", "conv_id": conv_id,
            "role": "assistant", "text": cmd_result.text, "final": True,
        })
        return

    if cmd_result.mode == "inline":
        text = cmd_result.text
        # cmd_ctx has preapproved_tools, activated_skills, extra_tools set
        state["_command_ctx"] = cmd_ctx
        state["_command_display"] = cmd_result.display_text
        # Persist command flags for subsequent turns in this conversation
        if cmd_ctx.skip_vault_retrieval:
            state["conv_flags"].setdefault(conv_id, {})["skip_vault_retrieval"] = True
        # Acknowledge the command so the user sees it was recognized
        skill_name = cmd_result.skill.name if cmd_result.skill else "unknown"
        await ws_send({
            "type": "command_ack", "conv_id": conv_id,
            "command": cmd_result.display_text,
            "skill": skill_name,
        })
    else:
        pass  # not a command, text unchanged

    # Check if a turn is already running for this conversation
    busy_convs = state.setdefault("busy_convs", set())
    pending_queue = state.setdefault("pending_msgs", {})

    # Pop pre-configured command state (set by dispatch_command for inline mode)
    command_ctx = state.pop("_command_ctx", None)
    command_display = state.pop("_command_display", "")

    if conv_id in busy_convs:
        mode = state["config"].agent.turn_on_new_message
        if mode == "cancel":
            cancel_ev = state["cancel_events"].get(conv_id)
            if cancel_ev and not cancel_ev.is_set():
                log.info(f"WS: cancelling current turn for {conv_id} (new message)")
                cancel_ev.set()
        else:
            log.info(f"WS: queuing message for busy conversation {conv_id}")
        pending_queue.setdefault(conv_id, []).append(
            {"text": text, "command_ctx": command_ctx,
             "command_display": command_display, "attachments": attachments,
             "wiki_page": wiki_page})
        return

    _start_agent_turn(state, index, conv_id, username, text, ws_send,
                      command_ctx=command_ctx, archive_text=command_display,
                      attachments=attachments, wiki_page=wiki_page)


def _start_agent_turn(state, index, conv_id, username, text, ws_send,
                      command_ctx=None, archive_text="", attachments=None,
                      wiki_page=None):
    """Launch an agent turn task with queue drain on completion."""
    busy_convs = state.setdefault("busy_convs", set())
    pending_queue = state.setdefault("pending_msgs", {})

    cancel_event = asyncio.Event()
    state["cancel_events"][conv_id] = cancel_event
    busy_convs.add(conv_id)

    conv_viewers = state.setdefault("conv_viewers", {})
    task = asyncio.create_task(
        _run_agent_turn(
            state["websocket"], state["app_ctx"], state["config"], state["event_bus"],
            index, conv_id, username, text, cancel_event,
            command_ctx=command_ctx, archive_text=archive_text,
            attachments=attachments, wiki_page=wiki_page,
            conv_viewers=conv_viewers,
            conv_flags=state.get("conv_flags", {}).get(conv_id),
        )
    )
    state["agent_tasks"].add(task)

    def _on_task_done(t, cid=conv_id):
        state["agent_tasks"].discard(t)
        state["cancel_events"].pop(cid, None)
        busy_convs.discard(cid)

        # Don't drain queue if the connection is closing
        if state.get("closing"):
            pending_queue.pop(cid, None)
            return

        # Drain pending messages for this conversation
        queued = pending_queue.pop(cid, [])
        if queued:
            texts = [q["text"] for q in queued]
            last_ctx = queued[-1].get("command_ctx")
            last_display = queued[-1].get("command_display", "")
            # Merge attachments from all queued messages
            all_attachments = []
            for q in queued:
                if q.get("attachments"):
                    all_attachments.extend(q["attachments"])
            # Use last wiki_page — it reflects what's currently open.
            # Earlier pages from queued messages are still available via @[[...]] syntax.
            last_wiki_page = queued[-1].get("wiki_page")
            combined = "\n".join(texts)
            log.info(f"WS: draining {len(queued)} queued message(s) for {cid}")
            _start_agent_turn(state, index, cid, username, combined, ws_send,
                              command_ctx=last_ctx, archive_text=last_display,
                              attachments=all_attachments or None,
                              wiki_page=last_wiki_page)

    task.add_done_callback(_on_task_done)


async def _handle_cancel_turn(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    event = state["cancel_events"].get(conv_id)
    if event:
        log.info(f"Cancelling agent turn for {conv_id}")
        event.set()
        # Also cancel the asyncio task for immediate interruption
        # (the event alone only works when checked in the streaming loop)
        for task in list(state.get("agent_tasks", set())):
            if not task.done():
                task.cancel()


async def _handle_set_model(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    model_name = msg.get("model", msg.get("level", ""))
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        await ws_send({"type": "error", "message": "Conversation not found"})
        return

    config = state["config"]
    if not model_name:
        await ws_send({"type": "error", "message": "No model specified"})
        return
    if model_name not in config.model_configs:
        await ws_send({"type": "error", "message": f"Unknown model: {model_name}"})
        return

    # Record model change in archive
    from ..archive import append_message
    append_message(config, conv_id, {"role": "model", "content": model_name})

    await ws_send({
        "type": "model_changed", "conv_id": conv_id,
        "model": model_name,
    })


async def _handle_confirm_response(ws_send, index, username, msg, state):
    tool_call_id = msg.get("tool_call_id", "")
    await state["event_bus"].publish({
        "type": "tool_confirm_response",
        "context_id": msg.get("context_id", ""),
        "tool": msg.get("tool", ""),
        "approved": msg.get("approved", False),
        **({"tool_call_id": tool_call_id} if tool_call_id else {}),
        **({"always": True} if msg.get("always") else {}),
        **({"add_pattern": True} if msg.get("add_pattern") else {}),
    })


_HANDLERS = {
    "select_conv": _handle_select_conv,
    "load_history": _handle_load_history,
    "send": _handle_send,
    "cancel_turn": _handle_cancel_turn,
    "set_effort": _handle_set_model,  # backward compat for old web UI
    "set_model": _handle_set_model,
    "confirm_response": _handle_confirm_response,
}


# -- Main WebSocket handler ----------------------------------------------------


async def websocket_chat(websocket: WebSocket, config, event_bus, app_ctx):
    """Handle a WebSocket chat connection."""
    from .auth import get_current_user
    from .conversations import ConversationIndex

    username = get_current_user(websocket, config)
    if not username:
        await websocket.close(code=4001, reason="Not authenticated")
        return

    await websocket.accept()
    log.info(f"WebSocket connected: {username}")

    async def ws_send(msg):
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    # Send available models immediately so the picker is visible before
    # any conversation is selected
    if config.model_configs:
        await ws_send({
            "type": "models_available",
            "available_models": sorted(config.model_configs.keys()),
            "default_model": config.default_model,
        })

    index = ConversationIndex(config)
    state = {
        "agent_tasks": set(),
        "cancel_events": {},
        "conv_flags": {},  # per-conversation flags (e.g., skip_vault_retrieval)
        "config": config,
        "event_bus": event_bus,
        "app_ctx": app_ctx,
        "websocket": websocket,
    }

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws_send({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")
            handler = _HANDLERS.get(msg_type)
            if handler:
                await handler(ws_send, index, username, msg, state)
            else:
                await ws_send({"type": "error", "message": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        log.info(f"WebSocket disconnected: {username}")
    except Exception as e:
        log.error(f"WebSocket error for {username}: {e}", exc_info=True)
    finally:
        # Remove this WebSocket from all conversation viewer sets
        for viewers in state.get("conv_viewers", {}).values():
            viewers.discard(state["websocket"])
        state["closing"] = True
        # Wait for all in-flight tasks, including any spawned by queue drain
        while state["agent_tasks"]:
            tasks = list(state["agent_tasks"])
            log.info(f"Waiting for {len(tasks)} in-flight web agent turn(s)")
            await asyncio.gather(*tasks, return_exceptions=True)


async def _run_agent_turn(websocket, app_ctx, config, event_bus,
                          index, conv_id, username, text, cancel_event=None,
                          command_ctx=None, archive_text="", attachments=None,
                          wiki_page=None, conv_viewers=None, conv_flags=None):
    """Run an agent turn for a web conversation, streaming events to WebSocket."""
    from ..agent import run_agent_turn  # deferred: circular dep
    from ..archive import read_archive
    from ..context import Context

    # Track all WebSockets viewing each conversation so events reach every
    # tab (including reconnections after a page reload mid-turn).
    if conv_viewers is None:
        conv_viewers = {}
    conv_viewers.setdefault(conv_id, set()).add(websocket)

    async def ws_send(msg):
        """Send JSON to all WebSockets currently viewing this conversation."""
        viewers = conv_viewers.get(conv_id, set())
        # Always include the originating WebSocket even if not yet registered
        targets = viewers | {websocket}
        for ws in list(targets):
            try:
                await ws.send_json(msg)
            except Exception:
                viewers.discard(ws)

    # Fork a request context for this turn
    from ..media import LocalFileMediaHandler
    ctx = Context(config=config, event_bus=event_bus)
    ctx.user_id = username
    ctx.channel_id = conv_id
    ctx.channel_name = "web"
    ctx.thread_id = ""
    ctx.conv_id = conv_id
    ctx.media_handler = LocalFileMediaHandler(config)
    ctx.wiki_page = wiki_page
    # Apply pre-configured command state (set by dispatch_command)
    if command_ctx:
        from ..commands import apply_command_ctx
        apply_command_ctx(ctx, command_ctx)
        ctx.tools.extra_definitions = command_ctx.tools.extra_definitions
    # Restore per-conversation flags from previous turns
    if conv_flags:
        if conv_flags.get("skip_vault_retrieval"):
            ctx.skip_vault_retrieval = True
    if cancel_event:
        ctx.cancelled = cancel_event

    # Set up streaming callback
    async def on_stream_chunk(chunk_type, data):
        if chunk_type == "text":
            streaming_buffer["text"] += data
            await ws_send({"type": "chunk", "conv_id": conv_id, "text": data})
        elif chunk_type == "done":
            # LLM iteration done — buffer will be finalized by llm_start
            # (next iteration) or message_complete (end of turn)
            pass

    from ..config import resolve_streaming
    if resolve_streaming(config, ctx.active_model):
        ctx.on_stream_chunk = on_stream_chunk

    # Track streaming state across LLM iterations
    streaming_buffer = {"text": ""}

    # Set up event forwarding for this turn's events.
    # Uses create_task to avoid blocking the event bus publish loop —
    # the event bus awaits each subscriber, and if we await websocket.send_json
    # inline, it can deadlock with request_confirmation which is also
    # awaiting on the same event loop.
    def on_turn_event(event):
        if event.get("context_id") != ctx.context_id:
            return
        event_type = event.get("type", "")

        async def _forward():
            if event_type == "llm_start":
                iteration = event.get("iteration", 1)
                if iteration > 1 and streaming_buffer["text"]:
                    await ws_send({
                        "type": "message_complete", "conv_id": conv_id,
                        "role": "assistant", "text": streaming_buffer["text"],
                    })
                    streaming_buffer["text"] = ""

            elif event_type == "text_before_tools":
                # Flush streamed text as a complete message before tools start
                text = streaming_buffer["text"] or event.get("text", "")
                if text:
                    await ws_send({
                        "type": "message_complete", "conv_id": conv_id,
                        "role": "assistant", "text": text,
                    })
                    streaming_buffer["text"] = ""

            elif event_type == "tool_start":
                await ws_send({
                    "type": "tool_start", "conv_id": conv_id,
                    "tool": event.get("tool", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                })

            elif event_type == "tool_status":
                await ws_send({
                    "type": "tool_status", "conv_id": conv_id,
                    "tool": event.get("tool", ""),
                    "message": event.get("message", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                })

            elif event_type == "tool_end":
                payload = {
                    "type": "tool_end", "conv_id": conv_id,
                    "tool": event.get("tool", ""),
                    "result_text": event.get("result_text", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                }
                short = event.get("display_short_text")
                if short:
                    payload["display_short_text"] = short
                await ws_send(payload)

            elif event_type == "vault_retrieval":
                text = event.get("text", "")
                if text:
                    await ws_send({
                        "type": "tool_status", "conv_id": conv_id,
                        "tool": "vault_retrieval",
                        "message": text,
                        "tool_call_id": "",
                    })

            elif event_type == "vault_references":
                text = event.get("text", "")
                if text:
                    await ws_send({
                        "type": "tool_status", "conv_id": conv_id,
                        "tool": "vault_references",
                        "message": text,
                        "tool_call_id": "",
                    })

            elif event_type == "tool_confirm_request":
                # Flush any pending streamed text before showing confirmation
                if streaming_buffer["text"]:
                    await ws_send({
                        "type": "message_complete", "conv_id": conv_id,
                        "role": "assistant", "text": streaming_buffer["text"],
                    })
                    streaming_buffer["text"] = ""
                log.info(f"Forwarding confirm request to web UI: {event.get('tool')}")
                await ws_send({
                    "type": "confirm_request", "conv_id": conv_id,
                    "context_id": event.get("context_id", ""),
                    "tool": event.get("tool", ""),
                    "command": event.get("command", ""),
                    "suggested_pattern": event.get("suggested_pattern", ""),
                    "message": event.get("message", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                    "approve_label": event.get("approve_label", ""),
                    "deny_label": event.get("deny_label", ""),
                })

            elif event_type == "reflection_result":
                visibility = config.reflection.visibility
                passed = event.get("passed", True)
                # hidden: suppress all; visible: only failures; debug: everything
                if visibility == "hidden":
                    pass
                elif visibility == "visible" and passed:
                    pass
                else:
                    await ws_send({
                        "type": "reflection_result", "conv_id": conv_id,
                        "passed": passed,
                        "critique": event.get("critique", ""),
                        "retry_number": event.get("retry_number", 0),
                        "raw_response": (
                            event.get("raw_response", "")
                            if visibility == "debug" else ""
                        ),
                        "error": event.get("error", ""),
                    })

            elif event_type == "compaction_end":
                await ws_send({
                    "type": "compaction_done", "conv_id": conv_id,
                    "before_messages": event.get("before_messages", 0),
                    "after_messages": event.get("after_messages", 0),
                })

        task = asyncio.create_task(_forward())
        forward_tasks.add(task)
        task.add_done_callback(forward_tasks.discard)

    forward_tasks: set[asyncio.Task] = set()
    turn_sub_id = event_bus.subscribe(on_turn_event)

    try:
        # Load history: use compacted base if available, then append newer messages
        from ..archive import read_compacted_history
        compacted = read_compacted_history(config, conv_id)
        if compacted:
            full = read_archive(config, conv_id)
            last_ts = compacted[-1].get("timestamp", "")
            newer = [m for m in full if m.get("timestamp", "") > last_ts]
            history = compacted + newer
        else:
            history = read_archive(config, conv_id)

        # Notify browser that processing has started
        await ws_send({
            "type": "turn_start", "conv_id": conv_id,
        })

        # Run the agent turn
        result = await run_agent_turn(ctx, text, history, archive_text=archive_text,
                                      attachments=attachments)

        # Notify browser of completion — clear streaming and send final text
        response_text = result.text if hasattr(result, "text") else str(result)
        streaming_buffer["text"] = ""  # prevent double-send from event handler
        await ws_send({
            "type": "message_complete",
            "conv_id": conv_id,
            "role": "assistant",
            "text": response_text,
            "final": True,
            "usage": {
                "prompt_tokens": ctx.tokens.last_prompt,
                "completion_tokens": ctx.tokens.total_completion,
                "total_tokens": ctx.tokens.total_prompt + ctx.tokens.total_completion,
            },
            "context_limit": config.compaction.max_tokens,
        })

        # Update conversation metadata
        index.touch(conv_id)

        # Auto-title: use first user message if title is still default
        conv = index.get(conv_id)
        if conv and conv.title == "New conversation":
            title = text[:100]
            if len(text) > 100:
                # Truncate at word boundary
                last_space = title.rfind(" ")
                if last_space > 50:
                    title = title[:last_space]
                title += "..."
            index.rename(conv_id, title)

    except asyncio.CancelledError:
        log.info(f"Web agent turn cancelled for {conv_id}")
        try:
            await ws_send({
                "type": "message_complete", "conv_id": conv_id,
                "role": "assistant", "text": "[cancelled]", "final": True,
            })
        except Exception:
            pass
    except Exception as e:
        log.error(f"Web agent turn failed: {e}", exc_info=True)
        try:
            await ws_send({
                "type": "error", "conv_id": conv_id,
                "message": f"Agent turn failed: {e}",
            })
        except Exception:
            pass
    finally:
        event_bus.unsubscribe(turn_sub_id)
