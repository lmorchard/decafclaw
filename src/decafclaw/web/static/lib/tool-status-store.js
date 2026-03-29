/**
 * @typedef {import('./conversation-store.js').ChatMessage} ChatMessage
 * @typedef {import('./conversation-store.js').PendingConfirm} PendingConfirm
 * @typedef {import('./websocket-client.js').WebSocketClient} WebSocketClient
 */

/**
 * Sub-store managing tool execution status and confirmation requests.
 */
export class ToolStatusStore {
  /** @type {string|null} */
  #toolStatus = null;
  /** @type {PendingConfirm[]} */
  #pendingConfirms = [];
  /** @type {() => void} */
  #onChange;
  /** @type {WebSocketClient} */
  #ws;

  /**
   * @param {() => void} onChange
   * @param {WebSocketClient} ws
   */
  constructor(onChange, ws) {
    this.#onChange = onChange;
    this.#ws = ws;
  }

  // -- Getters ----------------------------------------------------------------

  /** @returns {string|null} */
  get toolStatus() { return this.#toolStatus; }
  /** @returns {PendingConfirm[]} */
  get pendingConfirms() { return this.#pendingConfirms; }

  // -- Mutations --------------------------------------------------------------

  /** Reset state when switching conversations. */
  clear() {
    this.#toolStatus = null;
    this.#pendingConfirms = [];
  }

  clearToolStatus() {
    this.#toolStatus = null;
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
   * @param {ChatMessage[]} currentMessages - direct reference to message array for in-place updates
   * @returns {boolean} true if handled
   */
  handleMessage(msg, currentConvId, currentMessages) {
    switch (msg.type) {
      case 'tool_start':
        this.#toolStatus = `Running ${msg.tool}...`;
        if (msg.conv_id === currentConvId) {
          currentMessages.push({
            role: 'tool_call',
            content: `Running ${msg.tool}...`,
            tool: msg.tool,
            timestamp: new Date().toISOString(),
          });
        }
        return true;

      case 'tool_status':
        this.#toolStatus = `${msg.tool}: ${msg.message}`;
        if (msg.conv_id === currentConvId) {
          // memory_context arrives without a preceding tool_start — insert before user message
          if (msg.tool === 'memory_context') {
            /** @type {ChatMessage} */
            const mcMsg = {
              role: 'memory_context',
              content: msg.message,
              timestamp: new Date().toISOString(),
            };
            // Insert before the last user message (memory context precedes the user's turn)
            let lastUserIdx = -1;
            for (let i = currentMessages.length - 1; i >= 0; i--) {
              if (currentMessages[i].role === 'user') { lastUserIdx = i; break; }
            }
            if (lastUserIdx >= 0) {
              currentMessages.splice(lastUserIdx, 0, mcMsg);
            } else {
              currentMessages.push(mcMsg);
            }
          } else {
            // Update the last tool_call message if it exists
            const last = currentMessages[currentMessages.length - 1];
            if (last?.role === 'tool_call') {
              last.content = `${msg.tool}: ${msg.message}`;
            }
          }
        }
        return true;

      case 'tool_end':
        this.#toolStatus = null;
        if (msg.conv_id === currentConvId) {
          const idx = currentMessages.findLastIndex(m => m.role === 'tool_call');
          if (idx >= 0) {
            currentMessages[idx] = {
              role: 'tool',
              content: msg.result_text || '',
              tool: msg.tool,
              display_short_text: msg.display_short_text || '',
              timestamp: new Date().toISOString(),
            };
          }
        }
        return true;

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
          currentMessages.push({
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
