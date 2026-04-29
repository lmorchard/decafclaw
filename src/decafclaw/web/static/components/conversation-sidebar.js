import { LitElement, html, nothing } from 'lit';
import './context-inspector.js';
import './notification-inbox.js';
import './vault-sidebar.js';
import './files-sidebar.js';

export class ConversationSidebar extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    authClient: { type: Object, attribute: false },
    _conversations: { type: Array, state: true },
    _chatFolders: { type: Array, state: true },
    _chatSection: { type: String, state: true },
    _chatFolder: { type: String, state: true },
    _activeId: { type: String, state: true },
    _contextUsage: { type: Number, state: true },
    _contextLimit: { type: Number, state: true },
    _activeModel: { type: String, state: true },
    _availableModels: { type: Array, state: true },
    _defaultModel: { type: String, state: true },
    _collapsed: { type: Boolean, state: true },
    _mobileOpen: { type: Boolean, state: true },
    _sidebarTab: { type: String, state: true },
    _contextInspectorOpen: { type: Boolean, state: true },
    _contextVersion: { type: Number, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.store = null;
    this.authClient = null;
    this._conversations = [];
    /** @type {Array<{name: string, path: string, virtual?: boolean}>} */
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
    this._defaultModel = '';
    this._collapsed = localStorage.getItem('sidebar-collapsed') === 'true';
    this._mobileOpen = false;
    this._sidebarTab = 'conversations';
    this._contextInspectorOpen = false;
    this._contextVersion = 0;
  }

  openMobile() {
    this._mobileOpen = true;
  }

  closeMobile() {
    this._mobileOpen = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._onStoreChange = () => {
      const newId = this.store?.currentConvId;
      if (newId !== this._activeId) this._contextInspectorOpen = false;
      this._activeId = newId;
      const newUsage = this.store?.contextUsage || 0;
      if (newUsage !== this._contextUsage) this._contextVersion++;
      this._contextUsage = newUsage;
      this._contextLimit = this.store?.contextLimit || 0;
      this._activeModel = this.store?.activeModel || '';
      this._availableModels = this.store?.availableModels || [];
      this._defaultModel = this.store?.defaultModel || '';
      // Update conversation list and folders based on current section
      if (this._chatSection === '_archived') {
        this._conversations = this.store?.archivedConversations || [];
        this._chatFolders = this.store?.archivedFolders || [];
      } else if (this._chatSection === '_system') {
        this._conversations = this.store?.systemConversations || [];
        this._chatFolders = this.store?.systemFolders || [];
      } else {
        this._conversations = this.store?.conversations || [];
        this._chatFolders = this.store?.folders || [];
      }
    };
  }

  disconnectedCallback() {
    super.disconnectedCallback();
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
  }

  /** Clear the open page highlight (called when wiki pane is closed). */
  clearOpenPage() {
    const vs = /** @type {any} */ (this.querySelector('vault-sidebar'));
    vs?.clearOpenPage();
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

  #toggleCollapse() {
    this._collapsed = !this._collapsed;
    localStorage.setItem('sidebar-collapsed', String(this._collapsed));
  }

  #handleNew() {
    // Create in current folder (only in active section)
    const folder = this._chatSection === '' ? this._chatFolder : '';
    this.store?.createConversation('', '', folder);
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

    // Root "Chats" segment — always navigates to top level
    crumbs.push(html`
      <button type="button" class="vault-breadcrumb-segment"
        @click=${() => this.#navigateChatFolder('', '')}>Chats</button>
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
            : html`<button type="button" class="vault-breadcrumb-segment"
                @click=${() => this.#navigateChatFolder(section, path)}>${seg}</button>`
          }
        `);
      });
    }

    return html`<div class="vault-breadcrumbs">${crumbs}</div>`;
  }

  /** @param {string} convId */
  #handleSelect(convId) {
    this.store?.selectConversation(convId);
    this.closeMobile();
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
  #handleModelChange(e) {
    const model = /** @type {HTMLSelectElement} */ (e.target).value;
    this.store?.setModel(model);
  }

  /**
   * Render a single conversation item.
   * @param {object} conv
   * @param {object} [opts]
   * @param {boolean}  [opts.isActive]    - Whether this item is currently selected
   * @param {string}   [opts.extraClass]  - Additional CSS class (e.g. 'archived', 'system')
   * @param {string}   [opts.actionLabel] - Button label/symbol (e.g. '×', '↩')
   * @param {string}   [opts.actionTitle] - Button title text
   * @param {function} [opts.onAction]    - Click handler for the action button
   * @param {string}   [opts.action2Label] - Second action button label
   * @param {string}   [opts.action2Title] - Second action button title
   * @param {function} [opts.onAction2]   - Click handler for the second action button
   * @param {function} [opts.onDblClick]  - Double-click handler on the title span
   * @param {string}   [opts.badge]       - Optional badge text shown after title
   * @param {string}   [opts.titleSuffix] - Extra text appended to the title attr
   */
  #renderConversationItem(conv, opts = {}) {
    const classes = ['conv-item'];
    if (opts.extraClass) classes.push(opts.extraClass);
    if (opts.isActive) classes.push('active');

    const titleAttr = opts.titleSuffix ? `${conv.title} (${opts.titleSuffix})` : conv.title;

    return html`
      <div
        class=${classes.join(' ')}
        @click=${() => this.#handleSelect(conv.conv_id)}
        title=${titleAttr}
      >
        <span
          class="conv-title"
          @dblclick=${opts.onDblClick || nothing}
        >${conv.title}</span>
        ${opts.badge ? html`<span class="conv-type-badge">${opts.badge}</span>` : nothing}
        ${opts.onAction2 ? html`
          <button
            class="conv-archive"
            @click=${(/** @type {Event} */ e) => { e.stopPropagation(); opts.onAction2(conv.conv_id); }}
            title=${opts.action2Title || ''}
          >${opts.action2Label}</button>
        ` : nothing}
        ${opts.onAction ? html`
          <button
            class="conv-archive"
            @click=${(/** @type {Event} */ e) => { e.stopPropagation(); opts.onAction(conv.conv_id); }}
            title=${opts.actionTitle || ''}
          >${opts.actionLabel}</button>
        ` : nothing}
      </div>
    `;
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
    }
  }

  render() {
    // Collapsed-narrow view is desktop-only. When the user opens the
    // sidebar via hamburger on mobile, render the full content even if
    // `_collapsed` was set from a previous desktop session.
    if (this._collapsed && !this._mobileOpen) {
      return html`
        <div class="sidebar-header">
          <button class="collapse-btn" @click=${this.#toggleCollapse} title="Expand sidebar">›</button>
        </div>
      `;
    }

    return html`
      <div class="sidebar-header dc-overlay-header">
        <div class="sidebar-tabs">
          <button class="sidebar-tab ${this._sidebarTab === 'conversations' ? 'active' : ''}"
            @click=${() => this.#switchTab('conversations')}>Chats</button>
          <button class="sidebar-tab ${this._sidebarTab === 'wiki' ? 'active' : ''}"
            @click=${() => this.#switchTab('wiki')}>Vault</button>
          <button class="sidebar-tab ${this._sidebarTab === 'files' ? 'active' : ''}"
            @click=${() => this.#switchTab('files')}>Files</button>
        </div>
        <button class="mobile-close-btn dc-overlay-close-x" @click=${() => this.closeMobile()} title="Close sidebar">×</button>
        <button class="collapse-btn" @click=${this.#toggleCollapse} title="Collapse sidebar">‹</button>
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
      <div class="conv-list" style="${this._sidebarTab !== 'conversations' ? 'display:none' : ''}">
        ${this._chatSection === '' ? html`
          <div class="vault-action-btns">
            <button class="new-conv-btn outline" @click=${this.#handleNew} title="New chat (⌘K)">+ Chat</button>
            <button class="wiki-new-folder-btn outline" @click=${() => this.#createChatFolder()}>+ Folder</button>
          </div>
        ` : nothing}
        ${this.#renderChatBreadcrumbs()}
        ${this._chatFolders.map(f => {
          const isVirtual = f.virtual || f.path === '_archived' || f.path === '_system' || this._chatSection !== '';
          return html`
            <div class="conv-item wiki-item wiki-folder-item"
              @click=${() => {
                if (f.path === '_archived') {
                  this.#navigateChatFolder('_archived', '');
                } else if (f.path === '_system') {
                  this.#navigateChatFolder('_system', '');
                } else {
                  this.#navigateChatFolder(this._chatSection, f.path);
                }
              }}
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
                badge: c.conv_type,
                titleSuffix: c.conv_type,
              }))
            : this._conversations.map(c => this.#renderConversationItem(c, {
                isActive: c.conv_id === this._activeId,
                actionLabel: '\u00d7',
                actionTitle: 'Archive conversation',
                onAction: (id) => this.#handleArchive(id),
                onDblClick: (/** @type {Event} */ e) => { e.stopPropagation(); this.#handleRename(c.conv_id, c.title); },
              }))
        }
      </div>
      ${this._availableModels.length > 0 ? html`
      <div class="model-picker">
        <label class="model-picker-label" for="model-select">Model</label>
        <select id="model-select" class="model-select"
                @change=${(e) => this.#handleModelChange(e)}>
          ${(() => {
            const target = this._activeModel || this._defaultModel;
            return this._availableModels.map(m => html`
              <option value="${m}" ?selected=${m === target}>
                ${m}${m === this._defaultModel ? ' (default)' : ''}
              </option>
            `);
          })()}
        </select>
      </div>
      ` : nothing}
      ${this._contextLimit > 0 ? html`
        ${(() => {
          const pct = Math.round(this._contextUsage / this._contextLimit * 100);
          const cls = pct >= 100 ? 'full' : pct >= 80 ? 'warn' : '';
          return html`
            <div class="context-usage ${cls}" title="Click for details"
                 style="cursor:pointer;position:relative"
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
              <context-inspector
                .convId=${this._activeId || ''}
                .open=${this._contextInspectorOpen}
                .contextVersion=${this._contextVersion}
                @close=${() => { this._contextInspectorOpen = false; }}
              ></context-inspector>
            </div>
          `;
        })()}
      ` : nothing}
      <div class="sidebar-footer">
        <div class="footer-icons">
          <theme-toggle></theme-toggle>
          <notification-inbox
            @navigate-conversation=${(e) => this.#onNotificationNavigate(e)}
            @navigate-vault=${(e) => this.#onNotificationNavigateVault(e)}
          ></notification-inbox>
          <button class="config-btn" title="Agent Config" @click=${() => this.#openConfig()}>&#9881;</button>
        </div>
        <button class="logout-btn" @click=${() => this.authClient?.logout()} title="Sign out">Sign out</button>
      </div>
    `;
  }
}

customElements.define('conversation-sidebar', ConversationSidebar);
