// DO NOT EDIT — regenerate via 'make gen-message-types'
// Source: src/decafclaw/web/message_types.json

export const MESSAGE_TYPES = Object.freeze({
  BACKGROUND_EVENT: 'background_event',
  CANVAS_UPDATE: 'canvas_update',
  CHUNK: 'chunk',
  COMMAND_ACK: 'command_ack',
  COMPACTION_DONE: 'compaction_done',
  CONFIRM_REQUEST: 'confirm_request',
  CONFIRMATION_RESPONSE: 'confirmation_response',
  CONV_HISTORY: 'conv_history',
  CONV_SELECTED: 'conv_selected',
  ERROR: 'error',
  MESSAGE_COMPLETE: 'message_complete',
  MODEL_CHANGED: 'model_changed',
  MODELS_AVAILABLE: 'models_available',
  NOTIFICATION_CREATED: 'notification_created',
  NOTIFICATION_READ: 'notification_read',
  REFLECTION_RESULT: 'reflection_result',
  TOOL_END: 'tool_end',
  TOOL_START: 'tool_start',
  TOOL_STATUS: 'tool_status',
  TURN_COMPLETE: 'turn_complete',
  TURN_START: 'turn_start',
  USER_MESSAGE: 'user_message',
  CANCEL_TURN: 'cancel_turn',
  CONFIRM_RESPONSE: 'confirm_response',
  LOAD_HISTORY: 'load_history',
  SELECT_CONV: 'select_conv',
  SEND: 'send',
  SET_EFFORT: 'set_effort',
  SET_MODEL: 'set_model',
  WIDGET_RESPONSE: 'widget_response',
});

export const KNOWN_MESSAGE_TYPES = new Set(Object.values(MESSAGE_TYPES));
