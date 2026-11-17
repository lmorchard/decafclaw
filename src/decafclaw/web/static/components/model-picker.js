import { LitElement, html, nothing } from 'lit';

/** @typedef {{ name: string, models: string[] }} ModelGroup */

/**
 * Shared conversation model picker (#1441).
 *
 * Replaces the three native model `<select>`s with one searchable popup.
 * Every visible value is read from properties during render: the panel,
 * trigger, and highlighted row follow `activeModel`, `defaultModel`, and
 * `groups` as the store re-sends them, which preserves the #1440
 * session-switch sync behavior.
 */
export class ModelPicker extends LitElement {
  static properties = {
    groups: { type: Array },
    activeModel: { type: String, attribute: 'active-model' },
    defaultModel: { type: String, attribute: 'default-model' },
    disabled: { type: Boolean, reflect: true },
    convId: { type: String, attribute: 'conv-id' },
    inline: { type: Boolean, reflect: true },
    _open: { type: Boolean, state: true },
    _query: { type: String, state: true },
    _highlight: { type: Number, state: true },
    _panelStyle: { type: String, state: true },
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
    this._open = false;
    this._query = '';
    this._highlight = 0;
    this._panelStyle = '';
  }

  connectedCallback() {
    super.connectedCallback();
    document.addEventListener('click', this.#onDocClick);
    window.addEventListener('resize', this.#onViewportChange);
    window.addEventListener('scroll', this.#onViewportChange, true);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener('click', this.#onDocClick);
    window.removeEventListener('resize', this.#onViewportChange);
    window.removeEventListener('scroll', this.#onViewportChange, true);
  }

  #onDocClick = (/** @type {MouseEvent} */ e) => {
    if (!this._open) return;
    if (e.target instanceof Node && this.contains(e.target)) return;
    this.#close(false);
  };

  #onViewportChange = () => {
    if (this._open && !this.inline) this.#positionPanel();
  };

  /** @param {Map<string, unknown>} changed */
  willUpdate(changed) {
    if (changed.has('convId') && changed.get('convId') !== this.convId && this._open) {
      // Session switch: the panel belongs to the old conversation.
      this._open = false;
      this._query = '';
    }
  }

  /** @param {Map<string, unknown>} changed */
  updated(changed) {
    if (changed.has('_open') && this._open && !changed.get('_open')) {
      if (!this.inline) this.#positionPanel();
      const search = /** @type {HTMLInputElement | null} */ (this.querySelector('.model-picker-search'));
      search?.focus();
    }
    if (changed.has('_highlight')) {
      const row = /** @type {HTMLElement | null} */ (this.querySelector('.model-picker-option.highlighted'));
      row?.scrollIntoView?.({ block: 'nearest' });
    }
  }

  /** The model the trigger and selected marker represent. @returns {string} */
  #target() {
    return this.activeModel || this.defaultModel || '';
  }

  /** Flat configured model list from the current groups. @returns {string[]} */
  #configuredModels() {
    return (this.groups || []).flatMap((group) => group.models || []);
  }

  /**
   * Groups visible under the current search. Adds a synthetic "Current"
   * group when the active model is not configured, so an unconfigured
   * model stays visible and selectable as the current value.
   * @returns {ModelGroup[]}
   */
  #visibleGroups() {
    const target = this.#target();
    /** @type {ModelGroup[]} */
    let groups = this.groups && this.groups.length
      ? this.groups
      : [{ name: 'Models', models: [] }];
    if (target && !this.#configuredModels().includes(target)) {
      groups = [{ name: 'Current', models: [target] }, ...groups];
    }

    const query = (this._query || '').trim().toLowerCase();
    if (!query) return groups;
    return groups
      .map((group) => {
        const name = (group.name || '').toLowerCase();
        const models = name.includes(query)
          ? group.models
          : group.models.filter((model) => model.toLowerCase().includes(query));
        return { name: group.name, models };
      })
      .filter((group) => group.models.length > 0);
  }

