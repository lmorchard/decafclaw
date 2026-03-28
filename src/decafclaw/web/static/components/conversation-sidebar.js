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
    /** @type {Array<{title: string, modified: number}>} */
    this._wikiPages = [];
    this._wikiLoading = false;
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
    this.store?.addEventListener('change', this._onStoreChange);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.store?.removeEventListener('change', this._onStoreChange);
  }

  /** @param {Map} changedProps */
  updated(changedProps) {
    if (changedProps.has('store') && this.store) {
      this.store.addEventListener('change', this._onStoreChange);
      this._onStoreChange();
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
      const res = await fetch('/api/wiki');
      if (res.ok) {
        this._wikiPages = await res.json();
      }
    } catch (e) {
      // Silently fail
    } finally {
      this._wikiLoading = false;
    }
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

  #renderWikiTab() {
    if (this._wikiLoading) {
      return html`<div class="conv-list"><p style="padding: 1rem; color: var(--pico-muted-color);">Loading wiki pages...</p></div>`;
    }
    if (!this._wikiPages.length) {
      return html`<div class="conv-list"><p style="padding: 1rem; color: var(--pico-muted-color);">No wiki pages found.</p></div>`;
    }
    return html`
      <div class="conv-list">
        ${this._wikiPages.map(p => html`
          <div class="conv-item wiki-item" @click=${() => this.#handleWikiSelect(p.title)} title=${p.title}>
            <span class="conv-title">${p.title}</span>
          </div>
        `)}
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
            @click=${() => this.#switchTab('wiki')}>Wiki</button>
        </div>
        <button class="mobile-close-btn" @click=${() => this.closeMobile()} title="Close sidebar">&#10005;</button>
        <button class="collapse-btn" @click=${this.#toggleCollapse} title="Collapse sidebar">‹</button>
      </div>
      ${this._sidebarTab === 'wiki' ? this.#renderWikiTab() : nothing}
      <div class="conv-list" style="${this._sidebarTab !== 'conversations' ? 'display:none' : ''}">
        <button class="new-conv-btn outline" @click=${this.#handleNew} title="New conversation (⌘K)">+ New conversation</button>
        ${this._conversations.length === 0
          ? nothing
          : this._conversations.map(c => html`
            <div
              class="conv-item ${c.conv_id === this._activeId ? 'active' : ''}"
              @click=${() => this.#handleSelect(c.conv_id)}
              title=${c.title}
            >
              <span
                class="conv-title"
                @dblclick=${(/** @type {Event} */ e) => { e.stopPropagation(); this.#handleRename(c.conv_id, c.title); }}
              >${c.title}</span>
              <button
                class="conv-archive"
                @click=${(/** @type {Event} */ e) => { e.stopPropagation(); this.#handleArchive(c.conv_id); }}
                title="Archive conversation"
              >&times;</button>
            </div>
          `)
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
            : this._archived.map(c => html`
              <div
                class="conv-item archived"
                @click=${() => this.#handleSelect(c.conv_id)}
                title=${c.title}
              >
                <span class="conv-title">${c.title}</span>
                <button
                  class="conv-archive"
                  @click=${(/** @type {Event} */ e) => { e.stopPropagation(); this.#handleUnarchive(c.conv_id); }}
                  title="Unarchive conversation"
                >\u21a9</button>
              </div>
            `)
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
            : this._systemConversations.map(c => html`
              <div
                class="conv-item system ${c.conv_id === this._activeId ? 'active' : ''}"
                @click=${() => this.#handleSelect(c.conv_id)}
                title="${c.title} (${c.conv_type})"
              >
                <span class="conv-title">${c.title}</span>
                <span class="conv-type-badge">${c.conv_type}</span>
              </div>
            `)
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
        <button class="logout-btn" @click=${() => this.authClient?.logout()} title="Sign out">Sign out</button>
      </div>
    `;
  }
}

customElements.define('conversation-sidebar', ConversationSidebar);
