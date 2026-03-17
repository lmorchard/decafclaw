/**
 * @typedef {object} ConversationMeta
 * @property {string} conv_id
 * @property {string} title
 * @property {string} created_at
 * @property {string} updated_at
 */

/**
 * @typedef {object} ChatMessage
 * @property {string} role
 * @property {string} [content]
 * @property {string} [timestamp]
 * @property {string} [tool]
 * @property {string} [tool_call_id]
 * @property {object[]} [tool_calls]
 * @property {boolean} [_merged]
 * @property {{prompt_tokens: number, completion_tokens: number, total_tokens: number}|null} [usage]
 */

/**
 * @typedef {object} PendingConfirm
 * @property {string} context_id
 * @property {string} tool
 * @property {string} command
 * @property {string} suggested_pattern
 * @property {string} message
 */

import { WebSocketClient } from './websocket-client.js';

/**
 * Central state store for conversations.
 * Talks to the server via WebSocketClient, holds all UI state,
 * and emits 'change' events for components to re-render.
 *
 * @fires ConversationStore#change
 */
export class ConversationStore extends EventTarget {
  /** @type {WebSocketClient} */
  #ws;
  /** @type {ConversationMeta[]} */
  #conversations = [];
  /** @type {string|null} */
  #currentConvId = null;
  /** @type {ChatMessage[]} */
  #currentMessages = [];
  /** @type {string} */
  #streamingText = '';
  /** @type {boolean} */
  #busy = false;
  /** @type {boolean} */
  #hasMore = false;
  /** @type {PendingConfirm[]} */
  #pendingConfirms = [];
  /** @type {string|null} */
  #toolStatus = null;
  /** @type {ConversationMeta[]} */
  #archivedConversations = [];
  /** @type {number} prompt tokens used in the last completed turn */
  #contextUsage = 0;
  /** @type {number} effective context limit (compaction threshold) */
  #contextLimit = 0;

  /** @param {WebSocketClient} wsClient */
  constructor(wsClient) {
    super();
    this.#ws = wsClient;
    this.#ws.addEventListener('message', (e) => this.#handleMessage(/** @type {CustomEvent} */(e).detail));
    this.#ws.addEventListener('open', () => this.listConversations());
  }

  // -- Getters (read by components) -------------------------------------------

