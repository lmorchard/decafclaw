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
 * @property {string} [display_short_text]
 * @property {object[]} [tool_calls]
 * @property {boolean} [_merged]
 * @property {{prompt_tokens: number, completion_tokens: number, total_tokens: number}|null} [usage]
 */

/**
 * @typedef {object} PendingConfirm
 * @property {string} context_id
 * @property {string} tool
 * @property {string} tool_call_id
 * @property {string} command
 * @property {string} suggested_pattern
 * @property {string} message
 */

import { uploadFile } from './upload-client.js';
import { MessageStore } from './message-store.js';
import { ToolStatusStore } from './tool-status-store.js';
import { WebSocketClient } from './websocket-client.js';

/**
 * Central state store for conversations.
 * Talks to the server via WebSocketClient, holds all UI state,
 * and emits 'change' events for components to re-render.
 *
 * Message and tool-status state are delegated to sub-stores.
 *
 * @fires ConversationStore#change
 */
export class ConversationStore extends EventTarget {
  /** @type {WebSocketClient} */
  #ws;
  /** @type {MessageStore} */
  #messageStore;
  /** @type {ToolStatusStore} */
  #toolStatusStore;
  /** @type {ConversationMeta[]} */
  #conversations = [];
  /** @type {string|null} */
  #currentConvId = null;
  /** @type {boolean} */
  #busy = false;
  /** @type {ConversationMeta[]} */
  #archivedConversations = [];
  /** @type {number} prompt tokens used in the last completed turn */
  #contextUsage = 0;
  /** @type {number} effective context limit (compaction threshold) */
  #contextLimit = 0;
  /** @type {string} current effort level for the active conversation */
  #currentEffort = 'default';
  /** @type {string} resolved model name for the current effort level */
  #effortModel = '';
  /** @type {object[]} system conversations (schedule, heartbeat, etc.) */
  #systemConversations = [];
  /** @type {boolean} whether the current conversation is read-only */
  #readOnly = false;
  /** @type {string|null} message queued while creating a conversation */
  #pendingMessage = null;
  /** @type {object[]} attachments queued with pending message */
  #pendingAttachments = [];

  /** @param {WebSocketClient} wsClient */
  constructor(wsClient) {
    super();
    this.#ws = wsClient;
    const onChange = () => this.#emitChange();
    this.#messageStore = new MessageStore(onChange);
    this.#toolStatusStore = new ToolStatusStore(onChange, wsClient, this.#messageStore);
    this.#ws.addEventListener('message', (e) => this.#handleMessage(/** @type {CustomEvent} */(e).detail));
    this.#ws.addEventListener('open', () => this.listConversations());
  }

  // -- Getters (read by components) -------------------------------------------

