import { LitElement, html, nothing } from 'lit';

/**
 * @typedef {{ tag: string, count: number, pages: string[] }} TagEntry
 */

export class TagsSidebar extends LitElement {
  static properties = {
    active: { type: Boolean },
    _tags: { type: Array, state: true },
    _loading: { type: Boolean, state: true },
    _selectedTag: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.active = false;
    /** @type {TagEntry[]} */
    this._tags = [];
    this._loading = false;
    /** @type {string|null} */
    this._selectedTag = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._onVaultChanged = () => {
      if (this.active) this.#fetchTags();
    };
    window.addEventListener('vault-changed', this._onVaultChanged);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('vault-changed', this._onVaultChanged);
  }

  /** @param {Map} changedProps */
  updated(changedProps) {
    // Re-fetch on every false→true transition of `active`.
    if (changedProps.has('active') && this.active && !changedProps.get('active')) {
      this.#fetchTags();
    }
  }

  async #fetchTags() {
    this._loading = true;
    try {
      const res = await fetch('/api/vault/tags');
      if (res.ok) {
        const data = await res.json();
        this._tags = data.tags || [];
      } else {
        this._tags = [];
      }
    } catch {
      this._tags = [];
    } finally {
      this._loading = false;
    }
  }

  /** @param {string} tag */
  #selectTag(tag) {
    this._selectedTag = tag;
  }

  #clearSelection() {
    this._selectedTag = null;
  }

  /** @param {string} page */
  #openPage(page) {
    this.dispatchEvent(new CustomEvent('wiki-open', {
      detail: { page },
      bubbles: true,
      composed: true,
    }));
  }

  #renderTagList() {
    if (!this._tags.length) {
      return html`<p style="padding: 1rem; color: var(--pico-muted-color);">${this._loading ? 'Loading tags...' : 'No tags found.'}</p>`;
    }
    return html`
      ${this._tags.map(t => html`
        <div class="conv-item wiki-item" @click=${() => this.#selectTag(t.tag)} title=${t.tag}>
          <span class="conv-title">${t.tag}</span>
          <span class="conv-type-badge">${t.count}</span>
        </div>
      `)}
    `;
  }

  #renderTagPages() {
    const entry = this._tags.find(t => t.tag === this._selectedTag);
    const pages = entry?.pages || [];
    return html`
      <div class="vault-breadcrumbs">
        <button type="button" class="vault-breadcrumb-segment" @click=${() => this.#clearSelection()}>tags</button>
        <span class="vault-breadcrumb-sep">/</span>
        <span class="vault-breadcrumb-segment active">${this._selectedTag}</span>
      </div>
      ${pages.length
        ? pages.map(p => html`
            <div class="conv-item wiki-item" @click=${() => this.#openPage(p)} title=${p}>
              <span class="conv-title">${p}</span>
            </div>
          `)
        : html`<p style="padding: 1rem; color: var(--pico-muted-color);">No pages.</p>`
      }
    `;
  }

  render() {
    return html`
      <div class="conv-list">
        ${this._selectedTag ? this.#renderTagPages() : this.#renderTagList()}
      </div>
    `;
  }
}

customElements.define('tags-sidebar', TagsSidebar);
