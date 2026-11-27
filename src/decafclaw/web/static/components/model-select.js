import { LitElement, html } from 'lit';

import './picker-popup.js';

/** @typedef {import('./picker-popup.js').PickerItem} PickerItem */

/**
 * Settings model-pointer selector (#2011, extracted from the #1948 reuse).
 *
 * The settings consumer of `<picker-popup>`. It owns the semantics that make a
 * settings pointer different from a conversation's model:
 *
 * - the options are a flat list (`catalog.picker_models`), not provider groups;
 * - the pointer is a stored value, not a live conversation's model;
 * - `""` is a real, selectable choice meaning "this field inherits", labeled
 *   per field by `emptyLabel`.
 *
 * The popup supplies the trigger, panel placement, search, and keyboard
 * navigation. Nothing here knows about conversations, and it has no path to
 * `store.setModel` — the whole point of the split.
 *
 * Dispatches one bubbling `model-select` with `{ value }` per real change.
 * Picking the value already stored only closes the panel.
 */
export class ModelSelect extends LitElement {
  static properties = {
    options: { type: Array },
    value: { type: String },
    emptyLabel: { type: String, attribute: 'empty-label' },
    label: { type: String },
    disabled: { type: Boolean, reflect: true },
    inline: { type: Boolean, reflect: true },
  };

  createRenderRoot() {
    return this;
  }

  constructor() {
    super();
    /** @type {string[]} */
    this.options = [];
    this.value = '';
    this.emptyLabel = '';
    this.label = '';
    this.disabled = false;
    this.inline = false;
  }

  /** @param {string} model @returns {PickerItem} */
  #item(model) {
    return { value: model, label: model, selected: model === (this.value || '') };
  }

  /**
   * The inherit row, pinned by the popup while the search is empty. Kept out
   * of `items` so typing searches real models only.
   * @returns {PickerItem} */
  #emptyItem() {
    return {
      value: '',
      label: this.emptyLabel || '(default)',
      selected: !this.value,
    };
  }

  /** @param {CustomEvent<{ value: string }>} e */
  #onPick(e) {
    const value = e.detail.value;
    if (value === (this.value || '')) return;
    this.dispatchEvent(new CustomEvent('model-select', {
      detail: { value },
      bubbles: true,
      composed: true,
    }));
  }

  render() {
    return html`
      <picker-popup
        .items=${(this.options || []).map((model) => this.#item(model))}
        .emptyItem=${this.#emptyItem()}
        .label=${this.label || 'Model'}
        .placeholder=${this.emptyLabel || '(default)'}
        ?disabled=${this.disabled}
        ?inline=${this.inline}
        @pick=${this.#onPick}
      ></picker-popup>
    `;
  }
}

customElements.define('model-select', ModelSelect);
