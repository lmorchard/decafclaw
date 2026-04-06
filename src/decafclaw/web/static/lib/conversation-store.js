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
 * @property {Array<{text: string, timestamp: string}>} [statusHistory]
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

/**
 * @typedef {object} FolderEntry
 * @property {string} name
 * @property {string} path
 * @property {boolean} [virtual]
 */

import { uploadFile } from './upload-client.js';
import { encodePagePath } from './utils.js';
import { MessageStore } from './message-store.js';
import { ToolStatusStore } from './tool-status-store.js';
import { WebSocketClient } from './websocket-client.js';

/**
 * Central state store for conversations.
 * Conversation management (list, create, rename, archive, folders) uses REST.
 * Real-time chat streaming uses WebSocket.
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

  // -- Active conversations state --
  /** @type {ConversationMeta[]} */
  #conversations = [];
  /** @type {FolderEntry[]} */
  #folders = [];
  /** @type {string} */
  #currentFolder = '';

  // -- Archived conversations state --
  /** @type {ConversationMeta[]} */
  #archivedConversations = [];
  /** @type {FolderEntry[]} */
  #archivedFolders = [];
  /** @type {string} */
  #archivedCurrentFolder = '';

  // -- System conversations state --
  /** @type {object[]} */
  #systemConversations = [];
  /** @type {FolderEntry[]} */
  #systemFolders = [];
  /** @type {string} */
  #systemCurrentFolder = '';

  // -- Current conversation state --
  /** @type {string|null} */
  #currentConvId = null;
  /** @type {boolean} */
  #busy = false;
  /** @type {number} prompt tokens used in the last completed turn */
  #contextUsage = 0;
  /** @type {number} effective context limit (compaction threshold) */
  #contextLimit = 0;
  /** @type {string} current effort level for the active conversation */
  #currentEffort = 'default';
  /** @type {string} resolved model name for the current effort level */
  #effortModel = '';
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
  /** @returns {FolderEntry[]} */
  get folders() { return this.#folders; }
  /** @returns {string} */
  get currentFolder() { return this.#currentFolder; }
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
  /** @returns {FolderEntry[]} */
  get archivedFolders() { return this.#archivedFolders; }
  /** @returns {string} */
  get archivedCurrentFolder() { return this.#archivedCurrentFolder; }
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
  /** @returns {FolderEntry[]} */
  get systemFolders() { return this.#systemFolders; }
  /** @returns {string} */
  get systemCurrentFolder() { return this.#systemCurrentFolder; }
  /** @returns {boolean} */
  get isReadOnly() { return this.#readOnly; }

  // -- REST-based conversation management ------------------------------------

  /** @param {string} [folder] */
  async listConversations(folder = '') {
    try {
      const url = folder
        ? `/api/conversations?folder=${encodeURIComponent(folder)}`
        : '/api/conversations';
      const resp = await fetch(url);
      if (!resp.ok) return;
      const data = await resp.json();
      this.#conversations = data.conversations || [];
      this.#folders = data.folders || [];
      this.#currentFolder = data.folder || '';
      this.#emitChange();
    } catch (err) {
      console.error('Failed to list conversations:', err);
    }
  }

  /** @param {string} [folder] */
  async listArchivedConversations(folder = '') {
    try {
      const url = folder
        ? `/api/conversations/archived?folder=${encodeURIComponent(folder)}`
        : '/api/conversations/archived';
      const resp = await fetch(url);
      if (!resp.ok) return;
      const data = await resp.json();
      this.#archivedConversations = data.conversations || [];
      this.#archivedFolders = data.folders || [];
      this.#archivedCurrentFolder = data.folder || '';
      this.#emitChange();
    } catch (err) {
      console.error('Failed to list archived conversations:', err);
    }
  }

  /** @param {string} [folder] */
  async listSystemConversations(folder = '') {
    try {
      const url = folder
        ? `/api/conversations/system?folder=${encodeURIComponent(folder)}`
        : '/api/conversations/system';
      const resp = await fetch(url);
      if (!resp.ok) return;
      const data = await resp.json();
      this.#systemConversations = data.conversations || [];
      this.#systemFolders = data.folders || [];
      this.#systemCurrentFolder = data.folder || '';
      this.#emitChange();
    } catch (err) {
      console.error('Failed to list system conversations:', err);
    }
  }

  /**
   * @param {string} [title]
   * @param {string} [effort]
   * @param {string} [folder]
   */
  async createConversation(title = '', effort = '', folder = '') {
    try {
      /** @type {Record<string, string>} */
      const body = { title };
      if (effort) body.effort = effort;
      if (folder) body.folder = folder;
      const resp = await fetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) return;
      const conv = await resp.json();
      // Insert into local list and select
      this.#conversations.unshift(conv);
      if (conv.effort) this.#currentEffort = conv.effort;
      this.selectConversation(conv.conv_id);
      // Flush any message queued while the conversation was being created
      if (this.#pendingMessage) {
        const text = this.#pendingMessage;
        const atts = this.#pendingAttachments;
        this.#pendingMessage = null;
        this.#pendingAttachments = [];
        this.#uploadAndSend(conv.conv_id, text, atts);
      }
    } catch (err) {
      console.error('Failed to create conversation:', err);
    }
  }

  /** @param {string} convId @param {string} title */
  async renameConversation(convId, title) {
    try {
      const resp = await fetch(`/api/conversations/${convId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      if (!resp.ok) return;
      const updated = await resp.json();
      this.#conversations = this.#conversations.map(c =>
        c.conv_id === convId ? { ...c, ...updated } : c
      );
      this.#emitChange();
    } catch (err) {
      console.error('Failed to rename conversation:', err);
    }
  }

  /** @param {string} convId @param {string} folder */
  async moveConversation(convId, folder) {
    try {
      const resp = await fetch(`/api/conversations/${convId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder }),
      });
      if (!resp.ok) return;
      // Re-fetch current folder listing
      await this.listConversations(this.#currentFolder);
    } catch (err) {
      console.error('Failed to move conversation:', err);
    }
  }

  /** @param {string} convId */
  async archiveConversation(convId) {
    try {
      const resp = await fetch(`/api/conversations/${convId}/archive`, {
        method: 'POST',
      });
      if (!resp.ok) return;
      // If we're viewing the archived conversation, deselect it
      if (this.#currentConvId === convId) {
        this.#currentConvId = null;
        this.#messageStore.clear();
      }
      // Re-fetch current folder listing
      await this.listConversations(this.#currentFolder);
    } catch (err) {
      console.error('Failed to archive conversation:', err);
    }
  }

  /** @param {string} convId */
  async unarchiveConversation(convId) {
    try {
      const resp = await fetch(`/api/conversations/${convId}/unarchive`, {
        method: 'POST',
      });
      if (!resp.ok) return;
      // Re-fetch archived listing
      await this.listArchivedConversations(this.#archivedCurrentFolder);
    } catch (err) {
      console.error('Failed to unarchive conversation:', err);
    }
  }

  // -- Folder management (REST) -----------------------------------------------

  /** @param {string} path */
  async createFolder(path) {
    try {
      const resp = await fetch('/api/conversations/folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      if (!resp.ok) {
        const data = await resp.json();
        console.error('Failed to create folder:', data.error);
        return false;
      }
      await this.listConversations(this.#currentFolder);
      return true;
    } catch (err) {
      console.error('Failed to create folder:', err);
      return false;
    }
  }

  /** @param {string} path */
  async deleteFolder(path) {
    try {
      const resp = await fetch(`/api/conversations/folders/${encodePagePath(path)}`, {
        method: 'DELETE',
      });
      if (!resp.ok) {
        const data = await resp.json();
        console.error('Failed to delete folder:', data.error);
        return false;
      }
      await this.listConversations(this.#currentFolder);
      return true;
    } catch (err) {
      console.error('Failed to delete folder:', err);
      return false;
    }
  }

  /** @param {string} oldPath @param {string} newPath */
  async renameFolder(oldPath, newPath) {
    try {
      const resp = await fetch(`/api/conversations/folders/${encodePagePath(oldPath)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: newPath }),
      });
      if (!resp.ok) {
        const data = await resp.json();
        console.error('Failed to rename folder:', data.error);
        return false;
      }
      await this.listConversations(this.#currentFolder);
      return true;
    } catch (err) {
      console.error('Failed to rename folder:', err);
      return false;
    }
  }

  // -- WebSocket-based actions (chat streaming) --------------------------------

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
      this.createConversation('', effort, this.#currentFolder);
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
    const wikiPage = /** @type {any} */ (window).getOpenWikiPage?.();
    if (wikiPage) wsMsg.wiki_page = wikiPage;
    this.#ws.send(wsMsg);
    this.#emitChange();
  }

  /**
   * Upload any pending File objects, then send the message.
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

  // -- WebSocket message handling (chat streaming) ----------------------------

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
          this.listConversations(this.#currentFolder);
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
      case 'conv_selected':
        if (msg.read_only) this.#readOnly = true;
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
