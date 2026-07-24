/**
 * Vault page metadata panel — renders frontmatter as structured chrome
 * instead of letting the YAML reach the markdown renderer.
 *
 * Presentational: performs no I/O. The host (wiki-page) owns every PUT so it
 * can serialize metadata writes against wiki-editor's body autosave and keep
 * its mtime in sync.
 *
 * Read-only mode shows a compact strip that expands to full detail. Edit
 * controls arrive with the metadata-change / metadata-raw-save events.
 */

import { LitElement, html, nothing } from 'lit';

const EXPANDED_KEY = 'wiki-metadata-expanded';

/** Fields with purpose-built controls; everything else lives in raw YAML. */
const KNOWN_FIELDS = ['summary', 'importance', 'tags', 'keywords'];

/** Chips shown before the "+N" overflow in the collapsed strip. */
const CHIP_PREVIEW_LIMIT = 3;

export class WikiMetadata extends LitElement {
  static properties = {
    frontmatter: { attribute: false },
    frontmatterRaw: { attribute: false },
    frontmatterError: { attribute: false },
    readonly: { type: Boolean },
    _expanded: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {Record<string, any>} */ this.frontmatter = {};
    /** @type {string} */ this.frontmatterRaw = '';
    /** @type {string} */ this.frontmatterError = '';
    this.readonly = false;
    this._expanded = localStorage.getItem(EXPANDED_KEY) === 'true';
  }

  #toggle() {
    this._expanded = !this._expanded;
    localStorage.setItem(EXPANDED_KEY, String(this._expanded));
  }

  /**
   * @param {string} field
   * @returns {string[]}
   */
  #list(field) {
    const value = this.frontmatter?.[field];
    if (Array.isArray(value)) return value.map(v => String(v));
    if (typeof value === 'string' && value) return [value];
    return [];
  }

  /** Keys with no typed control — surfaced so they're never invisible. */
  #otherKeys() {
    return Object.keys(this.frontmatter || {})
      .filter(k => !KNOWN_FIELDS.includes(k))
      .sort();
  }

  #hasAnything() {
    return Boolean(this.frontmatterError)
      || Object.keys(this.frontmatter || {}).length > 0;
  }

  /** @param {string[]} tags */
  #renderChips(tags) {
    return tags.map(tag => html`<span class="wiki-md-chip">${tag}</span>`);
  }

  #renderStrip() {
    const summary = this.frontmatter?.summary
      ? String(this.frontmatter.summary)
      : '';
    const importance = this.frontmatter?.importance;
    const tags = this.#list('tags');
    const shown = tags.slice(0, CHIP_PREVIEW_LIMIT);
    const overflow = tags.length - shown.length;
    return html`
      ${summary ? html`<span class="wiki-md-summary-line">${summary}</span>` : nothing}
      <span class="wiki-md-facts">
        ${importance == null ? nothing : html`<span class="wiki-md-importance">${importance}</span>`}
        ${this.#renderChips(shown)}
        ${overflow > 0 ? html`<span class="wiki-md-overflow">+${overflow}</span>` : nothing}
      </span>
    `;
  }

  #renderDetail() {
    const others = this.#otherKeys();
    return html`
      <dl class="wiki-md-detail">
        ${this.frontmatter?.summary ? html`
          <dt>summary</dt><dd>${String(this.frontmatter.summary)}</dd>
        ` : nothing}
        ${this.frontmatter?.importance == null ? nothing : html`
          <dt>importance</dt><dd>${this.frontmatter.importance}</dd>
        `}
        ${this.#list('tags').length ? html`
          <dt>tags</dt><dd>${this.#renderChips(this.#list('tags'))}</dd>
        ` : nothing}
        ${this.#list('keywords').length ? html`
          <dt>keywords</dt><dd>${this.#renderChips(this.#list('keywords'))}</dd>
        ` : nothing}
        ${others.map(key => html`
          <dt>${key}</dt><dd>${JSON.stringify(this.frontmatter[key])}</dd>
        `)}
      </dl>
    `;
  }

  render() {
    if (!this.#hasAnything()) return nothing;

    const label = this._expanded ? 'Collapse metadata' : 'Expand metadata';
    return html`
      <div class="wiki-metadata ${this._expanded ? 'expanded' : ''}">
        <button
          type="button"
          class="wiki-md-toggle"
          aria-expanded=${this._expanded ? 'true' : 'false'}
          title=${label}
          aria-label=${label}
          @click=${() => this.#toggle()}
        >${this._expanded ? '▾' : '▸'}</button>
        <div class="wiki-md-content">
          ${this.frontmatterError
            ? html`<div class="wiki-md-error">Frontmatter is not valid YAML: ${this.frontmatterError}</div>`
            : nothing}
          ${this._expanded ? this.#renderDetail() : this.#renderStrip()}
        </div>
      </div>
    `;
  }
}

customElements.define('wiki-metadata', WikiMetadata);
