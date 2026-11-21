import { LitElement, html, nothing } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';
import { parentConvIdOf } from '../lib/conversation-store.js';
import { getConvFromUrl } from '../lib/conversation-url.js';
import { STATUS_LABELS, STATUS_TITLE_LABELS } from '../lib/constants.js';
import { activateOnEnterOrSpace } from '../lib/row-activation.js';
import { showToast } from '../lib/toast.js';
import './context-inspector.js';
import './shell-approval-panel.js';
import './notification-inbox.js';
import './model-picker.js';
import './vault-sidebar.js';
import './files-sidebar.js';
import './schedules-sidebar.js';
import './tags-sidebar.js';

/** Per-conversation sidebar collapse state: `sidebar-collapsed:<conv_id>` */
const COLLAPSE_STORAGE_PREFIX = 'sidebar-collapsed:';
/** Pre-per-conversation global key, migrated to the open conversation. */
const LEGACY_COLLAPSE_STORAGE_KEY = 'sidebar-collapsed';

export class ConversationSidebar extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    authClient: { type: Object, attribute: false },
    _conversations: { type: Array, state: true },
    _sessions: { type: Array, state: true },
    _chatFolders: { type: Array, state: true },
    _chatSection: { type: String, state: true },
    _chatFolder: { type: String, state: true },
    _activeId: { type: String, state: true },
    _contextUsage: { type: Number, state: true },
    _contextLimit: { type: Number, state: true },
    _activeModel: { type: String, state: true },
    _availableModels: { type: Array, state: true },
    _modelGroups: { type: Array, state: true },
    _defaultModel: { type: String, state: true },
    _activeMode: { type: String, state: true },
    _availableModes: { type: Array, state: true },
    _isBusy: { type: Boolean, state: true },
    _isReadOnly: { type: Boolean, state: true },
    _collapsed: { type: Boolean, state: true },
    _mobileOpen: { type: Boolean, state: true },
    _sidebarTab: { type: String, state: true },
    _contextInspectorOpen: { type: Boolean, state: true },
    _contextVersion: { type: Number, state: true },
    _shellApprovalOpen: { type: Boolean, state: true },
    _openSchedule: { type: String, state: true },
    _hasControlsStrip: { type: Boolean, state: true },
    _movePickerConvId: { type: String, state: true },
    _movePickerFolders: { type: Array, state: true },
    _movePickerLoading: { type: Boolean, state: true },
    _dropTarget: { type: String, state: true },
    _searchQuery: { type: String, state: true },
    _searchMatches: { type: Array, state: true },
    _searchPending: { type: Boolean, state: true },
    _searchError: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {import("../lib/conversation-store.js").ConversationStore | null} */
    this.store = null;
    this.authClient = null;
    /** @type {Array<import("../lib/conversation-store.js").ConversationMeta | import("../lib/conversation-store.js").SystemConversationMeta>} */
    this._conversations = [];
    /** @type {import("../lib/conversation-store.js").SessionEntry[]} server-grouped sessions (#1115) */
    this._sessions = [];
    /** @type {import("../lib/conversation-store.js").FolderEntry[]} */
    this._chatFolders = [];
    /** @type {string} which section: '' (active), '_archived', '_system' */
    this._chatSection = '';
    /** @type {string} folder path within the section */
    this._chatFolder = '';
    this._activeId = null;
    this._contextUsage = 0;
    this._contextLimit = 0;
    this._activeModel = '';
    this._availableModels = [];
    this._modelGroups = [];
    this._defaultModel = '';
    this._activeMode = 'default';
    this._availableModes = [];
    this._isBusy = false;
    this._isReadOnly = false;
    this._collapsed = false;
    // Read the URL's conversation before the store delivers one, so a
    // reload of a collapsed conversation does not flash the expanded
    // sidebar while the WebSocket connects.
    const urlConvId = getConvFromUrl();
    if (urlConvId) this.#applyCollapsed(urlConvId);
    /** @type {Set<string>} session ids the user collapsed (#1115 follow-up) */
    this._collapsedSessions = new Set();
    /** @type {Set<string>} session ids the user expanded (#1262) */
    this._expandedSessions = new Set();
    this._mobileOpen = false;
    this._sidebarTab = 'conversations';
    this._contextInspectorOpen = false;
    this._contextVersion = 0;
    this._shellApprovalOpen = false;
    this._openSchedule = '';
    this._hasControlsStrip = document.querySelector('chat-controls') !== null;
    /** @type {string} conv id of the open "Move to…" picker, or '' (#1277) */
    this._movePickerConvId = '';
    /** @type {string[]} full folder paths listed by the picker (#1277) */
    this._movePickerFolders = [];
    this._movePickerLoading = false;
    /** @type {string|null} folder path highlighted as a drop target (#1277) */
    this._dropTarget = null;
    /** @type {HTMLElement|null} row action that opened the picker (focus return) */
    this._movePickerTrigger = null;
    /** @type {string} root conv id being dragged, '' when no drag is active */
    this._draggingConvId = '';
    /** @type {string} current sidebar session-search query (#1451) */
    this._searchQuery = '';
    /** @type {Array<import('../lib/api-client/index.js').ConversationSearchMatch>} */
    this._searchMatches = [];
    this._searchPending = false;
    this._searchError = '';
    /** @type {ReturnType<typeof setTimeout>|null} debounce timer for search */
    this._searchTimer = null;
    /** @type {number} newest search request; stale responses are dropped */
    this._searchSeq = 0;
  }

  openMobile() {
    this._mobileOpen = true;
  }

  /** Open the per-conversation shell approval panel (mode-picker trigger). */
  #openShellApproval() {
    this._shellApprovalOpen = true;
  }

  closeMobile() {
    this._mobileOpen = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._onToggleInspector = () => {
      this._contextInspectorOpen = !this._contextInspectorOpen;
    };
    document.addEventListener('toggle-context-inspector', this._onToggleInspector);
    this._onToggleShellApproval = () => {
      this._shellApprovalOpen = !this._shellApprovalOpen;
    };
    document.addEventListener('toggle-shell-approval', this._onToggleShellApproval);
    this._onStoreChange = () => {
      const newId = this.store?.currentConvId;
      const newSessions = this.store?.sessions || [];
      if (newId !== this._activeId) {
        this._contextInspectorOpen = false;
        this._shellApprovalOpen = false;
        // Collapse is per conversation: each tab reflects the conversation
        // it is showing instead of one global width for the whole browser.
        this.#applyCollapsed(newId);
        // Selecting a conversation auto-expands its session group (#1115
        // follow-up): the user may have collapsed it earlier, but clicking
        // into that session's conversation signals interest again. Reads
        // straight from the store so it also works on the same change
        // event that first delivers the sessions data.
        for (const g of newSessions) {
          if ((g.conversations || []).some((c) => c?.conv_id === newId)) this._collapsedSessions.delete(g.session_id);
        }
        if (newSessions.length === 0 && newId) this._collapsedSessions.delete(parentConvIdOf(newId) || newId);
      }
      this._activeId = newId;
      const newUsage = this.store?.contextUsage || 0;
      if (newUsage !== this._contextUsage) this._contextVersion++;
      this._contextUsage = newUsage;
      this._contextLimit = this.store?.contextLimit || 0;
      this._activeModel = this.store?.activeModel || '';
      this._availableModels = this.store?.availableModels || [];
      this._modelGroups = this.store?.modelGroups || [];
      this._defaultModel = this.store?.defaultModel || '';
      this._activeMode = this.store?.activeMode || 'default';
      this._availableModes = this.store?.availableModes || [];
      this._isBusy = Boolean(this.store?.isBusy);
      this._isReadOnly = Boolean(this.store?.isReadOnly);
      // Update conversation list and folders based on current section
      if (this._chatSection === '_archived') {
        this._conversations = [...(this.store?.archivedConversations || [])];
        this._chatFolders = this.store?.archivedFolders || [];
      } else if (this._chatSection === '_system') {
        this._conversations = [...(this.store?.systemConversations || [])];
        this._chatFolders = this.store?.systemFolders || [];
      } else {
        this._conversations = [...(this.store?.conversations || [])];
        this._chatFolders = this.store?.folders || [];
        // Server-grouped sessions (#1115) — the hierarchy data for the
        // active section; client-side grouping remains as fallback.
        this._sessions = [...newSessions];
      }
      this.requestUpdate();
    };
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener('toggle-context-inspector', this._onToggleInspector);
    document.removeEventListener('toggle-shell-approval', this._onToggleShellApproval);
    this.store?.removeEventListener('change', this._onStoreChange);
  }

  /** @param {Map} changedProps */
  updated(changedProps) {
    if (changedProps.has('store')) {
      const oldStore = changedProps.get('store');
      if (oldStore) oldStore.removeEventListener('change', this._onStoreChange);
      if (this.store) {
        this.store.addEventListener('change', this._onStoreChange);
        this._onStoreChange();
      }
    }
    // [collapsed] drives the desktop width-clamp CSS — suppress it when
    // mobile-open so the hamburger flow shows the full sidebar even if
    // the desktop session left _collapsed=true.
    this.toggleAttribute('collapsed', this._collapsed && !this._mobileOpen);
    this.toggleAttribute('mobile-open', this._mobileOpen);

    // Sync mode selector value if present
    const modeSelect = /** @type {HTMLSelectElement | null} */ (this.querySelector('#mode-select'));
    if (modeSelect) {
      const target = this._activeMode || 'default';
      if (target && modeSelect.value !== target) {
        modeSelect.value = target;
      }
    }
  }

  /** @param {string} tab */
  #switchTab(tab) {
    this._sidebarTab = tab;
    this.dispatchEvent(new CustomEvent('sidebar-tab-change', {
      detail: { tab },
      bubbles: true,
      composed: true,
    }));
  }

  /** Public: switch to wiki tab (called from app.js when a wiki link is clicked). */
  switchToWiki() {
    if (this._sidebarTab !== 'wiki') {
      this._sidebarTab = 'wiki';
    }
  }

  /** Public: switch to files tab. */
  switchToFiles() {
    if (this._sidebarTab !== 'files') {
      this._sidebarTab = 'files';
    }
  }

  /**
   * Public: navigate the sidebar to a specific folder.
   * @param {string} folderPath
   */
  navigateToFolder(folderPath) {
    const vs = /** @type {any} */ (this.querySelector('vault-sidebar'));
    vs?.navigateToFolder(folderPath);
  }

  /**
   * Navigate the sidebar to the folder containing a given page path.
   * Called when a page is opened (e.g. via wiki-link) to keep sidebar in sync.
   * @param {string} pagePath
   */
  navigateToPageFolder(pagePath) {
    this._sidebarTab = 'wiki';
    const vs = /** @type {any} */ (this.querySelector('vault-sidebar'));
    vs?.navigateToPageFolder(pagePath);
    const ts = /** @type {any} */ (this.querySelector('tags-sidebar'));
    if (ts) ts.openPage = pagePath;
  }

  /** Clear the open page highlight (called when wiki pane is closed). */
  clearOpenPage() {
    const vs = /** @type {any} */ (this.querySelector('vault-sidebar'));
    vs?.clearOpenPage();
    const ts = /** @type {any} */ (this.querySelector('tags-sidebar'));
    if (ts) ts.openPage = null;
  }

  /**
   * Public: navigate the files-sidebar to a specific folder.
   * @param {string} folderPath
   */
  navigateToFilesFolder(folderPath) {
    this._sidebarTab = 'files';
    const fs = /** @type {any} */ (this.querySelector('files-sidebar'));
    fs?.navigateToFolder(folderPath);
  }

  /**
   * Navigate the files-sidebar to the folder containing a given file path,
   * and mark that file as open (for row highlighting).
   * @param {string} filePath
   */
  navigateToFileFolder(filePath) {
    this._sidebarTab = 'files';
    const fs = /** @type {any} */ (this.querySelector('files-sidebar'));
    fs?.navigateToFileFolder(filePath);
  }

  /** Clear the open file highlight (called when file pane is closed). */
  clearOpenFile() {
    const fs = /** @type {any} */ (this.querySelector('files-sidebar'));
    fs?.clearOpenFile();
  }

  /** Public: switch to schedules tab and highlight the given schedule name. */
  setOpenSchedule(name) {
    this._openSchedule = name;
    this.switchToSchedules();
  }

  /** Public: clear the open schedule highlight (called when schedule pane is closed). */
  clearOpenSchedule() {
    this._openSchedule = '';
  }

  /** Public: switch to the schedules tab. */
  switchToSchedules() {
    if (this._sidebarTab !== 'schedules') {
      this._sidebarTab = 'schedules';
    }
  }

  /** @param {CustomEvent} _e */
  #handleWikiOpen(_e) {
    // wiki-open bubbles + composed from <vault-sidebar>, so app.js already receives it.
    // Parent only needs to close the mobile drawer (previously done inside #handleWikiSelect).
    this.closeMobile();
  }

  /** @param {CustomEvent} _e */
  #handleFileOpen(_e) {
    // file-open bubbles + composed from <files-sidebar>, so app.js already receives it.
    // Parent only needs to close the mobile drawer.
    this.closeMobile();
  }

  /** Load the stored collapse state for a conversation. */
  #applyCollapsed(convId) {
    this._collapsed = convId ? this.#readCollapsed(convId) : false;
  }

  /** @param {string} convId */
  #readCollapsed(convId) {
    const key = `${COLLAPSE_STORAGE_PREFIX}${convId}`;
    const stored = localStorage.getItem(key);
    if (stored !== null) return stored === 'true';
    const legacy = localStorage.getItem(LEGACY_COLLAPSE_STORAGE_KEY);
    if (legacy === null) return false;
    // One-time upgrade: the old global preference applies to the
    // conversation open when it was read.
    localStorage.setItem(key, legacy);
    localStorage.removeItem(LEGACY_COLLAPSE_STORAGE_KEY);
    return legacy === 'true';
  }

  #toggleCollapse() {
    this._collapsed = !this._collapsed;
    const convId = this._activeId || this.store?.currentConvId || getConvFromUrl();
    if (convId) localStorage.setItem(`${COLLAPSE_STORAGE_PREFIX}${convId}`, String(this._collapsed));
  }

  async #handleHome() {
    try {
      const data = await DefaultService.getHomeConversationApiConversationsHomeGet();
      if (data?.conv_id) {
        this.store?.selectConversation(data.conv_id);
      }
    } catch (e) {
      console.warn('Failed to fetch home conversation', e);
    }
  }

  #handleNew() {
    // Create in current folder (only in active section)
    const folder = this._chatSection === '' ? this._chatFolder : '';
    const model = this._activeModel || this._defaultModel;
    this.store?.createConversation('', model, folder);
  }

  async #createChatFolder() {
    const name = prompt('New folder name:');
    if (!name || !name.trim()) return;
    if (name.trim().startsWith('_')) {
      alert('Folder names starting with "_" are reserved.');
      return;
    }
    const path = this._chatFolder
      ? `${this._chatFolder}/${name.trim()}`
      : name.trim();
    const ok = await this.store?.createFolder(path);
    if (!ok) alert('Failed to create folder.');
  }

  /**
   * @param {string} folderPath
   * @param {string} folderName
   */
  async #renameChatFolder(folderPath, folderName) {
    const newName = prompt('Rename folder:', folderName);
    if (!newName || !newName.trim() || newName.trim() === folderName) return;
    if (newName.trim().startsWith('_')) {
      alert('Folder names starting with "_" are reserved.');
      return;
    }
    // Build new path: replace last segment
    const parts = folderPath.split('/');
    parts[parts.length - 1] = newName.trim();
    const newPath = parts.join('/');
    const ok = await this.store?.renameFolder(folderPath, newPath);
    if (!ok) alert('Failed to rename folder.');
  }

  /** @param {string} folderPath */
  async #deleteChatFolder(folderPath) {
    if (!confirm(`Delete folder "${folderPath}"? It must be empty.`)) return;
    const ok = await this.store?.deleteFolder(folderPath);
    if (!ok) alert('Failed to delete folder. It may not be empty.');
  }

  #renderChatBreadcrumbs() {
    // Build breadcrumb path based on section and folder
    const section = this._chatSection;
    const folder = this._chatFolder;

    // At the very top level — no breadcrumbs needed
    if (!section && !folder) return nothing;

    const crumbs = [];

    // Root "Chats" segment — always navigates to top level. While dragging a
    // session it is a drop target for the top level (#1277).
    crumbs.push(html`
      <button type="button" class="vault-breadcrumb-segment${this._dropTarget === '' ? ' drop-target' : ''}"
        @click=${() => this.#navigateChatFolder('', '')}
        @dragover=${(/** @type {DragEvent} */ e) => this.#handleDragOver(e, '')}
        @dragleave=${(/** @type {DragEvent} */ e) => this.#handleDragLeave(e)}
        @drop=${(/** @type {DragEvent} */ e) => this.#handleDrop(e, '')}>Chats</button>
    `);

    // Section segment (Archived or System)
    if (section === '_archived') {
      if (folder) {
        crumbs.push(html`
          <span class="vault-breadcrumb-sep">/</span>
          <button type="button" class="vault-breadcrumb-segment"
            @click=${() => this.#navigateChatFolder('_archived', '')}>Archived</button>
        `);
      } else {
        crumbs.push(html`
          <span class="vault-breadcrumb-sep">/</span>
          <span class="vault-breadcrumb-segment active">Archived</span>
        `);
      }
    } else if (section === '_system') {
      if (folder) {
        crumbs.push(html`
          <span class="vault-breadcrumb-sep">/</span>
          <button type="button" class="vault-breadcrumb-segment"
            @click=${() => this.#navigateChatFolder('_system', '')}>System</button>
        `);
      } else {
        crumbs.push(html`
          <span class="vault-breadcrumb-sep">/</span>
          <span class="vault-breadcrumb-segment active">System</span>
        `);
      }
    }

    // Folder path segments
    if (folder) {
      const segments = folder.split('/');
      segments.forEach((seg, i) => {
        const path = segments.slice(0, i + 1).join('/');
        const isLast = i === segments.length - 1;
        crumbs.push(html`
          <span class="vault-breadcrumb-sep">/</span>
          ${isLast
            ? html`<span class="vault-breadcrumb-segment active">${seg}</span>`
            : html`<button type="button" class="vault-breadcrumb-segment${this._dropTarget === path ? ' drop-target' : ''}"
                @click=${() => this.#navigateChatFolder(section, path)}
                @dragover=${(/** @type {DragEvent} */ e) => this.#handleDragOver(e, path)}
                @dragleave=${(/** @type {DragEvent} */ e) => this.#handleDragLeave(e)}
                @drop=${(/** @type {DragEvent} */ e) => this.#handleDrop(e, path)}>${seg}</button>`
          }
        `);
      });
    }

    return html`<div class="vault-breadcrumbs">${crumbs}</div>`;
  }

  /** Search input above the conversation list (#1451). */
  #renderSearchBox() {
    const hasQuery = Boolean(this._searchQuery.trim());
    return html`
      <div class="conv-search">
        <input
          class="conv-search-input"
          type="search"
          placeholder="Search sessions…"
          aria-label="Search sessions"
          .value=${this._searchQuery}
          aria-busy=${this._searchPending ? 'true' : 'false'}
          @input=${this.#handleSearchInput}
          @keydown=${this.#handleSearchKeydown}
        />
        ${hasQuery ? html`
          <button
            type="button"
            class="conv-search-clear dc-icon-btn"
            title="Clear search"
            aria-label="Clear search"
            @click=${() => { this.#clearSearch(); this.#focusSearchInput(); }}
          >×</button>
        ` : nothing}
      </div>
    `;
  }

  /** @param {InputEvent} e */
  #handleSearchInput(e) {
    const query = /** @type {HTMLInputElement} */ (e.target).value;
    this._searchQuery = query;
    // Any in-flight request now answers an older query; drop its result.
    this._searchSeq++;
    if (this._searchTimer) {
      clearTimeout(this._searchTimer);
      this._searchTimer = null;
    }
    if (!query.trim()) {
      // Invalidate an in-flight request so it cannot repopulate the cleared box.
      this._searchSeq++;
      this._searchMatches = [];
      this._searchPending = false;
      this._searchError = '';
      return;
    }
    this._searchPending = true;
    this._searchTimer = setTimeout(() => {
      this._searchTimer = null;
      void this.#runSearch(query);
    }, 200);
  }

  /** @param {KeyboardEvent} e */
  #handleSearchKeydown(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      this.#clearSearch();
      this.#focusSearchInput();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (this._searchTimer) {
        clearTimeout(this._searchTimer);
        this._searchTimer = null;
      }
      void this.#runSearch(this._searchQuery);
    }
  }

  /** @param {string} query */
  async #runSearch(query) {
    if (!query.trim()) return;
    const seq = ++this._searchSeq;
    this._searchPending = true;
    this._searchError = '';
    try {
      const data = await DefaultService.searchConversationsApiConversationsSearchGet(query, 20, true);
      if (seq !== this._searchSeq) return;
      this._searchMatches = data.matches || [];
    } catch (err) {
      if (seq !== this._searchSeq) return;
      this._searchMatches = [];
      this._searchError = 'Search failed';
      console.error('Session search failed:', err instanceof ApiError ? err.body?.error : err);
    } finally {
      if (seq === this._searchSeq) this._searchPending = false;
    }
  }

  #clearSearch() {
    if (this._searchTimer) {
      clearTimeout(this._searchTimer);
      this._searchTimer = null;
    }
    this._searchSeq++;
    this._searchQuery = '';
    this._searchMatches = [];
    this._searchPending = false;
    this._searchError = '';
  }

  #focusSearchInput() {
    /** @type {HTMLInputElement|null} */ (this.querySelector('.conv-search-input'))?.focus();
  }

  /** @param {string} convId @param {string} title */
  #selectSearchResult(convId, title) {
    this.store?.selectConversation(convId, title);
    this.#clearSearch();
    this.closeMobile();
  }

  #renderSearchResults() {
    if (this._searchError) {
      return html`<p class="conv-search-status" role="alert">${this._searchError}</p>`;
    }
    if (this._searchPending && this._searchMatches.length === 0) {
      return html`<p class="conv-search-status">Searching…</p>`;
    }
    if (this._searchMatches.length === 0) {
      return html`<p class="conv-search-status">No matching sessions</p>`;
    }
    return html`
      <div class="conv-search-results" role="list" aria-label="Session search results">
        ${this._searchMatches.map((m) => html`
          <div
            class="conv-item conv-search-result${m.conv_id === this._activeId ? ' active' : ''}"
            role="listitem"
            tabindex="0"
            aria-label=${`Open conversation ${m.title}`}
            @click=${(/** @type {MouseEvent} */ e) => {
              if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
              this.#selectSearchResult(m.conv_id, m.title);
            }}
            @keydown=${activateOnEnterOrSpace(() => this.#selectSearchResult(m.conv_id, m.title))}
            title=${m.title}
          >
            <div class="conv-search-body">
              <div class="conv-search-heading">
                <span class="conv-title">${m.title}</span>
                ${m.archived ? html`<span class="conv-type-badge">Archived</span>` : nothing}
              </div>
              ${m.snippet ? html`<span class="conv-search-snippet">${m.snippet}</span>` : nothing}
              <span class="conv-search-meta">
                ${m.matched_role ? `matched ${m.matched_role} message · ` : ''}${(m.updated_at || '').slice(0, 16).replace('T', ' ')}
              </span>
            </div>
          </div>
        `)}
      </div>
    `;
  }

  /** @param {string} convId */
  #handleSelect(convId) {
    this.store?.selectConversation(convId);
    this.closeMobile();
  }

  /**
   * Open a conversation in its own browser tab (#1515). The `?conv=` deep
   * link is the same one `app.js` restores on startup, so the new tab selects
   * the conversation once its WebSocket opens.
   * @param {string} convId
   */
  #openConversationInNewTab(convId) {
    const params = new URLSearchParams({ conv: convId });
    window.open(`${location.pathname}?${params}`, '_blank', 'noopener');
  }

  /**
   * Select on a plain left click; open a new tab when the click carries a
   * new-tab modifier. Middle click arrives through `auxclick`, not `click`.
   * @param {MouseEvent} e
   * @param {string} convId
   */
  #handleConversationClick(e, convId) {
    if (e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
      this.#handleSelect(convId);
      return;
    }
    if (e.button === 0) {
      e.preventDefault();
      this.#openConversationInNewTab(convId);
    }
  }

  /**
   * @param {MouseEvent} e
   * @param {string} convId
   */
  #handleConversationAuxClick(e, convId) {
    if (e.button !== 1) return;
    // A middle click on a row button (archive, move, cancel) belongs to the
    // button, not to the row's open-in-new-tab action.
    if (/** @type {Element} */ (e.target).closest('button')) return;
    e.preventDefault();
    this.#openConversationInNewTab(convId);
  }

  /** @param {string} convId */
  #handleArchive(convId) {
    this.store?.archiveConversation(convId);
  }

  /**
   * @param {string} convId
   * @param {string} currentTitle
   */
  #handleRename(convId, currentTitle) {
    const newTitle = prompt('Rename conversation:', currentTitle);
    if (newTitle && newTitle !== currentTitle) {
      this.store?.renameConversation(convId, newTitle);
    }
  }

  /** @param {string} convId */
  #handleRetitle(convId) {
    this.store?.retitleConversation(convId);
  }

  /**
   * Root conversation ids currently listed for the active section. Only those
   * rows are move sources; subagent children always move with their root.
   * @param {string} convId
   * @returns {boolean}
   */
  #isRootConvId(convId) {
    const groups = this._sessions.length > 0 ? this._sessions : this.#groupSessions(this._conversations);
    return groups.some((g) => {
      const root = (g.conversations || [])[0];
      return (root?.conv_id || g.session_id) === convId;
    });
  }

  /** @param {DragEvent} e @param {string} convId */
  #handleDragStart(e, convId) {
    if (!e.dataTransfer) return;
    e.dataTransfer.setData('text/plain', convId);
    e.dataTransfer.effectAllowed = 'move';
    this._draggingConvId = convId;
  }

  #resetDragState() {
    this._draggingConvId = '';
    this._dropTarget = null;
  }

  /** @param {DragEvent} e @param {string} folderPath */
  #handleDragOver(e, folderPath) {
    // Only the active section offers move targets; Archived/System rows are
    // not move sources and their breadcrumbs are not destinations.
    if (this._chatSection !== '' || !this._draggingConvId) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    this._dropTarget = folderPath;
  }

  /** @param {DragEvent} e */
  #handleDragLeave(e) {
    if (this._chatSection !== '') return;
    const to = /** @type {Node|null} */ (e.relatedTarget);
    const from = /** @type {Node} */ (e.currentTarget);
    if (to && from.contains(to)) return;
    this._dropTarget = null;
  }

  /** @param {DragEvent} e @param {string} targetFolder */
  #handleDrop(e, targetFolder) {
    if (this._chatSection !== '') return;
    e.preventDefault();
    const transferred = e.dataTransfer?.getData('text/plain') || '';
    const convId = transferred || this._draggingConvId;
    this.#resetDragState();
    if (!convId || !this.#isRootConvId(convId)) return;
    void this.#moveConversationTo(convId, targetFolder);
  }

  /**
   * Open the "Move to…" picker for a session root row (#1277). Lists the top
   * level plus every user folder path so nested folders are reachable without
   * navigating to them.
   * @param {string} convId
   * @param {HTMLElement|null} [trigger]
   */
  async #openMovePicker(convId, trigger = null) {
    this._movePickerTrigger = trigger;
    this._movePickerConvId = convId;
    this._movePickerFolders = [];
    this._movePickerLoading = true;
    // The folder walk is one round trip per level, so the loading dialog can be
    // visible for a while. Move focus inside it right away: keydown (and so
    // Escape) only reaches the dialog when focus is within it.
    await this.updateComplete;
    /** @type {HTMLElement|null} */ (this.querySelector('.conv-move-picker-close'))?.focus();
    const folders = (await this.store?.listAllFolders?.()) || [];
    if (this._movePickerConvId !== convId) return;
    this._movePickerFolders = folders;
    this._movePickerLoading = false;
    await this.updateComplete;
    // With no enabled destination (top level is current and no folders were
    // listed) the dialog still needs an initial focus target inside itself,
    // or Escape never reaches #handleMovePickerKeydown.
    const first = /** @type {HTMLElement|null} */ (
      this.querySelector('.conv-move-option:not([disabled])')
        || this.querySelector('.conv-move-picker-close')
    );
    first?.focus();
  }

  #closeMovePicker() {
    const trigger = this._movePickerTrigger;
    const active = /** @type {HTMLElement|null} */ (document.activeElement);
    const focusWasInPicker = Boolean(active && this.contains(active) && active.closest('.conv-move-picker'));
    this._movePickerTrigger = null;
    this._movePickerConvId = '';
    this._movePickerFolders = [];
    this._movePickerLoading = false;
    if (focusWasInPicker && trigger?.isConnected) trigger.focus();
  }

  /** @param {KeyboardEvent} e */
  #handleMovePickerKeydown(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      this.#closeMovePicker();
    }
  }

  /**
   * Move a session root into a folder ('' = top level). Store refreshes both
   * the flat listing and the session hierarchy on success; a failure shows a
   * toast and leaves the listing untouched.
   * @param {string} convId
   * @param {string} folder
   */
  async #moveConversationTo(convId, folder) {
    const currentFolder = this.store?.currentFolder ?? this._chatFolder ?? '';
    if (folder === currentFolder) return;
    const moved = await this.store?.moveConversation?.(convId, folder);
    if (moved) {
      this.#closeMovePicker();
    } else {
      showToast('Failed to move conversation');
    }
  }

  /** @param {string} convId */
  #handleUnarchive(convId) {
    this.store?.unarchiveConversation(convId);
  }

  /** @param {string} convId */
  #handleDelete(convId) {
    if (!confirm('Permanently delete this conversation? This cannot be undone.')) return;
    this.store?.deleteConversation(convId);
  }

  #openConfig() {
    this.dispatchEvent(new CustomEvent('config-open', {
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {CustomEvent} e */
  #onNotificationNavigate(e) {
    const convId = e.detail?.convId;
    if (!convId) return;
    this.store?.selectConversation(convId);
    this.closeMobile();
  }

  /** @param {CustomEvent} e */
  #onNotificationNavigateVault(e) {
    const page = e.detail?.page;
    if (!page) return;
    this.dispatchEvent(new CustomEvent('wiki-open', {
      detail: { page },
      bubbles: true,
      composed: true,
    }));
    this.closeMobile();
  }

  /** @param {Event} e */
  #handleModeChange(e) {
    const mode = /** @type {HTMLSelectElement} */ (e.target).value;
    this.store?.setMode(mode);
  }

  /**
   * @param {'idle' | 'busy' | 'waiting' | 'finished' | string} status
   */
  #renderStatusIndicator(status) {
    if (!status || status === 'idle') return nothing;
    if (status === 'busy') {
      return html`<span class="conv-status busy" title="Agent is busy" aria-label="Agent is busy"><span class="conv-status-spinner"></span></span>`;
    }
    if (status === 'waiting') {
      return html`<span class="conv-status waiting" title="Waiting on an answer" aria-label="Waiting on an answer">&#x25cf;</span>`;
    }
    if (status === 'finished') {
      return html`<span class="conv-status finished" title="Turn finished" aria-label="Turn finished">&#x2713;</span>`;
    }
    return nothing;
  }

  /**
   * Render a conversation item row in the sidebar list.
   * @param {import("../lib/conversation-store.js").ConversationMeta | import("../lib/conversation-store.js").SystemConversationMeta} conv
   * @param {object}   [opts]
   * @param {boolean}  [opts.isActive]    - Whether this row is currently selected
   * @param {string}   [opts.extraClass]  - Additional CSS class ('archived', 'system')
   * @param {string}   [opts.actionLabel] - Primary action button label (e.g. '×' or '↩')
   * @param {string}   [opts.actionTitle] - Button title text
   * @param {function} [opts.onAction]    - Click handler for the action button
   * @param {string}   [opts.action2Label] - Second action button label
   * @param {string}   [opts.action2Title] - Second action button title
   * @param {function} [opts.onAction2]   - Click handler for the second action button
   * @param {function} [opts.onDblClick]  - Double-click handler on the title span
   * @param {function} [opts.onMove]      - Move-to-folder handler (root rows, #1277)
   * @param {string}   [opts.moveTitle]   - Move button title text
   * @param {boolean}  [opts.draggable]   - Root row can be dragged onto a folder (#1277)
   * @param {function} [opts.onDragStart] - Drag start handler (root rows)
   * @param {function} [opts.onDragEnd]   - Drag end handler (root rows)
   * @param {string}   [opts.badge]       - Optional badge text shown after title
   * @param {string}   [opts.titleSuffix] - Extra text appended to the title attr
   */
  #renderConversationItem(conv, opts = {}) {
    const classes = ['conv-item'];
    if (opts.extraClass) classes.push(opts.extraClass);
    if (opts.isActive) classes.push('active');

    const status = this.store?.getConversationStatus?.(conv.conv_id) || (/** @type {any} */ (conv).status) || 'idle';
    if (status && status !== 'idle') {
      classes.push(`status-${status}`);
    }

    let titleSuffix = opts.titleSuffix || '';
    if (status && status !== 'idle') {
      const statusLabel = STATUS_TITLE_LABELS[status] || status;
      titleSuffix = titleSuffix ? `${titleSuffix}, ${statusLabel}` : statusLabel;
    }
    const titleAttr = titleSuffix ? `${conv.title} (${titleSuffix})` : conv.title;

    return html`
      <div
        class=${classes.join(' ')}
        tabindex="0"
        draggable=${opts.draggable ? 'true' : nothing}
        aria-label=${`Open conversation ${conv.title}`}
        @click=${(/** @type {MouseEvent} */ e) => this.#handleConversationClick(e, conv.conv_id)}
        @auxclick=${(/** @type {MouseEvent} */ e) => this.#handleConversationAuxClick(e, conv.conv_id)}
        @keydown=${activateOnEnterOrSpace(() => this.#handleSelect(conv.conv_id))}
        @dragstart=${opts.onDragStart || nothing}
        @dragend=${opts.onDragEnd || nothing}
        title=${titleAttr}
      >
        <span
          class="conv-title"
          @dblclick=${opts.onDblClick || nothing}
        >${conv.title}</span>
        ${opts.badge ? html`<span class="conv-type-badge">${opts.badge}</span>` : nothing}
        ${this.#renderStatusIndicator(status)}
        ${opts.onMove ? html`
          <button
            class="conv-archive conv-move-btn"
            @click=${(/** @type {MouseEvent} */ e) => {
              e.stopPropagation();
              opts.onMove(conv.conv_id, /** @type {HTMLElement} */ (e.currentTarget));
            }}
            title=${opts.moveTitle || 'Move to folder'}
            aria-label=${opts.moveTitle || 'Move to folder'}
            aria-haspopup="dialog"
          >\u21AA</button>
        ` : nothing}
        ${opts.onAction2 ? html`
          <button
            class="conv-archive"
            @click=${(/** @type {Event} */ e) => { e.stopPropagation(); opts.onAction2(conv.conv_id); }}
            title=${opts.action2Title || ''}
            aria-label=${opts.action2Title || nothing}
          >${opts.action2Label}</button>
        ` : nothing}
        ${opts.onAction ? html`
          <button
            class="conv-archive"
            @click=${(/** @type {Event} */ e) => { e.stopPropagation(); opts.onAction(conv.conv_id); }}
            title=${opts.actionTitle || ''}
            aria-label=${opts.actionTitle || nothing}
          >${opts.actionLabel}</button>
        ` : nothing}
      </div>
    `;
  }

  /**
   * Group a flat conversation list into sessions (root plus delegated
   * subagent children) for the sidebar hierarchy (#1115). Fallback for
   * servers without `GET /api/sessions`.
   * @param {Array} convs
   * @returns {Array}
   */
  #groupSessions(convs) {
    const groups = new Map();
    const order = [];
    for (const c of convs || []) {
      const convId = c?.conv_id || '';
      const rootId = parentConvIdOf(convId) || convId;
      if (!groups.has(rootId)) {
        groups.set(rootId, []);
        order.push(rootId);
      }
      groups.get(rootId).push(c);
    }
    const sessions = order.map((rootId) => {
      const members = groups.get(rootId) || [];
      const root = members.find((c) => c?.conv_id === rootId) || members[0];
      const children = members.filter((c) => c?.conv_id && c.conv_id !== rootId);
      const conversations = children.length > 0 ? [root, ...children] : [root];
      const updatedAts = members.map((c) => c?.updated_at || '').sort();
      const updatedAt = updatedAts.length > 0 ? updatedAts[updatedAts.length - 1] : '';
      return {
        session_id: rootId,
        title: root?.title || '',
        updated_at: updatedAt,
        conversations,
      };
    });
    sessions.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
    return sessions;
  }

  /**
   * Render the active conversation list as session groups (parent plus
   * expandable subagent children). Archived / system sections keep their flat
   * render. Prefers the server-grouped sessions from `GET /api/sessions`
   * (#1115); client-side grouping of the flat list is the fallback for
   * servers without the endpoint.
   * @returns {import('lit').TemplateResult | typeof nothing}
   */
  #renderSessionGroups() {
    const groups = this._sessions.length > 0 ? this._sessions : this.#groupSessions(this._conversations);
    if (groups.length === 0) return nothing;
    return html`
      ${groups.map((g) => html`<div class="conv-session">${this.#renderSessionGroup(g)}</div>`)}
    `;
  }

  /**
   * Whether one session's subagent rows are shown.
   *
   * An explicit user toggle wins for the rest of the page session. Otherwise
   * only the selected session is expanded (#1262): the sidebar lists many
   * sessions, and showing every session's subagents by default buries the one
   * the user is working in.
   * @param {object} g
   * @returns {boolean}
   */
  #isSessionExpanded(g) {
    if (this._collapsedSessions.has(g.session_id)) return false;
    if (this._expandedSessions.has(g.session_id)) return true;
    return (g.conversations || []).some((c) => c?.conv_id === this._activeId);
  }

  /**
   * Toggle one session's subagent rows (#1115 follow-up, default inverted in #1262).
   * @param {object} g
   */
  #toggleSession(g) {
    if (this.#isSessionExpanded(g)) {
      this._collapsedSessions.add(g.session_id);
      this._expandedSessions.delete(g.session_id);
    } else {
      this._expandedSessions.add(g.session_id);
      this._collapsedSessions.delete(g.session_id);
    }
    this.requestUpdate();
  }

  /**
   * Render one session group: the parent row plus (optionally) its subagent
   * rows. Sessions with children get an expand/collapse chevron — expanded for
   * the selected session, collapsed for the rest, an explicit user toggle
   * winning until the page is reloaded, and re-expanded when a conversation in
   * the session is selected (#1115 follow-up, #1262).
   * @param {object} g
   * @returns {import('lit').TemplateResult}
   */
  #renderSessionGroup(g) {
    const convs = g.conversations || [];
    const parent = convs[0];
    const children = convs.slice(1);
    const parentOpts = {
      isActive: parent?.conv_id === this._activeId,
      actionLabel: '\u00d7',
      actionTitle: 'Archive conversation',
      onAction: (id) => this.#handleArchive(id),
      action2Label: '✨',
      action2Title: 'Regenerate title',
      onAction2: (id) => this.#handleRetitle(id),
      onDblClick: (/** @type {Event} */ e) => { e.stopPropagation(); this.#handleRename(parent.conv_id, parent.title); },
      // #1277 — root rows are the only move sources; children follow them.
      onMove: (/** @type {string} */ id, /** @type {HTMLElement} */ trigger) => {
        void this.#openMovePicker(id, trigger);
      },
      moveTitle: 'Move to folder\u2026',
      draggable: true,
      onDragStart: (/** @type {DragEvent} */ e) => this.#handleDragStart(e, parent.conv_id),
      onDragEnd: () => this.#resetDragState(),
    };
    const parentRow = this.#renderConversationItem(parent, parentOpts);
    if (children.length === 0) return parentRow;
    const expanded = this.#isSessionExpanded(g);
    return html`
      <div class="conv-session-head">
        <button
          class="conv-session-toggle${expanded ? ' open' : ''}"
          @click=${(/** @type {Event} */ e) => { e.stopPropagation(); this.#toggleSession(g); }}
          aria-expanded=${expanded ? 'true' : 'false'}
          aria-label=${expanded ? 'Collapse subagents' : 'Expand subagents'}
          title=${expanded ? 'Collapse subagents' : 'Expand subagents'}
        >▸</button>
        ${parentRow}
      </div>
      ${expanded ? children.map((c) => this.#renderSubagentRow(c)) : nothing}
    `;
  }

  /**
   * Render one subagent row — indented beneath its session parent — with its
   * status badge and a cancel control while the child turn is running.
   * @param {object} child
   * @returns {import('lit').TemplateResult}
   */
  #renderSubagentRow(child) {
    const status = this.store?.getConversationStatus?.(child.conv_id) || 'idle';
    const isBusy = status === 'busy';
    const isActive = child.conv_id === this._activeId;
    const classes = ['conv-item', 'conv-subagent'];
    if (isActive) classes.push('active');
    const titleAttr = status && status !== 'idle' ? `${child.title} (${status})` : child.title;
    return html`
      <div
        class=${classes.join(' ')}
        tabindex="0"
        aria-label=${`Open conversation ${child.title}`}
        @click=${() => this.#handleSelect(child.conv_id)}
        @keydown=${activateOnEnterOrSpace(() => this.#handleSelect(child.conv_id))}
        title=${titleAttr}
      >
        <span class="conv-title">${child.title}</span>
        ${this.#renderStatusIndicator(status)}
        <span class="conv-subagent-badge">${this.#subagentBadgeText(child.conv_id)}</span>
        ${isBusy ? html`
          <button
            class="conv-subagent-cancel"
            @click=${(/** @type {Event} */ e) => { e.stopPropagation(); this.store?.cancelSubagent?.(child.conv_id); }}
            title="Cancel subagent"
            aria-label="Cancel subagent"
          >${'✕'} Cancel</button>
        ` : nothing}
      </div>
    `;
  }

  /**
   * Subagent status badge text: Running / Idle / Done / Waiting.
   * @param {string} convId
   * @returns {string}
   */
  #subagentBadgeText(convId) {
    const status = this.store?.getConversationStatus?.(convId) || 'idle';
    return STATUS_LABELS[status] || status || STATUS_LABELS.idle;
  }

  /**
   * Navigate the chat sidebar to a section and folder.
   * @param {string} section — '' (active), '_archived', '_system'
   * @param {string} folder — folder path within the section
   */
  #navigateChatFolder(section, folder) {
    this._chatSection = section;
    this._chatFolder = folder;
    if (section === '_archived') {
      this.store?.listArchivedConversations(folder);
    } else if (section === '_system') {
      this.store?.listSystemConversations(folder);
    } else {
      this.store?.listConversations(folder);
      // Sessions drive the active section's hierarchy rendering (#1115).
      this.store?.listSessions(folder);
    }
  }

  render() {
    // Collapsed-narrow view is desktop-only. When the user opens the
    // sidebar via hamburger on mobile, render the full content even if
    // `_collapsed` was set from a previous desktop session.
    if (this._collapsed && !this._mobileOpen) {
      return html`
        <div class="sidebar-header">
          <button class="collapse-btn dc-icon-btn" @click=${this.#toggleCollapse} title="Expand sidebar" aria-label="Expand sidebar">›</button>
        </div>
      `;
    }

    const currentFolder = this.store?.currentFolder ?? this._chatFolder ?? '';

    return html`
      <div class="sidebar-header dc-overlay-header">
        <div class="sidebar-tabs">
          <button class="sidebar-tab ${this._sidebarTab === 'conversations' ? 'active' : ''}"
            @click=${() => this.#switchTab('conversations')}>Chats</button>
          <button class="sidebar-tab ${this._sidebarTab === 'wiki' ? 'active' : ''}"
            @click=${() => this.#switchTab('wiki')}>Vault</button>
          <button class="sidebar-tab ${this._sidebarTab === 'files' ? 'active' : ''}"
            @click=${() => this.#switchTab('files')}>Files</button>
          <button class="sidebar-tab ${this._sidebarTab === 'schedules' ? 'active' : ''}"
            @click=${() => this.#switchTab('schedules')}>Schedules</button>
          <button class="sidebar-tab ${this._sidebarTab === 'tags' ? 'active' : ''}"
            @click=${() => this.#switchTab('tags')}>Tags</button>
        </div>
        <button class="mobile-close-btn dc-overlay-close-x" @click=${() => this.closeMobile()} title="Close sidebar" aria-label="Close sidebar">×</button>
        <button class="collapse-btn dc-icon-btn" @click=${this.#toggleCollapse} title="Collapse sidebar" aria-label="Collapse sidebar">‹</button>
      </div>
      <vault-sidebar
        .active=${this._sidebarTab === 'wiki'}
        style="${this._sidebarTab !== 'wiki' ? 'display:none' : ''}"
        @wiki-open=${(e) => this.#handleWikiOpen(e)}
      ></vault-sidebar>
      <files-sidebar
        .active=${this._sidebarTab === 'files'}
        style="${this._sidebarTab !== 'files' ? 'display:none' : ''}"
        @file-open=${(e) => this.#handleFileOpen(e)}
      ></files-sidebar>
      <schedules-sidebar
        .active=${this._sidebarTab === 'schedules'}
        .openName=${this._openSchedule}
        style="${this._sidebarTab !== 'schedules' ? 'display:none' : ''}"
      ></schedules-sidebar>
      <tags-sidebar
        .active=${this._sidebarTab === 'tags'}
        style="${this._sidebarTab !== 'tags' ? 'display:none' : ''}"
        @wiki-open=${(e) => this.#handleWikiOpen(e)}
      ></tags-sidebar>
      <div class="conv-list" style="${this._sidebarTab !== 'conversations' ? 'display:none' : ''}">
        ${this.#renderSearchBox()}
        ${this._searchQuery.trim() ? this.#renderSearchResults() : html`
        ${this.#renderChatBreadcrumbs()}
        ${this._chatFolders.map(f => {
          const isVirtual = ('virtual' in f && f.virtual) || f.path === '_archived' || f.path === '_system' || this._chatSection !== '';
          const openFolder = () => {
            if (f.path === '_archived') {
              this.#navigateChatFolder('_archived', '');
            } else if (f.path === '_system') {
              this.#navigateChatFolder('_system', '');
            } else {
              this.#navigateChatFolder(this._chatSection, f.path);
            }
          };
          return html`
            <div class="conv-item wiki-item wiki-folder-item${!isVirtual && this._dropTarget === f.path ? ' drop-target' : ''}"
              tabindex="0"
              aria-label=${`Open folder ${f.name}`}
              @click=${openFolder}
              @keydown=${activateOnEnterOrSpace(openFolder)}
              @dragover=${!isVirtual ? (/** @type {DragEvent} */ e) => this.#handleDragOver(e, f.path) : nothing}
              @dragleave=${!isVirtual ? (/** @type {DragEvent} */ e) => this.#handleDragLeave(e) : nothing}
              @drop=${!isVirtual ? (/** @type {DragEvent} */ e) => this.#handleDrop(e, f.path) : nothing}
              @dblclick=${!isVirtual ? (/** @type {Event} */ e) => { e.stopPropagation(); this.#renameChatFolder(f.path, f.name); } : nothing}
              title=${f.path}>
              <span class="conv-title">\u{1F4C1} ${f.name}</span>
              ${!isVirtual ? html`
                <button class="conv-archive" aria-label="Delete folder"
                  @click=${(/** @type {Event} */ e) => { e.stopPropagation(); this.#deleteChatFolder(f.path); }}
                  title="Delete folder">\u00d7</button>
              ` : nothing}
            </div>
          `;
        })}
        ${this._conversations.length === 0 && this._chatFolders.length === 0
          ? html`<p style="padding: 0.5rem 0.75rem; color: var(--pico-muted-color); font-size: 0.85rem;">No conversations</p>`
          : nothing
        }
        ${this._chatSection === '_archived'
          ? this._conversations.map(c => this.#renderConversationItem(c, {
              extraClass: 'archived',
              actionLabel: '\u21a9',
              actionTitle: 'Unarchive conversation',
              onAction: (id) => this.#handleUnarchive(id),
              action2Label: '\u{1F5D1}',
              action2Title: 'Delete conversation permanently',
              onAction2: (id) => this.#handleDelete(id),
            }))
          : this._chatSection === '_system'
            ? this._conversations.map(c => this.#renderConversationItem(c, {
                isActive: c.conv_id === this._activeId,
                extraClass: 'system',
                badge: 'conv_type' in c ? c.conv_type : '',
                titleSuffix: 'conv_type' in c ? c.conv_type : '',
              }))
            : this.#renderSessionGroups()
        }
        `}
      </div>
      ${this._sidebarTab === 'conversations' && this._chatSection === '' && !this._searchQuery.trim() ? html`
        <div class="vault-action-btns">
          <button class="home-lobby-btn outline" @click=${() => this.#handleHome()} title="Home Lobby" aria-label="Home Lobby">⌂ Lobby</button>
          <button class="new-conv-btn outline" @click=${this.#handleNew} title="New chat (⌘K)" aria-label="New chat">+ Chat</button>
          <button class="wiki-new-folder-btn outline" @click=${() => this.#createChatFolder()} aria-label="New chat folder">+ Folder</button>
        </div>
      ` : nothing}
      ${!this._hasControlsStrip && this._availableModes.length > 0 ? html`
      <div class="mode-picker">
        <label class="mode-picker-label" for="mode-select">Mode</label>
        <select id="mode-select" class="mode-select"
                ?disabled=${this._isBusy || this._isReadOnly}
                @change=${(e) => this.#handleModeChange(e)}>
          ${(() => {
            const target = this._activeMode || 'default';
            const isCustom = target === 'custom';
            return html`
              ${isCustom ? html`
                <option value="custom" selected disabled>Custom</option>
              ` : nothing}
              ${this._availableModes.map(m => html`
                <option value="${m.name}" ?selected=${m.name === target} .selected=${m.name === target} title="${m.description}">
                  ${m.name}
                </option>
              `)}
            `;
          })()}
        </select>
        <button class="shell-approval-open-btn dc-icon-btn" title="Shell auto-approval" aria-label="Open shell auto-approval panel" @click=${() => this.#openShellApproval()}>&#9881;</button>
      </div>
      ` : nothing}
      ${!this._hasControlsStrip && this._availableModels.length > 0 ? html`
      <div class="model-picker">
        <label class="model-picker-label">Model</label>
        <model-picker
          .groups=${this._modelGroups}
          .activeModel=${this._activeModel}
          .defaultModel=${this._defaultModel}
          ?disabled=${this._isBusy || this._isReadOnly}
          .convId=${this._activeId || ''}
        ></model-picker>
      </div>
      ` : nothing}
      ${!this._hasControlsStrip && this._contextLimit > 0 ? html`
        ${(() => {
          const pct = Math.round(this._contextUsage / this._contextLimit * 100);
          const cls = pct >= 100 ? 'full' : pct >= 80 ? 'warn' : '';
          return html`
            <div class="context-usage ${cls}" title="Click for details"
                 style="cursor:pointer;position:relative"
                 role="button"
                 tabindex="0"
                 aria-expanded=${this._contextInspectorOpen}
                 @click=${() => { this._contextInspectorOpen = !this._contextInspectorOpen; }}
                 @keydown=${(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this._contextInspectorOpen = !this._contextInspectorOpen; }}}>
              <div class="context-usage-label">
                <span>Context</span>
                <span>${pct}%</span>
              </div>
              <div class="context-usage-bar">
                <div class="context-usage-fill" style="width: ${Math.min(100, pct)}%"></div>
              </div>
            </div>
          `;
        })()}
      ` : nothing}
      <context-inspector
        .convId=${this._activeId || ''}
        .open=${this._contextInspectorOpen}
        .contextVersion=${this._contextVersion}
        @close=${() => { this._contextInspectorOpen = false; }}
      ></context-inspector>
      <shell-approval-panel
        .convId=${this._activeId || ''}
        .open=${this._shellApprovalOpen}
        @close=${() => { this._shellApprovalOpen = false; }}
      ></shell-approval-panel>
      <div class="sidebar-footer">
        <div class="footer-icons">
          <theme-toggle></theme-toggle>
          <notification-inbox
            @navigate-conversation=${(e) => this.#onNotificationNavigate(e)}
            @navigate-vault=${(e) => this.#onNotificationNavigateVault(e)}
          ></notification-inbox>
          <button class="config-btn" title="Agent Config" aria-label="Agent Config" @click=${() => this.#openConfig()}>&#9881;</button>
        </div>
        <button class="logout-btn" @click=${() => this.authClient?.logout()} title="Sign out">Sign out</button>
      </div>
      ${this._movePickerConvId ? html`
        <div class="conv-move-picker-backdrop"
          @click=${(/** @type {MouseEvent} */ e) => { if (e.target === e.currentTarget) this.#closeMovePicker(); }}>
          <div class="conv-move-picker" role="dialog" aria-modal="true" aria-label="Move conversation"
            @keydown=${(/** @type {KeyboardEvent} */ e) => this.#handleMovePickerKeydown(e)}>
            <div class="conv-move-picker-header">
              <span>Move to\u2026</span>
              <button type="button" class="conv-move-picker-close dc-overlay-close-x"
                @click=${() => this.#closeMovePicker()} title="Close" aria-label="Close move dialog">\u00d7</button>
            </div>
            ${this._movePickerLoading
              ? html`<p class="conv-move-picker-empty">Loading folders\u2026</p>`
              : html`
                <button type="button" class="conv-move-option"
                  ?disabled=${currentFolder === ''}
                  @click=${() => this.#moveConversationTo(this._movePickerConvId, '')}>
                  Top level${currentFolder === '' ? ' (current)' : ''}
                </button>
                ${this._movePickerFolders.map((path) => html`
                  <button type="button" class="conv-move-option" title=${path}
                    ?disabled=${path === currentFolder}
                    @click=${() => this.#moveConversationTo(this._movePickerConvId, path)}>
                    ${path}${path === currentFolder ? ' (current)' : ''}
                  </button>
                `)}
                ${this._movePickerFolders.length === 0
                  ? html`<p class="conv-move-picker-empty">No folders yet. Create one with + Folder.</p>`
                  : nothing}
              `}
          </div>
        </div>
      ` : nothing}
    `;
  }
}

customElements.define('conversation-sidebar', ConversationSidebar);
