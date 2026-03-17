"""WebSocket handler for web gateway chat."""

import asyncio
import json
import logging

from starlette.websockets import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


async def websocket_chat(websocket: WebSocket, config, event_bus, app_ctx):
    """Handle a WebSocket chat connection.

    Authenticates from cookie, then enters a message loop:
    - Receives JSON commands from the browser
    - Dispatches to handlers (send, create_conv, list_convs, etc.)
    - Forwards event bus events to the browser for active conversations
    """
    from .auth import get_current_user
    from .conversations import ConversationIndex

    # Authenticate from cookie
    # Starlette exposes cookies before accept
    username = get_current_user(websocket, config)
    if not username:
        await websocket.close(code=4001, reason="Not authenticated")
        return

    await websocket.accept()
    log.info(f"WebSocket connected: {username}")

    async def ws_send(msg):
        """Send JSON to WebSocket, ignoring errors if connection closed."""
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    index = ConversationIndex(config)
    active_conv_ids: set[str] = set()
    agent_tasks: set[asyncio.Task] = set()
    cancel_events: dict[str, asyncio.Event] = {}

    # Note: per-turn event forwarding is handled in _run_agent_turn(),
    # which subscribes to the event bus for each turn's context_id.
    # No top-level event bus subscription needed here — _run_agent_turn
    # sends chunk, message_complete, tool_start, etc. directly to the WebSocket.

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws_send({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")

            if msg_type == "list_convs":
                convs = index.list_for_user(username)
                await ws_send({
                    "type": "conv_list",
                    "conversations": [{
                        "conv_id": c.conv_id, "title": c.title,
                        "created_at": c.created_at, "updated_at": c.updated_at,
                    } for c in convs],
                })

            elif msg_type == "list_archived":
                convs = index.list_for_user(username, include_archived=True)
                archived = [c for c in convs if c.archived]
                await ws_send({
                    "type": "archived_list",
                    "conversations": [{
                        "conv_id": c.conv_id, "title": c.title,
                        "created_at": c.created_at, "updated_at": c.updated_at,
                    } for c in archived],
                })

            elif msg_type == "unarchive_conv":
                conv_id = msg.get("conv_id", "")
                conv = index.get(conv_id)
                if conv and conv.user_id == username:
                    index.unarchive(conv_id)
                    await ws_send({"type": "conv_unarchived", "conv_id": conv_id})
                    # Refresh both lists
                    active = index.list_for_user(username)
                    await ws_send({
                        "type": "conv_list",
                        "conversations": [{
                            "conv_id": c.conv_id, "title": c.title,
                            "created_at": c.created_at, "updated_at": c.updated_at,
                        } for c in active],
                    })
                    all_convs = index.list_for_user(username, include_archived=True)
                    archived = [c for c in all_convs if c.archived]
                    await ws_send({
                        "type": "archived_list",
                        "conversations": [{
                            "conv_id": c.conv_id, "title": c.title,
                            "created_at": c.created_at, "updated_at": c.updated_at,
                        } for c in archived],
                    })
                else:
                    await ws_send({"type": "error", "message": "Conversation not found"})

            elif msg_type == "create_conv":
                title = msg.get("title", "")
                conv = index.create(username, title=title)
                active_conv_ids.add(conv.conv_id)
                await ws_send({
                    "type": "conv_created",
                    "conv_id": conv.conv_id, "title": conv.title,
                    "created_at": conv.created_at, "updated_at": conv.updated_at,
                })

            elif msg_type == "select_conv":
                conv_id = msg.get("conv_id", "")
                conv = index.get(conv_id)
                if conv and conv.user_id == username:
                    active_conv_ids.add(conv_id)
                    await ws_send({
                        "type": "conv_selected", "conv_id": conv_id,
                    })
                else:
                    await ws_send({
                        "type": "error", "message": f"Conversation not found: {conv_id}",
                    })

            elif msg_type == "load_history":
                conv_id = msg.get("conv_id", "")
                conv = index.get(conv_id)
                if not conv or conv.user_id != username:
                    await ws_send({
                        "type": "error", "message": "Conversation not found",
                    })
                    continue
                limit = msg.get("limit", 50)
                before = msg.get("before", "")
                messages, has_more = index.load_history(
                    conv_id, limit=limit, before=before
                )
                # On initial load (not paginating), estimate current context size
                estimated_tokens = None
                if not before:
                    from ..archive import read_archive as _read_archive
                    from ..archive import read_compacted_history
                    from ..compaction import _estimate_tokens, _flatten_messages
                    working = read_compacted_history(config, conv_id) \
                        or _read_archive(config, conv_id)
                    if working:
                        estimated_tokens = _estimate_tokens(
                            _flatten_messages(working)
                        )
                response = {
                    "type": "conv_history",
                    "conv_id": conv_id,
                    "messages": messages,
                    "has_more": has_more,
                    "context_limit": config.compaction_max_tokens,
                }
                if estimated_tokens is not None:
                    response["estimated_tokens"] = estimated_tokens
                await ws_send(response)

            elif msg_type == "rename_conv":
                conv_id = msg.get("conv_id", "")
                title = msg.get("title", "")
                conv = index.get(conv_id)
                if conv and conv.user_id == username:
                    updated = index.rename(conv_id, title)
                    if updated:
                        await ws_send({
                            "type": "conv_renamed",
                            "conv_id": conv_id, "title": updated.title,
                        })
                else:
                    await ws_send({
                        "type": "error", "message": "Conversation not found",
                    })

            elif msg_type == "archive_conv":
                conv_id = msg.get("conv_id", "")
                conv = index.get(conv_id)
                if conv and conv.user_id == username:
                    index.archive(conv_id)
                    active_conv_ids.discard(conv_id)
                    await ws_send({"type": "conv_archived", "conv_id": conv_id,
                                   "title": conv.title, "created_at": conv.created_at,
                                   "updated_at": conv.updated_at})
                    # Refresh the conversation list
                    convs = index.list_for_user(username)
                    await ws_send({
                        "type": "conv_list",
                        "conversations": [{
                            "conv_id": c.conv_id, "title": c.title,
                            "created_at": c.created_at, "updated_at": c.updated_at,
                        } for c in convs],
                    })
                else:
                    await ws_send({"type": "error", "message": "Conversation not found"})

            elif msg_type == "send":
                conv_id = msg.get("conv_id", "")
                text = msg.get("text", "").strip()
                if not conv_id or not text:
                    await ws_send({
                        "type": "error", "message": "conv_id and text required",
                    })
                    continue
                conv = index.get(conv_id)
                if not conv or conv.user_id != username:
                    await ws_send({
                        "type": "error", "message": "Conversation not found",
                    })
                    continue

                # Run agent turn in background task
                cancel_event = asyncio.Event()
                cancel_events[conv_id] = cancel_event
                task = asyncio.create_task(
                    _run_agent_turn(
                        websocket, app_ctx, config, event_bus,
                        index, conv_id, username, text, cancel_event,
                    )
                )
                agent_tasks.add(task)
                def _on_task_done(t, cid=conv_id):
                    agent_tasks.discard(t)
                    cancel_events.pop(cid, None)
                task.add_done_callback(_on_task_done)

            elif msg_type == "cancel_turn":
                conv_id = msg.get("conv_id", "")
                event = cancel_events.get(conv_id)
                if event:
                    log.info(f"Cancelling agent turn for {conv_id}")
                    event.set()

            elif msg_type == "confirm_response":
                # Bridge confirmation back to event bus
                await event_bus.publish({
                    "type": "tool_confirm_response",
                    "context_id": msg.get("context_id", ""),
                    "tool": msg.get("tool", ""),
                    "approved": msg.get("approved", False),
                    **({"always": True} if msg.get("always") else {}),
                    **({"add_pattern": True} if msg.get("add_pattern") else {}),
                })

            else:
                await ws_send({
                    "type": "error", "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        log.info(f"WebSocket disconnected: {username}")
    except Exception as e:
        log.error(f"WebSocket error for {username}: {e}", exc_info=True)
    finally:
        # Wait for in-flight agent tasks
        if agent_tasks:
            log.info(f"Waiting for {len(agent_tasks)} in-flight web agent turn(s)")
            await asyncio.gather(*agent_tasks, return_exceptions=True)


async def _run_agent_turn(websocket, app_ctx, config, event_bus,
                          index, conv_id, username, text, cancel_event=None):
    """Run an agent turn for a web conversation, streaming events to WebSocket."""
    from ..agent import run_agent_turn
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

    if config.llm_streaming:
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

            elif event_type == "tool_start":
                await ws_send({
                    "type": "tool_start", "conv_id": conv_id,
                    "tool": event.get("tool", ""),
                })

            elif event_type == "tool_status":
                await ws_send({
                    "type": "tool_status", "conv_id": conv_id,
                    "tool": event.get("tool", ""),
                    "message": event.get("message", ""),
                })

            elif event_type == "tool_end":
                await ws_send({
                    "type": "tool_end", "conv_id": conv_id,
                    "tool": event.get("tool", ""),
                    "result_text": event.get("result_text", ""),
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
                })

            elif event_type == "compaction_end":
                await ws_send({
                    "type": "compaction_done", "conv_id": conv_id,
                    "before_messages": event.get("before_messages", 0),
                    "after_messages": event.get("after_messages", 0),
                })

        asyncio.create_task(_forward())

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
            "context_limit": config.compaction_max_tokens,
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
