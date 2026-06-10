import { LitElement, html } from 'lit';

/**
 * Inline confirmation prompt for tool permissions.
 * Renders approve/deny/always buttons and dispatches the response.
 * For workflow_user_input action_type, renders a text input (or choice
 * buttons) so the user's answer is forwarded as data.value.
 */
export class ConfirmView extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    confirms: { type: Array, attribute: false },
    _inputValues: { type: Object, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.store = null;
    /** @type {Array} */
    this.confirms = [];
    /** @type {Object<string,string>} Map of confirmation_id -> current text value */
    this._inputValues = {};
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

  /**
   * @param {string} confirmationId
   * @param {string} value
   */
  #setInputValue(confirmationId, value) {
    this._inputValues = { ...this._inputValues, [confirmationId]: value };
  }

  /**
   * Render a workflow_user_input confirmation card with a text input
   * (or choice buttons if action_data.choices is non-empty).
   * @param {object} c
   */
  #renderWorkflowInput(c) {
    const choices = Array.isArray(c.action_data?.choices) ? c.action_data.choices : [];
    const confirmId = c.confirmation_id;

    if (choices.length > 0) {
      // Render a button per choice, plus a cancel button
      return html`
        <div class="confirm-card">
          <div class="confirm-header">
            <strong>${c.message}</strong>
          </div>
          <div class="confirm-buttons">
            ${choices.map(choice => html`
              <button class="outline" @click=${() =>
                this.#handleConfirm(c.context_id, c.tool, c.tool_call_id, true,
                  { data: { value: choice } })}>
                ${choice}
              </button>
            `)}
            <button class="outline secondary" @click=${() =>
              this.#handleConfirm(c.context_id, c.tool, c.tool_call_id, false)}>
              Cancel
            </button>
          </div>
        </div>`;
    }

    // Free-text input
    const currentValue = this._inputValues[confirmId] || '';
    const canSubmit = currentValue.trim().length > 0;

    /** @param {KeyboardEvent} e */
    const onKeyDown = (e) => {
      if (e.key === 'Enter' && canSubmit) {
        e.preventDefault();
        this.#handleConfirm(c.context_id, c.tool, c.tool_call_id, true,
          { data: { value: currentValue.trim() } });
      }
    };

    return html`
      <div class="confirm-card">
        <div class="confirm-header">
          <strong>${c.message}</strong>
        </div>
        <div class="confirm-input">
          <input
            type="text"
            placeholder="Your answer…"
            .value=${currentValue}
            @input=${(e) => this.#setInputValue(confirmId, e.target.value)}
            @keydown=${onKeyDown}
          />
        </div>
        <div class="confirm-buttons">
          <button class="outline" ?disabled=${!canSubmit}
            @click=${() =>
              this.#handleConfirm(c.context_id, c.tool, c.tool_call_id, true,
                { data: { value: currentValue.trim() } })}>
            Submit
          </button>
          <button class="outline secondary" @click=${() =>
            this.#handleConfirm(c.context_id, c.tool, c.tool_call_id, false)}>
            Cancel
          </button>
        </div>
      </div>`;
  }

  render() {
    if (!this.confirms?.length) return '';

    // Widget confirmations render inside their own tool message as the
    // widget UI (radios, checkboxes, etc.) — don't show approve/deny
    // buttons for them here.
    const visible = this.confirms.filter(
      c => c.action_type !== 'widget_response');
    if (!visible.length) return '';

    return html`${visible.map(c => {
      // Workflow user-input pauses get a dedicated text/choice affordance.
      if (c.action_type === 'workflow_user_input') {
        return this.#renderWorkflowInput(c);
      }

      return html`
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
        </div>`;
    })}`;
  }
}

customElements.define('confirm-view', ConfirmView);
