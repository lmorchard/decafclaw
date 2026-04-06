/**
 * @typedef {import('./conversation-store.js').ChatMessage} ChatMessage
 * @typedef {import('./conversation-store.js').PendingConfirm} PendingConfirm
 * @typedef {import('./websocket-client.js').WebSocketClient} WebSocketClient
 * @typedef {import('./message-store.js').MessageStore} MessageStore
 */

/**
 * Sub-store managing tool execution status and confirmation requests.
 */
export class ToolStatusStore {
  /** @type {Map<string, string>} tool_call_id → status text */
  #activeTools = new Map();
  /** @type {PendingConfirm[]} */
  #pendingConfirms = [];
  /** @type {() => void} */
  #onChange;
  /** @type {WebSocketClient} */
  #ws;
  /** @type {MessageStore} */
  #messageStore;

  /**
   * @param {() => void} onChange
   * @param {WebSocketClient} ws
   * @param {MessageStore} messageStore
   */
  constructor(onChange, ws, messageStore) {
    this.#onChange = onChange;
    this.#ws = ws;
    this.#messageStore = messageStore;
  }

  // -- Getters ----------------------------------------------------------------

  /** @returns {string|null} Combined status of all active tools, or null if idle. */
  get toolStatus() {
    if (this.#activeTools.size === 0) return null;
    return [...this.#activeTools.values()].join(' | ');
  }
  /** @returns {PendingConfirm[]} */
  get pendingConfirms() { return this.#pendingConfirms; }

  // -- Mutations --------------------------------------------------------------

  /** Reset state when switching conversations. */
  clear() {
    this.#activeTools.clear();
    this.#pendingConfirms = [];
  }

  clearToolStatus() {
    this.#activeTools.clear();
  }

  /**
   * @param {string} contextId
   * @param {string} tool
   * @param {string} toolCallId
   * @param {boolean} approved
   * @param {object} [extra]
   */
  respondToConfirm(contextId, tool, toolCallId = '', approved = false, extra = {}) {
    this.#ws.send({
      type: 'confirm_response',
      context_id: contextId,
      tool,
      approved,
      ...(toolCallId ? { tool_call_id: toolCallId } : {}),
      ...extra,
    });
    // Remove only this specific confirm
    if (toolCallId) {
      this.#pendingConfirms = this.#pendingConfirms.filter(c => c.tool_call_id !== toolCallId);
    } else {
      this.#pendingConfirms = this.#pendingConfirms.filter(c => !(c.context_id === contextId && c.tool === tool));
    }
    this.#onChange();
  }

  /**
   * Handle a WebSocket message if it's tool-related.
   * @param {object} msg
   * @param {string|null} currentConvId
   * @returns {boolean} true if handled
   */
  handleMessage(msg, currentConvId) {
    switch (msg.type) {
      case 'tool_start': {
        const tcId = msg.tool_call_id || '';
        this.#activeTools.set(tcId, `Running ${msg.tool}...`);
        if (msg.conv_id === currentConvId) {
          this.#messageStore.pushMessage({
            role: 'tool_call',
            content: `Running ${msg.tool}...`,
            tool: msg.tool,
            tool_call_id: tcId,
            timestamp: new Date().toISOString(),
          });
        }
        return true;
      }

      case 'tool_status': {
        const tcId = msg.tool_call_id || '';
        this.#activeTools.set(tcId, `${msg.tool}: ${msg.message}`);
        if (msg.conv_id === currentConvId) {
          if (msg.tool === 'vault_retrieval' || msg.tool === 'vault_references') {
            this.#messageStore.insertBeforeLastUser({
              role: msg.tool,
              content: msg.message,
              timestamp: new Date().toISOString(),
            });
          } else {
            this.#messageStore.updateToolCall(tcId, `${msg.tool}: ${msg.message}`);
          }
        }
        return true;
      }

      case 'tool_end': {
        const tcId = msg.tool_call_id || '';
        this.#activeTools.delete(tcId);
        if (msg.conv_id === currentConvId) {
          this.#messageStore.replaceToolCall(tcId, {
            role: 'tool',
            content: msg.result_text || '',
            tool: msg.tool,
            display_short_text: msg.display_short_text || '',
            timestamp: new Date().toISOString(),
          });
        }
        return true;
      }

      case 'confirm_request':
        this.#pendingConfirms = [...this.#pendingConfirms, {
          context_id: msg.context_id,
          tool: msg.tool,
          tool_call_id: msg.tool_call_id || '',
          command: msg.command || '',
          suggested_pattern: msg.suggested_pattern || '',
          message: msg.message || '',
        }];
        return true;

      case 'reflection_result':
        if (msg.conv_id === currentConvId) {
          const passed = msg.passed;
          const critique = msg.critique || '';
          const raw = msg.raw_response || '';
          const retryNum = msg.retry_number || 0;
          const detail = raw || critique || (passed ? 'Response passed evaluation' : 'No details');
          this.#messageStore.pushMessage({
            role: 'reflection',
            tool: passed ? 'reflection: PASS' : `reflection: retry ${retryNum}`,
            content: detail,
            timestamp: new Date().toISOString(),
          });
        }
        return true;

      default:
        return false;
    }
  }
}
