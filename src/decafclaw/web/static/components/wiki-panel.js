/**
 * Wiki side panel — hosts wiki page viewer.
 *
 * Opens when a [[wiki-link]] is clicked in chat or a page is selected
 * from the sidebar wiki tab. Designed as a foundation for a future
 * general-purpose artifacts/side panel.
 */

import { LitElement, html, nothing } from 'lit';
import './wiki-page.js';

export class WikiPanel extends LitElement {
  static properties = {
    open: { type: Boolean, reflect: true },
    page: { type: String },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.open = false;
    this.page = '';
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
    this.open = true;
  }

  close() {
    this.open = false;
  }

  /** @param {CustomEvent} e */
  _onWikiNavigate(e) {
    const page = e.detail?.page;
    if (page) {
      this.page = page;
    }
  }

  render() {
    if (!this.open) return nothing;

    return html`
      <div class="wiki-panel">
        <div class="wiki-panel-header">
          <h3 class="wiki-panel-title">${this.page || 'Wiki'}</h3>
          <button class="wiki-panel-close" @click=${this.close} title="Close">&times;</button>
        </div>
        <div class="wiki-panel-body">
          <wiki-page .page=${this.page} @wiki-navigate=${this._onWikiNavigate}></wiki-page>
        </div>
      </div>
    `;
  }
}

customElements.define('wiki-panel', WikiPanel);
