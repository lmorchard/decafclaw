import { LitElement, html, nothing } from 'lit';

/**
 * @typedef {{ value: string, label: string, selected?: boolean,
 *   badge?: string, warn?: string }} PickerItem
 * @typedef {{ name: string, items: PickerItem[] }} PickerGroup
 */

/**
 * Generic searchable popup selector (#2011).
 *
 * This element knows nothing about models, conversations, or settings. It
 * renders a trigger plus a viewport-anchored panel of rows and reports what
 * the user picked:
 *
 * - `items` — a flat `PickerItem[]`, or
 * - `groups` — `PickerGroup[]` when the caller wants provider-style headings
 *   (takes precedence over `items`), plus
 * - `emptyItem` — an optional row pinned above the list whenever the search
 *   box is empty. Its `value` is passed through like any other, so a caller
 *   can use it for "unset" without the primitive knowing what unset means.
 *
 * Per-item `selected` marks the current choice: the trigger shows that item's
 * label, `badge`, and `warn`, and the row gets a check. Everything visible is
 * read from properties during render, so a caller re-sending new props keeps
 * the trigger, list, and highlight in sync.
 *
 * Dispatches one bubbling, composed `pick` with `{ value, item }` per chosen
 * row — including the already-selected row and including `emptyItem`. Callers
 * that must ignore a no-op pick do so themselves.
 *
 * It performs no fetches and holds no caller domain state.
 */
export class PickerPopup extends LitElement {
  static properties = {
    items: { type: Array },
    groups: { type: Array },
    emptyItem: { type: Object },
    label: { type: String },
    placeholder: { type: String },
    disabled: { type: Boolean, reflect: true },
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
    /** @type {PickerItem[]} */
    this.items = [];
    /** @type {PickerGroup[]} */
    this.groups = [];
    /** @type {PickerItem | null} */
    this.emptyItem = null;
    this.label = '';
    this.placeholder = '';
    this.disabled = false;
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

  /** Close the panel without picking. Callers use this when their own state
   * invalidates the open panel (a conversation switch, a parent re-render). */
  close() {
    this.#close(false);
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
  updated(changed) {
    if (changed.has('_open') && this._open && !changed.get('_open')) {
      if (!this.inline) this.#positionPanel();
      const search = /** @type {HTMLInputElement | null} */ (this.querySelector('.picker-popup-search'));
      search?.focus();
    }
    if (changed.has('_highlight')) {
      const row = /** @type {HTMLElement | null} */ (this.querySelector('.picker-popup-option.highlighted'));
      row?.scrollIntoView?.({ block: 'nearest' });
    }
  }

  /** Unfiltered groups the caller supplied. @returns {PickerGroup[]} */
  #sourceGroups() {
    if (this.groups && this.groups.length) return this.groups;
    const items = this.items || [];
    return items.length ? [{ name: '', items }] : [];
  }

  /** Every item the caller supplied, ignoring search. @returns {PickerItem[]} */
  #allItems() {
    return this.#sourceGroups().flatMap((group) => group.items || []);
  }

