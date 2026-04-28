"""DO NOT EDIT — regenerate via 'make gen-message-types'

Source: src/decafclaw/web/message_types.json
"""

from __future__ import annotations

from enum import StrEnum


class WSMessageType(StrEnum):
    """WebSocket wire message types."""

    BACKGROUND_EVENT = "background_event"
    CANVAS_UPDATE = "canvas_update"
    CHUNK = "chunk"
    COMMAND_ACK = "command_ack"
    COMPACTION_DONE = "compaction_done"
    CONFIRM_REQUEST = "confirm_request"
    CONFIRMATION_RESPONSE = "confirmation_response"
    CONV_HISTORY = "conv_history"
    CONV_SELECTED = "conv_selected"
    ERROR = "error"
    MESSAGE_COMPLETE = "message_complete"
    MODEL_CHANGED = "model_changed"
    MODELS_AVAILABLE = "models_available"
    NOTIFICATION_CREATED = "notification_created"
    NOTIFICATION_READ = "notification_read"
    REFLECTION_RESULT = "reflection_result"
    TOOL_END = "tool_end"
    TOOL_START = "tool_start"
    TOOL_STATUS = "tool_status"
    TURN_COMPLETE = "turn_complete"
    TURN_START = "turn_start"
    USER_MESSAGE = "user_message"
    CANCEL_TURN = "cancel_turn"
    CONFIRM_RESPONSE = "confirm_response"
    LOAD_HISTORY = "load_history"
    SELECT_CONV = "select_conv"
    SEND = "send"
    SET_EFFORT = "set_effort"
    SET_MODEL = "set_model"
    WIDGET_RESPONSE = "widget_response"


KNOWN_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset(WSMessageType)

S2C_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset({
    WSMessageType.BACKGROUND_EVENT,
    WSMessageType.CANVAS_UPDATE,
    WSMessageType.CHUNK,
    WSMessageType.COMMAND_ACK,
    WSMessageType.COMPACTION_DONE,
    WSMessageType.CONFIRM_REQUEST,
    WSMessageType.CONFIRMATION_RESPONSE,
    WSMessageType.CONV_HISTORY,
    WSMessageType.CONV_SELECTED,
    WSMessageType.ERROR,
    WSMessageType.MESSAGE_COMPLETE,
    WSMessageType.MODEL_CHANGED,
    WSMessageType.MODELS_AVAILABLE,
    WSMessageType.NOTIFICATION_CREATED,
    WSMessageType.NOTIFICATION_READ,
    WSMessageType.REFLECTION_RESULT,
    WSMessageType.TOOL_END,
    WSMessageType.TOOL_START,
    WSMessageType.TOOL_STATUS,
    WSMessageType.TURN_COMPLETE,
    WSMessageType.TURN_START,
    WSMessageType.USER_MESSAGE,
})

C2S_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset({
    WSMessageType.CANCEL_TURN,
    WSMessageType.CONFIRM_RESPONSE,
    WSMessageType.LOAD_HISTORY,
    WSMessageType.SELECT_CONV,
    WSMessageType.SEND,
    WSMessageType.SET_EFFORT,
    WSMessageType.SET_MODEL,
    WSMessageType.WIDGET_RESPONSE,
})

BIDIRECTIONAL_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset()
