// DO NOT EDIT — regenerate via 'make gen-message-types'
// Source: src/decafclaw/web/message_types.json

export interface SrvBackgroundEvent {
  type: "background_event";
  conv_id: string;
  record: Record<string, unknown>;
}

export interface SrvCanvasUpdate {
  type: "canvas_update";
  conv_id: string;
  kind: string;
  active_tab: string | null;
  tab: Record<string, unknown> | null;
  closed_tab_id?: string;
}

export interface SrvChunk {
  type: "chunk";
  conv_id: string;
  text: string;
}

export interface SrvCommandAck {
  type: "command_ack";
  conv_id: string;
  command: string;
  skill?: string;
}

export interface SrvCompactionDone {
  type: "compaction_done";
  conv_id: string;
  before_messages: number;
  after_messages: number;
}

export interface SrvConfirmRequest {
  type: "confirm_request";
  conv_id: string;
  confirmation_id: string;
  action_type: string;
  tool: string;
  command: string;
  suggested_pattern: string;
  message: string;
  approve_label: string;
  deny_label: string;
  tool_call_id: string;
  action_data: Record<string, unknown>;
}

export interface SrvConfirmationResponse {
  type: "confirmation_response";
  conv_id: string;
  confirmation_id: string;
  approved: boolean;
  data?: Record<string, unknown>;
}

export interface SrvConvHistory {
  type: "conv_history";
  conv_id: string;
  messages: Array<Record<string, unknown>>;
  has_more: boolean;
  context_limit: number;
  read_only?: boolean;
  estimated_tokens?: number;
  active_model?: string;
  available_models?: string[];
  default_model?: string;
  turn_active?: boolean;
  pending_confirmation?: Record<string, unknown>;
}

export interface SrvConvSelected {
  type: "conv_selected";
  conv_id: string;
  read_only?: boolean;
  pending_confirmation?: Record<string, unknown>;
}

export interface SrvError {
  type: "error";
  message: string;
  conv_id?: string;
}

export interface SrvMessageComplete {
  type: "message_complete";
  conv_id: string;
  text: string;
  role?: string;
  final?: boolean;
  usage?: Record<string, unknown>;
  context_limit?: number;
}

export interface SrvModelChanged {
  type: "model_changed";
  conv_id: string;
  model: string;
}

export interface SrvModelsAvailable {
  type: "models_available";
  available_models: string[];
  default_model: string;
}

export interface SrvNotificationCreated {
  type: "notification_created";
  record: Record<string, unknown>;
  unread_count: number;
}

export interface SrvNotificationRead {
  type: "notification_read";
  ids: string[];
  unread_count: number;
}

export interface SrvReflectionResult {
  type: "reflection_result";
  conv_id: string;
  passed: boolean;
  critique: string;
  retry_number: number;
  raw_response: string;
  error: string;
}

export interface SrvToolEnd {
  type: "tool_end";
  conv_id: string;
  tool: string;
  tool_call_id: string;
  result_text: string;
  display_short_text?: string;
  widget?: Record<string, unknown>;
}

export interface SrvToolStart {
  type: "tool_start";
  conv_id: string;
  tool: string;
  tool_call_id: string;
}

export interface SrvToolStatus {
  type: "tool_status";
  conv_id: string;
  tool: string;
  tool_call_id: string;
  message: string;
}

export interface SrvTurnComplete {
  type: "turn_complete";
  conv_id: string;
}

export interface SrvTurnStart {
  type: "turn_start";
  conv_id: string;
}

export interface SrvUserMessage {
  type: "user_message";
  conv_id: string;
  text: string;
}

export interface SrvVaultChanged {
  type: "vault_changed";
  path: string;
  kind: string;
}

export interface CliCancelTurn {
  type: "cancel_turn";
  conv_id: string;
}

export interface CliConfirmResponse {
  type: "confirm_response";
  conv_id: string;
  confirmation_id: string;
  approved: boolean;
  always: boolean;
  add_pattern: boolean;
  data?: Record<string, unknown>;
}

export interface CliLoadHistory {
  type: "load_history";
  conv_id: string;
  limit: number;
  before: string | null;
}

export interface CliSelectConv {
  type: "select_conv";
  conv_id: string;
}

export interface CliSend {
  type: "send";
  conv_id: string;
  text: string;
  attachments: Array<Record<string, unknown>>;
}

export interface CliSetEffort {
  type: "set_effort";
  conv_id: string;
  model: string;
}

export interface CliSetModel {
  type: "set_model";
  conv_id: string;
  model: string;
}

export interface CliWidgetResponse {
  type: "widget_response";
  conv_id: string;
  confirmation_id: string;
  data: Record<string, unknown>;
}

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

export type ClientMessage =
  | CliCancelTurn
  | CliConfirmResponse
  | CliLoadHistory
  | CliSelectConv
  | CliSend
  | CliSetEffort
  | CliSetModel
  | CliWidgetResponse;
