/**
 * Wiki side panel — hosts wiki page viewer and page list browser.
 *
 * Opens when a [[wiki-link]] is clicked in chat. Designed as a foundation
 * for a future general-purpose artifacts/side panel.
 */

import { LitElement, html, nothing } from 'lit';
import './wiki-page.js';

/**
 * @param {number} ts — Unix timestamp (seconds)
 * @returns {string}
 */
function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export class WikiPanel extends LitElement {
  static properties = {
    open: { type: Boolean, reflect: true },
    page: { type: String },
    _showList: { state: true },
    _pages: { state: true },
    _listLoading: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.open = false;
    this.page = '';
    this._showList = false;
    /** @type {Array<{title: string, modified: number}>} */
    this._pages = [];
    this._listLoading = false;

    this._onKeyDown = this._onKeyDown.bind(this);
  }

  connectedCallback() {
    super.connectedCallback();
    document.addEventListener('keydown', this._onKeyDown);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener('keydown', this._onKeyDown);
  }

  /** @param {KeyboardEvent} e */
  _onKeyDown(e) {
    if (e.key === 'Escape' && this.open) {
      this.close();
    }
  }

  /** Open the panel to a specific page. */
  openPage(page) {
    this.page = page;
    this._showList = false;
    this.open = true;
  }

  /** Open the panel to the page list. */
  openList() {
    this._showList = true;
    this.open = true;
    this._fetchList();
  }

  close() {
    this.open = false;
  }

  async _fetchList() {
    this._listLoading = true;
    try {
      const res = await fetch('/api/wiki');
      if (res.ok) {
        this._pages = await res.json();
      }
    } catch (e) {
      // Silently fail — list just stays empty
    } finally {
      this._listLoading = false;
    }
  }

  /** @param {string} page */
  _selectPage(page) {
    this.page = page;
    this._showList = false;
  }

  /** @param {CustomEvent} e */
  _onWikiNavigate(e) {
    const page = e.detail?.page;
    if (page) {
      this.page = page;
      this._showList = false;
    }
  }

  _toggleList() {
    this._showList = !this._showList;
    if (this._showList) {
      this._fetchList();
    }
  }

  _renderList() {
    if (this._listLoading) {
      return html`<div class="wiki-list-loading">Loading pages...</div>`;
    }
    if (!this._pages.length) {
      return html`<div class="wiki-list-empty">No wiki pages found.</div>`;
    }
    return html`
      <div class="wiki-page-list">
        ${this._pages.map(p => html`
          <button class="wiki-page-list-item" @click=${() => this._selectPage(p.title)}>
            <span class="wiki-page-list-title">${p.title}</span>
            <span class="wiki-page-list-date">${formatDate(p.modified)}</span>
          </button>
        `)}
      </div>
    `;
  }

  render() {
    if (!this.open) return nothing;

    return html`
      <div class="wiki-panel">
        <div class="wiki-panel-header">
          <button class="wiki-panel-list-btn" @click=${this._toggleList}
            title=${this._showList ? 'Back to page' : 'Browse all pages'}>
            ${this._showList ? '\u2190' : '\u2630'}
          </button>
          <h3 class="wiki-panel-title">
            ${this._showList ? 'Wiki Pages' : (this.page || 'Wiki')}
          </h3>
          <button class="wiki-panel-close" @click=${this.close} title="Close">&times;</button>
        </div>
        <div class="wiki-panel-body">
          ${this._showList
            ? this._renderList()
            : html`<wiki-page .page=${this.page} @wiki-navigate=${this._onWikiNavigate}></wiki-page>`
          }
        </div>
      </div>
    `;
  }
}

customElements.define('wiki-panel', WikiPanel);