  /** @returns {ConversationMeta[]} sorted by updated_at desc */
  get conversations() { return this.#conversations; }
  /** @returns {string|null} */
  get currentConvId() { return this.#currentConvId; }
  /** @returns {ChatMessage[]} */
  get currentMessages() { return this.#currentMessages; }
  /** @returns {string} */
  get streamingText() { return this.#streamingText; }
  /** @returns {boolean} */
  get isBusy() { return this.#busy; }
  /** @returns {boolean} */
  get hasMore() { return this.#hasMore; }
  /** @returns {PendingConfirm[]} */
  get pendingConfirms() { return this.#pendingConfirms; }
  /** @returns {string|null} */
  get toolStatus() { return this.#toolStatus; }
  /** @returns {ConversationMeta[]} */
  get archivedConversations() { return this.#archivedConversations; }
  /** @returns {number} */
  get contextUsage() { return this.#contextUsage; }
  /** @returns {number} */
  get contextLimit() { return this.#contextLimit; }

  // -- Actions (called by components) -----------------------------------------

  listConversations() {
    this.#ws.send({ type: 'list_convs' });
  }

  /** @param {string} [title] */
  createConversation(title = '') {
    this.#ws.send({ type: 'create_conv', title });
  }

  /** @param {string} convId */
  selectConversation(convId) {
    this.#currentConvId = convId;
    this.#currentMessages = [];
    this.#streamingText = '';
    this.#hasMore = false;
    this.#pendingConfirms = [];
    this.#toolStatus = null;
    this.#contextUsage = 0;
    this.#contextLimit = 0;
    this.#ws.send({ type: 'select_conv', conv_id: convId });
    this.#ws.send({ type: 'load_history', conv_id: convId, limit: 50 });
    this.#emitChange();
  }

  /** @param {string} convId @param {string} title */
  renameConversation(convId, title) {
    this.#ws.send({ type: 'rename_conv', conv_id: convId, title });
  }

  listArchivedConversations() {
    this.#ws.send({ type: 'list_archived' });
  }

  /** @param {string} convId */
  unarchiveConversation(convId) {
    this.#ws.send({ type: 'unarchive_conv', conv_id: convId });
  }

  /** @param {string} convId */
  archiveConversation(convId) {
    this.#ws.send({ type: 'archive_conv', conv_id: convId });
    // If we're viewing the archived conversation, deselect it
    if (this.#currentConvId === convId) {
      this.#currentConvId = null;
      this.#currentMessages = [];
      this.#streamingText = '';
      this.#emitChange();
    }
  }

  /** @param {string} text */
  sendMessage(text) {
    if (!this.#currentConvId || !text.trim()) return;
    // Optimistically add user message
    this.#currentMessages.push({ role: 'user', content: text, timestamp: new Date().toISOString() });
    this.#busy = true;
    this.#streamingText = '';
    this.#toolStatus = null;
    this.#ws.send({ type: 'send', conv_id: this.#currentConvId, text });
    this.#emitChange();
  }

  cancelTurn() {
    if (!this.#currentConvId) return;
    this.#ws.send({ type: 'cancel_turn', conv_id: this.#currentConvId });
  }

  /** @param {string} [before] timestamp cursor */
  loadMoreHistory(before = '') {
    if (!this.#currentConvId) return;
    const cursor = before || (this.#currentMessages[0]?.timestamp || '');
    this.#ws.send({ type: 'load_history', conv_id: this.#currentConvId, limit: 50, before: cursor });
  }

  /**
   * @param {string} contextId
   * @param {string} tool
   * @param {boolean} approved
   * @param {object} [extra]
   */
  respondToConfirm(contextId, tool, approved, extra = {}) {
    this.#ws.send({
      type: 'confirm_response',
      context_id: contextId,
      tool,
      approved,
      ...extra,
    });
    // Remove from pending
    this.#pendingConfirms = this.#pendingConfirms.filter(c => c.context_id !== contextId);
    this.#emitChange();
  }

  // -- Message handling (from WebSocket) --------------------------------------

  /** @param {object} msg */
  #handleMessage(msg) {
    switch (msg.type) {
      case 'conv_list':
        this.#conversations = msg.conversations || [];
        break;

      case 'conv_created':
        this.#conversations.unshift(msg);
        this.selectConversation(msg.conv_id);
        break;

      case 'conv_selected':
        // Acknowledged — no state change needed
        break;

      case 'conv_archived':
        this.#conversations = this.#conversations.filter(c => c.conv_id !== msg.conv_id);
        if (this.#currentConvId === msg.conv_id) {
          this.#currentConvId = null;
          this.#currentMessages = [];
        }
        // Add to archived list so it appears immediately if the section is open
        this.#archivedConversations = [
          { conv_id: msg.conv_id, title: msg.title,
            created_at: msg.created_at, updated_at: msg.updated_at },
          ...this.#archivedConversations,
        ];
        break;

      case 'archived_list':
        this.#archivedConversations = msg.conversations || [];
        break;

      case 'conv_unarchived':
        this.#archivedConversations = this.#archivedConversations.filter(
          c => c.conv_id !== msg.conv_id
        );
        break;

      case 'conv_history':
        if (msg.conv_id === this.#currentConvId) {
          // Prepend older messages
          const existing = new Set(this.#currentMessages.map(m => m.timestamp));
          const newMsgs = (msg.messages || []).filter(m => !existing.has(m.timestamp));
          this.#currentMessages = [...this.#mergeToolMessages(newMsgs), ...this.#currentMessages];
          this.#hasMore = msg.has_more;
          if (msg.context_limit) this.#contextLimit = msg.context_limit;
          if (msg.estimated_tokens) this.#contextUsage = msg.estimated_tokens;
        }
        break;

      case 'conv_renamed':
        this.#conversations = this.#conversations.map(c =>
          c.conv_id === msg.conv_id ? { ...c, title: msg.title } : c
        );
        break;

      case 'turn_start':
        this.#busy = true;
        this.#streamingText = '';
        this.#toolStatus = null;
        break;

      case 'chunk':
        if (msg.conv_id === this.#currentConvId) {
          this.#streamingText += msg.text;
        }
        break;

      case 'message_complete':
        if (msg.conv_id === this.#currentConvId) {
          this.#currentMessages.push({
            role: msg.role || 'assistant',
            content: msg.text,
            timestamp: new Date().toISOString(),
            usage: msg.usage || null,
          });
          this.#streamingText = '';
          this.#toolStatus = null;
          // Only mark not-busy on final message
          if (msg.final) {
            this.#busy = false;
            if (msg.usage?.prompt_tokens) this.#contextUsage = msg.usage.prompt_tokens;
            if (msg.context_limit) this.#contextLimit = msg.context_limit;
            // Refresh conversation list (updated_at changed)
            this.listConversations();
          }
        }
        break;

      case 'tool_start':
        this.#toolStatus = `Running ${msg.tool}...`;
        // Add a tool-call message to the conversation (matches history format)
        if (msg.conv_id === this.#currentConvId) {
          this.#currentMessages.push({
            role: 'tool_call',
            content: `Running ${msg.tool}...`,
            tool: msg.tool,
            timestamp: new Date().toISOString(),
          });
        }
        break;

      case 'tool_status':
        this.#toolStatus = `${msg.tool}: ${msg.message}`;
        // Update the last tool_call message if it exists
        if (msg.conv_id === this.#currentConvId) {
          const last = this.#currentMessages[this.#currentMessages.length - 1];
          if (last?.role === 'tool_call') {
            last.content = `${msg.tool}: ${msg.message}`;
          }
        }
        break;

      case 'tool_end':
        this.#toolStatus = null;
        // Replace the live tool_call message with a completed tool result
        if (msg.conv_id === this.#currentConvId) {
          const idx = this.#currentMessages.findLastIndex(m => m.role === 'tool_call');
          if (idx >= 0) {
            this.#currentMessages[idx] = {
              role: 'tool',
              content: msg.result_text || '',
              tool: msg.tool,
              timestamp: new Date().toISOString(),
            };
          }
        }
        break;

      case 'confirm_request':
        this.#pendingConfirms = [...this.#pendingConfirms, {
          context_id: msg.context_id,
          tool: msg.tool,
          command: msg.command || '',
          suggested_pattern: msg.suggested_pattern || '',
          message: msg.message || '',
        }];
        break;

      case 'compaction_done':
        if (msg.conv_id === this.#currentConvId) {
          this.#currentMessages.push({
            role: 'compaction',
            content: `Conversation compacted: ${msg.before_messages} → ${msg.after_messages} messages`,
            timestamp: new Date().toISOString(),
          });
        }
        break;

      case 'error':
        console.error('Server error:', msg.message);
        this.#busy = false;
        this.#toolStatus = null;
        break;

      default:
        // Ignore unknown message types
        break;
    }
    this.#emitChange();
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
          for (let j = i + 1; j < messages.length; j++) {
            if (messages[j].role === 'tool' && messages[j].tool_call_id === tcId) {
              resultContent = messages[j].content || '';
              messages[j]._merged = true; // mark as consumed
              break;
            }
          }
          merged.push({
            role: 'tool',
            tool: toolName,
            content: resultContent,
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

  #emitChange() {
    this.dispatchEvent(new CustomEvent('change'));
  }
}
