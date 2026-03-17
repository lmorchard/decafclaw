import { LitElement, html } from 'lit';

/**
 * Inline confirmation prompt for tool permissions.
 * Renders approve/deny/always buttons and dispatches the response.
 */
export class ConfirmView extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    confirms: { type: Array, attribute: false },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.store = null;
    /** @type {Array} */
    this.confirms = [];
  }

  /**
   * @param {string} contextId
   * @param {string} tool
   * @param {boolean} approved
   * @param {object} [extra]
   */
  #handleConfirm(contextId, tool, approved, extra = {}) {
    this.store?.respondToConfirm(contextId, tool, approved, extra);
  }

  render() {
    if (!this.confirms?.length) return '';

    return html`${this.confirms.map(c => html`
      <div class="confirm-card">
        <div class="confirm-header">
          <strong>Confirm ${c.tool}:</strong>
          <pre class="confirm-command">${c.command}</pre>
        </div>
        <div class="confirm-buttons">
          <button class="outline" @click=${() => this.#handleConfirm(c.context_id, c.tool, true)}>
            Approve
          </button>
          <button class="outline secondary" @click=${() => this.#handleConfirm(c.context_id, c.tool, false)}>
            Deny
          </button>
          ${c.tool === 'shell' && c.suggested_pattern ? html`
            <button class="outline" @click=${() => this.#handleConfirm(c.context_id, c.tool, true, { add_pattern: true })}>
              Allow: ${c.suggested_pattern}
            </button>
          ` : html`
            <button class="outline" @click=${() => this.#handleConfirm(c.context_id, c.tool, true, { always: true })}>
              Always
            </button>
          `}
        </div>
      </div>
    `)}`;
  }
}

customElements.define('confirm-view', ConfirmView);
