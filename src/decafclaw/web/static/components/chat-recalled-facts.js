import { LitElement, html, nothing } from 'lit';

/**
 * Collapsible list of the semantic-memory facts injected into one turn (#1514).
 *
 * The chip is the collapsed state; clicking it reveals the exact atomic facts
 * the model received. Empty input renders nothing at all, so turns without a
 * memory hit leave no chip or spacing behind.
 */
export class ChatRecalledFacts extends LitElement {
  static properties = {
    facts: { type: Array, attribute: false },
    _expanded: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {string[]} */
    this.facts = [];
    this._expanded = false;
  }

  render() {
    const facts = (this.facts || []).filter((fact) => typeof fact === 'string' && fact.trim());
    if (!facts.length) return nothing;

    const label = facts.length === 1 ? 'Recalled 1 fact' : `Recalled ${facts.length} facts`;
    return html`
      <div class="recalled-facts">
        <button
          type="button"
          class="recalled-facts-toggle"
          aria-expanded=${this._expanded ? 'true' : 'false'}
          @click=${() => { this._expanded = !this._expanded; }}
        >
          <span class="recalled-facts-icon" aria-hidden="true">🧠</span>
          <span class="recalled-facts-label">${label}</span>
          <span class="recalled-facts-caret" aria-hidden="true">${this._expanded ? '▴' : '▾'}</span>
        </button>
        ${this._expanded ? html`
          <ul class="recalled-facts-list">
            ${facts.map((fact) => html`<li>${fact}</li>`)}
          </ul>
        ` : nothing}
      </div>
    `;
  }
}

customElements.define('chat-recalled-facts', ChatRecalledFacts);
