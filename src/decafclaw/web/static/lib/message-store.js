/**
 * @typedef {import('./conversation-store.js').ChatMessage} ChatMessage
 */

/**
 * Sub-store managing message state: history, streaming text, pagination.
 */
export class MessageStore {
  /** @type {ChatMessage[]} */
  #currentMessages = [];
  /** @type {string} */
  #streamingText = '';
  /** @type {boolean} */
  #hasMore = false;
  /** @type {() => void} */
  #onChange;

  /** @param {() => void} onChange */
  constructor(onChange) {
    this.#onChange = onChange;
  }

  // -- Getters ----------------------------------------------------------------

  /** @returns {ChatMessage[]} */
  get currentMessages() { return this.#currentMessages; }
  /** @returns {string} */
  get streamingText() { return this.#streamingText; }
  /** @returns {boolean} */
  get hasMore() { return this.#hasMore; }

  // -- Mutations --------------------------------------------------------------

  /** Reset state when switching conversations. */
  clear() {
    this.#currentMessages = [];
    this.#streamingText = '';
    this.#hasMore = false;
  }

  /** @param {ChatMessage} msg */
  pushMessage(msg) {
    this.#currentMessages.push(msg);
  }

  /** Update a tool_call message by tool_call_id (for status updates).
   * Accumulates a statusHistory array so the UI can show the full progress log.
   */
  updateToolCall(/** @type {string} */ toolCallId, /** @type {string} */ content) {
    if (!toolCallId) {
      // Fallback: update the last tool_call message (legacy behavior)
      const last = this.#currentMessages[this.#currentMessages.length - 1];
      if (last?.role === 'tool_call') last.content = content;
      return;
    }
    const msg = this.#currentMessages.find(
      m => m.role === 'tool_call' && m.tool_call_id === toolCallId
    );
    if (msg) {
      if (!msg.statusHistory) msg.statusHistory = [];
      msg.statusHistory.push({ text: content, timestamp: new Date().toISOString() });
      // Cap history to prevent unbounded growth from long-running tools
      if (msg.statusHistory.length > 200) {
        msg.statusHistory = msg.statusHistory.slice(-200);
      }
      msg.content = content;
    }
  }

  /** Mark a tool message's widget as submitted with the given response
   * data. Used by live confirmation_response events and multi-tab sync:
   * the widget flips to its post-submit state once the backend
   * resolves the pending confirmation.
   * @param {string} toolCallId
   * @param {object} response
   */
  markToolWidgetSubmitted(toolCallId, response) {
    if (!toolCallId) return;
    const msg = this.#currentMessages.find(
      m => m.role === 'tool' && m.tool_call_id === toolCallId
    );
    if (msg) {
      msg.submitted = true;
      msg.response = response;
    }
  }

  /** Replace a tool_call message by tool_call_id with a completed tool result.
   * Carries over statusHistory from the in-progress message so the UI can still show it.
   */
  replaceToolCall(/** @type {string} */ toolCallId, /** @type {ChatMessage} */ msg) {
    if (!toolCallId) {
      // Fallback: replace the last tool_call (legacy behavior)
      const idx = this.#currentMessages.findLastIndex(m => m.role === 'tool_call');
      if (idx >= 0) this.#currentMessages[idx] = msg;
      return;
    }
    const idx = this.#currentMessages.findIndex(
      m => m.role === 'tool_call' && m.tool_call_id === toolCallId
    );
    if (idx >= 0) {
      const old = this.#currentMessages[idx];
      if (old.statusHistory?.length) {
        msg.statusHistory = old.statusHistory;
      }
      this.#currentMessages[idx] = msg;
    }
  }

