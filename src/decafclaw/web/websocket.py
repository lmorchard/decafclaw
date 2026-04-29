"""WebSocket handler for web gateway chat.

This is a transport adapter — it handles WebSocket connections, input
parsing, and output formatting. Agent loop lifecycle and conversation
state are owned by the ConversationManager.
"""

import asyncio
import json
import logging
import re

from starlette.websockets import WebSocket, WebSocketDisconnect

from decafclaw.web.message_types import WSMessageType

log = logging.getLogger(__name__)

# conv_id must be safe for use as a filename — alphanumeric, hyphens, dots, underscores only
_SAFE_CONV_ID = re.compile(r"^[a-zA-Z0-9._-]+$")


def _is_safe_conv_id(conv_id: str) -> bool:
    """Reject conv_ids that could escape the conversations directory."""
    return bool(conv_id and _SAFE_CONV_ID.match(conv_id))


def _legacy_tool_name(action_type: str) -> str:
    """Map ConfirmationAction values to the legacy tool names the web UI expects."""
    return {"run_shell_command": "shell"}.get(action_type, action_type)


def _project_tool_end(event: dict, conv_id: str) -> dict:
    """Project a bus tool_end event into the WebSocket payload shape.

    Fields are included selectively: display_short_text and widget only
    appear when the upstream event set them, to keep the socket messages
    compact.
    """
    payload = {
        "type": WSMessageType.TOOL_END, "conv_id": conv_id,
        "tool": event.get("tool", ""),
        "result_text": event.get("result_text", ""),
        "tool_call_id": event.get("tool_call_id", ""),
    }
    short = event.get("display_short_text")
    if short:
        payload["display_short_text"] = short
    widget = event.get("widget")
    if widget:
        payload["widget"] = widget
    return payload


def _make_canvas_update_forwarder(state, conv_id):
    """Build a coroutine that forwards canvas_update events to ws_send.

    Used in unit tests; production code uses the inline branch in
    on_conv_event for performance.
    """
    ws_send = state["ws_send"]

    async def _forward(event):
        if event.get("type") != "canvas_update":
            return
        if event.get("conv_id") != conv_id:
            return
        out = {
            "type": WSMessageType.CANVAS_UPDATE,
            "conv_id": conv_id,
            "kind": event.get("kind", "update"),
            "active_tab": event.get("active_tab"),
            "tab": event.get("tab"),
        }
        # Phase 4 field for kind=close_tab
        if "closed_tab_id" in event:
            out["closed_tab_id"] = event["closed_tab_id"]
        await ws_send(out)

    return _forward


def _confirmation_to_dict(req) -> dict:
    """Convert a ConfirmationRequest to the dict shape the client expects."""
    action_data = req.action_data or {}
    action_type = req.action_type.value
    return {
        "confirmation_id": req.confirmation_id,
        "action_type": action_type,
        "tool": _legacy_tool_name(action_type),
        "command": action_data.get("command", req.message),
        "suggested_pattern": action_data.get("suggested_pattern", ""),
        "message": req.message,
        "approve_label": req.approve_label,
        "deny_label": req.deny_label,
        "tool_call_id": req.tool_call_id,
        "action_data": action_data,
    }


# -- WebSocket message handlers ------------------------------------------------


