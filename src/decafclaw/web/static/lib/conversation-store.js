import { MESSAGE_TYPES } from './message-types.js';

import { ApiError, DefaultService } from './api-client/index.js';

/** @typedef {import('./api-client/index.js').ConversationListingItem} ConversationMeta */
/** @typedef {import('./api-client/index.js').SystemConversationListingItem} SystemConversationMeta */
/** @typedef {import('./api-client/index.js').ConversationListingResponse['folders'][number]} FolderEntry */

/**
 * @typedef {object} ChatMessage
 * @property {string} role
 * @property {string} [content]
 * @property {string} [timestamp]
 * @property {string} [tool]
 * @property {string} [command]
 * @property {string} [tool_call_id]
 * @property {string} [display_short_text]
 * @property {object[]} [tool_calls]
 * @property {boolean} [_merged]
 * @property {Array<{text: string, timestamp: string}>} [statusHistory]
 * @property {{prompt_tokens: number, completion_tokens: number, total_tokens: number}|null} [usage]
 * @property {object} [record]
 * @property {{widget_type: string, target: string, data: object}|null} [widget]
 * @property {boolean} [submitted]
 * @property {object} [response]
 * @property {string} [source]
 * @property {AttachmentResponse[]} [attachments]
 */

/**
 * @typedef {object} PendingConfirm
 * @property {string} context_id
 * @property {string} confirmation_id
 * @property {string} conv_id
 * @property {string} tool
 * @property {string} tool_call_id
 * @property {string} command
 * @property {string} suggested_pattern
 * @property {string} [decline_reason]
 * @property {string} [suggested_rule]
 * @property {string} message
 * @property {string} approve_label
 * @property {string} deny_label
 * @property {string} action_type
 * @property {object} action_data
 * @property {number|null} [timeout]
 * @property {string} [timestamp]
 */

