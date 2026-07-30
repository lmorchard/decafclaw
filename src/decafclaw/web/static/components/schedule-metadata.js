/**
 * Schedule metadata panel — every frontmatter field a schedule supports.
 *
 * Presentational: performs no I/O. The host (schedule-page) owns every PUT
 * so metadata writes serialise against wiki-editor's body autosave, the
 * same division of labour wiki-metadata uses for vault pages.
 *
 * Emits `metadata-change` with one patch key per edit. Key names match
 * write_overlay's patch keys exactly.
 *
 * The raw view is read-only, unlike the vault panel's. Schedule
 * frontmatter maps onto a fixed dataclass and serialize_to_markdown
 * writes only recognised fields, so an editable box would accept a key
 * and silently drop it on the next write.
 */

import { LitElement, html, nothing } from 'lit';
import './chip-list.js';

/** Chip-backed fields that pre-approve actions past confirmation. */
const PERMISSION_LISTS = [
  ['allowed_tools', 'Allowed tools'],
  ['shell_patterns', 'Shell patterns'],
  ['email_recipients', 'Email recipients'],
];

export class ScheduleMetadata extends LitElement {
  static properties = {
    data: { attribute: false },
    models: { attribute: false },
    readonly: { type: Boolean },
    error: { type: String },
    _rawOpen: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {any} */ this.data = null;
    /** @type {string[]} */ this.models = [];
    this.readonly = false;
    /** Server message for the last failed write; '' when clear. */
    this.error = '';
    this._rawOpen = false;
  }

  /** @param {string} field @param {unknown} value */
  #emit(field, value) {
    this.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { [field]: value } },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {string} field @param {Event} e */
  #onText(field, e) {
    this.#emit(field, /** @type {HTMLInputElement} */ (e.target).value);
  }

  /** @param {string} field @param {string} label */
  #renderChips(field, label) {
    return html`
      <label>
        <span>${label}</span>
        <chip-list
          data-field=${field}
          .label=${label}
          .items=${this.data?.[field] ?? []}
          ?readonly=${this.readonly}
          @chips-change=${(/** @type {any} */ e) => this.#emit(field, e.detail.items)}
        ></chip-list>
      </label>
    `;
  }

  #renderModel() {
    const current = this.data?.model ?? '';
    // A stored value absent from model_configs renders as a flagged
    // option rather than a blank field — blank reads as "unset", which
    // is exactly how #729 stayed invisible.
    const unconfigured = current && !this.models.includes(current);
    // Selection is expressed per-option, never via `.value` on the
    // <select>. lit commits parts in tree order, so a `.value` binding
    // lands before the option child part has produced any options:
    // selectedIndex goes to -1, appending options triggers the select
    // reset algorithm, and "(default)" wins. lit then never re-commits
    // the unchanged string, so it cannot self-heal — a configured model
    // would read as unset, which is #729's ambiguity all over again.
    // Same pattern as the model picker in conversation-sidebar.js.
    return html`
      <label>
        <span>Model</span>
        <select
          class="sched-md-model"
          ?disabled=${this.readonly}
          @change=${(/** @type {Event} */ e) =>
            this.#emit('model', /** @type {HTMLSelectElement} */ (e.target).value)}
        >
          <option value="" ?selected=${!current}>(default)</option>
          ${unconfigured ? html`
            <option value=${current} selected>⚠ ${current} (not configured)</option>
          ` : nothing}
          ${this.models.map(m => html`
            <option value=${m} ?selected=${m === current}>${m}</option>
          `)}
        </select>
      </label>
    `;
  }

  #renderUnknown() {
    const keys = this.data?.unknown_keys ?? [];
    if (!keys.length) return nothing;
    const plural = keys.length === 1 ? 'key is' : 'keys are';
    return html`
      <div class="sched-md-unknown">
        ⚠ ${keys.length} ${plural} not recognized and ${keys.length === 1 ? 'is' : 'are'}
        ignored: ${keys.join(', ')}
      </div>
    `;
  }

  #renderRaw() {
    return html`
      <div class="sched-md-raw">
        <button
          type="button"
          class="sched-md-raw-toggle"
          @click=${() => { this._rawOpen = !this._rawOpen; }}
        >${this._rawOpen ? '▾' : '▸'} raw (read-only)</button>
        ${this._rawOpen ? html`
          <pre class="sched-md-raw-body">${this.data?.frontmatter_raw ?? ''}</pre>
        ` : nothing}
        ${this.#renderUnknown()}
      </div>
    `;
  }

  render() {
    if (!this.data) return nothing;
    return html`
      <div class="sched-md">
        ${this.error ? html`
          <div class="sched-md-error" role="alert">${this.error}</div>
        ` : nothing}
        <div class="sched-md-form">
          <label>
            <span>Cron</span>
            <input
              class="sched-md-cron"
              type="text"
              ?disabled=${this.readonly}
              .value=${this.data.schedule ?? ''}
              @change=${(/** @type {Event} */ e) => this.#onText('schedule', e)}
            />
          </label>
          <label>
            <span>Channel</span>
            <input
              class="sched-md-channel"
              type="text"
              placeholder="(default channel)"
              ?disabled=${this.readonly}
              .value=${this.data.channel ?? ''}
              @change=${(/** @type {Event} */ e) => this.#onText('channel', e)}
            />
          </label>
          ${this.#renderModel()}
          <label class="inline">
            <input
              class="sched-md-enabled"
              type="checkbox"
              ?disabled=${this.readonly}
              .checked=${Boolean(this.data.enabled)}
              @change=${(/** @type {Event} */ e) =>
                this.#emit('enabled', /** @type {HTMLInputElement} */ (e.target).checked)}
            />
            <span>Enabled</span>
          </label>
          <label>
            <span>Pre-script</span>
            <input
              class="sched-md-pre-script"
              type="text"
              placeholder="(none)"
              ?disabled=${this.readonly}
              .value=${this.data.pre_script ?? ''}
              @change=${(/** @type {Event} */ e) => this.#onText('pre_script', e)}
            />
          </label>
          ${this.#renderChips('required_skills', 'Required skills')}
        </div>

        <div class="sched-md-permissions">
          <div class="sched-md-permissions-title">
            ⚠ Permissions — these bypass confirmation
          </div>
          ${PERMISSION_LISTS.map(([f, l]) => this.#renderChips(f, l))}
        </div>

        ${this.#renderRaw()}
      </div>
    `;
  }
}

customElements.define('schedule-metadata', ScheduleMetadata);
