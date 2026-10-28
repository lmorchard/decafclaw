import { LitElement, html, nothing } from 'lit';

/** @typedef {{ name: string, description?: string }} ModeConfig */
/** @typedef {{ provider: string, model: string }} ModelConfig */

export class ChatControls extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    _activeModel: { type: String, state: true },
    _availableModels: { type: Array, state: true },
    _defaultModel: { type: String, state: true },
    _activeMode: { type: String, state: true },
    _availableModes: { type: Array, state: true },
    _contextUsage: { type: Number, state: true },
    _contextLimit: { type: Number, state: true },
    _isBusy: { type: Boolean, state: true },
    _collapsed: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.store = null;
    this._activeModel = '';
    this._availableModels = [];
    this._defaultModel = '';
    this._activeMode = 'default';
    this._availableModes = [];
    this._contextUsage = 0;
    this._contextLimit = 100000;
    this._isBusy = false;
    this._collapsed = true; // collapsed by default on mobile, auto-expanded on desktop
    this.#updateCollapseState();
  }

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener('resize', this.#updateCollapseState);
    if (this.store) {
      this.#syncFromStore();
      this.store.addEventListener('change', this.#onStoreChange);
    }
    this.#updateCollapseState();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('resize', this.#updateCollapseState);
    this.store?.removeEventListener('change', this.#onStoreChange);
  }

  static get observedAttributes() { return ['collapsed']; }

  attributeChangedCallback(name, _oldValue, newValue) {
    if (name === 'collapsed') {
      this._collapsed = newValue !== null;
    }
  }

  #updateCollapseState = () => {
    // Desktop: expand by default. Mobile (<640px): collapse to button.
    const shouldExpand = window.innerWidth >= 640;
    if (this._collapsed !== shouldExpand) {
      this._collapsed = shouldExpand;
      this.toggleAttribute('collapsed', this._collapsed);
    }
  };

  #onStoreChange = () => {
    this.#syncFromStore();
  };

  #syncFromStore() {
    if (!this.store) return;
    this._activeModel = this.store.activeModel || '';
    this._availableModels = this.store.availableModels || [];
    this._defaultModel = this.store.defaultModel || '';
    this._activeMode = this.store.activeMode || 'default';
    this._availableModes = this.store.availableModes || [];
    this._contextUsage = this.store.contextUsage || 0;
    this._contextLimit = this.store.contextLimit || 100000;
    this._isBusy = Boolean(this.store.isBusy);
  }

  updated(changedProps) {
    if (changedProps.has('store') && this.store) {
      this.#syncFromStore();
    }
    // Keep attribute in sync with state
    this.toggleAttribute('collapsed', this._collapsed);
  }

  #toggleCollapse() {
    this._collapsed = !this._collapsed;
    this.requestUpdate();
  }

  #handleModelChange(e) {
    const newModel = e.target.value;
    if (newModel && newModel !== this._activeModel) {
      this.dispatchEvent(new CustomEvent('set-model', {
        detail: { model: newModel },
        bubbles: true,
        composed: true,
      }));
    }
  }

  #handleModeChange(e) {
    const newMode = e.target.value;
    if (newMode && newMode !== this._activeMode) {
      this.dispatchEvent(new CustomEvent('set-mode', {
        detail: { mode: newMode },
        bubbles: true,
        composed: true,
      }));
    }
  }

  #formatContextUsage() {
    if (this._contextLimit <= 0) return '';
    const pct = Math.min(100, Math.round((this._contextUsage / this._contextLimit) * 100));
    let color = 'success';
    if (pct >= 90) color = 'error';
    else if (pct >= 75) color = 'warning';
    return `${pct}%`;
  }

  render() {
    const contextPct = this.#formatContextUsage();
    const modelUnconfigured = this._activeModel && !this._availableModels.includes(this._activeModel);
    // Use attribute for mobile collapsed state (set by #updateCollapseState)
    const isMobileCollapsed = this.hasAttribute('collapsed');
    // Desktop: show full controls inline. Mobile: collapse to button group.
    return html`
      <div class="controls-strip" ?collapsed=${isMobileCollapsed}>
        <!-- Model control -->
        ${this._availableModels.length > 0 ? html`
          <div class="control model-control">
            ${!isMobileCollapsed ? html`
              <label for="cc-model-select">Model</label>
              <select id="cc-model-select" ?disabled=${this._isBusy} @change=${this.#handleModelChange}>
                ${modelUnconfigured ? html`
                  <option value="${this._activeModel}" selected>⚠ ${this._activeModel} (not configured)</option>
                ` : nothing}
                ${this._availableModels.map(m => html`
                  <option value="${m}" ?selected=${m === this._activeModel}>${m}</option>
                `)}
              </select>
            ` : html`
              <span class="control-icon" title="Model: ${this._activeModel || '—'}">🤖</span>
            `}
          </div>
        ` : nothing}

        <!-- Mode control -->
        ${this._availableModes.length > 0 ? html`
          <div class="control mode-control">
            ${!isMobileCollapsed ? html`
              <label for="cc-mode-select">Mode</label>
              <select id="cc-mode-select" ?disabled=${this._isBusy} @change=${this.#handleModeChange}>
                ${this._availableModes.map(m => html`
                  <option value="${m.name}" ?selected=${m.name === this._activeMode} title="${m.description || ''}">${m.name}</option>
                `)}
              </select>
            ` : html`
              <span class="control-icon" title="Mode: ${this._activeMode}">🎯</span>
            `}
          </div>
        ` : nothing}

        <!-- Context usage control -->
        <div class="control context-control">
          ${!isMobileCollapsed ? html`
            <label>Context</label>
            <progress id="cc-context-progress" value="${this._contextUsage}" max="${this._contextLimit}"></progress>
            <span class="usage-label">${contextPct}</span>
          ` : html`
            <span class="control-icon context-icon ${contextPct === 'error' ? 'danger' : ''}" title="Context: ${this._contextUsage}/${this._contextLimit} (${contextPct})">📊</span>
          `}
        </div>

        <!-- Collapse toggle button (always visible on mobile when expanded) -->
        ${!isMobileCollapsed ? html`
          <button class="collapse-toggle dc-icon-btn" @click=${this.#toggleCollapse} title="Collapse controls">✕</button>
        ` : nothing}
      </div>
    `;
  }
}

customElements.define('chat-controls', ChatControls);
