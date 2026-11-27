import { LitElement, html } from 'lit';

import './picker-popup.js';

/** @typedef {{ name: string, models: string[] }} ModelGroup */
/** @typedef {import('./picker-popup.js').PickerItem} PickerItem */
/** @typedef {import('./picker-popup.js').PickerGroup} PickerGroup */

/**
 * Conversation model picker (#1441).
 *
 * This is the *conversation* consumer of the generic `<picker-popup>` (#2011).
 * It owns everything specific to a conversation's model: provider grouping,
 * the active→default fallback, the `default` badge, the unconfigured-model
 * warning, and closing the panel when the conversation changes. The popup owns
 * the trigger, panel placement, search, and keyboard navigation.
 *
 * Every visible value is read from properties during render, so `activeModel`,
 * `defaultModel`, and `groups` arriving from the store keep the trigger, list,
 * and highlight in sync across session switches (#1440).
 *
 * Dispatches one bubbling `set-model` with `{ model }` per real change. Picking
 * the model already in effect only closes the panel.
 */
export class ModelPicker extends LitElement {
  static properties = {
    groups: { type: Array },
    activeModel: { type: String, attribute: 'active-model' },
    defaultModel: { type: String, attribute: 'default-model' },
    disabled: { type: Boolean, reflect: true },
    convId: { type: String, attribute: 'conv-id' },
    inline: { type: Boolean, reflect: true },
  };

  createRenderRoot() {
    return this;
  }

  constructor() {
    super();
    /** @type {ModelGroup[]} */
    this.groups = [];
    this.activeModel = '';
    this.defaultModel = '';
    this.disabled = false;
    this.convId = '';
    this.inline = false;
  }

  /** The model the trigger and selected marker represent. @returns {string} */
  #target() {
    return this.activeModel || this.defaultModel || '';
  }

  /** Flat configured model list from the current groups. @returns {string[]} */
  #configuredModels() {
    return (this.groups || []).flatMap((group) => group.models || []);
  }

  /** @param {Map<string, unknown>} changed */
  willUpdate(changed) {
    if (changed.has('convId') && changed.get('convId') !== this.convId) {
      // Session switch: the panel belongs to the old conversation. The popup
      // has no idea what a conversation is, so this stays here.
      /** @type {any} */ (this.querySelector('picker-popup'))?.close();
    }
  }

  /** @param {string} model @returns {PickerItem} */
  #item(model) {
    const target = this.#target();
    const configured = this.#configuredModels();
    const selected = model === target;
    return {
      value: model,
      label: model,
      selected,
      badge: model === this.defaultModel ? 'default' : '',
      warn: selected && !configured.includes(model) ? '⚠ not configured' : '',
    };
  }

  /**
   * Provider groups for the popup, with a synthetic `Current` group in front
   * when the active model is not configured so an unconfigured model stays
   * visible and selectable as the current value.
   * @returns {PickerGroup[]}
   */
  #items() {
    const target = this.#target();
    /** @type {PickerGroup[]} */
    let groups = (this.groups || []).map((group) => ({
      name: group.name,
      items: (group.models || []).map((model) => this.#item(model)),
    }));
    if (target && !this.#configuredModels().includes(target)) {
      groups = [{ name: 'Current', items: [this.#item(target)] }, ...groups];
    }
    return groups;
  }

  /** @param {CustomEvent<{ value: string }>} e */
  #onPick(e) {
    const model = e.detail.value;
    // `""` is not a conversation concept: there is no "inherit" row here, and
    // an empty pick would mean "no model", which the store cannot express.
    if (!model || model === this.#target()) return;
    this.dispatchEvent(new CustomEvent('set-model', {
      detail: { model },
      bubbles: true,
      composed: true,
    }));
  }

  render() {
    return html`
      <picker-popup
        .groups=${this.#items()}
        .label=${'Model'}
        .placeholder=${'Select model'}
        ?disabled=${this.disabled}
        ?inline=${this.inline}
        @pick=${this.#onPick}
      ></picker-popup>
    `;
  }
}

customElements.define('model-picker', ModelPicker);
