import { LitElement, html, nothing } from 'lit';
import { subscribe as subscribeCanvas, resummon } from '../lib/canvas-state.js';

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
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.store?.removeEventListener('change', this.#onStoreChange);
    this.#canvasUnsub?.();
    this.#canvasUnsub = null;
  }

  connectedCallback() {
    super.connectedCallback();
    // Canvas resummon pill state (issue #1190): the control bar owns the
    // pill now, so subscribe to the shared canvas snapshot here.
    this.#canvasUnsub = subscribeCanvas((snap) => {
      this._canvasSnapshot = snap;
      this.requestUpdate();
    });
  }

  #onStoreChange = () => {
    this.#syncFromStore();
  };

  /** Latest canvas snapshot for the active conversation (pill visibility). */
  _canvasSnapshot = null;
  #canvasUnsub = null;

  #syncFromStore() {
    if (!this.store) return;
    this._activeModel = this.store.activeModel || '';
    this._availableModels = this.store.availableModels || [];
    this._defaultModel = this.store.defaultModel || '';
    this._activeMode = this.store.activeMode || 'default';
    this._availableModes = this.store.availableModes || [];
    this._contextUsage = this.store.contextUsage || 0;
    // The store uses 0 as the "no limit known" sentinel; `??` preserves it
    // (a bare `||` would coerce 0 to the 100000 default and mis-render usage).
    this._contextLimit = this.store.contextLimit ?? 100000;
    this._isBusy = Boolean(this.store.isBusy);
  }

  updated(changedProps) {
    if (changedProps.has('store')) {
      const oldStore = changedProps.get('store');
      if (oldStore) oldStore.removeEventListener('change', this.#onStoreChange);
      if (this.store) {
        // app.js assigns `store` after the element is connected, so the
        // listener must be registered here. The store populates availableModels/
        // availableModes asynchronously from a WebSocket message, so without
        // this the model/mode controls would never appear.
        this.store.addEventListener('change', this.#onStoreChange);
        this.#syncFromStore();
      }
    }

    // Sync model selector value if present
    const modelSelect = /** @type {HTMLSelectElement | null} */ (this.querySelector('#cc-model-select'));
    if (modelSelect) {
      const target = this._activeModel || this._defaultModel;
      if (target && modelSelect.value !== target) {
        modelSelect.value = target;
      }
    }

    // Sync mode selector value if present
    const modeSelect = /** @type {HTMLSelectElement | null} */ (this.querySelector('#cc-mode-select'));
    if (modeSelect) {
      const targetMode = this._activeMode;
      if (targetMode && modeSelect.value !== targetMode) {
        modeSelect.value = targetMode;
      }
    }
  }

  #handleModelChange(e) {
    const newModel = e.target.value;
    const current = this._activeModel || this._defaultModel;
    if (newModel && newModel !== current) {
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

  // Conversation action cluster (issue #1190): compact controls hosted in the
  // control bar, right of the mode selector. The context meter keeps the right
  // end of the strip via its own margin-left:auto.
  #handleTerminalClick() {
    const store = this.store;
    const convId = store?.currentConvId || '';
    if (!convId) return;
    store?.sendMessage?.('/terminal');
  }

  #handleSideQuestion() {
    this.dispatchEvent(new CustomEvent('open-side-question', { bubbles: true, composed: true }));
  }

  // The context meter summons the context inspector. The inspector is owned
  // by the sidebar, so we dispatch an event it listens for (mirroring set-model / set-mode).
  #handleContextClick() {
    this.dispatchEvent(new CustomEvent('toggle-context-inspector', {
      detail: { convId: this.store?.currentConvId || '' },
      bubbles: true,
      composed: true,
    }));
  }

  // The mode control row summons the per-conversation shell approval panel.
  // The sidebar owns the panel; it listens for this event on document (the
  // same pattern as toggle-context-inspector above).
  #handleShellApprovalClick() {
    this.dispatchEvent(new CustomEvent('toggle-shell-approval', {
      detail: { convId: this.store?.currentConvId || '' },
      bubbles: true,
      composed: true,
    }));
  }

  #getContextStatus() {
    if (this._contextLimit <= 0) return { pct: 0, formatted: '', cls: '', warn: false, danger: false };
    const pct = Math.round((this._contextUsage / this._contextLimit) * 100);
    const danger = pct >= 100;
    const warn = pct >= 80 && !danger;
    const cls = danger ? 'full' : warn ? 'warn' : '';
    return { pct: Math.min(100, pct), formatted: `${pct}%`, cls, warn, danger };
  }

  render() {
    const { formatted: contextPct, cls: contextCls } = this.#getContextStatus();
    const targetModel = this._activeModel || this._defaultModel;
    const modelUnconfigured = targetModel && !this._availableModels.includes(targetModel);
    const convId = this.store?.currentConvId || '';
    const canvas = this._canvasSnapshot;
    const showResummon = Boolean(
      convId && canvas && Array.isArray(canvas.tabs) && canvas.tabs.length > 0 && !canvas.visible,
    );

    return html`
      <!-- Model control -->
      ${this._availableModels.length > 0 ? html`
        <div class="control model-control">
          <label for="cc-model-select">Model</label>
          <select id="cc-model-select" .value=${targetModel} ?disabled=${this._isBusy} @change=${this.#handleModelChange}>
            ${modelUnconfigured ? html`
              <option value="${targetModel}" selected>⚠ ${targetModel} (not configured)</option>
            ` : nothing}
            ${this._availableModels.map(m => html`
              <option value="${m}" ?selected=${m === targetModel} .selected=${m === targetModel}>${m}</option>
            `)}
          </select>
        </div>
      ` : nothing}

      <!-- Mode control -->
      ${this._availableModes.length > 0 ? html`
        <div class="control mode-control">
          <label for="cc-mode-select">Mode</label>
          <select id="cc-mode-select" .value=${this._activeMode} ?disabled=${this._isBusy} @change=${this.#handleModeChange}>
            ${this._availableModes.map(m => html`
              <option value="${m.name}" ?selected=${m.name === this._activeMode} .selected=${m.name === this._activeMode} title="${m.description || ''}">${m.name}</option>
            `)}
          </select>
        </div>
      ` : nothing}

      <!-- Conversation actions (issue #1190): compact controls in the control
           bar, positioned right of the mode selector. The context meter below
           keeps the strip's right end via its own margin-left:auto.

           Issue #1192: the copy-conversation-menu moved OUT of this cluster
           into the context inspector raw pane (conversation export copy now
           lives there), so the cluster keeps the canvas resummon pill, the
           terminal button, and the side-question button.

           Issue #1263: the shell-approval gear lives here too — the cluster's
           compact 32px sizing is what keeps it level with its neighbours; in
           the mode control it kept the 44px floating-btn floor and looked
           oversized. -->
      ${convId ? html`
        <div class="control actions-control">
          ${showResummon && canvas ? html`
            <button
              type="button"
              class="canvas-resummon-pill dc-floating-btn"
              data-unread=${canvas.unreadDot ? 'true' : 'false'}
              title="Canvas"
              aria-label=${canvas.unreadDot ? 'Canvas (unread update)' : 'Canvas'}
              @click=${() => resummon()}
            >&#x1F4C4;<span class="resummon-label"> Canvas</span></button>
          ` : nothing}
          <button
            type="button"
            class="terminal-open-btn dc-floating-btn"
            title="Open a terminal"
            aria-label="Open a terminal"
            @click=${this.#handleTerminalClick}
          >&gt;_</button>
          <button
            type="button"
            class="side-question-action-btn dc-floating-btn"
            title="Ask side question"
            aria-label="Ask side question"
            @click=${this.#handleSideQuestion}
          >&#x2753;</button>
          <button
            type="button"
            class="shell-approval-open-btn dc-floating-btn"
            title="Shell auto-approval"
            aria-label="Open shell auto-approval panel"
            @click=${this.#handleShellApprovalClick}
          >&#9881;</button>
        </div>
      ` : nothing}

      <!-- Context usage control -->
      <div class="control context-control ${contextCls}" role="button" tabindex="0"
           title="Click for context details" @click=${this.#handleContextClick}
           @keydown=${(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.#handleContextClick(); }}}>
        <label>Context</label>
        <div class="context-usage-bar" id="cc-context-progress">
          <div class="context-usage-fill" style="width: ${this.#getContextStatus().pct}%"></div>
        </div>
        <span class="usage-label">${contextPct}</span>
      </div>
    `;
  }
}

customElements.define('chat-controls', ChatControls);
