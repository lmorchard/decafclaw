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
import './wiki-editor.js';

const EDIT_MODE_KEY = 'wiki-edit-mode';

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
    _content: { state: true },
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
    /** @type {string} */ this._content = '';
    /** @type {string} */ this._title = '';
    /** @type {number} */ this._modified = 0;
    this._loading = false;
    /** @type {string} */ this._error = '';
    // Default to edit mode, persisted in localStorage
    this._editing = localStorage.getItem(EDIT_MODE_KEY) !== 'false';
    this._renaming = false;
    /** @type {string} */ this._renameValue = '';
    /** @type {string} */ this._renameError = '';
  }

  /** @param {Map<string, any>} changed */
  willUpdate(changed) {
    if (changed.has('page') && this.page) {
      // If editing, flush save before switching pages
      if (this._editing) {
        /** @type {import('./wiki-editor.js').WikiEditor|null} */
        const editor = this.querySelector('wiki-editor');
        if (editor) editor.flushSave();
      }
      this._fetchPage();
    }
  }

  async _fetchPage() {
    this._loading = true;
    this._error = '';
    this._content = '';
    try {
      const res = await fetch('/api/vault/' + encodePagePath(this.page));
      if (!res.ok) {
        this._error = res.status === 404 ? `Page "${this.page}" not found.` : `Error loading page (${res.status}).`;
        return;
      }
      const data = await res.json();
      this._title = data.title;
      this._content = data.content;
      this._modified = data.modified;
    } catch (e) {
      this._error = 'Failed to load page.';
    } finally {
      this._loading = false;
    }
  }

  async _toggleMode() {
    if (this._editing) {
      // Flush editor save before switching to view mode
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
      window.dispatchEvent(new CustomEvent('vault-page-deleted', {
        detail: { page: this.page },
      }));
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

  render() {
    if (this._loading) {
      return html`<div class="wiki-page-loading">Loading...</div>`;
    }
    if (this._error) {
      return html`<div class="wiki-page-error">${this._error}</div>`;
    }
    if (!this._content) {
      return nothing;
    }

    const newTabUrl = '/vault/' + encodePagePath(this.page);
    const modeIcon = this._editing ? '\u{1f441}' : '\u{270e}';
    const modeTitle = this._editing ? 'Switch to view mode' : 'Switch to edit mode';
    const closeBtn = this.standalone ? nothing : html`
      <button class="wiki-close-btn" @click=${() => this._close()} title="Close wiki pane">&times;</button>
    `;
    const rightButtons = html`
      <button class="wiki-edit-btn" @click=${() => this._toggleMode()} title=${modeTitle}>${modeIcon}</button>
      <button class="wiki-delete-btn" @click=${() => this.#deletePage()} title="Delete page" aria-label="Delete page">\u{1F5D1}</button>
      <a href=${newTabUrl} target="_blank" rel="noopener" class="wiki-open-tab" title="Open in new tab">&#8599;</a>
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
          <button class="wiki-rename-btn" @click=${() => this.#startRename()} title="Rename / move page" aria-label="Rename / move page">\u{270e}</button>
        </span>
      `;

    if (this._editing) {
      return html`
        <div class="wiki-page">
          <wiki-editor
            page=${this.page}
            .content=${this._content}
            .modified=${this._modified}
            .toolbarLeft=${breadcrumbContent}
            .toolbarExtra=${rightButtons}
            @saved=${this._onSaved}
          ></wiki-editor>
        </div>
      `;
    }

    return html`
      <div class="wiki-page">
        <div class="wiki-page-toolbar">
          ${breadcrumbContent}
          <span class="wiki-editor-spacer"></span>
          ${this._modified ? html`<span class="wiki-page-date">${formatDate(this._modified)}</span>` : nothing}
          ${rightButtons}
        </div>
        <div class="wiki-page-body" @click=${this._handleClick}>
          ${unsafeHTML(renderMarkdown(this._content))}
        </div>
      </div>
    `;
  }
}

customElements.define('wiki-page', WikiPage);
