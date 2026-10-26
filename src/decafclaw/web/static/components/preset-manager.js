/**
 * Shell Auto-Approval Preset Manager (#1158).
 *
 * Lists all presets (builtin, config, custom) with source badges.
 * Builtin and config presets render read-only.
 * Custom presets can be created, edited, and deleted.
 * Warns before deleting a preset referenced by an active config mode or active_aux_approval_presets.
 */

import { LitElement, html, nothing } from 'lit';
import { ApiError } from '../lib/api-client/index.js';
import { deletePreset, load, savePreset, subscribe } from '../lib/presets-store.js';

export class PresetManager extends LitElement {
  static properties = {
    _presets: { state: true },
    _loading: { state: true },
    _editingPreset: { state: true },
    _editingName: { state: true },
    _editingRules: { state: true },
    _isNew: { state: true },
    _error: { state: true },
    _pendingDelete: { state: true },
    _modes: { state: true },
    _activePresets: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {import('../lib/api-client/index.js').ShellPresetItem[]} */
    this._presets = [];
    this._loading = false;
    /** @type {import('../lib/api-client/index.js').ShellPresetItem|null} */
    this._editingPreset = null;
    this._editingName = '';
    this._editingRules = '';
    this._isNew = false;
    this._error = '';
    /** @type {string|null} */
    this._pendingDelete = null;
    /** @type {Array<{name: string, presets: string[]}>} */
    this._modes = [];
    /** @type {string[]} */
    this._activePresets = [];
  }

  connectedCallback() {
    super.connectedCallback();
    this._unsub = subscribe(presets => {
      this._presets = presets;
    });
    void this.#fetchData();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._unsub) this._unsub();
  }

