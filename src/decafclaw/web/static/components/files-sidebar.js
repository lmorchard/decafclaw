import { LitElement, html, nothing } from 'lit';

/**
 * @typedef {{name: string, path: string, size: number, modified: number, kind: string, readonly: boolean, secret: boolean}} FileEntry
 * @typedef {{name: string, path: string}} FolderEntry
 */

export class FilesSidebar extends LitElement {
  static properties = {
    active: { type: Boolean },
    _currentFolder: { type: String, state: true },
    _files: { type: Array, state: true },
    _folders: { type: Array, state: true },
    _recentFiles: { type: Array, state: true },
    _view: { type: String, state: true },
    _loading: { type: Boolean, state: true },
    _showHidden: { type: Boolean, state: true },
    _openFilePath: { type: String, state: true },
    _everActivated: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.active = false;
    this._currentFolder = '';
    /** @type {FileEntry[]} */
    this._files = [];
    /** @type {FolderEntry[]} */
    this._folders = [];
    /** @type {FileEntry[]} */
    this._recentFiles = [];
    this._view = 'browse'; // 'browse' | 'recent'
    this._loading = false;
    this._showHidden = localStorage.getItem('files-show-hidden') === 'true';
    /** @type {string|null} */
    this._openFilePath = null;
    // Set to true on first Files-tab activation. Gates the workspace-file-deleted
    // listener so it doesn't fire before the user has opened the Files tab.
    this._everActivated = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._onFileDeleted = () => {
      if (!this._everActivated) return;
      this.#refresh();
    };
    window.addEventListener('workspace-file-deleted', this._onFileDeleted);
    // Auto-refetch current view on agent turn completion so newly-written files
    // appear without the user hitting Refresh. Scoped to the active tab to
    // avoid wasted fetches when the sidebar is showing something else.
    this._onTurnComplete = () => {
      if (!this.active || !this._everActivated) return;
      // Silent refetch — keep the existing listing visible instead of swapping
      // it for the "Loading files..." placeholder.
      this.#refresh({ silent: true });
    };
    window.addEventListener('turn-complete', this._onTurnComplete);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('workspace-file-deleted', this._onFileDeleted);
    window.removeEventListener('turn-complete', this._onTurnComplete);
  }

  /** @param {Map} changedProps */
  updated(changedProps) {
    // Re-fetch on every false→true transition of `active`, mirroring
    // <vault-sidebar>. `_everActivated` stays as a latch so the
    // workspace-file-deleted listener doesn't fire before the user has ever
    // opened the Files tab.
    if (changedProps.has('active') && this.active && !changedProps.get('active')) {
      this._everActivated = true;
      this.#refresh();
    }
  }

