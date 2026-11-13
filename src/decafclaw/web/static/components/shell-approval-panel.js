/**
 * Shell Auto-Approval Panel (#1159).
 *
 * Per-conversation shell auto-approval view: editable conversation rules,
 * read-only active preset rules, the always-on config guidance, and the
 * cached reviewer approval command patterns. Mutations go through the
 * conversation-scoped REST endpoints (PUT/DELETE rules, DELETE pattern,
 * POST promote); a rule add/remove clears the cached pattern cache server-side.
 *
 * Hosted from the conversation sidebar (body-level mount, like the context
 * inspector #1037) so it escapes any transformed ancestor. Opened via the
 * `toggle-shell-approval` document event dispatched from the mode-picker /
 * chat-controls trigger buttons (`.shell-approval-open-btn`).
 */

import { LitElement, html, nothing, render } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';
import { subscribe } from '../lib/presets-store.js';

/** @typedef {import('../lib/api-client/index.js').ConversationShellApprovalResponse} ApprovalData */
/** @typedef {import('../lib/api-client/index.js').ConversationShellApprovalPresetItem} PresetView */

const SOURCE_BADGE_LABELS = {
  builtin: 'Builtin',
  config: 'Config',
  custom: 'Custom',
};

export class ShellApprovalPanel extends LitElement {
  static properties = {
    convId: { type: String },
    open: { type: Boolean, reflect: true },
    _data: { type: Object, state: true },
    _loading: { type: Boolean, state: true },
    _error: { type: String, state: true },
    _busy: { type: Boolean, state: true },
    _presets: { type: Array, state: true },
    _newRuleText: { type: String, state: true },
    _promoteRule: { type: String, state: true },
    _promotePreset: { type: String, state: true },
    _newPresetName: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.convId = '';
    this.open = false;
    /** @type {ApprovalData | null} */
    this._data = null;
    this._loading = false;
    this._error = '';
    this._busy = false;
    /** @type {import('../lib/api-client/index.js').ShellPresetItem[]} */
    this._presets = [];
    this._newRuleText = '';
    this._promoteRule = '';
    this._promotePreset = '';
    this._newPresetName = '';
    /** @type {HTMLElement | null} */
    this._panelMount = null;
  }

  connectedCallback() {
    super.connectedCallback();
    // Live preset list for the promote picker (custom presets only).
    this._unsub = subscribe((ps) => {
      this._presets = [...ps];
    });
    if (!this._panelMount) {
      // Body-level mount so the panel escapes any transformed ancestor (the
      // mobile sidebar's translateX, which would otherwise become the
      // containing block for `position: fixed` and clip the panel). Mirrors
      // notification-inbox.js (#386) and context-inspector.js (#1037).
      this._panelMount = document.createElement('div');
      this._panelMount.className = 'shell-approval-panel-mount';
      document.body.appendChild(this._panelMount);
    }
  }

  updated(changed) {
    if (changed.has('open')) {
      if (this.open) {
        // Defer so the opening click doesn't immediately close via the
        // document capture-phase listener below.
        requestAnimationFrame(() => {
          this._onDocClick = (e) => {
            if (this._panelMount?.contains(e.target)) return;
            if (this.contains(e.target)) return;
            const trigger = document.querySelector('.shell-approval-open-btn');
            if (trigger && trigger.contains(e.target)) return;
            this.dispatchEvent(new Event('close'));
          };
          document.addEventListener('click', this._onDocClick, true);
        });
        void this.#fetchData();
      } else {
        this.#cleanupListeners();
      }
    }
    // Render the panel template into the body-level mount on every update,
    // so Lit reactivity continues to work even though the panel lives
    // outside the component's normal render root.
    if (this._panelMount) {
      render(this.#panel(), this._panelMount);
    }
  }

  #cleanupListeners() {
    if (this._onDocClick) {
      document.removeEventListener('click', this._onDocClick, true);
      this._onDocClick = null;
    }
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.#cleanupListeners();
    if (this._unsub) this._unsub();
    if (this._panelMount) {
      render(nothing, this._panelMount);
      this._panelMount.remove();
      this._panelMount = null;
    }
  }

