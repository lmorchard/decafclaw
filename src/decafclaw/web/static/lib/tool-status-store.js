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
    // Find the confirm to get its confirmation_id and conv_id
    const confirm = this.#pendingConfirms.find(c => {
      if (toolCallId) return c.tool_call_id === toolCallId;
      return c.context_id === contextId && c.tool === tool;
    });

    this.#ws.send({
      type: 'confirm_response',
      context_id: contextId,
      tool,
      approved,
      // Include confirmation_id and conv_id for manager-based routing
      ...(confirm?.confirmation_id ? { confirmation_id: confirm.confirmation_id } : {}),
      ...(confirm?.conv_id ? { conv_id: confirm.conv_id } : {}),
      ...(toolCallId ? { tool_call_id: toolCallId } : {}),
      ...extra,
    });
    // Remove only this specific confirm
    if (confirm?.confirmation_id) {
      this.#pendingConfirms = this.#pendingConfirms.filter(c => c.confirmation_id !== confirm.confirmation_id);
    } else if (toolCallId) {
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

      case 'confirm_request': {
        // Only show confirmations for the active conversation (#235)
        if (msg.conv_id && msg.conv_id !== currentConvId) return true;
        // Deduplicate by confirmation_id (can arrive via both conv_history and live event)
        const cid = msg.confirmation_id || '';
        if (cid && this.#pendingConfirms.some(c => c.confirmation_id === cid)) {
          return true; // already have this one
        }
        this.#pendingConfirms = [...this.#pendingConfirms, {
          context_id: msg.context_id || '',
          confirmation_id: msg.confirmation_id || '',
          conv_id: msg.conv_id || '',
          tool: msg.tool || '',
          tool_call_id: msg.tool_call_id || '',
          command: msg.command || '',
          suggested_pattern: msg.suggested_pattern || '',
          message: msg.message || '',
          approve_label: msg.approve_label || '',
          deny_label: msg.deny_label || '',
          action_type: msg.action_type || '',
          action_data: msg.action_data || {},
        }];
        return true;
      }

      case 'confirmation_response': {
        // Multi-tab sync: remove the resolved confirmation widget
        const resolvedId = msg.confirmation_id || '';
        if (resolvedId) {
          this.#pendingConfirms = this.#pendingConfirms.filter(c => c.confirmation_id !== resolvedId);
        }
        return true;
      }

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
