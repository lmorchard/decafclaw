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
      const res = await fetch('/api/vault/' + encodeURIComponent(this.page));
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

    const newTabUrl = '/vault/' + encodeURIComponent(this.page);
    const modeIcon = this._editing ? '\u{1f441}' : '\u{270e}';
    const modeTitle = this._editing ? 'Switch to view mode' : 'Switch to edit mode';
    const closeBtn = this.standalone ? nothing : html`
      <button class="wiki-close-btn" @click=${() => this._close()} title="Close wiki pane">&times;</button>
    `;
    const rightButtons = html`
      <button class="wiki-edit-btn" @click=${() => this._toggleMode()} title=${modeTitle}>${modeIcon}</button>
      <a href=${newTabUrl} target="_blank" rel="noopener" class="wiki-open-tab" title="Open in new tab">&#8599;</a>
      ${closeBtn}
    `;

    if (this._editing) {
      return html`
        <div class="wiki-page">
          <wiki-editor
            page=${this.page}
            .content=${this._content}
            .modified=${this._modified}
            .toolbarExtra=${rightButtons}
            @saved=${this._onSaved}
          ></wiki-editor>
        </div>
      `;
    }

    return html`
      <div class="wiki-page">
        <div class="wiki-page-toolbar">
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