  /** @returns {ConversationMeta[]} sorted by updated_at desc */
  get conversations() { return this.#conversations; }
  /** @returns {string|null} */
  get currentConvId() { return this.#currentConvId; }
  /** @returns {ChatMessage[]} */
  get currentMessages() { return this.#messageStore.currentMessages; }
  /** @returns {string} */
  get streamingText() { return this.#messageStore.streamingText; }
  /** @returns {boolean} */
  get isBusy() { return this.#busy; }
  /** @returns {boolean} */
  get hasMore() { return this.#messageStore.hasMore; }
  /** @returns {PendingConfirm[]} */
  get pendingConfirms() { return this.#toolStatusStore.pendingConfirms; }
  /** @returns {string|null} */
  get toolStatus() { return this.#toolStatusStore.toolStatus; }
  /** @returns {ConversationMeta[]} */
  get archivedConversations() { return this.#archivedConversations; }
  /** @returns {number} */
  get contextUsage() { return this.#contextUsage; }
  /** @returns {number} */
  get contextLimit() { return this.#contextLimit; }
  /** @returns {string} */
  get currentEffort() { return this.#currentEffort; }
  /** @returns {string} */
  get effortModel() { return this.#effortModel; }
  /** @returns {object[]} */
  get systemConversations() { return this.#systemConversations; }
  /** @returns {boolean} */
  get isReadOnly() { return this.#readOnly; }

  // -- Actions (called by components) -----------------------------------------

  listConversations() {
    this.#ws.send({ type: 'list_convs' });
  }

  /** @param {string} [title] @param {string} [effort] */
  createConversation(title = '', effort = '') {
    const msg = { type: 'create_conv', title };
    if (effort) msg.effort = effort;
    this.#ws.send(msg);
  }

  /** @param {string} convId */
  selectConversation(convId) {
    this.#currentConvId = convId;
    this.#messageStore.clear();
    this.#toolStatusStore.clear();
    this.#contextUsage = 0;
    this.#contextLimit = 0;
    this.#currentEffort = 'default';
    this.#effortModel = '';
    this.#readOnly = false;
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

  listSystemConversations() {
    this.#ws.send({ type: 'list_system_convs' });
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
      this.#messageStore.clear();
      this.#emitChange();
    }
  }

  /**
   * @param {string} text
   * @param {{filename: string, path: string, mime_type: string}[]} [attachments]
   */
  sendMessage(text, attachments = []) {
    if (!text.trim() && !attachments.length) return;
    if (!this.#currentConvId) {
      // No conversation selected — create one and queue the message
      this.#pendingMessage = text;
      this.#pendingAttachments = attachments;
      const effort = this.#currentEffort !== 'default' ? this.#currentEffort : '';
      this.createConversation('', effort);
      return;
    }
    // Optimistically add user message
    const userMsg = { role: 'user', content: text, timestamp: new Date().toISOString() };
    if (attachments.length) userMsg.attachments = attachments;
    this.#messageStore.pushMessage(userMsg);
    this.#busy = true;
    this.#messageStore.clearStreamingText();
    this.#toolStatusStore.clearToolStatus();
    const wsMsg = { type: 'send', conv_id: this.#currentConvId, text };
    if (attachments.length) wsMsg.attachments = attachments;
    this.#ws.send(wsMsg);
    this.#emitChange();
  }

  /**
   * Upload any pending File objects, then send the message.
   * Used when a conversation was just created and files need uploading.
   * @param {string} convId
   * @param {string} text
   * @param {object[]} attachments
   */
  async #uploadAndSend(convId, text, attachments) {
    const uploaded = [];
    for (const att of attachments) {
      if (att.file) {
        try {
          const result = await uploadFile(convId, att.file);
          uploaded.push(result);
        } catch (err) {
          console.warn('Upload failed:', err.message);
        }
      } else {
        uploaded.push(att);
      }
    }
    this.sendMessage(text, uploaded);
  }

  /** @param {string} level */
  setEffort(level) {
    if (!this.#currentConvId) return;
    this.#ws.send({ type: 'set_effort', conv_id: this.#currentConvId, level });
  }

  cancelTurn() {
    if (!this.#currentConvId) return;
    this.#ws.send({ type: 'cancel_turn', conv_id: this.#currentConvId });
  }

  /** @param {string} [before] timestamp cursor */
  loadMoreHistory(before = '') {
    if (!this.#currentConvId) return;
    const cursor = before || (this.#messageStore.currentMessages[0]?.timestamp || '');
    this.#ws.send({ type: 'load_history', conv_id: this.#currentConvId, limit: 50, before: cursor });
  }

  /**
   * @param {string} contextId
   * @param {string} tool
   * @param {string} toolCallId
   * @param {boolean} approved
   * @param {object} [extra]
   */
  respondToConfirm(contextId, tool, toolCallId = '', approved = false, extra = {}) {
    this.#toolStatusStore.respondToConfirm(contextId, tool, toolCallId, approved, extra);
  }

  // -- Message handling (from WebSocket) --------------------------------------

  /** @param {object} msg */
  #handleMessage(msg) {
    // Delegate to sub-stores first
    if (this.#messageStore.handleMessage(msg, this.#currentConvId)) {
      // Handle side effects that live in ConversationStore
      if (msg.type === 'conv_history' && msg.conv_id === this.#currentConvId) {
        if (msg.context_limit) this.#contextLimit = msg.context_limit;
        if (msg.estimated_tokens) this.#contextUsage = msg.estimated_tokens;
        if (msg.current_effort) this.#currentEffort = msg.current_effort;
        if (msg.effort_model) this.#effortModel = msg.effort_model;
        if (msg.read_only) this.#readOnly = true;
      }
      if (msg.type === 'message_complete' && msg.conv_id === this.#currentConvId) {
        this.#toolStatusStore.clearToolStatus();
        if (msg.final) {
          this.#busy = false;
          if (msg.usage?.prompt_tokens) this.#contextUsage = msg.usage.prompt_tokens;
          if (msg.context_limit) this.#contextLimit = msg.context_limit;
          this.listConversations();
        }
      }
      this.#emitChange();
      return;
    }

    if (this.#toolStatusStore.handleMessage(msg, this.#currentConvId)) {
      this.#emitChange();
      return;
    }

    // Messages handled directly by ConversationStore
    switch (msg.type) {
      case 'conv_list':
        this.#conversations = msg.conversations || [];
        break;

      case 'conv_created':
        this.#conversations.unshift(msg);
        if (msg.effort) this.#currentEffort = msg.effort;
        this.selectConversation(msg.conv_id);
        // Flush any message queued while the conversation was being created
        if (this.#pendingMessage) {
          const text = this.#pendingMessage;
          const atts = this.#pendingAttachments;
          this.#pendingMessage = null;
          this.#pendingAttachments = [];
          this.#uploadAndSend(msg.conv_id, text, atts);
        }
        break;

      case 'conv_selected':
        if (msg.read_only) this.#readOnly = true;
        break;

      case 'system_conv_list':
        this.#systemConversations = msg.conversations || [];
        break;

      case 'conv_archived':
        this.#conversations = this.#conversations.filter(c => c.conv_id !== msg.conv_id);
        if (this.#currentConvId === msg.conv_id) {
          this.#currentConvId = null;
          this.#messageStore.clear();
        }
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

      case 'conv_renamed':
        this.#conversations = this.#conversations.map(c =>
          c.conv_id === msg.conv_id ? { ...c, title: msg.title } : c
        );
        break;

      case 'turn_start':
        this.#busy = true;
        this.#messageStore.clearStreamingText();
        this.#toolStatusStore.clearToolStatus();
        break;

      case 'effort_changed':
        if (msg.conv_id === this.#currentConvId) {
          this.#currentEffort = msg.level || 'default';
          this.#effortModel = msg.model || '';
        }
        break;

      case 'error':
        console.error('Server error:', msg.message);
        this.#busy = false;
        this.#toolStatusStore.clearToolStatus();
        if (this.#currentConvId && this.#messageStore.currentMessages.length === 0) {
          this.#currentConvId = null;
        }
        break;

      default:
        break;
    }
    this.#emitChange();
  }

  #emitChange() {
    this.dispatchEvent(new CustomEvent('change'));
  }
}