  /** @param {{silent?: boolean}} [opts] */
  #refresh(opts = {}) {
    if (this._view === 'recent') this.#fetchRecent(opts);
    else this.#fetchBrowse(opts);
  }

  /** @param {{silent?: boolean}} [opts] */
  async #fetchBrowse(opts = {}) {
    const silent = opts.silent === true;
    if (!silent) this._loading = true;
    try {
      const url = this._currentFolder
        ? `/api/workspace?folder=${encodeURIComponent(this._currentFolder)}`
        : '/api/workspace';
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        this._folders = data.folders || [];
        this._files = data.files || [];
      } else {
        // Silent (auto-refresh on turn-complete) errors shouldn't blank the
        // listing the user is looking at — a transient blip is invisible.
        if (silent) {
          console.debug('files-sidebar: silent refresh got non-ok response', res.status);
          return;
        }
        this._folders = [];
        this._files = [];
        if (res.status === 404) this._currentFolder = '';
      }
    } catch (e) {
      if (silent) {
        console.debug('files-sidebar: silent refresh failed', e);
        return;
      }
      this._folders = [];
      this._files = [];
    } finally {
      if (!silent) this._loading = false;
    }
  }

  /** @param {{silent?: boolean}} [opts] */
  async #fetchRecent(opts = {}) {
    const silent = opts.silent === true;
    if (!silent) this._loading = true;
    try {
      const res = await fetch('/api/workspace/recent');
      if (res.ok) {
        const data = await res.json();
        this._recentFiles = data.files || [];
      } else {
        if (silent) {
          console.debug('files-sidebar: silent refresh got non-ok response', res.status);
          return;
        }
        this._recentFiles = [];
      }
    } catch (e) {
      if (silent) {
        console.debug('files-sidebar: silent refresh failed', e);
        return;
      }
      this._recentFiles = [];
    } finally {
      if (!silent) this._loading = false;
    }
  }

  #switchView(view) {
    this._view = view;
    if (view === 'recent') this.#fetchRecent();
    else this.#fetchBrowse();
  }

  /** @param {string} folderPath */
  #navigateToFolder(folderPath) {
    this._currentFolder = folderPath;
    if (this._view !== 'browse') this._view = 'browse';
    this.#fetchBrowse();
  }

  /**
   * Public: navigate the sidebar to a specific folder.
   * @param {string} folderPath
   */
  navigateToFolder(folderPath) {
    this.#navigateToFolder(folderPath);
  }

  /**
   * Navigate the sidebar to the folder containing a given file path.
   * @param {string} filePath
   */
  navigateToFileFolder(filePath) {
    this._openFilePath = filePath;
    const lastSlash = filePath.lastIndexOf('/');
    const folder = lastSlash >= 0 ? filePath.substring(0, lastSlash) : '';
    if (folder !== this._currentFolder || this._view !== 'browse') {
      this._currentFolder = folder;
      this._view = 'browse';
      this.#fetchBrowse();
    }
  }

  /** Clear the open file highlight (called when file pane is closed). */
  clearOpenFile() {
    this._openFilePath = null;
  }

  /** @param {FileEntry} f */
  #handleFileClick(f) {
    if (f.secret) return;
    this.dispatchEvent(new CustomEvent('file-open', {
      detail: { path: f.path, kind: f.kind, readonly: f.readonly },
      bubbles: true,
      composed: true,
    }));
  }

  #toggleShowHidden() {
    this._showHidden = !this._showHidden;
    localStorage.setItem('files-show-hidden', String(this._showHidden));
  }

  /** Format a UNIX-seconds timestamp as a short relative time. */
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

  /** Format a byte count as a short human-readable size (e.g. "1.2 KB"). */
  #formatBytes(n) {
    if (n == null || isNaN(n)) return '';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  }

  /** @param {FileEntry} f */
  #fileIcon(f) {
    if (f.secret) return '\u{1F510}'; // 🔐
    if (f.readonly) return '\u{1F512}'; // 🔒
    if (f.kind === 'image') return '\u{1F5BC}\u{FE0F}'; // 🖼️
    if (f.kind === 'text') return '\u{1F4C4}'; // 📄
    return '\u{1F4E6}'; // 📦
  }

  /** @param {string} name */
  #isHidden(name) {
    return name.startsWith('.');
  }

  #renderBreadcrumbs() {
    if (!this._currentFolder) {
      return html`<span class="vault-breadcrumb-segment active">files</span>`;
    }
    const segments = this._currentFolder.split('/');
    return html`
      <button type="button" class="vault-breadcrumb-segment" @click=${() => this.#navigateToFolder('')}>files</button>
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

  #renderBrowse() {
    const folders = this._showHidden
      ? this._folders
      : this._folders.filter(f => !this.#isHidden(f.name));
    const files = this._showHidden
      ? this._files
      : this._files.filter(f => !this.#isHidden(f.name));
    const hasContent = folders.length > 0 || files.length > 0;
    return html`
      <div class="vault-breadcrumbs">${this.#renderBreadcrumbs()}</div>
      ${folders.map(f => html`
        <div class="conv-item wiki-item wiki-folder-item" @click=${() => this.#navigateToFolder(f.path)} title=${f.path}>
          <span class="conv-title">\u{1F4C1} ${f.name}</span>
        </div>
      `)}
      ${files.map(f => {
        const isOpen = f.path === this._openFilePath;
        const secretTitle = f.secret ? 'This file is hidden from the UI by policy' : f.path;
        return html`
          <div
            class="conv-item wiki-item ${isOpen ? 'active' : ''} ${f.secret ? 'secret' : ''}"
            @click=${() => this.#handleFileClick(f)}
            title=${secretTitle}
          >
            <span class="conv-title">${this.#fileIcon(f)} ${f.name}</span>
            <span class="recent-time">${this.#formatRelativeTime(f.modified)} · ${this.#formatBytes(f.size)}</span>
          </div>
        `;
      })}
      ${!hasContent
        ? html`<p style="padding: 1rem; color: var(--pico-muted-color);">Empty folder.</p>`
        : nothing
      }
    `;
  }

  #renderRecent() {
    const files = this._showHidden
      ? this._recentFiles
      : this._recentFiles.filter(f => !this.#isHidden(f.name));
    if (!files.length) {
      return html`<p style="padding: 1rem; color: var(--pico-muted-color);">No recent changes.</p>`;
    }
    return html`
      ${files.map(f => {
        const isOpen = f.path === this._openFilePath;
        const lastSlash = f.path.lastIndexOf('/');
        const folder = lastSlash >= 0 ? f.path.substring(0, lastSlash) : '';
        const secretTitle = f.secret ? 'This file is hidden from the UI by policy' : f.path;
        return html`
          <div
            class="conv-item wiki-item recent-item ${isOpen ? 'active' : ''} ${f.secret ? 'secret' : ''}"
            @click=${() => this.#handleFileClick(f)}
            title=${secretTitle}
          >
            ${folder ? html`<span class="recent-folder">${folder}/</span>` : nothing}
            <span class="conv-title">${this.#fileIcon(f)} ${f.name}</span>
            <span class="recent-time">${this.#formatRelativeTime(f.modified)} · ${this.#formatBytes(f.size)}</span>
          </div>
        `;
      })}
    `;
  }

  render() {
    return html`
      <div class="conv-list">
        <div class="vault-view-toggle">
          <button class="${this._view === 'browse' ? 'active' : ''}"
            @click=${() => this.#switchView('browse')}>Browse</button>
          <button class="${this._view === 'recent' ? 'active' : ''}"
            @click=${() => this.#switchView('recent')}>Recent</button>
        </div>
        <div class="vault-action-btns">
          <label style="display:flex;align-items:center;gap:0.4rem;font-size:0.85em;margin:0;cursor:pointer;">
            <input type="checkbox" .checked=${this._showHidden} @change=${() => this.#toggleShowHidden()} style="margin:0;" />
            Show hidden
          </label>
          <button class="outline" @click=${() => this.#refresh()} title="Refresh">Refresh</button>
        </div>
        ${this._loading
          ? html`<p style="padding: 1rem; color: var(--pico-muted-color);">Loading files...</p>`
          : (this._view === 'browse' ? this.#renderBrowse() : this.#renderRecent())
        }
      </div>
    `;
  }
}

customElements.define('files-sidebar', FilesSidebar);
