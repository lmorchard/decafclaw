import { MESSAGE_TYPES } from './message-types.js';
import {
  CHILD_ID_SEPARATOR,
  isSideEffectCommand,
  SIDE_EFFECT_COMMANDS,
} from './constants.js';

import { ApiError, DefaultService } from './api-client/index.js';

/** @typedef {import('./api-client/index.js').ConversationListingItem} ConversationMeta */
/** @typedef {import('./api-client/index.js').SystemConversationListingItem} SystemConversationMeta */
/** @typedef {import('./api-client/index.js').ConversationListingResponse['folders'][number]} FolderEntry */
/** @typedef {import('./api-client/index.js').SessionEntry} SessionEntry */

/**
 * @typedef {object} ChatMessage
 * @property {string} role
 * @property {string} [content]
 * @property {string} [thinking]
 * @property {object} [provenance]
 * @property {string[]} [recalled_facts]
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
 * @property {object} [data]
 * @property {{widget_type: string, target: string, data: object}|null} [widget]
 * @property {boolean} [submitted]
 * @property {object} [response]
 * @property {boolean} [is_error]
 * @property {boolean} [timed_out]
 * @property {number|null} [timeout_sec]
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
 * Parent conversation id for delegated children (`{root}--child-{hex}`,
 * #1112/#1115 session layout), or '' for standalone conversations.
 * @param {string} convId
 * @returns {string}
 */