import { uploadFile } from './upload-client.js';
/** @typedef {import('./api-client/index.js').AttachmentResponse} AttachmentResponse */
/** @typedef {{file: File}} PendingAttachment */
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
  /** @type {SystemConversationMeta[]} */
  #systemConversations = [];
  /** @type {FolderEntry[]} */
  #systemFolders = [];
  /** @type {string} */
  #systemCurrentFolder = '';

  // -- Activity status state --
  /** @type {Map<string, 'idle' | 'busy' | 'waiting' | 'finished'>} */
  #conversationStatuses = new Map();
  /** @type {Map<string, number>} monotonic revision counter per conversation */
  #statusRevisions = new Map();
  #statusSequence = 0;
  // -- Current conversation state --
  /** @type {string|null} */
  #currentConvId = null;
  /** @type {boolean} */
  #busy = false;
  /** @type {number} prompt tokens used in the last completed turn */
  #contextUsage = 0;
  /** @type {number} effective context limit (compaction threshold) */
  #contextLimit = 0;
  /** @type {string} active model config name for the conversation */
  #activeModel = '';
  /** @type {string[]} available model config names from server */
  #availableModels = [];
  /** @type {string} default model from server config */
  #defaultModel = '';
  /** @type {string} active session mode for the conversation */
  #activeMode = 'default';
  /** @type {{name: string, description: string, presets: string[], promoted_tools: string[]}[]} available session modes from server */
  #availableModes = [];
  /** @type {{name: string, description: string, argument_hint: string}[]} user-invokable commands from server */
  #commands = [];
  /** @type {boolean} whether the current conversation is read-only */
  #readOnly = false;
  /** @type {string|null} message queued while creating a conversation */
  #pendingMessage = null;
  /** @type {Array<AttachmentResponse | PendingAttachment>} attachments queued with pending message */
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
    // A reconnect gives us a brand-new socket, and the server tracks stream
    // subscriptions per socket — so without this, every per-conversation push
    // (canvas_update, sticky, tool status, streamed output) is delivered to
    // nobody until a full page reload. Deliberately NOT selectConversation():
    // that clears the message store and re-issues LOAD_HISTORY, which would
    // blank the transcript and refetch 50 messages on every transient blip.
    this.#ws.addEventListener('open', () => this.#resubscribe());
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
  get activeModel() { return this.#activeModel; }
  /** @returns {string[]} */
  get availableModels() { return this.#availableModels; }
  /** @returns {string} */
  get defaultModel() { return this.#defaultModel; }
  /** @returns {string} */
  get activeMode() { return this.#activeMode; }
  /** @returns {{name: string, description: string, presets: string[], promoted_tools: string[]}[]} */
  get availableModes() { return this.#availableModes; }
  /** @returns {{name: string, description: string, argument_hint: string}[]} */
  get commands() { return this.#commands; }
  /** @returns {SystemConversationMeta[]} */
  get systemConversations() { return this.#systemConversations; }
  /** @returns {FolderEntry[]} */
  get systemFolders() { return this.#systemFolders; }
  /** @returns {string} */
  get systemCurrentFolder() { return this.#systemCurrentFolder; }
  /** @returns {boolean} */
  get isReadOnly() { return this.#readOnly; }
  /**
   * @param {string} convId
   * @returns {'idle' | 'busy' | 'waiting' | 'finished'}
   */
  getConversationStatus(convId) {
    return this.#conversationStatuses.get(convId) || 'idle';
  }

  /**
   * @param {string} convId
   * @param {'idle' | 'busy' | 'waiting' | 'finished'} status
   */
  #setConversationStatus(convId, status) {
    this.#statusSequence++;
    this.#statusRevisions.set(convId, this.#statusSequence);
    this.#conversationStatuses.set(convId, status);
  }

  /**
   * @param {Array<{conv_id: string, status?: string}>} items
   * @param {number} reqSeq
   */
  #syncConversationStatuses(items, reqSeq) {
    for (const c of items) {
      if (!c.conv_id) continue;
      // Skip if a newer websocket/local transition occurred since this REST request was issued
      const lastUpdateSeq = this.#statusRevisions.get(c.conv_id) || 0;
      if (lastUpdateSeq > reqSeq) continue;

      const current = this.#conversationStatuses.get(c.conv_id);
      if (c.status === 'busy' || c.status === 'waiting') {
        this.#conversationStatuses.set(c.conv_id, c.status);
        this.#statusRevisions.set(c.conv_id, reqSeq);
      } else if (current !== 'finished') {
        this.#conversationStatuses.set(c.conv_id, /** @type {any} */ (c.status) || 'idle');
        this.#statusRevisions.set(c.conv_id, reqSeq);
      }
    }
  }

  // -- REST-based conversation management ------------------------------------

  /** @param {string} [folder] */
  async listConversations(folder = '') {
    const reqSeq = this.#statusSequence;
    try {
      const data = await DefaultService.listConversationsApiConversationsGet(folder || undefined);
      this.#conversations = data.conversations || [];
      this.#folders = data.folders || [];
      this.#currentFolder = data.folder || '';
      this.#syncConversationStatuses(this.#conversations, reqSeq);
      this.#emitChange();
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to list conversations:', err);
    }
  }

  /** @param {string} [folder] */
  async listArchivedConversations(folder = '') {
    const reqSeq = this.#statusSequence;
    try {
      const data = await DefaultService.listArchivedConversationsApiConversationsArchivedGet(folder || undefined);
      this.#archivedConversations = data.conversations || [];
      this.#archivedFolders = data.folders || [];
      this.#archivedCurrentFolder = data.folder || '';
      this.#syncConversationStatuses(this.#archivedConversations, reqSeq);
      this.#emitChange();
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to list archived conversations:', err);
    }
  }

  /** @param {string} [folder] */
  async listSystemConversations(folder = '') {
    const reqSeq = this.#statusSequence;
    try {
      const data = await DefaultService.listSystemConversationsApiConversationsSystemGet(folder || undefined);
      this.#systemConversations = data.conversations || [];
      this.#systemFolders = data.folders || [];
      this.#systemCurrentFolder = data.folder || '';
      this.#syncConversationStatuses(this.#systemConversations, reqSeq);
      this.#emitChange();
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to list system conversations:', err);
    }
  }

  /**
   * @param {string} [title]
   * @param {string} [model]
   * @param {string} [folder]
   */
  async createConversation(title = '', model = '', folder = '') {
    try {
      const conv = await DefaultService.createConversationApiConversationsPost({
        title, ...(model ? { model } : {}), ...(folder ? { folder } : {}),
      });
      // Insert into local list and select
      this.#conversations.unshift(conv);
      if (conv.model) this.#activeModel = conv.model;
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
      if (err instanceof ApiError) return;
      console.error('Failed to create conversation:', err);
    }
  }

  /** @param {string} convId @param {string} title */
  async renameConversation(convId, title) {
    try {
      const updated = await DefaultService.renameConversationApiConversationsIdPatch(convId, { title });
      this.#conversations = this.#conversations.map(c =>
        c.conv_id === convId ? { ...c, ...updated } : c
      );
      this.#emitChange();
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to rename conversation:', err);
    }
  }

  /** @param {string} convId @param {string} folder */
  async moveConversation(convId, folder) {
    try {
      await DefaultService.renameConversationApiConversationsIdPatch(convId, { folder }, true);
      // Re-fetch current folder listing
      await this.listConversations(this.#currentFolder);
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to move conversation:', err);
    }
  }

  /** @param {string} convId */
  async archiveConversation(convId) {
    try {
      await DefaultService.archiveConversationApiConversationsIdArchivePost(convId, true);
      // If we're viewing the archived conversation, deselect it
      if (this.#currentConvId === convId) {
        this.#currentConvId = null;
        this.#messageStore.clear();
      }
      // Re-fetch current folder listing
      await this.listConversations(this.#currentFolder);
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to archive conversation:', err);
    }
  }

  /** @param {string} convId */
  async deleteConversation(convId) {
    try {
      await DefaultService.deleteConversationApiConversationsIdDelete(convId, true);
      // If we're viewing the deleted conversation, deselect it
      if (this.#currentConvId === convId) {
        this.#currentConvId = null;
        this.#messageStore.clear();
      }
      // Re-fetch archived listing to update folder counts
      await this.listArchivedConversations(this.#archivedCurrentFolder);
      this.#emitChange();
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to delete conversation:', err);
    }
  }

  /** @param {string} convId */
  async unarchiveConversation(convId) {
    try {
      await DefaultService.unarchiveConversationApiConversationsIdUnarchivePost(convId, true);
      // Re-fetch archived listing
      await this.listArchivedConversations(this.#archivedCurrentFolder);
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to unarchive conversation:', err);
    }
  }

  // -- Folder management (REST) -----------------------------------------------

  /** @param {string} path */
  async createFolder(path) {
    try {
      await DefaultService.createConvFolderApiConversationsFoldersPost({ path }, true);
      await this.listConversations(this.#currentFolder);
      return true;
    } catch (err) {
      console.error('Failed to create folder:', err instanceof ApiError ? err.body?.error : err);
      return false;
    }
  }

  /** @param {string} path */
  async deleteFolder(path) {
    try {
      await DefaultService.deleteConvFolderApiConversationsFoldersPathDelete(path, true);
      await this.listConversations(this.#currentFolder);
      return true;
    } catch (err) {
      console.error('Failed to delete folder:', err instanceof ApiError ? err.body?.error : err);
      return false;
    }
  }

  /** @param {string} oldPath @param {string} newPath */
  async renameFolder(oldPath, newPath) {
    try {
      await DefaultService.renameConvFolderApiConversationsFoldersPathPut(oldPath, { path: newPath }, true);
      await this.listConversations(this.#currentFolder);
      return true;
    } catch (err) {
      console.error('Failed to rename folder:', err instanceof ApiError ? err.body?.error : err);
      return false;
    }
  }

  // -- WebSocket-based actions (chat streaming) --------------------------------

  /** @param {string} convId */
  selectConversation(convId) {
    this.#currentConvId = convId;
    if (this.#conversationStatuses.get(convId) === 'finished') {
      this.#setConversationStatus(convId, 'idle');
    }
    this.#busy = false;
    this.#messageStore.clear();
    this.#toolStatusStore.clear();
    this.#contextUsage = 0;
    this.#contextLimit = 0;
    this.#activeModel = '';
    this.#activeMode = 'default';
    this.#readOnly = false;
    this.#ws.send({ type: MESSAGE_TYPES.SELECT_CONV, conv_id: convId });
    this.#ws.send({ type: MESSAGE_TYPES.LOAD_HISTORY, conv_id: convId, limit: 50 });
    // Not on socket `open`: #704's guard says a reconnect with nothing
    // selected puts nothing on the wire, and this list is not worth weakening
    // it for. The accepted cost is an empty menu until a conversation exists.
    this.#ws.send({ type: MESSAGE_TYPES.LIST_COMMANDS });
    this.#emitChange();
  }

  /**
   * Re-subscribe the (re)connected socket to the current conversation.
   *
   * Reuses `select_conv` rather than a bespoke wire type because
   * `_handle_select_conv` already subscribes the socket (`web/websocket.py:173`)
   * and its `conv_selected` reply is idempotent for the client. Mirrors the
   * TUI's `__reconnected` handler (`tui/src/App.tsx:83`).
   *
   * No-op when nothing is selected: there is no stream to rejoin, and the
   * initial connect is handled by app.js's one-shot open handler.
   */
  #resubscribe() {
    if (!this.#currentConvId) return;
    this.#ws.send({ type: MESSAGE_TYPES.SELECT_CONV, conv_id: this.#currentConvId });
    // Re-ask every time, not once: an MCP server that connected while we were
    // disconnected contributes prompts the cached list has never seen.
    this.#ws.send({ type: MESSAGE_TYPES.LIST_COMMANDS });
    // Full refetch of history for the active conversation
    this.#busy = false;
    this.#messageStore.clear();
    this.#toolStatusStore.clear();
    this.#ws.send({ type: MESSAGE_TYPES.LOAD_HISTORY, conv_id: this.#currentConvId, limit: 50 });
  }

  /**
   * @param {string} text
   * @param {Array<AttachmentResponse | PendingAttachment>} [attachments]
   */
  sendMessage(text, attachments = []) {
    if (!text.trim() && !attachments.length) return;
    if (!this.#currentConvId) {
      // No conversation selected — create one and queue the message
      this.#pendingMessage = text;
      this.#pendingAttachments = attachments;
      this.createConversation('', this.#activeModel, this.#currentFolder);
      return;
    }
    // `/terminal` is a human-only side-effect command (#442): it opens a
    // canvas tab via the separate canvas channel and runs NO agent turn, so
    // it must not engage the conversation's busy/turn machinery. Sending it
    // like a normal message would set `#busy` with nothing to clear it (the
    // server sends no MESSAGE_COMPLETE for it) — a perpetual spinner. Fire it
    // off without the busy flag or an optimistic user echo. Mirrors the
    // server-side guard in _handle_send.
    const trimmed = text.trim();
    if (trimmed === '/terminal' || trimmed.startsWith('/terminal ')) {
      this.#ws.send({ type: MESSAGE_TYPES.SEND, conv_id: this.#currentConvId, text });
      return;
    }
    // Optimistically add user message
    const userMsg = { role: 'user', content: text, timestamp: new Date().toISOString() };
    if (attachments.length) userMsg.attachments = attachments;
    this.#messageStore.pushMessage(userMsg);
    this.#busy = true;
    this.#setConversationStatus(this.#currentConvId, 'busy');
    this.#messageStore.clearStreamingText();
    this.#toolStatusStore.clearToolStatus();
    const wsMsg = { type: MESSAGE_TYPES.SEND, conv_id: this.#currentConvId, text };
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
   * @param {Array<AttachmentResponse | PendingAttachment>} attachments
   */
  async #uploadAndSend(convId, text, attachments) {
    /** @type {AttachmentResponse[]} */
    const uploaded = [];
    for (const att of attachments) {
      if ('file' in att) {
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

  /** @param {string} model */
  setModel(model) {
    this.#activeModel = model;
    if (this.#currentConvId) {
      this.#ws.send({ type: MESSAGE_TYPES.SET_MODEL, conv_id: this.#currentConvId, model });
    }
    this.#emitChange();
  }

  /** @param {string} mode */
  setMode(mode) {
    if (this.#currentConvId) {
      this.#ws.send({ type: MESSAGE_TYPES.SET_MODE, conv_id: this.#currentConvId, mode });
    }
  }

  cancelTurn() {
    if (!this.#currentConvId) return;
    this.#ws.send({ type: MESSAGE_TYPES.CANCEL_TURN, conv_id: this.#currentConvId });
  }

  /** @param {string} [before] timestamp cursor */
  loadMoreHistory(before = '') {
    if (!this.#currentConvId) return;
    const cursor = before || (this.#messageStore.currentMessages[0]?.timestamp || '');
    this.#ws.send({ type: MESSAGE_TYPES.LOAD_HISTORY, conv_id: this.#currentConvId, limit: 50, before: cursor });
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

  /**
   * Send a widget_response for an input widget submission.
   * @param {string} toolCallId
   * @param {object} data
   */
  respondToWidget(toolCallId, data) {
    this.#toolStatusStore.respondToWidget(toolCallId, data);
  }

  // -- WebSocket message handling (chat streaming) ----------------------------

  /** @param {object} msg */
  #handleMessage(msg) {
    // Delegate to sub-stores first
    if (this.#messageStore.handleMessage(msg, this.#currentConvId)) {
      // Handle side effects that live in ConversationStore
      if (msg.type === MESSAGE_TYPES.CONV_HISTORY && msg.conv_id === this.#currentConvId) {
        if (msg.context_limit) this.#contextLimit = msg.context_limit;
        if (msg.estimated_tokens) this.#contextUsage = msg.estimated_tokens;
        if (msg.active_model) this.#activeModel = msg.active_model;
        if (msg.available_models) this.#availableModels = msg.available_models;
        if (msg.default_model) this.#defaultModel = msg.default_model;
        if (msg.active_mode) this.#activeMode = msg.active_mode;
        if (msg.available_modes) this.#availableModes = msg.available_modes;
        if (msg.read_only) this.#readOnly = true;
        if (msg.turn_active) this.#busy = true;
        // Restore pending confirmation from server state (survives reload)
        if (msg.pending_confirmation) {
          this.#toolStatusStore.handleMessage({
            type: MESSAGE_TYPES.CONFIRM_REQUEST,
            conv_id: this.#currentConvId,
            ...msg.pending_confirmation,
          }, this.#currentConvId);
        }
      }
      if (msg.type === MESSAGE_TYPES.MESSAGE_COMPLETE && msg.conv_id === this.#currentConvId) {
        this.#toolStatusStore.clearToolStatus();
        if (msg.final) {
          this.#busy = false;
          this.#setConversationStatus(msg.conv_id, 'idle');
          // #1005 — prefer the explicit context_usage field (the meter's
          // value, which reflects the post-compaction estimate on a
          // compacting turn); fall back to usage.prompt_tokens for older
          // servers that predate the dedicated field. usage.prompt_tokens
          // itself has a different meaning (the last LLM call's actual prompt
          // size) — only the fallback path above treats it as a meter value,
          // matching the historical behavior before #1005.
          const meter = msg.context_usage ?? msg.usage?.prompt_tokens;
          if (meter) this.#contextUsage = meter;
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
      case MESSAGE_TYPES.CONV_SELECTED:
        if (msg.read_only) this.#readOnly = true;
        // Restore pending confirmation from server state (survives reload)
        if (msg.pending_confirmation) {
          this.#toolStatusStore.handleMessage({
            type: MESSAGE_TYPES.CONFIRM_REQUEST,
            conv_id: msg.conv_id || this.#currentConvId,
            ...msg.pending_confirmation,
          }, this.#currentConvId);
        }
        break;

      case MESSAGE_TYPES.CONVERSATION_STATUS: {
        const convId = msg.conv_id;
        if (convId) {
          if (msg.status === 'finished') {
            if (convId === this.#currentConvId) {
              this.#setConversationStatus(convId, 'idle');
            } else {
              this.#setConversationStatus(convId, 'finished');
            }
          } else {
            this.#setConversationStatus(convId, msg.status || 'idle');
          }
          this.#emitChange();
        }
        break;
      }

      case MESSAGE_TYPES.TURN_START:
        if (msg.conv_id) {
          this.#setConversationStatus(msg.conv_id, 'busy');
        }
        if (!msg.conv_id || msg.conv_id === this.#currentConvId) {
          this.#busy = true;
          this.#messageStore.clearStreamingText();
          this.#toolStatusStore.clearToolStatus();
        }
        break;

      case MESSAGE_TYPES.MODEL_CHANGED:
        if (msg.conv_id === this.#currentConvId) {
          this.#activeModel = msg.model || '';
          this.#emitChange();
        }
        break;

      case MESSAGE_TYPES.MODE_CHANGED:
        if (msg.conv_id === this.#currentConvId) {
          this.#activeMode = msg.mode || '';
          this.#emitChange();
        }
        break;

      case MESSAGE_TYPES.MODELS_AVAILABLE:
        if (msg.available_models) this.#availableModels = msg.available_models;
        if (msg.default_model) this.#defaultModel = msg.default_model;
        break;

      case MESSAGE_TYPES.COMMAND_LIST:
        if (msg.commands) this.#commands = msg.commands;
        break;

      case MESSAGE_TYPES.ERROR:
        console.error('Server error:', msg.message);
        if (msg.conv_id) {
          this.#setConversationStatus(msg.conv_id, 'idle');
        } else if (this.#currentConvId) {
          this.#setConversationStatus(this.#currentConvId, 'idle');
        }
        if (!msg.conv_id || msg.conv_id === this.#currentConvId) {
          this.#busy = false;
          this.#toolStatusStore.clearToolStatus();
        }
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
