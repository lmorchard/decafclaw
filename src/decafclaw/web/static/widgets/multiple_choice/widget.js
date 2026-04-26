import { LitElement, html, nothing } from 'lit';

/**
 * Multiple-choice input widget. Radios for single selection (default),
 * checkboxes when data.allow_multiple is true. Submit dispatches
 * widget-response with `{selected}` (string or array), then the widget
 * transitions to a submitted state (disabled controls, relabeled
 * button, winning option highlighted).
 *
 * Props:
 *   data      { prompt, options: [{value, label, description?}], allow_multiple? }
 *   submitted boolean — true after user submits or when reloaded from archive
 *   response  { selected: string | string[] } — populated on reload
 */
export class MultipleChoiceWidget extends LitElement {
  static properties = {
    data: { type: Object },
    submitted: { type: Boolean },
    response: { type: Object, attribute: false },
    _selectedSingle: { type: String, state: true },
    _selectedMulti: { type: Array, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {{prompt: string, options: {value: string, label: string, description?: string}[], allow_multiple?: boolean}|null} */
    this.data = null;
    this.submitted = false;
    /** @type {{selected?: string|string[]}|null} */
    this.response = null;
    this._selectedSingle = '';
    /** @type {string[]} */
    this._selectedMulti = [];
  }

  /** @param {Map<string, any>} changed */
  updated(changed) {
    // On reload, seed local selection state from the archived response
    // so the render can show the winning choice in the submitted UI.
    if ((changed.has('response') || changed.has('submitted'))
        && this.submitted && this.response) {
      const sel = this.response.selected;
      if (Array.isArray(sel)) {
        this._selectedMulti = sel.map(String);
      } else if (typeof sel === 'string') {
        this._selectedSingle = sel;
      }
    }
  }

  _onRadioChange(e) {
    this._selectedSingle = /** @type {HTMLInputElement} */ (e.target).value;
  }

  _onCheckboxChange(e) {
    const target = /** @type {HTMLInputElement} */ (e.target);
    const value = target.value;
    if (target.checked) {
      if (!this._selectedMulti.includes(value)) {
        this._selectedMulti = [...this._selectedMulti, value];
      }
    } else {
      this._selectedMulti = this._selectedMulti.filter(v => v !== value);
    }
  }

  _canSubmit() {
    if (this.submitted) return false;
    if (this._isMulti()) return this._selectedMulti.length > 0;
    return !!this._selectedSingle;
  }

  _isMulti() {
    return !!this.data?.allow_multiple;
  }

  _onSubmit() {
    if (!this._canSubmit()) return;
    const detail = this._isMulti()
      ? { selected: [...this._selectedMulti] }
      : { selected: this._selectedSingle };
    this.dispatchEvent(new CustomEvent('widget-response', {
      detail,
      bubbles: true,
      composed: true,
    }));
    // Don't flip `submitted` locally — the parent tool-message flips
    // it via the broadcast confirmation_response event so all tabs
    // stay in sync.
  }

  /** @param {{value: string, label: string, description?: string}} option */
  _renderOption(option) {
    const multi = this._isMulti();
    const name = multi ? `mc-${option.value}` : 'mc-single';
    const type = multi ? 'checkbox' : 'radio';
    const isChecked = multi
      ? this._selectedMulti.includes(option.value)
      : this._selectedSingle === option.value;
    const isWinner = this.submitted && isChecked;
    const classes = isWinner
      ? 'widget-multiple-choice__option widget-multiple-choice__option--winner'
      : 'widget-multiple-choice__option';
    return html`
      <label class=${classes}>
        <input
          type=${type}
          name=${name}
          value=${option.value}
          .checked=${isChecked}
          ?disabled=${this.submitted}
          @change=${multi ? this._onCheckboxChange : this._onRadioChange}
        />
        <span class="widget-multiple-choice__label">${option.label}</span>
        ${option.description ? html`
          <small class="widget-multiple-choice__description">${option.description}</small>
        ` : nothing}
      </label>
    `;
  }

  render() {
    const d = this.data;
    if (!d || !Array.isArray(d.options)) {
      return html`<div class="widget-multiple-choice widget-multiple-choice--empty"><em>no options</em></div>`;
    }
    const submitLabel = this.submitted ? 'Submitted' : 'Submit';
    return html`
      <div class="widget-multiple-choice">
        ${d.prompt ? html`<p class="widget-multiple-choice__prompt">${d.prompt}</p>` : nothing}
        <div class="widget-multiple-choice__options"
             role=${this._isMulti() ? 'group' : 'radiogroup'}>
          ${d.options.map(o => this._renderOption(o))}
        </div>
        <div class="widget-multiple-choice__actions">
          <button
            type="button"
            class="widget-multiple-choice__submit"
            ?disabled=${!this._canSubmit()}
            @click=${this._onSubmit}
          >${submitLabel}</button>
        </div>
      </div>
    `;
  }
}

customElements.define('dc-widget-multiple-choice', MultipleChoiceWidget);
