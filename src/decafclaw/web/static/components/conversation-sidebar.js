import { LitElement, html, nothing } from 'lit';

export class ConversationSidebar extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    authClient: { type: Object, attribute: false },
    _conversations: { type: Array, state: true },
    _archived: { type: Array, state: true },
    _activeId: { type: String, state: true },
    _showArchived: { type: Boolean, state: true },
    _contextUsage: { type: Number, state: true },
    _contextLimit: { type: Number, state: true },
    _currentEffort: { type: String, state: true },
    _effortModel: { type: String, state: true },
    _systemConversations: { type: Array, state: true },
    _showSystem: { type: Boolean, state: true },
    _collapsed: { type: Boolean, state: true },
    _mobileOpen: { type: Boolean, state: true },
    _sidebarTab: { type: String, state: true },
    _wikiPages: { type: Array, state: true },
    _wikiLoading: { type: Boolean, state: true },
    _vaultFolder: { type: String, state: true },
    _vaultFolders: { type: Array, state: true },
    _openWikiPage: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.store = null;
    this.authClient = null;
    this._conversations = [];
    this._archived = [];
    this._activeId = null;
    this._showArchived = false;
    this._contextUsage = 0;
    this._contextLimit = 0;
    this._currentEffort = 'default';
    this._effortModel = '';
    this._systemConversations = [];
    this._showSystem = false;
    this._collapsed = localStorage.getItem('sidebar-collapsed') === 'true';
    this._mobileOpen = false;
    this._sidebarTab = 'conversations';
    /** @type {Array<{title: string, path?: string, folder?: string, modified: number}>} */
    this._wikiPages = [];
    this._wikiLoading = false;
    this._vaultFolder = '';
    /** @type {Array<{name: string, path: string}>} */
    this._vaultFolders = [];
    /** @type {string|null} */
    this._openWikiPage = null;
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
      this._conversations = this.store?.conversations || [];
      this._archived = this.store?.archivedConversations || [];
      this._activeId = this.store?.currentConvId;
      this._contextUsage = this.store?.contextUsage || 0;
      this._contextLimit = this.store?.contextLimit || 0;
      this._currentEffort = this.store?.currentEffort || 'default';
      this._effortModel = this.store?.effortModel || '';
      this._systemConversations = this.store?.systemConversations || [];
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
    this.toggleAttribute('collapsed', this._collapsed);
    this.toggleAttribute('mobile-open', this._mobileOpen);
  }

  /** @param {string} tab */
  #switchTab(tab) {
    this._sidebarTab = tab;
    if (tab === 'wiki') {
      this.#fetchWikiPages();
    }
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
      this.#fetchWikiPages();
    }
  }

  async #fetchWikiPages() {
    this._wikiLoading = true;
    try {
      const url = this._vaultFolder
        ? `/api/vault?folder=${encodeURIComponent(this._vaultFolder)}`
        : '/api/vault';
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        this._vaultFolders = data.folders || [];
        this._wikiPages = data.pages || [];
      } else {
        this._vaultFolders = [];
        this._wikiPages = [];
        if (res.status === 404) this._vaultFolder = '';
      }
    } catch (e) {
      this._vaultFolders = [];
      this._wikiPages = [];
    } finally {
      this._wikiLoading = false;
    }
  }

  /** @param {string} folderPath */
  #navigateToFolder(folderPath) {
    this._vaultFolder = folderPath;
    this.#fetchWikiPages();
  }

  /**
   * Public: navigate the sidebar to a specific folder.
   * @param {string} folderPath
   */
  navigateToFolder(folderPath) {
    this.#navigateToFolder(folderPath);
  }

  /**
   * Navigate the sidebar to the folder containing a given page path.
   * Called when a page is opened (e.g. via wiki-link) to keep sidebar in sync.
   * @param {string} pagePath
   */
  navigateToPageFolder(pagePath) {
    this._openWikiPage = pagePath;
    const lastSlash = pagePath.lastIndexOf('/');
    const folder = lastSlash >= 0 ? pagePath.substring(0, lastSlash) : '';
    if (folder !== this._vaultFolder) {
      this._vaultFolder = folder;
      this.#fetchWikiPages();
    }
  }

  /** Clear the open page highlight (called when wiki pane is closed). */
  clearOpenPage() {
    this._openWikiPage = null;
  }

  /** @param {string} page */
  #handleWikiSelect(page) {
    this.dispatchEvent(new CustomEvent('wiki-open', {
      detail: { page },
      bubbles: true,
      composed: true,
    }));
    this.closeMobile();
  }

  #toggleCollapse() {
    this._collapsed = !this._collapsed;
    localStorage.setItem('sidebar-collapsed', String(this._collapsed));
  }

  #handleNew() {
    this.store?.createConversation();
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

  #openConfig() {
    this.dispatchEvent(new CustomEvent('config-open', {
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {string} level */
  #handleEffortClick(level) {
    if (this._activeId) {
      // Active conversation — send to server
      this.store?.setEffort(level);
    } else {
      // No conversation yet — update local state for next creation
      this._currentEffort = level;
    }
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

  #toggleArchived() {
    this._showArchived = !this._showArchived;
    if (this._showArchived) {
      this.store?.listArchivedConversations();
    }
  }

  #toggleSystem() {
    this._showSystem = !this._showSystem;
    if (this._showSystem) {
      this.store?.listSystemConversations();
    }
  }

  #createWikiPage() {
    const name = prompt('New page name:');
    if (!name || !name.trim()) return;
    const fullName = this._vaultFolder
      ? `${this._vaultFolder}/${name.trim()}`
      : name.trim();
    this.#createPageInFolder(fullName);
  }

  async #createVaultFolder() {
    const name = prompt('New folder name:');
    if (!name || !name.trim()) return;
    const folderPath = this._vaultFolder
      ? `${this._vaultFolder}/${name.trim()}`
      : name.trim();
    try {
      const res = await fetch('/api/vault/folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: folderPath }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.error || `Failed to create folder (${res.status})`);
        return;
      }
      this.#navigateToFolder(folderPath);
    } catch (e) {
      alert('Failed to create folder.');
    }
  }

  /** @param {string} fullName */
  async #createPageInFolder(fullName) {
    try {
      const res = await fetch('/api/vault', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: fullName }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.error || `Failed to create page (${res.status})`);
        return;
      }
      this.dispatchEvent(new CustomEvent('wiki-open', {
        detail: { page: fullName, editing: true },
        bubbles: true,
        composed: true,
      }));
      this.#fetchWikiPages();
    } catch (e) {
      alert('Failed to create page.');
    }
  }

  #renderVaultBreadcrumbs() {
    if (!this._vaultFolder) {
      return html`<span class="vault-breadcrumb-segment active">vault</span>`;
    }
    const segments = this._vaultFolder.split('/');
    return html`
      <button type="button" class="vault-breadcrumb-segment" @click=${() => this.#navigateToFolder('')}>vault</button>
      ${segments.map((seg, i) => {
        const path = segments.slice(0, i + 1).join('/');
        const isLast = i === segments.length - 1;
        return html`
          <span class="vault-breadcrumb-sep">/</span>
          ${isLast
            ? html`<span class="vault-breadcrumb-segment active">${seg}</span>`
            : html`<button type="button" class="vault-breadcrumb-segment" @click=${() => this.#navigateToFolder(path)}>${seg}</button>`
          }
        `;
      })}
    `;
  }

  #renderWikiTab() {
    if (this._wikiLoading) {
      return html`<div class="conv-list"><p style="padding: 1rem; color: var(--pico-muted-color);">Loading vault pages...</p></div>`;
    }
    const hasContent = this._vaultFolders.length > 0 || this._wikiPages.length > 0;
    return html`
      <div class="conv-list">
        <div class="vault-action-btns">
          <button class="wiki-new-page-btn outline" @click=${() => this.#createWikiPage()}>+ Page</button>
          <button class="wiki-new-folder-btn outline" @click=${() => this.#createVaultFolder()}>+ Folder</button>
        </div>
        <div class="vault-breadcrumbs">${this.#renderVaultBreadcrumbs()}</div>
        ${this._vaultFolders.map(f => html`
          <div class="conv-item wiki-item wiki-folder-item" @click=${() => this.#navigateToFolder(f.path)} title=${f.path}>
            <span class="conv-title">\u{1F4C1} ${f.name}</span>
          </div>
        `)}
        ${this._wikiPages.map(p => {
          const pagePath = p.path || p.title;
          const isOpen = pagePath === this._openWikiPage;
          return html`
            <div class="conv-item wiki-item ${isOpen ? 'active' : ''}" @click=${() => this.#handleWikiSelect(pagePath)} title=${pagePath}>
              <span class="conv-title">${p.title}</span>
            </div>
          `;
        })}
        ${!hasContent
          ? html`<p style="padding: 1rem; color: var(--pico-muted-color);">Empty folder.</p>`
          : nothing
        }
      </div>
    `;
  }

  render() {
    if (this._collapsed) {
      return html`
        <div class="sidebar-header">
          <button class="collapse-btn" @click=${this.#toggleCollapse} title="Expand sidebar">›</button>
        </div>
      `;
    }

    return html`
      <div class="sidebar-header">
        <div class="sidebar-tabs">
          <button class="sidebar-tab ${this._sidebarTab === 'conversations' ? 'active' : ''}"
            @click=${() => this.#switchTab('conversations')}>Chats</button>
          <button class="sidebar-tab ${this._sidebarTab === 'wiki' ? 'active' : ''}"
            @click=${() => this.#switchTab('wiki')}>Vault</button>
        </div>
        <button class="mobile-close-btn" @click=${() => this.closeMobile()} title="Close sidebar">&#10005;</button>
        <button class="collapse-btn" @click=${this.#toggleCollapse} title="Collapse sidebar">‹</button>
      </div>
      ${this._sidebarTab === 'wiki' ? this.#renderWikiTab() : nothing}
      <div class="conv-list" style="${this._sidebarTab !== 'conversations' ? 'display:none' : ''}">
        <button class="new-conv-btn outline" @click=${this.#handleNew} title="New chat (⌘K)">+ Chat</button>
        ${this._conversations.length === 0
          ? nothing
          : this._conversations.map(c => this.#renderConversationItem(c, {
              isActive: c.conv_id === this._activeId,
              actionLabel: '\u00d7',
              actionTitle: 'Archive conversation',
              onAction: (id) => this.#handleArchive(id),
              onDblClick: (/** @type {Event} */ e) => { e.stopPropagation(); this.#handleRename(c.conv_id, c.title); },
            }))
        }

        <div
          class="archived-toggle"
          @click=${this.#toggleArchived}
        >
          <span>${this._showArchived ? '\u25bc' : '\u25b6'} Archived</span>
        </div>

        ${this._showArchived ? html`
          ${this._archived.length === 0
            ? html`<p style="padding: 0.25rem 0.75rem; color: var(--pico-muted-color); font-size: 0.85rem;">No archived conversations</p>`
            : this._archived.map(c => this.#renderConversationItem(c, {
                extraClass: 'archived',
                actionLabel: '\u21a9',
                actionTitle: 'Unarchive conversation',
                onAction: (id) => this.#handleUnarchive(id),
              }))
          }
        ` : nothing}

        <div
          class="archived-toggle"
          @click=${this.#toggleSystem}
        >
          <span>${this._showSystem ? '\u25bc' : '\u25b6'} System</span>
        </div>

        ${this._showSystem ? html`
          ${this._systemConversations.length === 0
            ? html`<p style="padding: 0.25rem 0.75rem; color: var(--pico-muted-color); font-size: 0.85rem;">No system conversations</p>`
            : this._systemConversations.map(c => this.#renderConversationItem(c, {
                isActive: c.conv_id === this._activeId,
                extraClass: 'system',
                badge: c.conv_type,
                titleSuffix: c.conv_type,
              }))
          }
        ` : nothing}
      </div>
      <div class="effort-picker" title="${this._effortModel ? `Model: ${this._effortModel}` : ''}">
        <div class="effort-picker-label">Effort</div>
        <div class="effort-picker-buttons">
          ${['fast', 'default', 'strong'].map(level => html`
            <button
              class="effort-btn ${this._currentEffort === level ? 'active' : ''}"
              @click=${() => this.#handleEffortClick(level)}
            >${level}</button>
          `)}
        </div>
      </div>
      ${this._contextLimit > 0 ? html`
        ${(() => {
          const pct = Math.round(this._contextUsage / this._contextLimit * 100);
          const cls = pct >= 100 ? 'full' : pct >= 80 ? 'warn' : '';
          return html`
            <div class="context-usage ${cls}" title="${this._contextUsage.toLocaleString()} / ${this._contextLimit.toLocaleString()} tokens">
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
      <div class="sidebar-footer">
        <theme-toggle></theme-toggle>
        <button class="config-btn" title="Agent Config" @click=${() => this.#openConfig()}>&#9881;</button>
        <button class="logout-btn" @click=${() => this.authClient?.logout()} title="Sign out">Sign out</button>
      </div>
    `;
  }
}

customElements.define('conversation-sidebar', ConversationSidebar);
