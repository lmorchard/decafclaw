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
   * @param {string} toolCallId
   * @param {boolean} approved
   * @param {object} [extra]
   */
  #handleConfirm(contextId, tool, toolCallId, approved, extra = {}) {
    this.store?.respondToConfirm(contextId, tool, toolCallId, approved, extra);
  }

  render() {
    if (!this.confirms?.length) return '';

    // Widget confirmations render inside their own tool message as the
    // widget UI (radios, checkboxes, etc.) — don't show approve/deny
    // buttons for them here.
    const visible = this.confirms.filter(
      c => c.action_type !== 'widget_response');
    if (!visible.length) return '';

    return html`${visible.map(c => html`
      <div class="confirm-card">
        <div class="confirm-header">
          <strong>Confirm ${c.tool}:</strong>
          <pre class="confirm-command">${c.command}</pre>
        </div>
        <div class="confirm-buttons">
          <button class="outline" @click=${() => this.#handleConfirm(c.context_id, c.tool, c.tool_call_id, true)}>
            ${c.approve_label || 'Approve'}
          </button>
          <button class="outline secondary" @click=${() => this.#handleConfirm(c.context_id, c.tool, c.tool_call_id, false)}>
            ${c.deny_label || 'Deny'}
          </button>
          ${c.approve_label ? '' : c.tool === 'shell' && c.suggested_pattern ? html`
            <button class="outline" @click=${() => this.#handleConfirm(c.context_id, c.tool, c.tool_call_id, true, { add_pattern: true })}>
              Allow: ${c.suggested_pattern}
            </button>
          ` : html`
            <button class="outline" @click=${() => this.#handleConfirm(c.context_id, c.tool, c.tool_call_id, true, { always: true })}>
              Always
            </button>
          `}
        </div>
      </div>
    `)}`;
  }
}

customElements.define('confirm-view', ConfirmView);
