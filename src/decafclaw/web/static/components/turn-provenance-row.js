import { LitElement, html, nothing } from 'lit';

/**
 * Capsule row for passive context a turn ingested during PRE_COMPOSE (#1563).
 *
 * Each capsule is a button showing its source icon and item count; opening one
 * reveals the exact items the source injected. Tool calls stay separate: they
 * are deliberate agent actions, not passive input provenance.
 */

/** @typedef {{ icon: string, singular: string, plural: string }} CapsuleMeta */

/** @type {Record<string, CapsuleMeta>} */
const CAPSULE_META = {
  facts: { icon: '🧠', singular: 'fact', plural: 'facts' },
  journals: { icon: '📖', singular: 'journal entry', plural: 'journal entries' },
  vault: { icon: '🏛️', singular: 'vault page', plural: 'vault pages' },
  scratchpad: { icon: '📝', singular: 'note', plural: 'notes' },
};

const KNOWN_ORDER = ['facts', 'journals', 'vault', 'scratchpad'];

/** @param {string} key @returns {CapsuleMeta} */
function metaFor(key) {
  return CAPSULE_META[key] || { icon: '•', singular: key, plural: key };
}

/** @param {object|null|undefined} provenance */
export function provenanceHasItems(provenance) {
  if (!provenance || typeof provenance !== 'object') return false;
  return Object.values(provenance).some((items) => Array.isArray(items) && items.length > 0);
}

/** @param {object|null|undefined} provenance @returns {[string, object[]][]} */
function capsuleEntries(provenance) {
  if (!provenance || typeof provenance !== 'object') return [];
  const known = KNOWN_ORDER
    .filter((key) => Array.isArray(provenance[key]) && provenance[key].length > 0)
    .map((key) => /** @type {[string, object[]]} */ ([key, provenance[key]]));
  const unknown = Object.keys(provenance)
    .filter((key) => !KNOWN_ORDER.includes(key) && Array.isArray(provenance[key]) && provenance[key].length > 0)
    .map((key) => /** @type {[string, object[]]} */ ([key, provenance[key]]));
  return [...known, ...unknown];
}

/** @param {object} item @returns {string} */
function primaryText(item) {
  if (typeof item.fact === 'string') return item.fact;
  if (typeof item.text === 'string') return item.text;
  if (typeof item.excerpt === 'string') return item.excerpt;
  return '';
}

export class TurnProvenanceRow extends LitElement {
  static properties = {
    provenance: { type: Object, attribute: false },
    _expandedKey: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {Record<string, object[]>} */
    this.provenance = {};
    this._expandedKey = '';
  }

  /** @param {string} key */
  #toggle(key) {
    this._expandedKey = this._expandedKey === key ? '' : key;
  }

  /** @param {string} key @param {object[]} items */
  #renderPanel(key, items) {
    return html`
      <div class="turn-provenance-panel" role="region" aria-label=${`${key} context`}>
        <ul>
          ${items.map((item) => this.#renderItem(key, item))}
        </ul>
      </div>
    `;
  }

  /** @param {string} key @param {object} item */
  #renderItem(key, item) {
    const text = primaryText(item);
    if (key === 'vault') {
      return html`
        <li class="turn-provenance-vault">
          <span class="turn-provenance-page">${item.page || ''}</span>
          ${item.score != null ? html`<span class="turn-provenance-score">score ${item.score}</span>` : nothing}
          ${text ? html`<p>${text}</p>` : nothing}
        </li>
      `;
    }
    if (key === 'journals' || key === 'scratchpad') {
      return html`
        <li>
          ${item.timestamp ? html`<span class="turn-provenance-ts">${item.timestamp}</span> ` : nothing}
          ${text}
        </li>
      `;
    }
    return html`<li>${text || JSON.stringify(item)}</li>`;
  }

  render() {
    const capsules = capsuleEntries(this.provenance);
    if (!capsules.length) return nothing;

    return html`
      <div class="turn-provenance" role="group" aria-label="Turn context provenance">
        <div class="turn-provenance-capsules">
          ${capsules.map(([key, items]) => {
            const meta = metaFor(key);
            const label = items.length === 1 ? meta.singular : meta.plural;
            const expanded = this._expandedKey === key;
            return html`
              <button
                type="button"
                class="turn-provenance-capsule"
                data-source=${key}
                aria-expanded=${expanded ? 'true' : 'false'}
                @click=${() => this.#toggle(key)}
              >
                <span class="turn-provenance-icon" aria-hidden="true">${meta.icon}</span>
                <span class="turn-provenance-count">${items.length} ${label}</span>
                <span class="turn-provenance-caret" aria-hidden="true">${expanded ? '▴' : '▾'}</span>
              </button>
            `;
          })}
        </div>
        ${capsules.map(([key, items]) => (this._expandedKey === key ? this.#renderPanel(key, items) : nothing))}
      </div>
    `;
  }
}

customElements.define('turn-provenance-row', TurnProvenanceRow);