export function parentConvIdOf(convId) {
  const id = String(convId || '');
  const idx = id.indexOf(CHILD_ID_SEPARATOR);
  return idx > 0 ? id.slice(0, idx) : '';
}

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

  // -- Session hierarchy state (#1115) --
  /** @type {SessionEntry[]} */
  #sessions = [];
  /** Debounce handle for the pushed-event hierarchy refresh (#1115 follow-up). */
  #sessionsRefreshTimer = 0;

  // -- Listing request ordering (#1443) --
  // Each listing can have several requests in flight (folder clicks, mutation
  // refreshes, reconnect). Only the newest request may apply its response.
  #conversationsSeq = 0;
  #archivedSeq = 0;
  #systemSeq = 0;
  #sessionsSeq = 0;

  // -- Activity status state --
  /** @type {Map<string, 'idle' | 'busy' | 'waiting' | 'finished'>} */
  #conversationStatuses = new Map();
  /** @type {Map<string, number>} monotonic revision counter per conversation */
  #statusRevisions = new Map();
  #statusSequence = 0;
  // -- Current conversation state --
  /** @type {string|null} */
  #currentConvId = null;
  /** @type {string} last resolved display title of the selected conversation */
  #currentConvTitle = '';
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
  /** @type {{name: string, models: string[]}[]} provider-grouped picker models (#1441) */
  #modelGroups = [];
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
    // Sessions feed the sidebar hierarchy (#1115); refresh alongside the
    // flat list so delegated children appear without a page reload.
    // A reconnect refreshes the folder being viewed, not the top level
    // (#1443): this handler also runs after every server restart, and
    // defaulting to '' here desynced the store from the sidebar's folder.
    // A chat then created in that folder was created there but the sidebar
    // refetched the top level, so its row stayed invisible until a reload.
    this.#ws.addEventListener('open', () => {
      this.listConversations(this.#currentFolder);
      this.listSessions(this.#currentFolder);
    });
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
  /** @returns {string} */
  get streamingThinking() { return this.#messageStore.streamingThinking; }
  /** @returns {Record<string, object[]>} */
  get streamingProvenance() { return this.#messageStore.streamingProvenance; }
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
  /**
   * Provider groups for the conversation model picker (#1441). A payload
   * that predates `model_groups` still yields one usable group from the
   * flat `availableModels` list.
   * @returns {{name: string, models: string[]}[]}
   */
  get modelGroups() {
    if (this.#modelGroups.length > 0) return this.#modelGroups;
    if (this.#availableModels.length === 0) return [];
    return [{ name: 'Models', models: this.#availableModels }];
  }
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
  /** @returns {SessionEntry[]} */
  get sessions() { return this.#sessions; }
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
   * Display title for a conversation, from whichever listing holds it: the
   * active, archived, or system flat lists, then the session hierarchy (which
   * is where delegated children appear). Empty for an id the store has never
   * listed, e.g. one created in another tab before the next refresh (#1503).
   * @param {string} convId
   * @returns {string}
   */
  getConversationTitle(convId) {
    if (!convId) return '';
    const flatLists = [this.#conversations, this.#archivedConversations, this.#systemConversations];
    for (const list of flatLists) {
      const match = (list || []).find((c) => c?.conv_id === convId);
      if (match?.title) return match.title;
    }
    for (const session of this.#sessions || []) {
      const match = (session?.conversations || []).find((c) => c?.conv_id === convId);
      if (match?.title) return match.title;
    }
    return '';
  }

  /**
   * Display title of the selected conversation, or ''. Listings are
   * folder-scoped, so the last resolved title is remembered: navigating the
   * sidebar to another folder, or a listing refresh that drops the selected
   * conversation, must not erase the open tab's name (#1503).
   * @returns {string}
   */
  get currentConversationTitle() {
    if (!this.#currentConvId) return '';
    const resolved = this.getConversationTitle(this.#currentConvId);
    if (resolved) this.#currentConvTitle = resolved;
    return this.#currentConvTitle;
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
    const seq = ++this.#conversationsSeq;
    const reqSeq = this.#statusSequence;
    try {
      const data = await DefaultService.listConversationsApiConversationsGet(folder || undefined);
      if (seq !== this.#conversationsSeq) return;
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
    const seq = ++this.#archivedSeq;
    const reqSeq = this.#statusSequence;
    try {
      const data = await DefaultService.listArchivedConversationsApiConversationsArchivedGet(folder || undefined);
      if (seq !== this.#archivedSeq) return;
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
    const seq = ++this.#systemSeq;
    const reqSeq = this.#statusSequence;
    try {
      const data = await DefaultService.listSystemConversationsApiConversationsSystemGet(folder || undefined);
      if (seq !== this.#systemSeq) return;
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
   * List sessions (root conversation + delegated subagents) from
   * `GET /api/sessions` (#1115 sidebar hierarchy).
   * @param {string} [folder]
   */
  async listSessions(folder = '') {
    const seq = ++this.#sessionsSeq;
    const reqSeq = this.#statusSequence;
    try {
      const data = await DefaultService.listSessionsApiSessionsGet(folder || undefined);
      if (seq !== this.#sessionsSeq) return;
      this.#sessions = data.sessions || [];
      this.#syncConversationStatuses(
        (this.#sessions || []).flatMap((s) => s.conversations || []),
        reqSeq,
      );
      this.#emitChange();
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to list sessions:', err);
    }
  }

  /**
   * Every user folder path, at any depth (#1277). Walks the existing
   * one-level `GET /api/conversations?folder=` listings breadth-first
   * instead of adding a route. Virtual folders (`_archived`, `_system`) and
   * any `_`-prefixed path are excluded, since they are not move targets.
   * Reads the endpoint directly so the current listing is left untouched.
   * @returns {Promise<string[]>}
   */
  async listAllFolders() {
    /** @type {string[]} */
    const found = [];
    const seen = new Set();
    const queue = [''];
    while (queue.length > 0) {
      const parent = /** @type {string} */ (queue.shift());
      let data;
      try {
        data = await DefaultService.listConversationsApiConversationsGet(parent || undefined);
      } catch (err) {
        if (!(err instanceof ApiError)) console.error('Failed to list folders:', err);
        break;
      }
      for (const f of data.folders || []) {
        if (/** @type {any} */ (f).virtual) continue;
        const path = f.path || f.name;
        if (!path || path.split('/').some((seg) => seg.startsWith('_'))) continue;
        if (seen.has(path)) continue;
        seen.add(path);
        found.push(path);
        queue.push(path);
      }
    }
    return found.sort();
  }

  /**
   * True when convId is already present in the store's listings — flat,
   * session hierarchy, archived, or system. A pushed status event for an
   * id the store has never seen is a freshly spawned subagent whose row
   * cannot exist yet (#1115 follow-up).
   * @param {string} convId
   */
  #knowsConversation(convId) {
    const inList = (/** @type {Array<any>} */ list) => (list || []).some((c) => c?.conv_id === convId);
    if (inList(this.#conversations) || inList(this.#archivedConversations) || inList(this.#systemConversations)) {
      return true;
    }
    return (this.#sessions || []).some((s) => inList(s?.conversations));
  }

  /**
   * Debounced hierarchy refetch: a spawn batch fires several pushed events
   * (TURN_START + conversation_status per child) — one refetch covers them.
   * Without this, subagent rows only appear on page reload or when the
   * parent's whole turn finishes (#1115 follow-up).
   */
  #scheduleSessionsRefresh() {
    clearTimeout(this.#sessionsRefreshTimer);
    this.#sessionsRefreshTimer = setTimeout(() => {
      void this.listSessions(this.#currentFolder);
    }, 300);
  }

  /**
   * Cancel an in-flight subagent turn (#1115). Sends the cancel signal for
   * the child conversation; the server routes it through the conversation's
   * turn machinery.
   * @param {string} convId
   */
  cancelSubagent(convId) {
    if (!convId) return;
    this.#ws.send({ type: MESSAGE_TYPES.CANCEL_TURN, conv_id: convId });
  }

  /**
   * Create a conversation and select it. The model and folder default to the
   * current ones, so ⌘K matches the + Chat button (#1443).
   * @param {string} [title]
   * @param {string} [model]
   * @param {string} [folder]
   */
  async createConversation(title = '', model = this.#activeModel || this.#defaultModel, folder = this.#currentFolder) {
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
      // #1443 — the active sidebar renders the session hierarchy when it is
      // non-empty, so a new conversation is invisible until that list is
      // refetched. #1274 covered rename/archive/unarchive/move, not create.
      // Refetch the folder the conversation was created in, not
      // `#currentFolder`: the sidebar passes its rendered folder explicitly,
      // and a stale store folder would otherwise refetch the wrong listing
      // and leave the new row invisible.
      // Refresh after the queued message starts so the extra GET never delays
      // the first turn.
      await this.listSessions(folder);
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
      // #1274 — the sidebar hierarchy renders from sessions, not the flat list;
      // refresh it so a rename shows without a page reload.
      await this.listSessions(this.#currentFolder);
    } catch (err) {
      if (err instanceof ApiError) return;
      console.error('Failed to rename conversation:', err);
    }
  }

  /**
   * Request an on-demand title regeneration for a conversation (#1156).
   * Server-side side-effect command handled via websocket like /retitle.
   * @param {string} convId
   */
  retitleConversation(convId) {
    if (!convId) return;
    this.#ws.send({ type: MESSAGE_TYPES.SEND, conv_id: convId, text: SIDE_EFFECT_COMMANDS.RETITLE });
  }

  /**
   * Fork the current conversation into a seeded top-level conversation
   * (#1570). General forks branch the recent exchange; the `dissect`
   * template carries a failing tool call and its exact error. Used by the
   * errored-tool-card button; the server also appends a notice card and
   * starts the child turn.
   * @param {{toolCallId?: string, instruction?: string, template?: string}} [options]
   * @returns {Promise<string|null>} new conversation id, or null on failure
   */
  async forkConversation({ toolCallId = '', instruction = '', template = 'general' } = {}) {
    if (!this.#currentConvId || parentConvIdOf(this.#currentConvId)) return null;
    try {
      const conv = await DefaultService.forkConversationApiConversationsIdForkPost(
        this.#currentConvId,
        {
          template,
          ...(toolCallId ? { tool_call_id: toolCallId } : {}),
          ...(instruction ? { instruction } : {}),
        },
      );
      return conv?.conv_id || null;
    } catch (err) {
      if (!(err instanceof ApiError)) console.error('Failed to fork conversation:', err);
      return null;
    }
  }

  /**
   * Move a conversation (a session's root) into a folder; '' is the top level.
   * Returns false when the move failed so callers can tell the user (#1277).
   * @param {string} convId @param {string} folder
   * @returns {Promise<boolean>}
   */
  async moveConversation(convId, folder) {
    try {
      await DefaultService.renameConversationApiConversationsIdPatch(convId, { folder }, true);
      // Re-fetch current folder listing
      await this.listConversations(this.#currentFolder);
      // #1274 — a folder move changes which session group the conversation
      // belongs to; refresh the hierarchy so it follows. #1277 — the write
      // must update both sources or the Chats listing keeps the stale folder.
      await this.listSessions(this.#currentFolder);
      return true;
    } catch (err) {
      if (err instanceof ApiError) return false;
      console.error('Failed to move conversation:', err);
      return false;
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
      // #1274 — the archived conversation must leave the sidebar hierarchy too;
      // it renders from sessions, which the flat-list refetch above does not touch.
      await this.listSessions(this.#currentFolder);
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
      // Re-fetch archived listing to update folder counts. This is the only
      // refresh delete needs: a deleted conversation was already out of the
      // active hierarchy (it left when archived), so no sessions refetch — and
      // the listArchivedConversations emission above re-renders the section.
      await this.listArchivedConversations(this.#archivedCurrentFolder);
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
      // #1274 — the restored conversation must reappear in the sidebar hierarchy.
      await this.listSessions(this.#currentFolder);
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
    // Never carry the previous conversation's name into the new tab title.
    this.#currentConvTitle = this.getConversationTitle(convId);
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
    // Delegated children are viewable read-only (#1115): only the parent
    // conversation can be prompted. Child-shaped ids are gated immediately
    // — don't wait for the server's CONV_SELECTED read_only echo, or the
    // input blinks enabled for the round-trip.
    this.#readOnly = Boolean(parentConvIdOf(convId));
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
      this.createConversation();
      return;
    }
    // Delegated children are read-only (#1115): the transcript is inspectable
    // but not promptable. The server refuses sends to child conversations
    // too; this keeps the optimistic local echo from lying about it.
    if (parentConvIdOf(this.#currentConvId)) return;
    // Side-effect commands (terminal, retitle, update — #442, #1156, #981) are
    // human-only: they run NO agent turn, so they must not engage the
    // conversation's busy/turn machinery. Sending them like a normal message
    // would set `#busy` with nothing to clear it (the server sends no
    // MESSAGE_COMPLETE for them) — a perpetual spinner. Fire them off without
    // the busy flag or an optimistic user echo. Mirrors the server-side guards
    // in _handle_send.
    if (isSideEffectCommand(text)) {
      this.#ws.send({ type: MESSAGE_TYPES.SEND, conv_id: this.#currentConvId, text });
      return;
    }
    // Capture the turn's busy state BEFORE the optimistic update below sets
    // `#busy = true` (#1442). A Send while a turn runs steers it; an idle
    // Send omits the flag so the server starts a normal turn. The flag is
    // omitted (not `false`) for idle sends so the frame matches older clients.
    const wasBusy = this.#busy;
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
    if (wasBusy) wsMsg.steering = true;
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
        // Grouped payload (#1441). Older servers omit it: keep whatever flat
        // list arrived and let the getter synthesize a single group.
        if (Array.isArray(msg.model_groups)) this.#modelGroups = msg.model_groups;
        else if ('available_models' in msg) this.#modelGroups = [];
        // `in` (not truthiness): an explicitly cleared default arrives as ''
        // and must clear the stale marker (#1211).
        if ('default_model' in msg) this.#defaultModel = msg.default_model || '';
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
      if (msg.type === MESSAGE_TYPES.FORK_NOTICE && msg.conv_id === this.#currentConvId) {
        // The new fork is a separate session in the sidebar; refetch so its
        // row appears without a page reload.
        this.listSessions(this.#currentFolder);
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
          // Refresh the session hierarchy: a finished turn may have spawned
          // subagent children (#1115) that only a refetch will reveal.
          this.listSessions(this.#currentFolder);
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
        // The server names the conversation it just subscribed. Listings are
        // folder-scoped, so for a deep link into another folder this is the
        // only title available (#1503). A late reply for a conversation we
        // already left must not rename the current tab.
        if (msg.title && msg.conv_id === this.#currentConvId) {
          this.#currentConvTitle = msg.title;
        }
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
          // A pushed status for a conversation the store has never seen is
          // a freshly spawned subagent — refetch the hierarchy so its row
          // appears without a page reload (#1115 follow-up).
          if (!this.#knowsConversation(convId)) this.#scheduleSessionsRefresh();
        }
        break;
      }

      case MESSAGE_TYPES.CONV_RENAMED: {
        // #1072 — background auto-titling renamed a conversation; patch the
        // in-memory listing so the sidebar re-renders without a refetch.
        const convId = msg.conv_id;
        const conv = convId ? this.#conversations.find((c) => c.conv_id === convId) : undefined;
        if (conv && msg.title) {
          conv.title = msg.title;
          if (msg.updated_at) {
            conv.updated_at = msg.updated_at;
            this.#conversations.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
          }
        }
        // A rename pushed for the selected conversation updates the tab title
        // even when no listing holds that conversation (#1503).
        if (convId === this.#currentConvId && msg.title) {
          this.#currentConvTitle = msg.title;
        }
        // #1274 — the sidebar hierarchy renders from sessions, which this patch
        // does not touch. Refresh it (debounced) when the renamed conversation is
        // present, so a background title change shows without a page reload. Gated
        // on the hierarchy containing it: an unlisted id needs no refetch.
        if (convId && msg.title && this.#sessions.some(
          (s) => (s?.conversations || []).some((c) => c?.conv_id === convId),
        )) {
          this.#scheduleSessionsRefresh();
        }
        break;
      }

      case MESSAGE_TYPES.TURN_START:
        if (msg.conv_id) {
          this.#setConversationStatus(msg.conv_id, 'busy');
          // Newly spawned subagent mid-parent-turn: its row doesn't exist
          // yet — refetch the hierarchy (debounced) (#1115 follow-up).
          if (!this.#knowsConversation(msg.conv_id)) this.#scheduleSessionsRefresh();
        }
        if (!msg.conv_id || msg.conv_id === this.#currentConvId) {
          this.#busy = true;
          this.#messageStore.clearStreamingText();
          this.#toolStatusStore.clearToolStatus();
        }
        break;

      case MESSAGE_TYPES.STREAM_STATE:
        // #1439 — a full history load carries the unarchived segment, so a
        // client that switched away mid-turn rebuilds what it missed. Apply
        // only for the visible conversation with a live turn; a late frame
        // for a conversation we left must not resurrect streaming text.
        if (msg.conv_id === this.#currentConvId && this.#busy) {
          this.#messageStore.setStreamingState(msg.text || '', msg.thinking || '');
          this.#emitChange();
        }
        break;

      case MESSAGE_TYPES.TURN_COMPLETE:
        // A finished turn leaves no stale streaming block (#1439).
        if (!msg.conv_id || msg.conv_id === this.#currentConvId) {
          this.#messageStore.clearStreamingText();
          this.#emitChange();
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
        this.#modelGroups = Array.isArray(msg.model_groups) ? msg.model_groups : [];
        // `in` (not truthiness): an explicitly cleared default arrives as ''
        // and must clear the stale marker (#1211).
        if ('default_model' in msg) this.#defaultModel = msg.default_model || '';
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