  async #fetchData() {
    this._loading = true;
    this._error = '';
    try {
      await load();
      // Also inspect modes or window state if available
      const w = /** @type {any} */ (window);
      if (w.__DECAFCLAW_MODES__) {
        this._modes = w.__DECAFCLAW_MODES__;
      }
      if (w.__DECAFCLAW_ACTIVE_PRESETS__) {
        this._activePresets = w.__DECAFCLAW_ACTIVE_PRESETS__;
      }
    } catch (e) {
      this._error = 'Failed to load presets';
    } finally {
      this._loading = false;
    }
  }

  #startCreate() {
    this._editingPreset = null;
    this._editingName = '';
    this._editingRules = '';
    this._isNew = true;
    this._error = '';
  }

  /** @param {import('../lib/api-client/index.js').ShellPresetItem} preset */
  #startEdit(preset) {
    if (preset.source !== 'custom') return;
    this._editingPreset = preset;
    this._editingName = preset.name;
    this._editingRules = preset.rules.join('\n');
    this._isNew = false;
    this._error = '';
  }

  #cancelEdit() {
    this._editingPreset = null;
    this._editingName = '';
    this._editingRules = '';
    this._isNew = false;
    this._error = '';
  }

  async #saveEdit() {
    const name = this._editingName.trim();
    if (!name) {
      this._error = 'Preset name is required';
      return;
    }
    const rules = this._editingRules
      .split('\n')
      .map(r => r.trim().replace(/^-\s*/, ''))
      .filter(Boolean);
    if (!rules.length) {
      this._error = 'At least one rule is required';
      return;
    }

    try {
      await savePreset(name, rules);
      this.#cancelEdit();
    } catch (err) {
      if (err instanceof ApiError && err.body?.error) {
        this._error = err.body.error;
      } else {
        this._error = (err instanceof Error ? err.message : 'Failed to save preset');
      }
    }
  }

  /** @param {string} name */
  #promptDelete(name) {
    this._pendingDelete = name;
  }

  #cancelDelete() {
    this._pendingDelete = null;
  }

  async #confirmDelete() {
    if (!this._pendingDelete) return;
    const name = this._pendingDelete;
    this._pendingDelete = null;
    try {
      await deletePreset(name);
    } catch (err) {
      if (err instanceof ApiError && err.body?.error) {
        this._error = err.body.error;
      } else {
        this._error = (err instanceof Error ? err.message : 'Failed to delete preset');
      }
    }
  }

  /**
   * Check if preset is referenced by configured modes or active presets.
   * @param {string} name
   * @returns {string[]} referencing sources
   */
  getReferences(name) {
    const refs = [];
    for (const m of this._modes) {
      if (m.presets && m.presets.includes(name)) {
        refs.push(`mode '${m.name}'`);
      }
    }
    if (this._activePresets && this._activePresets.includes(name)) {
      refs.push('active_aux_approval_presets');
    }
    return refs;
  }

  render() {
    if (this._editingPreset || this._isNew) {
      return this.#renderEditor();
    }

    return html`
      <div class="preset-manager">
        <div class="preset-manager-header">
          <span class="preset-manager-title">Shell Auto-Approval Presets</span>
          <button class="preset-create-btn outline" @click=${() => this.#startCreate()}>+ New Preset</button>
        </div>

        ${this._error ? html`<div class="preset-error-banner">${this._error}</div>` : nothing}

        ${this._pendingDelete ? this.#renderDeleteWarning(this._pendingDelete) : nothing}

        ${this._loading ? html`<p class="preset-loading">Loading presets...</p>` : nothing}

        <div class="preset-list">
          ${this._presets.map(p => html`
            <div class="preset-item ${p.source}" data-preset-name=${p.name}>
              <div class="preset-item-header">
                <span class="preset-name">${p.name}</span>
                <span class="preset-source-badge ${p.source}">${p.source}</span>
                ${p.shadows_config ? html`<span class="preset-shadow-badge" title="Shadows config preset of same name">shadows config</span>` : nothing}
                <div class="preset-actions">
                  ${p.source === 'custom' ? html`
                    <button class="dc-icon-btn preset-edit-btn" @click=${() => this.#startEdit(p)} title="Edit preset" aria-label="Edit preset">&#9998;</button>
                    <button class="dc-icon-btn preset-delete-btn" @click=${() => this.#promptDelete(p.name)} title="Delete preset" aria-label="Delete preset">&#128465;</button>
                  ` : html`
                    <span class="preset-readonly-label">Read-only</span>
                  `}
                </div>
              </div>
              <ul class="preset-rules-list">
                ${p.rules.map(r => html`<li>${r}</li>`)}
              </ul>
            </div>
          `)}
        </div>
      </div>
    `;
  }

  /** @param {string} name */
  #renderDeleteWarning(name) {
    const refs = this.getReferences(name);
    return html`
      <div class="preset-delete-confirm-card" role="dialog" aria-label="Confirm preset deletion">
        <div class="preset-delete-warning-title">Delete preset '${name}'?</div>
        ${refs.length ? html`
          <div class="preset-delete-warning-body">
            ⚠️ Warning: This preset is currently referenced by: <strong>${refs.join(', ')}</strong>.
            Deleting it may cause those modes or settings to fail to resolve rules.
          </div>
        ` : html`
          <div class="preset-delete-warning-body">Are you sure you want to delete this custom preset?</div>
        `}
        <div class="preset-confirm-actions">
          <button class="preset-confirm-delete-btn" @click=${() => void this.#confirmDelete()}>Delete</button>
          <button class="outline" @click=${() => this.#cancelDelete()}>Cancel</button>
        </div>
      </div>
    `;
  }

  #renderEditor() {
    return html`
      <div class="preset-editor">
        <div class="preset-editor-header">
          <button class="dc-icon-btn preset-back-btn" @click=${() => this.#cancelEdit()} title="Back to presets" aria-label="Back">&larr;</button>
          <span class="preset-editor-title">${this._isNew ? 'New Custom Preset' : `Edit Preset: ${this._editingName}`}</span>
        </div>

        ${this._error ? html`<div class="preset-error-banner">${this._error}</div>` : nothing}

        <div class="preset-editor-body">
          <label>
            Preset Name
            <input
              type="text"
              class="preset-name-input"
              .value=${this._editingName}
              ?disabled=${!this._isNew}
              @input=${(/** @type {any} */ e) => { this._editingName = e.target.value; }}
              placeholder="e.g. backend_tests"
            />
          </label>

          <label>
            Rules (one rule per line)
            <textarea
              class="preset-rules-input"
              rows="6"
              .value=${this._editingRules}
              @input=${(/** @type {any} */ e) => { this._editingRules = e.target.value; }}
              placeholder="Auto-approve pytest in tests/&#10;Auto-approve ruff check"
            ></textarea>
          </label>

          <div class="preset-editor-actions">
            <button class="preset-save-btn" @click=${() => void this.#saveEdit()}>Save Preset</button>
            <button class="outline" @click=${() => this.#cancelEdit()}>Cancel</button>
          </div>
        </div>
      </div>
    `;
  }
}

customElements.define('preset-manager', PresetManager);