  /** Insert a message before the last user message (for memory context). */
  insertBeforeLastUser(/** @type {ChatMessage} */ msg) {
    let lastUserIdx = -1;
    for (let i = this.#currentMessages.length - 1; i >= 0; i--) {
      if (this.#currentMessages[i].role === 'user') { lastUserIdx = i; break; }
    }
    if (lastUserIdx >= 0) {
      this.#currentMessages.splice(lastUserIdx, 0, msg);
    } else {
      this.#currentMessages.push(msg);
    }
  }

  clearStreamingText() {
    this.#streamingText = '';
  }

  /**
   * Handle a WebSocket message if it's message-related.
   * @param {object} msg
   * @param {string|null} currentConvId
   * @returns {boolean} true if handled
   */
  handleMessage(msg, currentConvId) {
    switch (msg.type) {
      case 'conv_history':
        if (msg.conv_id === currentConvId) {
          const existing = new Set(this.#currentMessages.map(m => m.timestamp));
          const newMsgs = (msg.messages || []).filter(
            /** @param {ChatMessage} m */ (m) => !existing.has(m.timestamp)
              && m.role !== 'wake_trigger'  // synthetic wake prompts are not user messages
          ).map(/** @param {ChatMessage} m */ (m) => {
            // Map background_event archive records to a renderable form
            if (m.role === 'background_event') {
              return { role: 'background_event', record: m, timestamp: m.timestamp };
            }
            return m;
          });
          this.#currentMessages = [...this.#mergeToolMessages(newMsgs), ...this.#currentMessages];
          this.#hasMore = msg.has_more;
        }
        return true;

      case 'chunk':
        if (msg.conv_id === currentConvId) {
          this.#streamingText += msg.text;
        }
        return true;

      case 'message_complete':
        if (msg.conv_id === currentConvId) {
          this.#currentMessages.push({
            role: msg.role || 'assistant',
            content: msg.text,
            timestamp: new Date().toISOString(),
            usage: msg.usage || null,
          });
          this.#streamingText = '';
        }
        return true;

      case 'user_message':
        // Multi-tab sync: add user message if not already present
        // (the originating tab adds it locally before the event arrives).
        //
        // Scan backwards past non-user transport/meta messages
        // (role: 'command' from command_ack, 'system' from compaction, etc.)
        // to find the nearest actual user message before deduping. The
        // previous check only looked at the last message, which doubled
        // the echo whenever command_ack or similar landed in between
        // the optimistic add and this event — see !newsletter / any
        // inline command's double-bubble symptom.
        if (msg.conv_id === currentConvId) {
          let foundDupe = false;
          for (let i = this.#currentMessages.length - 1; i >= 0; i--) {
            const m = this.#currentMessages[i];
            if (m.role === 'user') {
              foundDupe = m.content === msg.text;
              break;
            }
          }
          if (!foundDupe) {
            this.#currentMessages.push({
              role: 'user',
              content: msg.text,
              timestamp: new Date().toISOString(),
            });
          }
        }
        return true;

      case 'command_ack':
        if (msg.conv_id === currentConvId) {
          this.#currentMessages.push({
            role: 'command',
            content: `Running skill: ${msg.skill}`,
            timestamp: new Date().toISOString(),
          });
        }
        return true;

      case 'compaction_done':
        if (msg.conv_id === currentConvId) {
          this.#currentMessages.push({
            role: 'compaction',
            content: `Conversation compacted: ${msg.before_messages} → ${msg.after_messages} messages`,
            timestamp: new Date().toISOString(),
          });
        }
        return true;

      case 'background_event':
        if (msg.conv_id === currentConvId) {
          const record = msg.record || {};
          this.#currentMessages.push({
            role: 'background_event',
            record,
            content: '',
            timestamp: record.timestamp || new Date().toISOString(),
          });
        }
        return true;

      default:
        return false;
    }
  }

  /**
   * Merge adjacent assistant+tool_calls and tool-result messages from history
   * into combined tool messages, so they render as single rows.
   * @param {ChatMessage[]} messages
   * @returns {ChatMessage[]}
   */
  #mergeToolMessages(messages) {
    /** @type {ChatMessage[]} */
    const merged = [];
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];

      // Assistant message with tool_calls — look ahead for matching tool results
      if (msg.role === 'assistant' && msg.tool_calls?.length) {
        // If there's text content, emit it as a plain assistant message
        if (msg.content) {
          merged.push({ ...msg, tool_calls: undefined });
        }
        // For each tool call, try to find its result in the following messages
        for (const tc of msg.tool_calls) {
          const toolName = tc.function?.name || 'tool';
          const tcId = tc.id;
          // Look ahead for matching tool result
          let resultContent = '';
          let resultShortText = '';
          /** @type {object|null} */
          let resultWidget = null;
          let resultSubmitted = false;
          /** @type {object|null} */
          let resultResponse = null;
          for (let j = i + 1; j < messages.length; j++) {
            if (messages[j].role === 'tool' && messages[j].tool_call_id === tcId) {
              resultContent = messages[j].content || '';
              resultShortText = messages[j].display_short_text || '';
              resultWidget = messages[j].widget || null;
              resultSubmitted = !!messages[j].submitted;
              resultResponse = messages[j].response || null;
              messages[j]._merged = true; // mark as consumed
              break;
            }
          }
          merged.push({
            role: 'tool',
            tool_call_id: tcId,
            tool: toolName,
            content: resultContent,
            display_short_text: resultShortText,
            widget: resultWidget,
            submitted: resultSubmitted,
            response: resultResponse,
            timestamp: msg.timestamp,
          });
        }
        continue;
      }

      // Skip tool results that were already merged
      if (msg._merged) continue;

      merged.push(msg);
    }
    return merged;
  }
}
