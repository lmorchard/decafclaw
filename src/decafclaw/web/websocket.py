"""WebSocket handler for web gateway chat."""

import asyncio
import json
import logging

from starlette.websockets import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


# -- WebSocket message handlers ------------------------------------------------


async def _handle_list_convs(ws_send, index, username, msg, state):
    convs = index.list_for_user(username)
    await ws_send({"type": "conv_list", "conversations": [c.to_dict() for c in convs]})


async def _handle_list_archived(ws_send, index, username, msg, state):
    convs = index.list_for_user(username, include_archived=True)
    archived = [c for c in convs if c.archived]
    await ws_send({"type": "archived_list", "conversations": [c.to_dict() for c in archived]})


async def _handle_unarchive_conv(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    conv = index.get(conv_id)
    if conv and conv.user_id == username:
        index.unarchive(conv_id)
        await ws_send({"type": "conv_unarchived", "conv_id": conv_id})
        active = index.list_for_user(username)
        await ws_send({"type": "conv_list", "conversations": [c.to_dict() for c in active]})
        all_convs = index.list_for_user(username, include_archived=True)
        archived = [c for c in all_convs if c.archived]
        await ws_send({"type": "archived_list", "conversations": [c.to_dict() for c in archived]})
    else:
        await ws_send({"type": "error", "message": "Conversation not found"})


async def _handle_create_conv(ws_send, index, username, msg, state):
    title = msg.get("title", "")
    conv = index.create(username, title=title)

    await ws_send({"type": "conv_created", **conv.to_dict()})


async def _handle_select_conv(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    conv = index.get(conv_id)
    if conv and conv.user_id == username:

        await ws_send({"type": "conv_selected", "conv_id": conv_id})
    else:
        await ws_send({"type": "error", "message": f"Conversation not found: {conv_id}"})


async def _handle_load_history(ws_send, index, username, msg, state):
    config = state["config"]
    conv_id = msg.get("conv_id", "")
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        await ws_send({"type": "error", "message": "Conversation not found"})
        return
    limit = msg.get("limit", 50)
    before = msg.get("before", "")
    messages, has_more = index.load_history(conv_id, limit=limit, before=before)
    estimated_tokens = None
    if not before:
        from ..archive import read_archive as _read_archive
        from ..archive import read_compacted_history
        from ..compaction import estimate_tokens, flatten_messages
        working = read_compacted_history(config, conv_id) or _read_archive(config, conv_id)
        if working:
            estimated_tokens = estimate_tokens(flatten_messages(working))
    response = {
        "type": "conv_history", "conv_id": conv_id,
        "messages": messages, "has_more": has_more,
        "context_limit": config.compaction.max_tokens,
    }
    if estimated_tokens is not None:
        response["estimated_tokens"] = estimated_tokens
    await ws_send(response)


async def _handle_rename_conv(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    title = msg.get("title", "")
    conv = index.get(conv_id)
    if conv and conv.user_id == username:
        updated = index.rename(conv_id, title)
        if updated:
            await ws_send({"type": "conv_renamed", "conv_id": conv_id, "title": updated.title})
    else:
        await ws_send({"type": "error", "message": "Conversation not found"})


async def _handle_archive_conv(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    conv = index.get(conv_id)
    if conv and conv.user_id == username:
        index.archive(conv_id)

        await ws_send({"type": "conv_archived", **conv.to_dict()})
        convs = index.list_for_user(username)
        await ws_send({"type": "conv_list", "conversations": [c.to_dict() for c in convs]})
    else:
        await ws_send({"type": "error", "message": "Conversation not found"})


async def _handle_send(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    text = msg.get("text", "").strip()
    if not conv_id or not text:
        await ws_send({"type": "error", "message": "conv_id and text required"})
        return
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        await ws_send({"type": "error", "message": "Conversation not found"})
        return

    # -- Command detection --
    from ..commands import format_help, parse_command_trigger
    from ..skills import find_command

    trigger = parse_command_trigger(text, prefix="/") or parse_command_trigger(text, prefix="!")
    log.debug(f"Command trigger check: text={text!r} trigger={trigger}")
    if trigger:
        cmd_name, cmd_args = trigger
        if cmd_name == "help":
            discovered = getattr(state["config"], "discovered_skills", [])
            help_text = format_help(discovered, prefix="/")
            await ws_send({
                "type": "message_complete", "conv_id": conv_id,
                "role": "assistant", "text": help_text, "final": True,
            })
            return
        discovered = getattr(state["config"], "discovered_skills", [])
        command_skill = find_command(cmd_name, discovered)
        if command_skill is None:
            await ws_send({
                "type": "message_complete", "conv_id": conv_id,
                "role": "assistant", "final": True,
                "text": f"Unknown command: `{cmd_name}`. Type `/help` for available commands.",
            })
            return

        # Fork mode: execute and return response without agent turn
        if command_skill.context == "fork":
            from ..commands import execute_command
            from ..context import Context

            ctx = Context(config=state["config"], event_bus=state["event_bus"])
            ctx.user_id = username
            ctx.conv_id = conv_id
            try:
                mode, result = await execute_command(ctx, command_skill, cmd_args)
            except Exception as e:
                result = f"[error: command failed: {e}]"
            await ws_send({
                "type": "message_complete", "conv_id": conv_id,
                "role": "assistant", "text": result, "final": True,
            })
            return

        # Inline mode: substitute body, pass skill info to _run_agent_turn
        # for pre-activation of native tools and required skills
        from ..commands import substitute_body

        text = substitute_body(command_skill.body, cmd_args,
                               skill_dir=str(command_skill.location))
        state["_command_skill"] = command_skill
    else:
        command_skill = None

    # Check if a turn is already running for this conversation
    busy_convs = state.setdefault("busy_convs", set())
    pending_queue = state.setdefault("pending_msgs", {})

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
            {"text": text, "command_skill": command_skill})
        return

    _start_agent_turn(state, index, conv_id, username, text, ws_send,
                      command_skill=command_skill)


def _start_agent_turn(state, index, conv_id, username, text, ws_send,
                      command_skill=None):
    """Launch an agent turn task with queue drain on completion."""
    busy_convs = state.setdefault("busy_convs", set())
    pending_queue = state.setdefault("pending_msgs", {})

    cancel_event = asyncio.Event()
    state["cancel_events"][conv_id] = cancel_event
    busy_convs.add(conv_id)

    task = asyncio.create_task(
        _run_agent_turn(
            state["websocket"], state["app_ctx"], state["config"], state["event_bus"],
            index, conv_id, username, text, cancel_event,
            command_skill=command_skill,
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
            # Queued items are dicts with text and optional command_skill
            texts = [q["text"] for q in queued]
            # Use command_skill from the last queued message (most recent intent)
            last_skill = queued[-1].get("command_skill")
            combined = "\n".join(texts)
            log.info(f"WS: draining {len(queued)} queued message(s) for {cid}")
            _start_agent_turn(state, index, cid, username, combined, ws_send,
                              command_skill=last_skill)

    task.add_done_callback(_on_task_done)


async def _handle_cancel_turn(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    event = state["cancel_events"].get(conv_id)
    if event:
        log.info(f"Cancelling agent turn for {conv_id}")
        event.set()


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
    "list_convs": _handle_list_convs,
    "list_archived": _handle_list_archived,
    "unarchive_conv": _handle_unarchive_conv,
    "create_conv": _handle_create_conv,
    "select_conv": _handle_select_conv,
    "load_history": _handle_load_history,
    "rename_conv": _handle_rename_conv,
    "archive_conv": _handle_archive_conv,
    "send": _handle_send,
    "cancel_turn": _handle_cancel_turn,
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

    index = ConversationIndex(config)
    state = {
        "agent_tasks": set(),
        "cancel_events": {},
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
        state["closing"] = True
        # Wait for all in-flight tasks, including any spawned by queue drain
        while state["agent_tasks"]:
            tasks = list(state["agent_tasks"])
            log.info(f"Waiting for {len(tasks)} in-flight web agent turn(s)")
            await asyncio.gather(*tasks, return_exceptions=True)


async def _run_agent_turn(websocket, app_ctx, config, event_bus,
                          index, conv_id, username, text, cancel_event=None,
                          command_skill=None):
    """Run an agent turn for a web conversation, streaming events to WebSocket."""
    from ..agent import run_agent_turn  # deferred: circular dep
    from ..archive import read_archive
    from ..context import Context

    async def ws_send(msg):
        """Send JSON to WebSocket, ignoring errors if connection closed."""
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    # Fork a request context for this turn
    ctx = Context(config=config, event_bus=event_bus)
    ctx.user_id = username
    ctx.channel_id = conv_id
    ctx.channel_name = "web"
    ctx.thread_id = ""
    ctx.conv_id = conv_id
    # Apply command skill state (inline mode)
    if command_skill:
        from ..commands import prepare_command_context
        await prepare_command_context(ctx, command_skill)
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

    if config.llm.streaming:
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
                await ws_send({
                    "type": "tool_end", "conv_id": conv_id,
                    "tool": event.get("tool", ""),
                    "result_text": event.get("result_text", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                })

            elif event_type == "tool_confirm_request":
                log.info(f"Forwarding confirm request to web UI: {event.get('tool')}")
                await ws_send({
                    "type": "confirm_request", "conv_id": conv_id,
                    "context_id": event.get("context_id", ""),
                    "tool": event.get("tool", ""),
                    "command": event.get("command", ""),
                    "suggested_pattern": event.get("suggested_pattern", ""),
                    "message": event.get("message", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
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
        result = await run_agent_turn(ctx, text, history)

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
                "prompt_tokens": ctx.last_prompt_tokens,
                "completion_tokens": ctx.total_completion_tokens,
                "total_tokens": ctx.total_prompt_tokens + ctx.total_completion_tokens,
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
