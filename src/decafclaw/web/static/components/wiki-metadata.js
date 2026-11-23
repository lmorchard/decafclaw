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
 *
 * Expansion is host-owned: the disclosure toggle lives in the page toolbar
 * (so it shares the breadcrumb row instead of claiming its own line), and the
 * host passes `expanded` down.
 */

import { LitElement, html, nothing } from 'lit';
import './chip-list.js';

/** Fields with purpose-built controls; everything else lives in raw YAML. */
const KNOWN_FIELDS = ['summary', 'importance', 'tags', 'keywords'];

/**
 * Whether a page has metadata worth showing. Edit mode always offers
 * controls; read-only mode needs frontmatter or a frontmatter parse error.
 * The host uses this to decide whether to render the toolbar disclosure.
 *
 * @param {Record<string, unknown>|null|undefined} frontmatter
 * @param {string} frontmatterError
 * @param {boolean} readonly
 * @returns {boolean}
 */
export function hasMetadata(frontmatter, frontmatterError, readonly) {
  if (!readonly) return true;
  return Boolean(frontmatterError) || Object.keys(frontmatter || {}).length > 0;
}

/** Chips shown before the "+N" overflow in the collapsed strip. */
const CHIP_PREVIEW_LIMIT = 3;

export class WikiMetadata extends LitElement {
  static properties = {
    frontmatter: { attribute: false },
    frontmatterRaw: { attribute: false },
    frontmatterError: { attribute: false },
    metaError: { attribute: false },
    readonly: { type: Boolean },
    /** Host-owned disclosure state; the toggle itself renders in the toolbar. */
    expanded: { attribute: false },
    _rawOpen: { state: true },
    _rawText: { state: true },
    _rawError: { state: true },
    _rawDirty: { state: true },
    _summaryDirty: { state: true },
    _importanceDirty: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {import('../lib/api-client/index.js').VaultPageResponse['frontmatter']} */ this.frontmatter = {};
    /** @type {string} */ this.frontmatterRaw = '';
    /** @type {string} */ this.frontmatterError = '';
    /**
     * Set by the host when a metadata write (typed patch or raw replace)
     * fails. `status: 'conflict'` (409) offers Reload/Overwrite; any other
     * status offers Retry. Cleared by the host on the next successful write.
     * @type {{status: 'conflict'|'error', message: string} | null}
     */
    this.metaError = null;
    this.readonly = false;
    this.expanded = false;
    this._rawOpen = false;
    this._rawText = '';
    this._rawError = '';
    this._rawDirty = false;
    // Typed controls commit on `change` (blur). Until then their DOM value is
    // ahead of `frontmatter`, so a host refresh must not rebind `.value`.
    this._summaryDirty = false;
    this._importanceDirty = false;
  }

  /** @param {Map<string, unknown>} changed */
  willUpdate(changed) {
    // The server's frontmatter replaced what these controls were bound to, so
    // any uncommitted text is gone with it. This also clears flags left over
    // when the host navigates to another page.
    if (changed.has('frontmatter')) {
      this._summaryDirty = false;
      this._importanceDirty = false;
    }
    // Reseed the raw editor from the server's bytes whenever the page's
    // frontmatter changes underneath us, unless the user is mid-edit or
    // typed while a save was in flight.
    if (changed.has('frontmatterRaw') && !this._rawOpen && !this._rawDirty) {
      this._rawText = this.frontmatterRaw;
    }
  }