  async #fetchData() {
    if (!this.convId) return;
    this._loading = true;
    this._error = '';
    this._data = null;
    try {
      this._data = await DefaultService.getConversationShellApprovalApiConversationsIdShellApprovalGet(this.convId);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        this._error = e instanceof ApiError ? `HTTP ${e.status}` : e.message || 'Failed to load';
      }
    } finally {
      this._loading = false;
    }
  }

  async #addRule(text) {
    const trimmed = (text || '').trim();
    if (!trimmed) return;
    this._busy = true;
    this._error = '';
    try {
      await DefaultService.putConversationShellRuleApiConversationsIdShellApprovalRulesPut(this.convId, { rule: trimmed });
      this._promoteRule = '';
      this._newRuleText = '';
      await this.#fetchData();
    } catch (e) {
      this._error = e instanceof ApiError ? e.body?.error || `HTTP ${e.status}` : e.message || 'Failed to add rule';
    } finally {
      this._busy = false;
    }
  }

  async #deleteRule(text) {
    this._busy = true;
    this._error = '';
    try {
      await DefaultService.deleteConversationShellRuleApiConversationsIdShellApprovalRulesDelete(this.convId, { rule: text });
      await this.#fetchData();
    } catch (e) {
      this._error = e instanceof ApiError ? e.body?.error || `HTTP ${e.status}` : e.message || 'Failed to delete rule';
    } finally {
      this._busy = false;
    }
  }

  async #deletePattern(pattern) {
    this._busy = true;
    this._error = '';
    try {
      await DefaultService.deleteConversationShellPatternApiConversationsIdShellApprovalPatternsDelete(this.convId, { pattern });
      await this.#fetchData();
    } catch (e) {
      this._error = e instanceof ApiError ? e.body?.error || `HTTP ${e.status}` : e.message || 'Failed to delete pattern';
    } finally {
      this._busy = false;
    }
  }

  async #promote(rule, presetName) {
    this._busy = true;
    this._error = '';
    try {
      await DefaultService.postConversationShellPromoteApiConversationsIdShellApprovalPromotePost(this.convId, { rule, preset: presetName });
      this._promoteRule = '';
      this._promotePreset = '';
      this._newPresetName = '';
      await this.#fetchData();
    } catch (e) {
      this._error = e instanceof ApiError ? e.body?.error || `HTTP ${e.status}` : e.message || 'Failed to promote rule';
    } finally {
      this._busy = false;
    }
  }

  _close() {
    this.dispatchEvent(new Event('close'));
  }

  #renderConfigGuidance(data) {
    const guidance = data?.config_guidance || '';
    return html`
      <div class="sap-section">
        <div class="sap-section-title">Always-On Config Guidance</div>
        <div class="sap-config-guidance">${guidance || 'No config-level guidance set'}</div>
      </div>
    `;
  }

  #renderConversationRules(data) {
    const rules = data?.conversation_rules || [];
    const busy = this._busy || data?.status === 'busy' || data?.status === 'waiting';
    const editable = !busy;
    return html`
      <div class="sap-section">
        <div class="sap-section-title">Conversation Rules (editable)</div>
        ${this._error ? html`<div class="sap-error-banner">${this._error}</div>` : nothing}
        ${rules.length === 0 ? html`<div class="sap-empty">No conversation rules yet</div>` : html`
          <ul class="sap-rule-list">
            ${rules.map(r => html`
              <li class="sap-rule">
                <span class="sap-rule-text">${r}</span>
                <span class="sap-rule-actions">
                  <button
                    class="dc-icon-btn sap-promote-btn"
                    title="Promote this rule into a custom preset"
                    aria-label="Promote rule"
                    ?disabled=${busy}
                    @click=${() => this.#startPromote(r)}
                  >&#127942;</button>
                  <button
                    class="dc-icon-btn sap-delete-btn"
                    title="Delete this conversation rule"
                    aria-label="Delete rule"
                    ?disabled=${busy}
                    @click=${() => this.#deleteRule(r)}
                  >&#10005;</button>
                </span>
              </li>
            `)}
          </ul>
        `}
        <div class="sap-add-row">
          <input
            type="text"
            class="sap-rule-input"
            placeholder="Add a shell auto-approval rule (e.g. Auto-approve make test in this repo)"
            .value=${this._newRuleText}
            ?disabled=${!editable}
            @input=${(/** @type {any} */ e) => { this._newRuleText = e.target.value; }}
          />
          <button class="sap-add-btn" ?disabled=${!editable || !this._newRuleText?.trim()} @click=${() => { const t = this._newRuleText?.trim(); if (t) void this.#addRule(t); }}>Add Rule</button>
        </div>
      </div>
    `;
  }

  #renderPresetRules(data) {
    const presets = data?.presets || [];
    return html`
      <div class="sap-section">
        <div class="sap-section-title">Preset Rules (read-only, active presets)</div>
        ${presets.length === 0 ? html`<div class="sap-empty">No active presets</div>` : presets.map(p => html`
          <div class="sap-preset" data-preset-name=${p.name}>
            <div class="sap-preset-header">
              <span class="sap-preset-name">${p.name}</span>
              <span class="preset-source-badge ${p.source}">${SOURCE_BADGE_LABELS[p.source] || p.source}</span>
              ${p.activated_via?.length ? html`<span class="sap-activated-via">via ${p.activated_via.join(', ')}</span>` : nothing}
            </div>
            <ul class="preset-rules-list">
              ${(p.rules || []).map(r => html`<li>${r}</li>`)}
            </ul>
          </div>
        `)}
      </div>
    `;
  }

  #renderCachedPatterns(data) {
    const patterns = data?.cached_patterns || [];
    const busy = this._busy || data?.status === 'busy' || data?.status === 'waiting';
    return html`
      <div class="sap-section">
        <div class="sap-section-title">Approved Command Patterns (cached)</div>
        ${patterns.length === 0 ? html`<div class="sap-empty">No cached approval patterns</div>` : html`
          <ul class="sap-pattern-list">
            ${patterns.map(p => html`
              <li class="sap-pattern">
                <span class="sap-pattern-text">${p}</span>
                <button
                  class="dc-icon-btn sap-delete-pattern-btn"
                  title="Revoke this cached pattern"
                  aria-label="Delete pattern"
                  ?disabled=${busy}
                  @click=${() => this.#deletePattern(p)}
                >&#10005;</button>
              </li>
            `)}
          </ul>
        `}
      </div>
    `;
  }

  #renderPromoteForm() {
    if (!this._promoteRule) return nothing;
    const customPresets = this._presets.filter(p => p.source === 'custom');
    const busy = this._busy;
    const canPromote = (this._promotePreset && this._promotePreset !== '') || (this._newPresetName && this._newPresetName.trim());
    return html`
      <div class="sap-section">
        <div class="sap-section-title">Promote Rule to Custom Preset</div>
        <div class="sap-promote-row">
          <select class="sap-preset-select" ?disabled=${busy} @change=${(/** @type {any} */ e) => { this._promotePreset = e.target.value; }}>
            <option value="">New preset…</option>
            ${customPresets.map(p => html`<option value=${p.name}>${p.name}</option>`)}
          </select>
          <input
            type="text"
            class="sap-new-preset-input"
            placeholder="New preset name (e.g. backend_tests)"
            .value=${this._newPresetName}
            ?disabled=${busy}
            @input=${(/** @type {any} */ e) => { this._newPresetName = e.target.value; }}
          />
          <button class="sap-promote-btn" ?disabled=${busy || !canPromote} @click=${() => {
            const presetName = this._promotePreset || (this._newPresetName || '').trim();
            if (!presetName) return;
            void this.#promote(this._promoteRule, presetName);
          }}>Promote</button>
          <button class="sap-cancel-btn" @click=${() => this.#cancelPromote()}>Cancel</button>
        </div>
      </div>
    `;
  }

  #startPromote(rule) {
    this._promoteRule = rule;
    this._promotePreset = '';
    this._newPresetName = '';
  }

  #cancelPromote() {
    this._promoteRule = '';
    this._promotePreset = '';
    this._newPresetName = '';
  }

  #panel() {
    if (!this.open || !this.convId) return nothing;
    const data = this._data;
    return html`
      <div class="shell-approval-overlay">
        <div class="shell-approval-panel" role="dialog" aria-modal="true" aria-label="Shell auto-approval">
          <div class="sap-header">
            <h3>Shell Auto-Approval</h3>
            <button class="dc-icon-btn sap-close-btn" @click=${() => this._close()} title="Close shell approval panel" aria-label="Close shell approval panel">&times;</button>
          </div>
          <div class="sap-body">
            ${this._loading ? html`<div class="sap-loading">Loading…</div>` : nothing}
            ${this._error ? html`<div class="sap-error-banner">${this._error}</div>` : nothing}
            ${data ? html`
              ${this.#renderConfigGuidance(data)}
              ${this._promoteRule ? this.#renderPromoteForm() : nothing}
              ${this.#renderConversationRules(data)}
              ${this.#renderPresetRules(data)}
              ${this.#renderCachedPatterns(data)}
            ` : nothing}
          </div>
        </div>
      </div>
    `;
  }

  render() {
    // The panel renders into this._panelMount (document.body) via updated();
    // the host itself stays an empty anchor in its parent template.
    return nothing;
  }
}

customElements.define('shell-approval-panel', ShellApprovalPanel);
