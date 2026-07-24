"""DO NOT EDIT — regenerate via 'make gen-message-types'

Source: src/decafclaw/web/message_types.json
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict


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
    STICKY_CLEAR = "sticky_clear"
    STICKY_SET = "sticky_set"
    TOOL_END = "tool_end"
    TOOL_START = "tool_start"
    TOOL_STATUS = "tool_status"
    TURN_COMPLETE = "turn_complete"
    TURN_START = "turn_start"
    USER_MESSAGE = "user_message"
    VAULT_CHANGED = "vault_changed"
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
    WSMessageType.STICKY_CLEAR,
    WSMessageType.STICKY_SET,
    WSMessageType.TOOL_END,
    WSMessageType.TOOL_START,
    WSMessageType.TOOL_STATUS,
    WSMessageType.TURN_COMPLETE,
    WSMessageType.TURN_START,
    WSMessageType.USER_MESSAGE,
    WSMessageType.VAULT_CHANGED,
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


# -- TypedDicts (one per wire message) --

class SrvBackgroundEvent(TypedDict):
    type: Literal[WSMessageType.BACKGROUND_EVENT]
    conv_id: str
    record: dict[str, object]

class SrvCanvasUpdate(TypedDict):
    type: Literal[WSMessageType.CANVAS_UPDATE]
    conv_id: str
    kind: str
    active_tab: str | None
    tab: dict[str, object] | None
    closed_tab_id: NotRequired[str]

class SrvChunk(TypedDict):
    type: Literal[WSMessageType.CHUNK]
    conv_id: str
    text: str

class SrvCommandAck(TypedDict):
    type: Literal[WSMessageType.COMMAND_ACK]
    conv_id: str
    command: str
    skill: NotRequired[str]

class SrvCompactionDone(TypedDict):
    type: Literal[WSMessageType.COMPACTION_DONE]
    conv_id: str
    before_messages: int
    after_messages: int

class SrvConfirmRequest(TypedDict):
    type: Literal[WSMessageType.CONFIRM_REQUEST]
    conv_id: str
    confirmation_id: str
    action_type: str
    tool: str
    command: str
    suggested_pattern: str
    message: str
    approve_label: str
    deny_label: str
    tool_call_id: str
    action_data: dict[str, object]

class SrvConfirmationResponse(TypedDict):
    type: Literal[WSMessageType.CONFIRMATION_RESPONSE]
    conv_id: str
    confirmation_id: str
    approved: bool
    data: NotRequired[dict[str, object]]

class SrvConvHistory(TypedDict):
    type: Literal[WSMessageType.CONV_HISTORY]
    conv_id: str
    messages: list[dict[str, object]]
    has_more: bool
    context_limit: int
    read_only: NotRequired[bool]
    estimated_tokens: NotRequired[int]
    active_model: NotRequired[str]
    available_models: NotRequired[list[str]]
    default_model: NotRequired[str]
    turn_active: NotRequired[bool]
    pending_confirmation: NotRequired[dict[str, object]]

class SrvConvSelected(TypedDict):
    type: Literal[WSMessageType.CONV_SELECTED]
    conv_id: str
    read_only: NotRequired[bool]
    pending_confirmation: NotRequired[dict[str, object]]

class SrvError(TypedDict):
    type: Literal[WSMessageType.ERROR]
    message: str
    conv_id: NotRequired[str]

class SrvMessageComplete(TypedDict):
    type: Literal[WSMessageType.MESSAGE_COMPLETE]
    conv_id: str
    text: str
    role: NotRequired[str]
    final: NotRequired[bool]
    usage: NotRequired[dict[str, object]]
    context_limit: NotRequired[int]

class SrvModelChanged(TypedDict):
    type: Literal[WSMessageType.MODEL_CHANGED]
    conv_id: str
    model: str

class SrvModelsAvailable(TypedDict):
    type: Literal[WSMessageType.MODELS_AVAILABLE]
    available_models: list[str]
    default_model: str

class SrvNotificationCreated(TypedDict):
    type: Literal[WSMessageType.NOTIFICATION_CREATED]
    record: dict[str, object]
    unread_count: int

class SrvNotificationRead(TypedDict):
    type: Literal[WSMessageType.NOTIFICATION_READ]
    ids: list[str]
    unread_count: int

class SrvReflectionResult(TypedDict):
    type: Literal[WSMessageType.REFLECTION_RESULT]
    conv_id: str
    passed: bool
    critique: str
    retry_number: int
    raw_response: str
    error: str

class SrvStickyClear(TypedDict):
    type: Literal[WSMessageType.STICKY_CLEAR]
    conv_id: str

class SrvStickySet(TypedDict):
    type: Literal[WSMessageType.STICKY_SET]
    conv_id: str
    widget_type: str
    data: dict[str, object]

class SrvToolEnd(TypedDict):
    type: Literal[WSMessageType.TOOL_END]
    conv_id: str
    tool: str
    tool_call_id: str
    result_text: str
    display_short_text: NotRequired[str]
    widget: NotRequired[dict[str, object]]

class SrvToolStart(TypedDict):
    type: Literal[WSMessageType.TOOL_START]
    conv_id: str
    tool: str
    tool_call_id: str

class SrvToolStatus(TypedDict):
    type: Literal[WSMessageType.TOOL_STATUS]
    conv_id: str
    tool: str
    tool_call_id: str
    message: str

class SrvTurnComplete(TypedDict):
    type: Literal[WSMessageType.TURN_COMPLETE]
    conv_id: str

class SrvTurnStart(TypedDict):
    type: Literal[WSMessageType.TURN_START]
    conv_id: str

class SrvUserMessage(TypedDict):
    type: Literal[WSMessageType.USER_MESSAGE]
    conv_id: str
    text: str

class SrvVaultChanged(TypedDict):
    type: Literal[WSMessageType.VAULT_CHANGED]
    path: str
    kind: str

class CliCancelTurn(TypedDict):
    type: Literal[WSMessageType.CANCEL_TURN]
    conv_id: str

class CliConfirmResponse(TypedDict):
    type: Literal[WSMessageType.CONFIRM_RESPONSE]
    conv_id: str
    confirmation_id: str
    approved: bool
    always: bool
    add_pattern: bool
    data: NotRequired[dict[str, object]]

class CliLoadHistory(TypedDict):
    type: Literal[WSMessageType.LOAD_HISTORY]
    conv_id: str
    limit: int
    before: str | None

class CliSelectConv(TypedDict):
    type: Literal[WSMessageType.SELECT_CONV]
    conv_id: str

class CliSend(TypedDict):
    type: Literal[WSMessageType.SEND]
    conv_id: str
    text: str
    attachments: list[dict[str, object]]

class CliSetEffort(TypedDict):
    type: Literal[WSMessageType.SET_EFFORT]
    conv_id: str
    model: str

class CliSetModel(TypedDict):
    type: Literal[WSMessageType.SET_MODEL]
    conv_id: str
    model: str

class CliWidgetResponse(TypedDict):
    type: Literal[WSMessageType.WIDGET_RESPONSE]
    conv_id: str
    confirmation_id: str
    data: dict[str, object]


# -- Discriminated unions --

ServerMessage = SrvBackgroundEvent | SrvCanvasUpdate | SrvChunk | SrvCommandAck | SrvCompactionDone | SrvConfirmRequest | SrvConfirmationResponse | SrvConvHistory | SrvConvSelected | SrvError | SrvMessageComplete | SrvModelChanged | SrvModelsAvailable | SrvNotificationCreated | SrvNotificationRead | SrvReflectionResult | SrvStickyClear | SrvStickySet | SrvToolEnd | SrvToolStart | SrvToolStatus | SrvTurnComplete | SrvTurnStart | SrvUserMessage | SrvVaultChanged

ClientMessage = CliCancelTurn | CliConfirmResponse | CliLoadHistory | CliSelectConv | CliSend | CliSetEffort | CliSetModel | CliWidgetResponse


# -- Callable alias for ws_send and friends --

WSSendCallable = Callable[[ServerMessage], Awaitable[None]]
