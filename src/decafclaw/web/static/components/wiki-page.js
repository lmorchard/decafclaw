/**
 * Wiki page viewer/editor — fetches a wiki page and shows it in either
 * read-only (markdown) or edit (Milkdown WYSIWYG) mode.
 *
 * Mode preference is persisted in localStorage. Defaults to edit mode.
 * Dispatches 'wiki-navigate' events when [[wiki-links]] are clicked (panel mode).
 */

import { LitElement, html, nothing } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { renderMarkdown } from '../lib/markdown.js';
import { encodePagePath } from '../lib/utils.js';
import { WikiWriteMutex } from '../lib/wiki-page-write-mutex.js';
import './wiki-editor.js';
import './wiki-metadata.js';

const EDIT_MODE_KEY = 'wiki-edit-mode';

/**
 * An explicit destination for a metadata PUT, used when it must not be read
 * off `this` — i.e. when flushing a pending edit for the page we're leaving.
 * @typedef {{page: string, modified: number}} MetaTarget
 */

/**
 * @param {number} ts — Unix timestamp (seconds)
 * @returns {string}
 */
function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export class WikiPage extends LitElement {
  static properties = {
    page: { type: String },
    standalone: { type: Boolean },
    _body: { state: true },
    _frontmatter: { state: true },
    _frontmatterRaw: { state: true },
    _frontmatterError: { state: true },
    _metaError: { state: true },
    _orphanMetaError: { state: true },
    _loaded: { state: true },
    _title: { state: true },
    _modified: { state: true },
    _loading: { state: true },
    _error: { state: true },
    _editing: { state: true },
    _renaming: { state: true },
    _renameValue: { state: true },
    _renameError: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.page = '';
    this.standalone = false;
    /** @type {string} */ this._body = '';
    /** @type {Record<string, any>} */ this._frontmatter = {};
    /** @type {string} */ this._frontmatterRaw = '';
    /** @type {string} */ this._frontmatterError = '';
    /**
     * Set when a metadata write (typed patch or raw replace) fails. Passed
     * straight through to <wiki-metadata> for display.
     * @type {{status: 'conflict'|'error', message: string} | null}
     */
    this._metaError = null;
    /**
     * Set when a metadata write aimed at a page we have already navigated
     * away from fails. Deliberately *not* `_metaError`: that state drives
     * Reload/Overwrite/Retry buttons which all act on the page currently on
     * screen, so reusing it would re-misdeliver the departed page's fields.
     * Survives navigation and `_fetchPage()` so the failure can't vanish.
     * @type {string}
     */
    this._orphanMetaError = '';
    this._loaded = false;
    /** @type {string} */ this._title = '';
    /** @type {number} */ this._modified = 0;
    this._loading = false;
    /** @type {string} */ this._error = '';
    // Default to edit mode, persisted in localStorage
    this._editing = localStorage.getItem(EDIT_MODE_KEY) !== 'false';
    this._renaming = false;
    /** @type {string} */ this._renameValue = '';
    /** @type {string} */ this._renameError = '';
    this.#mutex = new WikiWriteMutex(this.#apiPut.bind(this));
  }

  /** @type {ReturnType<typeof setTimeout> | null} */
  #metaTimer = null;
  /** @type {WikiWriteMutex} */
  #mutex;

  /** @param {Map<string, any>} changed */
  willUpdate(changed) {
    if (!changed.has('page')) return;
    // Lit has already assigned the NEW page to `this.page` by the time
    // willUpdate runs, so anything that must reach the page we're *leaving*
    // has to be told which page that is. `this._modified` is still the old
    // page's mtime here, so it travels with it.
    /** @type {string} */
    const prev = changed.get('page') || '';
    if (this._editing && prev) {
      void this.#flushMetadata({ page: prev, modified: this._modified });
      // <wiki-editor> needs no target: it holds its own `page` property, and
      // Lit has not re-rendered it yet, so its save still aims at `prev`.
      /** @type {import('./wiki-editor.js').WikiEditor|null} */
      const editor = this.querySelector('wiki-editor');
      if (editor) editor.flushSave();
    }
    // `page` going empty is the wiki pane closing (app.js). Flushing above
    // rather than bailing early is the point: the debounce timer would
    // otherwise fire 600ms later and PUT to whatever page opens next.
    if (this.page) this._fetchPage();
  }

  /** @param {CustomEvent} e */
  _onMetadataChange(e) {
    if (!this.#mutex.queueFields(e.detail.fields)) return;
    if (this.#metaTimer != null) clearTimeout(this.#metaTimer);
    this.#metaTimer = setTimeout(() => { this.#flushMetadata(); }, 600);
  }

  /**
   * Send any debounced typed patch now. Resolves when the write completes.
   * @param {import('../lib/wiki-page-write-mutex.js').MetaTarget} [target] Page to write to, when it is no longer the
   *   one `this.page` names — i.e. we're navigating away from it.
   */
  async #flushMetadata(target) {
    if (this.#metaTimer != null) {
      clearTimeout(this.#metaTimer);
      this.#metaTimer = null;
    }
    await this.#mutex.flush(this.page, this._modified, target);
    this._syncMutexState();
  }

  /** @param {CustomEvent} e */
  async _onMetadataRawSave(e) {
    const res = await this.#mutex.saveRaw(e.detail.raw, this.page, this._modified);
    if (res?.ok) {
      /** @type {any} */ (this.querySelector('wiki-metadata'))?.closeRaw();
    } else if (res?.error && res.error !== 'Metadata was modified externally.' && res.error !== 'Resolve the pending metadata conflict above before saving raw YAML.') {
      // Malformed YAML etc. — keep the raw editor's existing inline error.
      /** @type {any} */ (this.querySelector('wiki-metadata'))?.setRawError(res.error);
    } else if (res?.error === 'Resolve the pending metadata conflict above before saving raw YAML.') {
      /** @type {any} */ (this.querySelector('wiki-metadata'))?.setRawError(res.error);
    }
    this._syncMutexState();
  }

  /** Refetch the page from the server, discarding any pending local metadata edit. */
  async _onMetadataReload() {
    this.#mutex.reload();
    this._syncMutexState();
    /** @type {any} */ (this.querySelector('wiki-metadata'))?.closeRaw();
    await this._fetchPage();
  }

  /** Resend the last failed write, skipping the server's mtime check. */
  async _onMetadataOverwrite() {
    const wasRaw = this.#mutex.lastMetaAttempt?.kind === 'raw';
    await this.#mutex.overwrite(this.page, this._modified);
    if (wasRaw && !this.#mutex.metaError) {
      /** @type {any} */ (this.querySelector('wiki-metadata'))?.closeRaw();
    }
    this._syncMutexState();
  }

  /** Retry the last failed write with the normal (checked) path. */
  async _onMetadataRetry() {
    await this.#mutex.retry(this.page, this._modified);
    this._syncMutexState();
  }

  _syncMutexState() {
    this._metaError = this.#mutex.metaError;
    this._orphanMetaError = this.#mutex.orphanMetaError;
  }

  /**
   * @param {object} body
   * @param {string} page
   * @param {number} modified
   * @returns {Promise<{ok: boolean, status: number, error: string, data?: any}>}
   */
  async #apiPut(body, page, modified) {
    try {
      const res = await fetch('/api/vault/' + encodePagePath(page), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return { ok: false, status: res.status, error: data.error || `Save failed (${res.status})` };
      }
      
      // Adopt the response into our own state only while we're still showing
      // the page it was written to.
      if (page === this.page) {
        this._frontmatter = data.frontmatter ?? {};
        this._frontmatterRaw = data.frontmatter_raw ?? '';
        this._frontmatterError = data.frontmatter_error ?? '';
        this._modified = data.modified;
        /** @type {any} */
        const editor = this.querySelector('wiki-editor');
        if (editor) editor.modified = data.modified;
      }
      return { ok: true, status: res.status, error: '', data };
    } catch (err) {
      return { ok: false, status: 0, error: 'Save failed (network error)' };
    }
  }

  async _fetchPage() {
    this.#mutex.reload();
    this._syncMutexState();
    this._loading = true;
    this._error = '';
    this._loaded = false;
    this._body = '';
    try {
      const res = await fetch('/api/vault/' + encodePagePath(this.page));
      if (!res.ok) {
        this._error = res.status === 404 ? `Page "${this.page}" not found.` : `Error loading page (${res.status}).`;
        return;
      }
      const data = await res.json();
      this._title = data.title;
      this._body = data.body ?? '';
      this._frontmatter = data.frontmatter ?? {};
      this._frontmatterRaw = data.frontmatter_raw ?? '';
      this._frontmatterError = data.frontmatter_error ?? '';
      this._modified = data.modified;
      this._loaded = true;
    } catch (e) {
      this._error = 'Failed to load page.';
    } finally {
      this._loading = false;
    }
  }

  async _toggleMode() {
    if (this._editing) {
      // Flush editor save before switching to view mode
      await this.#flushMetadata();
      // A failed flush leaves an unresolved _metaError with its
      // Reload/Overwrite/Retry affordance. Switching to view mode now would
      // call _fetchPage(), which unconditionally wipes #pendingFields and
      // _metaError and overwrites _frontmatter from the server — silently
      // discarding the edit and the only visible sign anything went wrong.
      // Stay in edit mode so the conflict banner remains visible instead.
      if (this._metaError) return;
      /** @type {import('./wiki-editor.js').WikiEditor|null} */
      const editor = this.querySelector('wiki-editor');
      if (editor) await editor.flushSave();
      this._editing = false;
      await this._fetchPage();
    } else {
      this._editing = true;
    }
    localStorage.setItem(EDIT_MODE_KEY, String(this._editing));
  }

  /** Public method to force edit mode (called from app.js for new pages). */
  startEditing() {
    this._editing = true;
    localStorage.setItem(EDIT_MODE_KEY, 'true');
  }

  _close() {
    this.dispatchEvent(new CustomEvent('wiki-close', {
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {CustomEvent} e */
  _onSaved(e) {
    this._modified = e.detail.modified;
  }

  /**
   * The editor's conflict Reload refetched the page, so its mtime is fresh
   * and ours is stale — the next metadata write would 409 for no reason.
   * @param {CustomEvent} e
   */
  _onReloaded(e) {
    this._modified = e.detail.modified;
  }

  #startRename() {
    this._renaming = true;
    this._renameValue = this.page;
    this._renameError = '';
  }

  #cancelRename() {
    this._renaming = false;
    this._renameError = '';
  }

  /** @param {KeyboardEvent} e */
  #handleRenameKey(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      this.#submitRename();
    } else if (e.key === 'Escape') {
      this.#cancelRename();
    }
  }

  async #submitRename() {
    const newPath = this._renameValue.trim();
    if (!newPath || newPath === this.page) {
      this.#cancelRename();
      return;
    }
    try {
      // Flush any pending editor save before moving the file
      /** @type {import('./wiki-editor.js').WikiEditor|null} */
      const editor = this.querySelector('wiki-editor');
      if (editor) await editor.flushSave();

      const res = await fetch('/api/vault/' + encodePagePath(this.page), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rename_to: newPath }),
      });
      if (res.status === 409) {
        this._renameError = 'A page already exists at that path.';
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        this._renameError = data.error || `Rename failed (${res.status})`;
        return;
      }
      this._renaming = false;
      this._renameError = '';
      // Navigate to the new page
      this.dispatchEvent(new CustomEvent('wiki-open', {
        detail: { page: newPath },
        bubbles: true,
        composed: true,
      }));
    } catch {
      this._renameError = 'Rename failed.';
    }
  }

  async #deletePage() {
    if (!confirm(`Delete "${this.page}"?`)) return;
    try {
      const res = await fetch('/api/vault/' + encodePagePath(this.page), {
        method: 'DELETE',
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.error || `Delete failed (${res.status})`);
        return;
      }
      this._close();
    } catch {
      alert('Delete failed.');
    }
  }

  /** @param {string} folderPath */
  #navigateFolder(folderPath) {
    this.dispatchEvent(new CustomEvent('wiki-navigate-folder', {
      detail: { folder: folderPath },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {Event} e */
  _handleClick(e) {
    const link = /** @type {HTMLElement} */ (e.target).closest('a.wiki-link');
    if (!link) return;
    const page = link.getAttribute('data-wiki-page');
    if (!page) return;

    if (this.standalone) return; // navigate normally via <a> href

    e.preventDefault();
    this.dispatchEvent(new CustomEvent('wiki-navigate', {
      detail: { page },
      bubbles: true,
      composed: true,
    }));
  }

  /**
   * Failure notice for a metadata write aimed at a page that is no longer on
   * screen. Rendered by the host rather than <wiki-metadata> so it survives
   * that panel's readonly/empty gating, and carries no Reload/Overwrite/Retry
   * — those would act on the wrong page.
   */
  #renderOrphanError() {
    if (!this._orphanMetaError) return nothing;
    return html`
      <div class="wiki-page-orphan-error">
        <span>${this._orphanMetaError}</span>
        <button
          type="button"
          class="wiki-page-orphan-dismiss"
          aria-label="Dismiss"
          @click=${() => { this._orphanMetaError = ''; }}
        >&times;</button>
      </div>
    `;
  }

  render() {
    const orphan = this.#renderOrphanError();
    if (this._loading) {
      return html`${orphan}<div class="wiki-page-loading">Loading...</div>`;
    }
    if (this._error) {
      return html`${orphan}<div class="wiki-page-error">${this._error}</div>`;
    }
    // Guard on load state, not on body text: a page with frontmatter but an
    // empty body is legitimate and must still render its metadata.
    if (!this._loaded) {
      return orphan;
    }

    const newTabUrl = '/vault/' + encodePagePath(this.page);
    const modeIcon = this._editing ? '\u{1f441}' : '\u{270e}';
    const modeTitle = this._editing ? 'Switch to view mode' : 'Switch to edit mode';
    const closeBtn = this.standalone ? nothing : html`
      <button class="wiki-close-btn dc-icon-btn" @click=${() => this._close()} title="Close wiki pane" aria-label="Close wiki pane">&times;</button>
    `;
    const rightButtons = html`
      <button class="wiki-edit-btn dc-icon-btn" @click=${() => this._toggleMode()} title=${modeTitle} aria-label=${modeTitle}>${modeIcon}</button>
      <button class="wiki-delete-btn dc-icon-btn" @click=${() => this.#deletePage()} title="Delete page" aria-label="Delete page">\u{1F5D1}</button>
      <a href=${newTabUrl} target="_blank" rel="noopener" class="wiki-open-tab dc-icon-btn" title="Open in new tab" aria-label="Open in new tab">&#8599;</a>
      ${closeBtn}
    `;

    // Breadcrumb content: folder segments (clickable) + page name (not clickable) + rename
    const parts = this.page.split('/');
    const pageName = parts[parts.length - 1];
    const folderParts = parts.slice(0, -1);
    const breadcrumbContent = this._renaming
      ? html`
        <div class="wiki-rename-bar">
          <input
            class="wiki-rename-input"
            aria-label="New page path"
            .value=${this._renameValue}
            @input=${(/** @type {InputEvent} */ e) => { this._renameValue = /** @type {HTMLInputElement} */ (e.target).value; }}
            @keydown=${(/** @type {KeyboardEvent} */ e) => this.#handleRenameKey(e)}
          />
          <button class="wiki-rename-ok" @click=${() => this.#submitRename()} title="Confirm rename" aria-label="Confirm rename">\u2713</button>
          <button class="wiki-rename-cancel" @click=${() => this.#cancelRename()} title="Cancel rename" aria-label="Cancel rename">\u2717</button>
          ${this._renameError ? html`<span class="wiki-rename-error">${this._renameError}</span>` : nothing}
        </div>
      `
      : html`
        <span class="wiki-page-breadcrumbs">
          ${folderParts.map((seg, i) => {
            const folderPath = folderParts.slice(0, i + 1).join('/');
            return html`
              <button type="button" class="wiki-bc-segment" @click=${() => this.#navigateFolder(folderPath)}>${seg}</button>
              <span class="wiki-bc-sep">/</span>
            `;
          })}
          <span class="wiki-bc-page">${pageName}</span>
          <button class="wiki-rename-btn dc-icon-btn" @click=${() => this.#startRename()} title="Rename / move page" aria-label="Rename / move page">\u{270e}</button>
        </span>
      `;

    const metadataPanel = html`
      <wiki-metadata
        ?readonly=${!this._editing}
        .frontmatter=${this._frontmatter}
        .frontmatterRaw=${this._frontmatterRaw}
        .frontmatterError=${this._frontmatterError}
        .metaError=${this._metaError}
        @metadata-change=${this._onMetadataChange}
        @metadata-raw-save=${this._onMetadataRawSave}
        @metadata-reload=${this._onMetadataReload}
        @metadata-overwrite=${this._onMetadataOverwrite}
        @metadata-retry=${this._onMetadataRetry}
      ></wiki-metadata>
    `;

    if (this._editing) {
      return html`
        <div class="wiki-page">
          ${orphan}
          ${metadataPanel}
          <wiki-editor
            page=${this.page}
            .content=${this._body}
            .modified=${this._modified}
            .toolbarLeft=${breadcrumbContent}
            .toolbarExtra=${rightButtons}
            @saved=${this._onSaved}
            @reloaded=${this._onReloaded}
          ></wiki-editor>
        </div>
      `;
    }

    return html`
      <div class="wiki-page">
        ${orphan}
        <div class="wiki-page-toolbar">
          ${breadcrumbContent}
          <span class="wiki-editor-spacer"></span>
          ${this._modified ? html`<span class="wiki-page-date">${formatDate(this._modified)}</span>` : nothing}
          ${rightButtons}
        </div>
        ${metadataPanel}
        <div class="wiki-page-body" @click=${this._handleClick}>
          ${unsafeHTML(renderMarkdown(this._body))}
        </div>
      </div>
    `;
  }
}

customElements.define('wiki-page', WikiPage);