  /** @returns {string[]} */
  #flatModels() {
    return this.#visibleGroups().flatMap((group) => group.models);
  }

  #highlightIndex() {
    const rows = this.#flatModels();
    if (rows.length === 0) return -1;
    return Math.min(Math.max(this._highlight, 0), rows.length - 1);
  }

  #openPanel() {
    this._query = '';
    this._panelStyle = '';
    const rows = this.#flatModels();
    const target = this.#target();
    this._highlight = Math.max(0, rows.indexOf(target));
    this.#positionPanel();
    this._open = true;
  }

  /** @param {boolean} returnFocus */
  #close(returnFocus) {
    this._open = false;
    this._query = '';
    if (returnFocus && !this.inline) {
      const trigger = /** @type {HTMLElement | null} */ (this.querySelector('.model-picker-trigger'));
      trigger?.focus();
    }
  }

  #positionPanel() {
    const trigger = /** @type {HTMLElement | null} */ (this.querySelector('.model-picker-trigger'));
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const viewportWidth = window.innerWidth || 1024;
    const viewportHeight = window.innerHeight || 768;
    const width = Math.min(352, viewportWidth - 16);
    const left = Math.min(Math.max(8, rect.left), Math.max(8, viewportWidth - width - 8));
    // The control strip sits above the composer near the bottom of the
    // viewport, so "below the trigger" often has no room. Flip the panel
    // above the trigger when that side has more space, and bound it so the
    // list scrolls inside the viewport.
    const spaceBelow = viewportHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const desired = Math.min(320, viewportHeight - 16);
    if (spaceBelow < 200 && spaceAbove > spaceBelow) {
      const maxHeight = Math.min(desired, spaceAbove);
      const bottom = viewportHeight - rect.top + 4;
      this._panelStyle = `--mp-left:${left}px;--mp-top:auto;--mp-bottom:${bottom}px;--mp-width:${width}px;--mp-max:${maxHeight}px;`;
    } else {
      const top = rect.bottom + 4;
      const maxHeight = Math.min(desired, Math.max(120, spaceBelow));
      this._panelStyle = `--mp-left:${left}px;--mp-top:${top}px;--mp-bottom:auto;--mp-width:${width}px;--mp-max:${maxHeight}px;`;
    }
  }

  /** @param {string} model */
  #choose(model) {
    const target = this.#target();
    this.#close(true);
    if (model && model !== target) {
      this.dispatchEvent(new CustomEvent('set-model', {
        detail: { model },
        bubbles: true,
        composed: true,
      }));
    }
  }

  /** @param {KeyboardEvent} e */
  #onSearchKeydown(e) {
    const rows = this.#flatModels();
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (rows.length) this._highlight = Math.min(this.#highlightIndex() + 1, rows.length - 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (rows.length) this._highlight = Math.max(this.#highlightIndex() - 1, 0);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const index = this.#highlightIndex();
      if (index >= 0) this.#choose(rows[index]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (this.inline) {
        this._query = '';
      } else {
        this.#close(true);
      }
    }
  }

  #renderSearch() {
    return html`
      <input
        type="search"
        class="model-picker-search"
        placeholder="Search models…"
        aria-label="Search models"
        .value=${this._query}
        ?disabled=${this.disabled}
        @input=${(/** @type {InputEvent} */ e) => {
          this._query = /** @type {HTMLInputElement} */ (e.target).value;
          this._highlight = 0;
        }}
        @keydown=${this.#onSearchKeydown}
      />
    `;
  }

  #renderList() {
    const target = this.#target();
    const groups = this.#visibleGroups();
    let index = 0;
    const highlight = this.#highlightIndex();
    return html`
      <div class="model-picker-list" role="listbox" aria-label="Models">
        ${groups.length === 0 ? html`
          <div class="model-picker-no-match">No models match “${(this._query || '').trim()}”.</div>
        ` : groups.map((group) => html`
          <div class="model-picker-group">
            <div class="model-picker-group-name">${group.name}</div>
            ${group.models.map((model) => {
              const rowIndex = index++;
              const isTarget = model === target;
              return html`
                <button
                  type="button"
                  role="option"
                  class="model-picker-option${isTarget ? ' selected' : ''}${rowIndex === highlight ? ' highlighted' : ''}"
                  aria-selected=${isTarget ? 'true' : 'false'}
                  data-model=${model}
                  ?disabled=${this.disabled}
                  @click=${() => this.#choose(model)}
                  @mousemove=${() => { this._highlight = rowIndex; }}
                >
                  <span class="model-picker-check" aria-hidden="true">${isTarget ? '✓' : ''}</span>
                  <span class="model-picker-model">${model}</span>
                  ${model === this.defaultModel ? html`<span class="model-picker-default">default</span>` : nothing}
                  ${isTarget && !this.#configuredModels().includes(model) ? html`
                    <span class="model-picker-warning">⚠ not configured</span>
                  ` : nothing}
                </button>
              `;
            })}
          </div>
        `)}
      </div>
    `;
  }

  render() {
    const target = this.#target();
    const configured = this.#configuredModels();
    const unconfigured = Boolean(target && !configured.includes(target));
    const isDefault = Boolean(target && target === this.defaultModel);

    if (this.inline) {
      return html`
        <div class="model-picker-inline">
          ${this.#renderSearch()}
          ${this.#renderList()}
        </div>
      `;
    }

    return html`
      <div class="model-picker-root">
        <button
          type="button"
          class="model-picker-trigger"
          aria-haspopup="listbox"
          aria-expanded=${this._open ? 'true' : 'false'}
          aria-label="Model"
          ?disabled=${this.disabled}
          @click=${(/** @type {MouseEvent} */ e) => {
            e.stopPropagation();
            if (this._open) this.#close(false);
            else this.#openPanel();
          }}
        >
          <span class="model-picker-value">${target || 'Select model'}</span>
          ${isDefault ? html`<span class="model-picker-default">default</span>` : nothing}
          ${unconfigured ? html`<span class="model-picker-warning" title="This model is not configured">⚠ not configured</span>` : nothing}
          <span class="model-picker-caret" aria-hidden="true">▾</span>
        </button>
        ${this._open ? html`
          <div
            class="model-picker-panel"
            style=${this._panelStyle}
            @keydown=${(/** @type {KeyboardEvent} */ e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                this.#close(true);
              }
            }}
          >
            ${this.#renderSearch()}
            ${this.#renderList()}
          </div>
        ` : nothing}
      </div>
    `;
  }
}

customElements.define('model-picker', ModelPicker);
