import { LitElement, html, nothing } from 'lit';

export class VaultSidebar extends LitElement {
  static properties = {
    active: { type: Boolean },
    _wikiPages: { type: Array, state: true },
    _wikiLoading: { type: Boolean, state: true },
    _vaultFolder: { type: String, state: true },
    _vaultFolders: { type: Array, state: true },
    _openWikiPage: { type: String, state: true },
    _vaultView: { type: String, state: true },
    _recentPages: { type: Array, state: true },
    _everActivated: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.active = false;
    /** @type {Array<{title: string, path?: string, folder?: string, modified: number}>} */
    this._wikiPages = [];
    this._wikiLoading = false;
    this._vaultFolder = '';
    /** @type {Array<{name: string, path: string}>} */
    this._vaultFolders = [];
    /** @type {string|null} */
    this._openWikiPage = null;
    this._vaultView = 'browse'; // 'browse' | 'recent'
    /** @type {Array<{title: string, path: string, folder: string, modified: number}>} */
    this._recentPages = [];
    // Set to true on first Vault-tab activation. Gates the vault-page-deleted
    // listener so it doesn't fire before the user has opened the Vault tab.
    this._everActivated = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._onVaultPageDeleted = () => {
      // Only refetch if the Vault tab has been activated at least once —
      // avoids firing requests before the user has ever opened it.
      if (!this._everActivated) return;
      if (this._vaultView === 'recent') this.#fetchRecentPages();
      else this.#fetchWikiPages();
    };
    window.addEventListener('vault-page-deleted', this._onVaultPageDeleted);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('vault-page-deleted', this._onVaultPageDeleted);
  }

  /** @param {Map} changedProps */
  updated(changedProps) {
    // Re-fetch on every false→true transition of `active`, matching the
    // pre-refactor behavior where clicking the Vault tab always refreshed
    // the current view. `_everActivated` stays as a latch so the
    // `vault-page-deleted` listener doesn't fire before the user has ever
    // opened the Vault tab.
    if (changedProps.has('active') && this.active && !changedProps.get('active')) {
      this._everActivated = true;
      if (this._vaultView === 'recent') this.#fetchRecentPages();
      else this.#fetchWikiPages();
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

  async #fetchRecentPages() {
    this._wikiLoading = true;
    try {
      const res = await fetch('/api/vault/recent');
      if (res.ok) {
        const data = await res.json();
        this._recentPages = data.pages || [];
      } else {
        this._recentPages = [];
      }
    } catch (e) {
      this._recentPages = [];
    } finally {
      this._wikiLoading = false;
    }
  }

  #switchVaultView(view) {
    this._vaultView = view;
    if (view === 'recent') {
      this.#fetchRecentPages();
    } else {
      this.#fetchWikiPages();
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

  /** Format a timestamp as a relative time string (e.g., "2h ago"). */
  #formatRelativeTime(mtime) {
    const seconds = Math.floor((Date.now() / 1000) - mtime);
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d ago`;
    return new Date(mtime * 1000).toLocaleDateString();
  }

  #renderVaultBrowse() {
    const hasContent = this._vaultFolders.length > 0 || this._wikiPages.length > 0;
    return html`
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
    `;
  }

  #renderVaultRecent() {
    if (!this._recentPages.length) {
      return html`<p style="padding: 1rem; color: var(--pico-muted-color);">No recent changes.</p>`;
    }
    return html`
      ${this._recentPages.map(p => {
        const pagePath = p.path || p.title;
        const isOpen = pagePath === this._openWikiPage;
        return html`
          <div class="conv-item wiki-item recent-item ${isOpen ? 'active' : ''}" @click=${() => this.#handleWikiSelect(pagePath)} title=${pagePath}>
            ${p.folder ? html`<span class="recent-folder">${p.folder}/</span>` : nothing}
            <span class="conv-title">${p.title}</span>
            <span class="recent-time">${this.#formatRelativeTime(p.modified)}</span>
          </div>
        `;
      })}
    `;
  }

  render() {
    if (this._wikiLoading) {
      return html`<div class="conv-list"><p style="padding: 1rem; color: var(--pico-muted-color);">Loading vault pages...</p></div>`;
    }
    return html`
      <div class="conv-list">
        <div class="vault-view-toggle">
          <button class="${this._vaultView === 'browse' ? 'active' : ''}"
            @click=${() => this.#switchVaultView('browse')}>Browse</button>
          <button class="${this._vaultView === 'recent' ? 'active' : ''}"
            @click=${() => this.#switchVaultView('recent')}>Recent</button>
        </div>
        ${this._vaultView === 'browse' ? this.#renderVaultBrowse() : this.#renderVaultRecent()}
      </div>
    `;
  }
}

customElements.define('vault-sidebar', VaultSidebar);
