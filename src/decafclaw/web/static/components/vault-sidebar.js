import { LitElement, html, nothing } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';
import { activateOnEnterOrSpace } from '../lib/row-activation.js';
import { isBookmarked, load as loadBookmarks, subscribe as subscribeBookmarks, toggle as toggleBookmark } from '../lib/bookmarks-store.js';

/** @param {unknown} body */
function responseError(body) {
  if (!body || typeof body !== 'object' || !("error" in body)) return '';
  return typeof body.error === 'string' ? body.error : '';
}

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
    _bookmarks: { type: Object, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.active = false;
    /** @type {import('../lib/api-client/index.js').VaultPageListEntry[]} */
    this._wikiPages = [];
    this._wikiLoading = false;
    this._vaultFolder = '';
    /** @type {import('../lib/api-client/index.js').VaultFolderEntry[]} */
    this._vaultFolders = [];
    /** @type {string|null} */
    this._openWikiPage = null;
    this._vaultView = 'browse'; // 'browse' | 'recent'
    /** @type {import('../lib/api-client/index.js').VaultPageListEntry[]} */
    this._recentPages = [];
    /** @type {import('../lib/api-client/index.js').BookmarksResponse} */
    this._bookmarks = { files: [], vault: [] };
  }

  connectedCallback() {
    super.connectedCallback();
    this._onVaultChanged = () => {
      if (this._vaultView === 'recent') this.#fetchRecentPages();
      else this.#fetchWikiPages();
    };
    window.addEventListener('vault-changed', this._onVaultChanged);
    // Pinned Bookmarks section (#1086): shared store, one GET per session.
    this._unsubBookmarks = subscribeBookmarks(snap => { this._bookmarks = snap; });
    void loadBookmarks();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('vault-changed', this._onVaultChanged);
    if (this._unsubBookmarks) this._unsubBookmarks();
  }

  /** @param {Map} changedProps */
  updated(changedProps) {
    // Re-fetch on every false→true transition of `active` — clicking the
    // Vault tab refreshes the current view as a fallback (and as the
    // initial-load trigger before any mutation event has fired).
    if (changedProps.has('active') && this.active && !changedProps.get('active')) {
      if (this._vaultView === 'recent') this.#fetchRecentPages();
      else this.#fetchWikiPages();
    }
  }

  async #fetchWikiPages() {
    this._wikiLoading = true;
    try {
      const data = await DefaultService.wrapperApiVaultGet(
        this._vaultFolder || undefined,
      );
      this._vaultFolders = data.folders;
      this._wikiPages = data.pages;
    } catch (e) {
      this._vaultFolders = [];
      this._wikiPages = [];
      if (e instanceof ApiError && e.status === 404) this._vaultFolder = '';
    } finally {
      this._wikiLoading = false;
    }
  }

  async #fetchRecentPages() {
    this._wikiLoading = true;
    try {
      const data = await DefaultService.wrapperApiVaultRecentGet();
      this._recentPages = data.pages;
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
    // Recent view isn't folder-scoped — refetching here would flip
    // _wikiLoading and replace the list with a placeholder, resetting scroll.
    if (this._vaultView === 'recent') return;
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
      await DefaultService.wrapperApiVaultFoldersPost({ folder: folderPath }, true);
      this.#navigateToFolder(folderPath);
    } catch (e) {
      if (e instanceof ApiError) {
        /** @type {unknown} */ const body = e.body;
        alert(responseError(body) || `Failed to create folder (${e.status})`);
      } else alert('Failed to create folder.');
    }
  }

  /** @param {string} fullName */
  async #createPageInFolder(fullName) {
    try {
      await DefaultService.wrapperApiVaultPost({ name: fullName }, true);
      this.dispatchEvent(new CustomEvent('wiki-open', {
        detail: { page: fullName, editing: true },
        bubbles: true,
        composed: true,
      }));
      this.#fetchWikiPages();
    } catch (e) {
      if (e instanceof ApiError) {
        /** @type {unknown} */ const body = e.body;
        alert(responseError(body) || `Failed to create page (${e.status})`);
      } else alert('Failed to create page.');
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

  /**
   * Format a timestamp as a relative time string (e.g., "2h ago").
   * @param {number} mtime
   */
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

  // ── Bookmarks (#1086) ─────────────────────────────────────────────────

  /** @param {import('../lib/api-client/index.js').BookmarkEntry} b */
  #openBookmark(b) {
    if (b.kind === 'folder') {
      this.#navigateToFolder(b.path);
      return;
    }
    this.dispatchEvent(new CustomEvent('wiki-open', {
      detail: { page: b.path },
      bubbles: true,
      composed: true,
    }));
  }

  #renderBookmarks() {
    const entries = this._bookmarks.vault;
    if (!entries.length) return nothing;
    return html`
      <div class="bookmark-section">
        <div class="bookmark-section-header">\u{1F516} Bookmarks</div>
        ${entries.map(b => html`
          <div class="conv-item wiki-item bookmark-row" title=${b.path}
            tabindex="0"
            aria-label=${b.kind === 'folder' ? `Open folder ${b.label}` : `Open page ${b.label}`}
            @click=${() => this.#openBookmark(b)}
            @keydown=${activateOnEnterOrSpace(() => this.#openBookmark(b))}>
            <span class="conv-title">${b.kind === 'folder' ? '\u{1F4C1}' : '\u{1F4C4}'} ${b.label}</span>
          </div>
        `)}
      </div>
    `;
  }

  /**
   * ☆/★ toggle rendered on folder rows.
   * @param {import('../lib/bookmarks-store.js').BookmarkTarget} target
   * @param {string} folderPath
   * @param {string} folderName
   */
  #folderBookmarkBtn(target, folderPath, folderName) {
    const on = this._bookmarks[target].some(b => b.path === folderPath);
    const title = on ? 'Remove bookmark' : 'Bookmark this folder';
    return html`
      <button type="button" class="folder-bm-btn dc-icon-btn ${on ? 'active' : ''}"
        title=${title} aria-label=${`${on ? 'Remove' : 'Add'} bookmark for ${folderName}`}
        @click=${(/** @type {Event} */ e) => { e.stopPropagation(); void this.#toggleFolderBookmark(target, folderPath, folderName); }}>
        ${on ? '★' : '☆'}
      </button>
    `;
  }

  /**
   * @param {import('../lib/bookmarks-store.js').BookmarkTarget} target
   * @param {string} folderPath
   * @param {string} folderName
   */
  async #toggleFolderBookmark(target, folderPath, folderName) {
    try {
      await toggleBookmark(target, folderPath, target === 'files' ? 'dir' : 'folder', folderName);
    } catch (e) {
      if (e instanceof ApiError) {
        console.debug('vault-sidebar: bookmark toggle failed', e.status);
      } else {
        console.debug('vault-sidebar: bookmark toggle failed', e);
      }
    }
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
        <div class="conv-item wiki-item wiki-folder-item" title=${f.path}
          tabindex="0" aria-label=${`Open folder ${f.name}`}
          @click=${() => this.#navigateToFolder(f.path)}
          @keydown=${activateOnEnterOrSpace(() => this.#navigateToFolder(f.path))}>
          <span class="conv-title">\u{1F4C1} ${f.name}</span>
          ${this.#folderBookmarkBtn('vault', f.path, f.name)}
        </div>
      `)}
      ${this._wikiPages.map(p => {
        const pagePath = p.path || p.title;
        const isOpen = pagePath === this._openWikiPage;
        return html`
          <div class="conv-item wiki-item ${isOpen ? 'active' : ''}" title=${pagePath}
            tabindex="0" aria-label=${`Open page ${p.title}`}
            @click=${() => this.#handleWikiSelect(pagePath)}
            @keydown=${activateOnEnterOrSpace(() => this.#handleWikiSelect(pagePath))}>
            <span class="conv-title">${p.title}</span>
            ${p.summary ? html`<span class="wiki-item-summary">${p.summary}</span>` : nothing}
          </div>
        `;
      })}
      ${!hasContent
        ? html`<p style="padding: 1rem; color: var(--pico-muted-color);">${this._wikiLoading ? 'Loading vault pages...' : 'Empty folder.'}</p>`
        : nothing
      }
    `;
  }

  #renderVaultRecent() {
    if (!this._recentPages.length) {
      return html`<p style="padding: 1rem; color: var(--pico-muted-color);">${this._wikiLoading ? 'Loading vault pages...' : 'No recent changes.'}</p>`;
    }
    return html`
      ${this._recentPages.map(p => {
        const pagePath = p.path || p.title;
        const isOpen = pagePath === this._openWikiPage;
        return html`
          <div class="conv-item wiki-item recent-item ${isOpen ? 'active' : ''}" title=${pagePath}
            tabindex="0" aria-label=${`Open page ${p.folder ? `${p.folder}/` : ''}${p.title}`}
            @click=${() => this.#handleWikiSelect(pagePath)}
            @keydown=${activateOnEnterOrSpace(() => this.#handleWikiSelect(pagePath))}>
            ${p.folder ? html`<span class="recent-folder">${p.folder}/</span>` : nothing}
            <span class="conv-title">${p.title}</span>
            <span class="recent-time">${this.#formatRelativeTime(p.modified)}</span>
            ${p.summary ? html`<span class="wiki-item-summary">${p.summary}</span>` : nothing}
          </div>
        `;
      })}
    `;
  }

  render() {
    // Don't gate the whole list on _wikiLoading — replacing the list with a
    // placeholder during refetch destroys the user's scroll position. Render
    // the list as-is; only the empty-state line swaps to "Loading..." so a
    // first-load gives feedback. Refetches with existing data update in place.
    return html`
      <div class="conv-list">
        ${this.#renderBookmarks()}
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