  /**
   * @param {string} field
   * @param {unknown} value — null removes the key
   */
  #emitChange(field, value) {
    this.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { [field]: value } },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {string} field @param {string[]} tags */
  #emitList(field, tags) {
    this.#emitChange(field, tags.length ? tags : null);
  }

  #toggleRaw() {
    this._rawOpen = !this._rawOpen;
    this._rawError = '';
    if (this._rawOpen) {
      this._rawText = this.frontmatterRaw;
      this._rawDirty = false;  // Fresh copy from server
    }
  }

  #saveRaw() {
    this._rawError = '';
    this._rawDirty = false;  // About to save; reset so we can detect typing during flight
    this.dispatchEvent(new CustomEvent('metadata-raw-save', {
      detail: { raw: this._rawText },
      bubbles: true,
      composed: true,
    }));
  }

  /** Called by the host when a raw save is rejected. @param {string} message */
  setRawError(message) {
    this._rawError = message;
  }

  /** Called by the host after a raw save succeeds. */
  closeRaw() {
    this._rawOpen = false;
    this._rawError = '';
  }

  /**
   * Public: true if the metadata panel holds local edits the server has not
   * seen: an open raw YAML draft, or a typed field whose `change` (blur) has
   * not fired yet. A closed raw editor (Cancel discards) is never pending, so
   * a canceled draft cannot block later refreshes. Hosts use this to avoid
   * clobbering local edits on an external refresh.
   */
  hasPendingChanges() {
    return (this._rawOpen && this._rawDirty)
      || this._summaryDirty
      || this._importanceDirty;
  }

  /** @param {string} type */
  #emitMetaAction(type) {
    this.dispatchEvent(new CustomEvent(type, { bubbles: true, composed: true }));
  }

  /** Conflict/error banner for a failed metadata write (typed patch or raw replace). */
  #renderMetaError() {
    if (!this.metaError) return nothing;
    const isConflict = this.metaError.status === 'conflict';
    return html`
      <div class="wiki-md-conflict">
        <span>${this.metaError.message}</span>
        ${isConflict ? html`
          <button
            type="button"
            class="wiki-md-conflict-btn"
            aria-label="Reload page, discarding pending metadata edits"
            @click=${() => this.#emitMetaAction('metadata-reload')}
          >Reload</button>
          <button
            type="button"
            class="wiki-md-conflict-btn"
            aria-label="Overwrite server metadata with local changes"
            @click=${() => this.#emitMetaAction('metadata-overwrite')}
          >Overwrite</button>
        ` : html`
          <button
            type="button"
            class="wiki-md-conflict-btn"
            aria-label="Retry metadata save"
            @click=${() => this.#emitMetaAction('metadata-retry')}
          >Retry</button>
        `}
      </div>
    `;
  }

  /** @param {string} field @param {string} label */
  #renderChipInput(field, label) {
    return html`
      <dt>${label}</dt>
      <dd>
        <chip-list
          .label=${label}
          .items=${this.#list(field)}
          @chips-change=${(/** @type {CustomEvent<{items: string[]}>} */ e) => this.#emitList(field, e.detail.items)}
        ></chip-list>
      </dd>
    `;
  }

  #renderEditControls() {
    const importance = this.frontmatter?.importance;
    const disabled = Boolean(this.frontmatterError);
    return html`
      <dl class="wiki-md-detail">
        <dt>summary</dt>
        <dd>
          <textarea
            class="wiki-md-summary-input"
            rows="2"
            aria-label="Summary"
            ?disabled=${disabled}
            .value=${this.frontmatter?.summary ? String(this.frontmatter.summary) : ''}
            @input=${() => { this._summaryDirty = true; }}
            @change=${(/** @type {Event} */ e) => {
              const value = /** @type {HTMLTextAreaElement} */ (e.target).value.trim();
              this._summaryDirty = false;
              this.#emitChange('summary', value || null);
            }}
          ></textarea>
        </dd>
        <dt>importance</dt>
        <dd>
          <input
            type="range"
            min="0" max="1" step="0.05"
            aria-label="Importance"
            ?disabled=${disabled}
            .value=${importance == null ? '0.5' : String(importance)}
            @input=${() => { this._importanceDirty = true; }}
            @change=${(/** @type {Event} */ e) => {
              const value = Number(/** @type {HTMLInputElement} */ (e.target).value);
              this._importanceDirty = false;
              this.#emitChange('importance', value);
            }}
          />
          <span class="wiki-md-importance">${importance == null ? '—' : importance}</span>
        </dd>
        ${disabled ? nothing : this.#renderChipInput('tags', 'tags')}
        ${disabled ? nothing : this.#renderChipInput('keywords', 'keywords')}
      </dl>
      <div class="wiki-md-raw">
        <button type="button" class="wiki-md-raw-toggle" @click=${() => this.#toggleRaw()}>
          ${this._rawOpen ? '▾' : '▸'} edit raw YAML
        </button>
        ${this._rawOpen ? html`
          <textarea
            class="wiki-md-raw-input"
            rows="8"
            aria-label="Raw frontmatter YAML"
            .value=${this._rawText}
            @input=${(/** @type {Event} */ e) => {
              this._rawText = /** @type {HTMLTextAreaElement} */ (e.target).value;
              this._rawDirty = true;  // User typed; don't reseed from server response
            }}
          ></textarea>
          <div class="wiki-md-raw-actions">
            <button type="button" @click=${() => this.#saveRaw()}>Save YAML</button>
            <button type="button" class="secondary" @click=${() => this.#toggleRaw()}>Cancel</button>
            ${this._rawError ? html`<span class="wiki-md-error">${this._rawError}</span>` : nothing}
          </div>
        ` : nothing}
      </div>
    `;
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
    return hasMetadata(this.frontmatter, this.frontmatterError, this.readonly);
  }

  /** @param {string[]} tags */
  #renderChips(tags) {
    return tags.map(tag => html`<span class="dc-chip">${tag}</span>`);
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

    return html`
      <div class="wiki-metadata ${this.expanded ? 'expanded' : ''}">
        <div class="wiki-md-content">
          ${this.#renderMetaError()}
          ${this.frontmatterError
            ? html`<div class="wiki-md-error">Frontmatter is not valid YAML: ${this.frontmatterError}</div>`
            : nothing}
          ${this.expanded
            ? (this.readonly ? this.#renderDetail() : this.#renderEditControls())
            : this.#renderStrip()}
        </div>
      </div>
    `;
  }
}

customElements.define('wiki-metadata', WikiMetadata);
