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
    _collapsed: { type: Boolean, state: true },
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
    this._collapsed = localStorage.getItem('sidebar-collapsed') === 'true';
  }

  connectedCallback() {
    super.connectedCallback();
    this._onStoreChange = () => {
      this._conversations = this.store?.conversations || [];
      this._archived = this.store?.archivedConversations || [];
      this._activeId = this.store?.currentConvId;
      this._contextUsage = this.store?.contextUsage || 0;
      this._contextLimit = this.store?.contextLimit || 0;
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

  #toggleArchived() {
    this._showArchived = !this._showArchived;
    if (this._showArchived) {
      this.store?.listArchivedConversations();
    }
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
        <h3>Conversations</h3>
        <button class="collapse-btn" @click=${this.#toggleCollapse} title="Collapse sidebar">‹</button>
      </div>
      <div class="conv-list">
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
