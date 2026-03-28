/**
 * Wiki page viewer — fetches and renders a wiki page as markdown.
 *
 * Used in both the side panel and standalone wiki page.
 * Dispatches 'wiki-navigate' events when [[wiki-links]] are clicked (panel mode).
 * In standalone mode, wiki links are regular <a> tags to /wiki/{page}.
 */

import { LitElement, html, nothing } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { renderMarkdown } from '../lib/markdown.js';

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
  }

  /** @param {Map<string, any>} changed */
  willUpdate(changed) {
    if (changed.has('page') && this.page) {
      this._fetchPage();
    }
  }

  async _fetchPage() {
    this._loading = true;
    this._error = '';
    this._content = '';
    try {
      const res = await fetch('/api/wiki/' + encodeURIComponent(this.page));
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

  /** @param {Event} e */
  _handleClick(e) {
    const link = /** @type {HTMLElement} */ (e.target).closest('a.wiki-link');
    if (!link) return;
    const page = link.getAttribute('data-wiki-page');
    if (!page) return;

    if (this.standalone) {
      // Standalone mode: navigate normally via <a> href
      return;
    }

    // Panel mode: prevent default, dispatch navigate event
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

    const newTabUrl = '/wiki/' + encodeURIComponent(this.page);

    return html`
      <div class="wiki-page" @click=${this._handleClick}>
        <div class="wiki-page-header">
          <h2 class="wiki-page-title">${this._title}</h2>
          <div class="wiki-page-meta">
            ${this._modified ? html`<span class="wiki-page-date">${formatDate(this._modified)}</span>` : nothing}
            <a href=${newTabUrl} target="_blank" rel="noopener" class="wiki-open-tab" title="Open in new tab">&#8599;</a>
          </div>
        </div>
        <div class="wiki-page-body">
          ${unsafeHTML(renderMarkdown(this._content))}
        </div>
      </div>
    `;
  }
}

customElements.define('wiki-page', WikiPage);
