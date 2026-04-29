import { LitElement, html, nothing } from 'lit';

/**
 * Free-form text input widget. Renders a single text input by default,
 * a textarea when a field has `multiline: true`, or a small multi-field
 * form when `fields` has more than one entry. Submit dispatches
 * widget-response with the field values keyed by `key`.
 *
 * Props:
 *   data      { prompt, fields: [{key, label, placeholder?, default?,
 *                                  multiline?, required?, max_length?}],
 *              submit_label? }
 *   submitted boolean — true after the user submits or when reloaded
 *   response  { [key]: string } — populated on reload
 */
export class TextInputWidget extends LitElement {
  static properties = {
    data: { type: Object },
    submitted: { type: Boolean },
    response: { type: Object, attribute: false },
    _values: { type: Object, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {{prompt: string, fields: Array<Object>, submit_label?: string}|null} */
    this.data = null;
    this.submitted = false;
    /** @type {Object<string,string>|null} */
    this.response = null;
    /** @type {Object<string,string>} */
    this._values = {};
  }

  /** @param {Map<string, any>} changed */
  updated(changed) {
    if ((changed.has('response') || changed.has('submitted'))
        && this.submitted && this.response) {
      this._values = { ...this.response };
      return;
    }
    if (changed.has('data') && this.data && !this.submitted) {
      /** @type {Object<string,string>} */
      const seeded = {};
      for (const f of (this.data.fields || [])) {
        seeded[f.key] = (f.default ?? '');
      }
      this._values = seeded;
    }
  }

  _onInput(key, e) {
    const value = /** @type {HTMLInputElement | HTMLTextAreaElement} */ (e.target).value;
    this._values = { ...this._values, [key]: value };
  }

  _isSingleLineSingleField() {
    const fields = this.data?.fields || [];
    return fields.length === 1 && !fields[0].multiline;
  }

  _canSubmit() {
    if (this.submitted) return false;
    for (const f of (this.data?.fields || [])) {
      const required = f.required !== false;
      if (required && !((this._values[f.key] || '').trim())) return false;
    }
    return true;
  }

  _onSubmit() {
    if (!this._canSubmit()) return;
    this.dispatchEvent(new CustomEvent('widget-response', {
      detail: { ...this._values },
      bubbles: true,
      composed: true,
    }));
    // Don't flip submitted locally — the parent flips it via the
    // broadcast confirmation_response event so all tabs stay in sync.
  }

  _onKeyDown(e) {
    if (e.key !== 'Enter') return;
    if ((e.metaKey || e.ctrlKey) || this._isSingleLineSingleField()) {
      e.preventDefault();
      this._onSubmit();
    }
  }

  /** @param {string} key */
  _fieldId(key) {
    // HTML id can't contain whitespace and shouldn't contain arbitrary
    // punctuation. Sanitize to [A-Za-z0-9_-]; collapse runs to a single
    // dash. Empty after sanitize falls back to `tf-` so attribute is
    // still present (validation upstream guarantees non-empty key).
    const safe = String(key)
      .trim()
      .replace(/[^A-Za-z0-9_-]+/g, '-')
      .replace(/^-+|-+$/g, '');
    return `tf-${safe || 'field'}`;
  }

  /** @param {{key: string, label: string, placeholder?: string, default?: string, multiline?: boolean, required?: boolean, max_length?: number}} f */
  _renderField(f) {
    const value = this._values[f.key] ?? '';
    const required = f.required !== false;
    const id = this._fieldId(f.key);
    const placeholder = f.placeholder || '';
    const maxlength = f.max_length || undefined;
    const input = f.multiline
      ? html`
          <textarea
            id=${id}
            placeholder=${placeholder}
            maxlength=${maxlength ?? nothing}
            ?required=${required}
            ?disabled=${this.submitted}
            .value=${value}
            @input=${(e) => this._onInput(f.key, e)}
            @keydown=${(e) => this._onKeyDown(e)}
            rows="3"
          ></textarea>`
      : html`
          <input
            type="text"
            id=${id}
            placeholder=${placeholder}
            maxlength=${maxlength ?? nothing}
            ?required=${required}
            ?disabled=${this.submitted}
            .value=${value}
            @input=${(e) => this._onInput(f.key, e)}
            @keydown=${(e) => this._onKeyDown(e)}
          />`;
    return html`
      <label class="widget-text-input__field" for=${id}>
        <span class="widget-text-input__label">${f.label}</span>
        ${input}
      </label>`;
  }

  render() {
    const d = this.data;
    if (!d || !Array.isArray(d.fields) || d.fields.length === 0) {
      return html`<div class="widget-text-input widget-text-input--empty"><em>no fields</em></div>`;
    }
    const submitLabel = this.submitted
      ? 'Submitted'
      : (d.submit_label || 'Submit');
    return html`
      <div class="widget-text-input">
        ${d.prompt ? html`<p class="widget-text-input__prompt">${d.prompt}</p>` : nothing}
        <div class="widget-text-input__fields">
          ${d.fields.map((f) => this._renderField(f))}
        </div>
        <div class="widget-text-input__actions">
          <button
            type="button"
            class="widget-text-input__submit"
            ?disabled=${!this._canSubmit()}
            @click=${this._onSubmit}
          >${submitLabel}</button>
        </div>
      </div>`;
  }
}

customElements.define('dc-widget-text-input', TextInputWidget);
