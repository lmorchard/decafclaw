/**
 * WebSocket message types for the decafclaw bot.
 *
 * Mirrors src/decafclaw/web/message_types.json. Hand-typed for the spike.
 * Codegen-shaped: when we promote, replace with generated file using the
 * same exported names. See spec for the A->B promotion path.
 */

// ---- Server -> client ----

export interface SrvBackgroundEvent { type: "background_event"; conv_id: string; event: Record<string, unknown>; }
export interface SrvCanvasUpdate { type: "canvas_update"; conv_id: string; state: Record<string, unknown>; }
export interface SrvChunk { type: "chunk"; conv_id: string; text: string; }
export interface SrvCommandAck { type: "command_ack"; conv_id: string; command: string; }
export interface SrvCompactionDone { type: "compaction_done"; conv_id: string; }
export interface SrvConfirmRequest { type: "confirm_request"; conv_id: string; request_id: string; kind: string; payload: Record<string, unknown>; }
export interface SrvConfirmationResponse { type: "confirmation_response"; conv_id: string; request_id: string; decision: string; }
export interface SrvConvHistory { type: "conv_history"; conv_id: string; messages: Array<Record<string, unknown>>; before: string | null; }
export interface SrvConvSelected { type: "conv_selected"; conv_id: string; model: string | null; }
export interface SrvError { type: "error"; message: string; conv_id: string | null; }
// NOTE: manifest declares {message: object} but server (conversation_manager.py)
// actually emits {text: string, ...}. Matching the wire reality, not the manifest.
export interface SrvMessageComplete { type: "message_complete"; conv_id: string; text: string; }
export interface SrvModelChanged { type: "model_changed"; conv_id: string; model: string; }
export interface SrvModelsAvailable { type: "models_available"; models: string[]; }
export interface SrvNotificationCreated { type: "notification_created"; notification: Record<string, unknown>; }
export interface SrvNotificationRead { type: "notification_read"; id: string; }
export interface SrvReflectionResult { type: "reflection_result"; conv_id: string; result: Record<string, unknown>; }
export interface SrvToolEnd { type: "tool_end"; conv_id: string; tool_call_id: string; name: string; ok: boolean; result: string | Record<string, unknown>; }
export interface SrvToolStart { type: "tool_start"; conv_id: string; tool_call_id: string; name: string; input: Record<string, unknown>; }
export interface SrvToolStatus { type: "tool_status"; conv_id: string; tool_call_id: string; status: string; }
export interface SrvTurnComplete { type: "turn_complete"; conv_id: string; }
export interface SrvTurnStart { type: "turn_start"; conv_id: string; }
// NOTE: manifest declares {message: object} but server (websocket.py:505) actually
// emits {text: string}. Matching the wire reality, not the manifest.
export interface SrvUserMessage { type: "user_message"; conv_id: string; text: string; }
// NOTE: manifest has two fields: path + kind. Plan omitted kind — corrected here.
export interface SrvVaultChanged { type: "vault_changed"; path: string; kind: string; }

export type ServerMessage =
  | SrvBackgroundEvent
  | SrvCanvasUpdate
  | SrvChunk
  | SrvCommandAck
  | SrvCompactionDone
  | SrvConfirmRequest
  | SrvConfirmationResponse
  | SrvConvHistory
  | SrvConvSelected
  | SrvError
  | SrvMessageComplete
  | SrvModelChanged
  | SrvModelsAvailable
  | SrvNotificationCreated
  | SrvNotificationRead
  | SrvReflectionResult
  | SrvToolEnd
  | SrvToolStart
  | SrvToolStatus
  | SrvTurnComplete
  | SrvTurnStart
  | SrvUserMessage
  | SrvVaultChanged;

// ---- Client -> server ----

export interface CliCancelTurn { type: "cancel_turn"; conv_id: string; }
export interface CliConfirmResponse { type: "confirm_response"; conv_id: string; request_id: string; decision: string; extras: Record<string, unknown>; }
export interface CliLoadHistory { type: "load_history"; conv_id: string; limit: number; before: string | null; }
export interface CliSelectConv { type: "select_conv"; conv_id: string; }
export interface CliSend { type: "send"; conv_id: string; text: string; attachments: Array<Record<string, unknown>>; }
// NOTE: `set_effort` is a deprecated alias for `set_model` (older web clients).
// The manifest field is `model: string` — intentionally matches set_model's field.
export interface CliSetEffort { type: "set_effort"; conv_id: string; model: string; }
export interface CliSetModel { type: "set_model"; conv_id: string; model: string; }
export interface CliWidgetResponse { type: "widget_response"; conv_id: string; request_id: string; value: Record<string, unknown>; }

export type ClientMessage =
  | CliCancelTurn
  | CliConfirmResponse
  | CliLoadHistory
  | CliSelectConv
  | CliSend
  | CliSetEffort
  | CliSetModel
  | CliWidgetResponse;
