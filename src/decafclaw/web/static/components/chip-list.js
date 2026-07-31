/**
 * Chip list — a set of short string values with add/remove controls.
 *
 * Presentational: performs no I/O and never mutates `items`. Emits
 * `chips-change` with the full next array; the host decides what that
 * means (vault treats an empty array as "remove the key", schedules
 * writes it through as an empty list).
 */

import { LitElement, html, nothing } from 'lit';

export class ChipList extends LitElement {
  static properties = {
    label: { type: String },
    items: { attribute: false },
    readonly: { type: Boolean },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {string} */ this.label = '';
    /** @type {string[]} */ this.items = [];
    this.readonly = false;
  }

  /** @param {string[]} next */
  #emit(next) {
    this.dispatchEvent(new CustomEvent('chips-change', {
      detail: { items: next },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {string} item */
  #remove(item) {
    this.#emit(this.items.filter(i => i !== item));
  }

  /** @param {KeyboardEvent} e */
  #onKey(e) {
    if (e.key !== 'Enter' && e.key !== ',') return;
    e.preventDefault();
    const input = /** @type {HTMLInputElement} */ (e.target);
    const value = input.value.trim().replace(/,$/, '');
    if (!value) return;
    if (this.items.includes(value)) { input.value = ''; return; }
    this.#emit([...this.items, value]);
    input.value = '';
  }

  render() {
    return html`
      ${this.items.map(item => html`
        <span class="dc-chip">
          ${item}
          ${this.readonly ? nothing : html`
            <button
              type="button"
              class="dc-chip-x"
              title="Remove ${item}"
              aria-label="Remove ${item}"
              @click=${() => this.#remove(item)}
            >&times;</button>
          `}
        </span>
      `)}
      ${this.readonly ? nothing : html`
        <input
          class="dc-chip-input"
          type="text"
          placeholder="add…"
          aria-label="Add ${this.label}"
          @keydown=${(/** @type {KeyboardEvent} */ e) => this.#onKey(e)}
        />
      `}
    `;
  }
}

customElements.define('chip-list', ChipList);