async def _handle_select_conv(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    if not _is_safe_conv_id(conv_id):
        await ws_send({"type": WSMessageType.ERROR, "message": "Invalid conversation ID"})
        return
    conv = index.get(conv_id)
    if conv and conv.user_id == username:
        response = {"type": WSMessageType.CONV_SELECTED, "conv_id": conv_id}
        # Check for pending confirmation via the manager
        manager = state.get("manager")
        if manager:
            conv_state = manager.get_state(conv_id)
            if conv_state and conv_state.pending_confirmation:
                response["pending_confirmation"] = _confirmation_to_dict(
                    conv_state.pending_confirmation)
        await ws_send(response)
        _subscribe_to_conv(state, conv_id)
    else:
        # Check if it's a system conversation (archive exists on disk)
        # Reject other users' web conversations
        if conv_id.startswith("web-") and "--child-" not in conv_id:
            await ws_send({"type": WSMessageType.ERROR,
                           "message": f"Conversation not found: {conv_id}"})
            return
        from ..archive import archive_path
        if archive_path(state["config"], conv_id).exists():
            await ws_send({"type": WSMessageType.CONV_SELECTED, "conv_id": conv_id,
                           "read_only": True})
            _subscribe_to_conv(state, conv_id)
        else:
            await ws_send({"type": WSMessageType.ERROR,
                           "message": f"Conversation not found: {conv_id}"})


def _annotate_widget_responses(messages: list[dict],
                               hidden_roles: set[str]) -> list[dict]:
    """Pair resolved widget confirmations with their tool records.

    Walks the messages looking for a confirmation_request whose
    action_type is "widget_response" (carries tool_call_id →
    confirmation_id mapping) and a following confirmation_response
    (carries selection in `data`). Attaches `submitted=True` +
    `response=data` to the matching tool record. Hidden roles are
    stripped from the returned list.
    """
    widget_responses_by_tool: dict[str, dict] = {}
    pending_widget_ids: dict[str, str] = {}  # confirmation_id -> tool_call_id
    for m in messages:
        role = m.get("role", "")
        if role == "confirmation_request" and \
                m.get("action_type") == "widget_response":
            cid = m.get("confirmation_id", "")
            tcid = m.get("tool_call_id", "")
            if cid and tcid:
                pending_widget_ids[cid] = tcid
        elif role == "confirmation_response":
            cid = m.get("confirmation_id", "")
            tcid = pending_widget_ids.pop(cid, "")
            if tcid:
                # The presence of a confirmation_response is the real
                # "submitted" signal — data may legitimately be empty
                # for widgets where the submit itself is the answer.
                raw = m.get("data")
                widget_responses_by_tool[tcid] = raw if isinstance(
                    raw, dict) else {}

    visible: list[dict] = []
    for m in messages:
        if m.get("role") in hidden_roles:
            continue
        if m.get("role") == "tool":
            tcid = m.get("tool_call_id", "")
            resp = widget_responses_by_tool.get(tcid)
            if resp is not None:
                m = dict(m)
                m["submitted"] = True
                m["response"] = resp
        visible.append(m)
    return visible


async def _handle_load_history(ws_send, index, username, msg, state):
    config = state["config"]
    conv_id = msg.get("conv_id", "")
    if not _is_safe_conv_id(conv_id):
        await ws_send({"type": WSMessageType.ERROR, "message": "Invalid conversation ID"})
        return
    conv = index.get(conv_id)
    is_owner = conv and conv.user_id == username
    if not is_owner:
        # Reject other users' web conversations
        if conv_id.startswith("web-") and "--child-" not in conv_id:
            await ws_send({"type": WSMessageType.ERROR, "message": "Conversation not found"})
            return
        # Allow read-only access if archive exists on disk (system conversations)
        from ..archive import archive_path
        if not archive_path(config, conv_id).exists():
            await ws_send({"type": WSMessageType.ERROR, "message": "Conversation not found"})
            return
    try:
        limit = min(max(1, int(msg.get("limit", 50))), 500)
    except (TypeError, ValueError):
        limit = 50
    before = msg.get("before", "")
    # Metadata roles that should not be rendered as chat messages
    _HIDDEN_ROLES = {"effort", "model", "confirmation_request", "confirmation_response",
                     "wake_trigger"}

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

    # Pair resolved widget confirmations with their tool records so the
    # frontend can show submitted input widgets on reload.
    messages = _annotate_widget_responses(messages, _HIDDEN_ROLES)

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
        "type": WSMessageType.CONV_HISTORY, "conv_id": conv_id,
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

    # Check if a turn is active for this conversation via the manager
    manager = state.get("manager")
    if manager:
        conv_state = manager.get_state(conv_id)
        if conv_state and conv_state.busy:
            response["turn_active"] = True
        # Include pending confirmation if any
        if conv_state and conv_state.pending_confirmation:
            response["pending_confirmation"] = _confirmation_to_dict(
                conv_state.pending_confirmation)

    # Subscribe this WebSocket as a viewer for live events
    _subscribe_to_conv(state, conv_id)

    await ws_send(response)


async def _handle_send(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    text = msg.get("text", "").strip()
    attachments = msg.get("attachments") or None
    wiki_page = msg.get("wiki_page") or None
    if not conv_id or (not text and not attachments):
        await ws_send({"type": WSMessageType.ERROR, "message": "conv_id and text (or attachments) required"})
        return
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        await ws_send({"type": WSMessageType.ERROR, "message": "Conversation not found"})
        return

    manager = state.get("manager")
    if not manager:
        await ws_send({"type": WSMessageType.ERROR, "message": "Chat service unavailable"})
        return

    # -- Command dispatch (centralized in commands.py) --
    from ..commands import dispatch_command
    from ..context import Context

    cmd_ctx = Context(config=state["config"], event_bus=state["event_bus"])
    cmd_ctx.user_id = username
    cmd_ctx.conv_id = conv_id
    cmd_ctx.manager = manager
    cmd_result = await dispatch_command(cmd_ctx, text)

    if cmd_result.mode in ("help", "unknown", "error"):
        await ws_send({
            "type": WSMessageType.MESSAGE_COMPLETE, "conv_id": conv_id,
            "role": "assistant", "text": cmd_result.text, "final": True,
        })
        return

    if cmd_result.mode == "fork":
        await ws_send({
            "type": WSMessageType.MESSAGE_COMPLETE, "conv_id": conv_id,
            "role": "assistant", "text": cmd_result.text, "final": True,
        })
        return

    command_ctx = None
    archive_text = ""

    if cmd_result.mode == "inline":
        text = cmd_result.text
        command_ctx = cmd_ctx
        archive_text = cmd_result.display_text
        # Persist command flags for subsequent turns in this conversation
        if cmd_ctx.skip_vault_retrieval:
            manager.set_flag(conv_id, "skip_vault_retrieval", True)
        # Acknowledge the command so the user sees it was recognized
        skill_name = cmd_result.skill.name if cmd_result.skill else "unknown"
        await ws_send({
            "type": WSMessageType.COMMAND_ACK, "conv_id": conv_id,
            "command": cmd_result.display_text,
            "skill": skill_name,
        })

    # Ensure we're subscribed to this conversation's events
    _subscribe_to_conv(state, conv_id)

    # Transport-specific context setup
    def context_setup(ctx):
        from ..media import LocalFileMediaHandler
        ctx.media_handler = LocalFileMediaHandler(state["config"])
        ctx.channel_name = "web"
        ctx.thread_id = ""

    # Send message through the manager — it handles queueing and turn lifecycle
    await manager.send_message(
        conv_id, text,
        user_id=username,
        context_setup=context_setup,
        archive_text=archive_text,
        attachments=attachments,
        command_ctx=command_ctx,
        wiki_page=wiki_page,
    )

    # Auto-title: use first user message if title is still default
    conv = index.get(conv_id)
    if conv and conv.title == "New conversation":
        title = text[:100]
        if len(text) > 100:
            last_space = title.rfind(" ")
            if last_space > 50:
                title = title[:last_space]
            title += "..."
        index.rename(conv_id, title)

    # Touch conversation metadata
    index.touch(conv_id)


async def _handle_cancel_turn(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    manager = state.get("manager")
    if manager:
        await manager.cancel_turn(conv_id)
        log.info(f"Cancelling agent turn for {conv_id}")


async def _handle_set_model(ws_send, index, username, msg, state):
    conv_id = msg.get("conv_id", "")
    model_name = msg.get("model", msg.get("level", ""))
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        await ws_send({"type": WSMessageType.ERROR, "message": "Conversation not found"})
        return

    config = state["config"]
    if not model_name:
        await ws_send({"type": WSMessageType.ERROR, "message": "No model specified"})
        return
    if model_name not in config.model_configs:
        await ws_send({"type": WSMessageType.ERROR, "message": f"Unknown model: {model_name}"})
        return

    # Record model change in archive
    from ..archive import append_message
    append_message(config, conv_id, {"role": "model", "content": model_name})

    # Update manager's conversation state
    manager = state.get("manager")
    if manager:
        manager.set_flag(conv_id, "active_model", model_name)

    await ws_send({
        "type": WSMessageType.MODEL_CHANGED, "conv_id": conv_id,
        "model": model_name,
    })


async def _handle_widget_response(ws_send, index, username, msg, state):
    """Route a widget-input submission through the confirmation infra.

    Widget submits are always 'approved=True' in the confirmation sense;
    the user's selection rides on ``data``. The manager resolves the
    pending confirmation, which wakes the agent loop's pause-await.
    """
    manager = state.get("manager")
    conv_id = msg.get("conv_id", "")
    confirmation_id = msg.get("confirmation_id", "")
    # Defensive: coerce non-dict `data` to {} so on_response callbacks
    # can assume they're always handed a dict. Clients SHOULD send a
    # dict; a non-dict here means the client is malformed / malicious.
    raw_data = msg.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    if raw_data is not None and not isinstance(raw_data, dict):
        log.warning(
            "widget_response data is not a dict (got %s); coercing to {}",
            type(raw_data).__name__)

    if manager and conv_id and confirmation_id:
        await manager.respond_to_confirmation(
            conv_id, confirmation_id,
            approved=True, data=data)
    else:
        log.warning(
            "widget_response missing manager / conv_id / confirmation_id; "
            "dropping (msg=%s)", msg)


async def _handle_confirm_response(ws_send, index, username, msg, state):
    """Route confirmation response through the manager."""
    manager = state.get("manager")
    conv_id = msg.get("conv_id", "")
    confirmation_id = msg.get("confirmation_id", "")

    if manager and conv_id and confirmation_id:
        await manager.respond_to_confirmation(
            conv_id,
            confirmation_id,
            approved=msg.get("approved", False),
            always=msg.get("always", False),
            add_pattern=msg.get("add_pattern", False),
        )
    else:
        # Legacy fallback: publish to event bus for non-manager confirmations
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


def _subscribe_to_conv(state, conv_id):
    """Subscribe this WebSocket to a conversation's event stream.

    Unsubscribes from all other conversations first — the web UI only
    shows one conversation at a time, so stale subscriptions just waste
    bandwidth pushing events the client will ignore.
    """
    manager = state.get("manager")
    if not manager:
        return
    subscriptions = state.setdefault("conv_subscriptions", {})
    if conv_id in subscriptions:
        return  # already subscribed

    # Unsubscribe from other conversations
    for old_id, old_sub_id in list(subscriptions.items()):
        if old_id != conv_id:
            manager.unsubscribe(old_id, old_sub_id)
            del subscriptions[old_id]

    ws_send = state["ws_send"]
    config = state["config"]
    streaming_buffer = {"text": ""}

    async def on_conv_event(event):
        """Format manager events as WebSocket JSON messages."""
        event_type = event.get("type", "")
        event_conv_id = event.get("conv_id", conv_id)

        if event_type == "user_message":
            # Multi-tab sync: show user messages from other tabs.
            # Sent as distinct type so the client can deduplicate
            # (the originating tab already has the message locally).
            await ws_send({
                "type": WSMessageType.USER_MESSAGE, "conv_id": event_conv_id,
                "text": event.get("text", ""),
            })

        elif event_type == "chunk":
            streaming_buffer["text"] += event.get("text", "")
            await ws_send({
                "type": WSMessageType.CHUNK, "conv_id": event_conv_id,
                "text": event.get("text", ""),
            })

        elif event_type == "stream_done":
            pass  # buffer finalized by llm_start or message_complete

        elif event_type == "llm_start":
            iteration = event.get("iteration", 1)
            if iteration > 1 and streaming_buffer["text"]:
                await ws_send({
                    "type": WSMessageType.MESSAGE_COMPLETE, "conv_id": event_conv_id,
                    "role": "assistant", "text": streaming_buffer["text"],
                })
                streaming_buffer["text"] = ""

        elif event_type == "text_before_tools":
            text = streaming_buffer["text"] or event.get("text", "")
            if text:
                await ws_send({
                    "type": WSMessageType.MESSAGE_COMPLETE, "conv_id": event_conv_id,
                    "role": "assistant", "text": text,
                })
                streaming_buffer["text"] = ""

        elif event_type == "tool_start":
            await ws_send({
                "type": WSMessageType.TOOL_START, "conv_id": event_conv_id,
                "tool": event.get("tool", ""),
                "tool_call_id": event.get("tool_call_id", ""),
            })

        elif event_type == "tool_status":
            await ws_send({
                "type": WSMessageType.TOOL_STATUS, "conv_id": event_conv_id,
                "tool": event.get("tool", ""),
                "message": event.get("message", ""),
                "tool_call_id": event.get("tool_call_id", ""),
            })

        elif event_type == "tool_end":
            await ws_send(_project_tool_end(event, event_conv_id))

        elif event_type == "canvas_update":
            if event_conv_id == conv_id:
                payload = {
                    "type": WSMessageType.CANVAS_UPDATE,
                    "conv_id": event_conv_id,
                    "kind": event.get("kind", "update"),
                    "active_tab": event.get("active_tab"),
                    "tab": event.get("tab"),
                }
                if "closed_tab_id" in event:
                    payload["closed_tab_id"] = event["closed_tab_id"]
                await ws_send(payload)

        elif event_type == "vault_retrieval":
            text = event.get("text", "")
            if text:
                await ws_send({
                    "type": WSMessageType.TOOL_STATUS, "conv_id": event_conv_id,
                    "tool": "vault_retrieval",
                    "message": text, "tool_call_id": "",
                })

        elif event_type == "vault_references":
            text = event.get("text", "")
            if text:
                await ws_send({
                    "type": WSMessageType.TOOL_STATUS, "conv_id": event_conv_id,
                    "tool": "vault_references",
                    "message": text, "tool_call_id": "",
                })

        elif event_type == "confirmation_request":
            # Flush any pending streamed text
            if streaming_buffer["text"]:
                await ws_send({
                    "type": WSMessageType.MESSAGE_COMPLETE, "conv_id": event_conv_id,
                    "role": "assistant", "text": streaming_buffer["text"],
                })
                streaming_buffer["text"] = ""
            action_type = event.get("action_type", "")
            action_data = event.get("action_data", {})
            log.info("Forwarding confirm request to web UI: %s",
                     action_type)
            await ws_send({
                "type": WSMessageType.CONFIRM_REQUEST, "conv_id": event_conv_id,
                "confirmation_id": event.get("confirmation_id", ""),
                "action_type": action_type,
                # Provide tool/command for backward compat with confirm-view
                "tool": _legacy_tool_name(action_type),
                "command": action_data.get("command", event.get("message", "")),
                "suggested_pattern": action_data.get("suggested_pattern", ""),
                "message": event.get("message", ""),
                "approve_label": event.get("approve_label", ""),
                "deny_label": event.get("deny_label", ""),
                "tool_call_id": event.get("tool_call_id", ""),
                "action_data": action_data,
            })

        elif event_type == "confirmation_response":
            # Forward to all tabs so non-originating tabs clear the widget
            payload = {
                "type": WSMessageType.CONFIRMATION_RESPONSE, "conv_id": event_conv_id,
                "confirmation_id": event.get("confirmation_id", ""),
                "approved": event.get("approved", False),
            }
            data = event.get("data")
            if data is not None:
                # Widget responses ride here so other tabs can show the
                # selection in their post-submit widget state.
                payload["data"] = data
            await ws_send(payload)

        elif event_type == "message_complete":
            if event.get("suppress_user_message"):
                return  # WAKE turn ended with BACKGROUND_WAKE_OK — silent end
            if event.get("final"):
                streaming_buffer["text"] = ""
            await ws_send(event)

        elif event_type == "turn_start":
            await ws_send({"type": WSMessageType.TURN_START, "conv_id": event_conv_id})

        elif event_type == "turn_complete":
            streaming_buffer["text"] = ""
            await ws_send({"type": WSMessageType.TURN_COMPLETE, "conv_id": event_conv_id})

        elif event_type == "error":
            await ws_send({
                "type": WSMessageType.ERROR, "conv_id": event_conv_id,
                "message": event.get("message", ""),
            })

        elif event_type == "reflection_result":
            visibility = config.reflection.visibility
            passed = event.get("passed", True)
            if visibility == "hidden":
                pass
            elif visibility == "visible" and passed:
                pass
            else:
                await ws_send({
                    "type": WSMessageType.REFLECTION_RESULT, "conv_id": event_conv_id,
                    "passed": passed,
                    "critique": event.get("critique", ""),
                    "retry_number": event.get("retry_number", 0),
                    "raw_response": (
                        event.get("raw_response", "")
                        if visibility == "debug" else ""
                    ),
                    "error": event.get("error", ""),
                })

        elif event_type == "background_event":
            await ws_send({
                "type": WSMessageType.BACKGROUND_EVENT, "conv_id": event_conv_id,
                "record": event.get("record", {}),
            })

        elif event_type == "compaction_end":
            await ws_send({
                "type": WSMessageType.COMPACTION_DONE, "conv_id": event_conv_id,
                "before_messages": event.get("before_messages", 0),
                "after_messages": event.get("after_messages", 0),
            })

    sub_id = manager.subscribe(conv_id, on_conv_event)
    subscriptions[conv_id] = sub_id


def _unsubscribe_all(state):
    """Unsubscribe from all conversation event streams."""
    manager = state.get("manager")
    if not manager:
        return
    subscriptions = state.get("conv_subscriptions", {})
    for conv_id, sub_id in subscriptions.items():
        manager.unsubscribe(conv_id, sub_id)
    subscriptions.clear()


_HANDLERS = {
    WSMessageType.SELECT_CONV: _handle_select_conv,
    WSMessageType.LOAD_HISTORY: _handle_load_history,
    WSMessageType.SEND: _handle_send,
    WSMessageType.CANCEL_TURN: _handle_cancel_turn,
    WSMessageType.SET_EFFORT: _handle_set_model,  # backward compat for old web UI
    WSMessageType.SET_MODEL: _handle_set_model,
    WSMessageType.CONFIRM_RESPONSE: _handle_confirm_response,
    WSMessageType.WIDGET_RESPONSE: _handle_widget_response,
}


# -- Notification event forwarding --------------------------------------------


def _make_notification_forwarder(ws_send):
    """Build an event-bus subscriber that forwards notification events
    to a single WebSocket's ``ws_send``.

    The bus dispatches every event to every subscriber; this callback
    filters by ``event["type"]`` and copies the fields the frontend
    needs. Other event types are ignored.
    """
    async def _forward(event: dict):
        t = event.get("type")
        if t == "notification_created":
            await ws_send({
                "type": WSMessageType.NOTIFICATION_CREATED,
                "record": event["record"],
                "unread_count": event["unread_count"],
            })
        elif t == "notification_read":
            await ws_send({
                "type": WSMessageType.NOTIFICATION_READ,
                "ids": event["ids"],
                "unread_count": event["unread_count"],
            })
    return _forward


# -- Main WebSocket handler ----------------------------------------------------


async def websocket_chat(websocket: WebSocket, config, event_bus, app_ctx,
                         manager=None):
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
        except Exception as exc:
            log.debug("ws send failed (client likely disconnected): %s", exc)

    # Send available models immediately so the picker is visible before
    # any conversation is selected
    if config.model_configs:
        await ws_send({
            "type": WSMessageType.MODELS_AVAILABLE,
            "available_models": sorted(config.model_configs.keys()),
            "default_model": config.default_model,
        })

    index = ConversationIndex(config)
    state = {
        "config": config,
        "event_bus": event_bus,
        "app_ctx": app_ctx,
        "websocket": websocket,
        "ws_send": ws_send,
        "manager": manager,
    }

    # Forward notification events from the global event bus to this socket.
    # The bus dispatches every event to every subscriber, so the forwarder
    # filters by type. Subscribe immediately before entering the try block
    # so no setup code runs between subscribe() and the finally that
    # unsubscribes — otherwise a raise during setup could leak the
    # subscriber. See docs/notifications.md for the push architecture.
    notif_sub_id = event_bus.subscribe(_make_notification_forwarder(ws_send))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws_send({"type": WSMessageType.ERROR, "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")
            handler = _HANDLERS.get(msg_type)
            if handler:
                await handler(ws_send, index, username, msg, state)
            else:
                log.warning("ws: unknown inbound message type from %s: %r", username, msg_type)
                await ws_send({"type": WSMessageType.ERROR, "message": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        log.info(f"WebSocket disconnected: {username}")
    except Exception as e:
        log.error(f"WebSocket error for {username}: {e}", exc_info=True)
    finally:
        event_bus.unsubscribe(notif_sub_id)
        _unsubscribe_all(state)