  /** The item the trigger and check mark represent. @returns {PickerItem | null} */
  #selectedItem() {
    return this.#allItems().find((item) => item.selected) || null;
  }

  /**
   * Groups visible under the current search. Matches a group name (keeping all
   * its items) or an individual item's value/label.
   * @returns {PickerGroup[]}
   */
  #visibleGroups() {
    const query = (this._query || '').trim().toLowerCase();
    if (!query) return this.#sourceGroups();
    return this.#sourceGroups()
      .map((group) => {
        const name = (group.name || '').toLowerCase();
        const items = name.includes(query)
          ? group.items
          : group.items.filter((item) => (item.value || '').toLowerCase().includes(query)
            || (item.label || '').toLowerCase().includes(query));
        return { name: group.name, items };
      })
      .filter((group) => group.items.length > 0);
  }

  /** Whether the pinned empty row is showing: only while the query is empty. */
  #showEmpty() {
    return Boolean(this.emptyItem) && !(this._query || '').trim();
  }

  /**
   * Keyboard-navigable rows, in render order: the pinned empty row first while
   * the search is empty, then every visible item. Typing searches real items
   * only, so the empty row cannot be reached by filtering.
   * @returns {PickerItem[]}
   */
  #rows() {
    const items = this.#visibleGroups().flatMap((group) => group.items);
    return this.#showEmpty() && this.emptyItem ? [this.emptyItem, ...items] : items;
  }

  #highlightIndex() {
    const rows = this.#rows();
    if (rows.length === 0) return -1;
    return Math.min(Math.max(this._highlight, 0), rows.length - 1);
  }

  #openPanel() {
    this._query = '';
    this._panelStyle = '';
    const rows = this.#rows();
    const selected = this.#selectedItem();
    const index = selected ? rows.findIndex((row) => row.value === selected.value) : -1;
    this._highlight = Math.max(0, index);
    this.#positionPanel();
    this._open = true;
  }

  /** @param {boolean} returnFocus */
  #close(returnFocus) {
    this._open = false;
    this._query = '';
    if (returnFocus && !this.inline) {
      const trigger = /** @type {HTMLElement | null} */ (this.querySelector('.picker-popup-trigger'));
      trigger?.focus();
    }
  }

  #positionPanel() {
    const trigger = /** @type {HTMLElement | null} */ (this.querySelector('.picker-popup-trigger'));
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
      this._panelStyle = `--pp-left:${left}px;--pp-top:auto;--pp-bottom:${bottom}px;--pp-width:${width}px;--pp-max:${maxHeight}px;`;
    } else {
      const top = rect.bottom + 4;
      const maxHeight = Math.min(desired, Math.max(120, spaceBelow));
      this._panelStyle = `--pp-left:${left}px;--pp-top:${top}px;--pp-bottom:auto;--pp-width:${width}px;--pp-max:${maxHeight}px;`;
    }
  }

  /** @param {PickerItem} item */
  #pick(item) {
    this.#close(true);
    this.dispatchEvent(new CustomEvent('pick', {
      detail: { value: item.value, item },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {KeyboardEvent} e */
  #onSearchKeydown(e) {
    const rows = this.#rows();
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (rows.length) this._highlight = Math.min(this.#highlightIndex() + 1, rows.length - 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (rows.length) this._highlight = Math.max(this.#highlightIndex() - 1, 0);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const index = this.#highlightIndex();
      if (index >= 0) this.#pick(rows[index]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (this.inline) {
        this._query = '';
      } else {
        this.#close(true);
      }
    }
  }

  /**
   * One option row. `data-value` is the machine-readable identity so callers
   * and tests never have to parse the visible label.
   * @param {PickerItem} item
   * @param {number} index
   * @param {number} highlight
   * @param {boolean} isEmpty whether this is the caller's pinned empty row
   */
  #renderOption(item, index, highlight, isEmpty = false) {
    const selected = Boolean(item.selected);
    return html`
      <button
        type="button"
        role="option"
        class="picker-popup-option${selected ? ' selected' : ''}${index === highlight ? ' highlighted' : ''}${isEmpty ? ' picker-popup-option-empty' : ''}"
        aria-selected=${selected ? 'true' : 'false'}
        data-value=${item.value}
        ?disabled=${this.disabled}
        @click=${() => this.#pick(item)}
        @mousemove=${() => { this._highlight = index; }}
      >
        <span class="picker-popup-check" aria-hidden="true">${selected ? '✓' : ''}</span>
        <span class="picker-popup-label">${item.label}</span>
        ${item.badge ? html`<span class="picker-popup-badge">${item.badge}</span>` : nothing}
        ${item.warn ? html`<span class="picker-popup-warning">${item.warn}</span>` : nothing}
      </button>
    `;
  }

  #renderSearch() {
    return html`
      <input
        type="search"
        class="picker-popup-search"
        placeholder="Search…"
        aria-label="Search"
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
    const groups = this.#visibleGroups();
    const showEmpty = this.#showEmpty();
    const query = (this._query || '').trim();
    // With the empty row pinned, an empty `groups` is not "no match" — the
    // caller still has one selectable row. Only say "no matches" when the user
    // is actually filtering.
    const noRows = groups.length === 0 && !showEmpty;
    let index = showEmpty ? 1 : 0;
    const highlight = this.#highlightIndex();
    return html`
      <div class="picker-popup-list" role="listbox" aria-label=${this.label || 'Options'}>
        ${showEmpty && this.emptyItem ? this.#renderOption(this.emptyItem, 0, highlight, true) : nothing}
        ${noRows ? html`
          <div class="picker-popup-no-match">No matches for “${query}”.</div>
        ` : groups.map((group) => html`
          <div class="picker-popup-group">
            ${group.name ? html`
              <div class="picker-popup-group-name">${group.name}</div>
            ` : nothing}
            ${group.items.map((item) => this.#renderOption(item, index++, highlight))}
          </div>
        `)}
      </div>
    `;
  }

  render() {
    const selected = this.#selectedItem();

    if (this.inline) {
      return html`
        <div class="picker-popup-inline">
          ${this.#renderSearch()}
          ${this.#renderList()}
        </div>
      `;
    }

    return html`
      <div class="picker-popup-root">
        <button
          type="button"
          class="picker-popup-trigger"
          aria-haspopup="listbox"
          aria-expanded=${this._open ? 'true' : 'false'}
          aria-label=${this.label || 'Select'}
          ?disabled=${this.disabled}
          @click=${(/** @type {MouseEvent} */ e) => {
            e.stopPropagation();
            if (this._open) this.#close(false);
            else this.#openPanel();
          }}
        >
          <span class="picker-popup-value">${selected ? selected.label : (this.placeholder || 'Select…')}</span>
          ${selected?.badge ? html`<span class="picker-popup-badge">${selected.badge}</span>` : nothing}
          ${selected?.warn ? html`<span class="picker-popup-warning" title=${selected.warn}>${selected.warn}</span>` : nothing}
          <span class="picker-popup-caret" aria-hidden="true">▾</span>
        </button>
        ${this._open ? html`
          <div
            class="picker-popup-panel"
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

customElements.define('picker-popup', PickerPopup);
