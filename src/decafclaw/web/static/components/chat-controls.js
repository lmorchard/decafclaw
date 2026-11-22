import { LitElement, html, nothing } from 'lit';
import { subscribe as subscribeCanvas, resummon } from '../lib/canvas-state.js';
import { SIDE_EFFECT_COMMANDS } from '../lib/constants.js';
import './model-picker.js';

/** @typedef {{ provider: string, model: string }} ModelConfig */

export class ChatControls extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    _activeModel: { type: String, state: true },
    _modelGroups: { type: Array, state: true },
    _defaultModel: { type: String, state: true },
    _contextUsage: { type: Number, state: true },
    _contextLimit: { type: Number, state: true },
    _isBusy: { type: Boolean, state: true },
    _isReadOnly: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.store = null;
    this._activeModel = '';
    this._modelGroups = [];
    this._defaultModel = '';
    this._contextUsage = 0;
    this._contextLimit = 100000;
    this._isBusy = false;
    this._isReadOnly = false;
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
    this._modelGroups = this.store.modelGroups || [];
    this._defaultModel = this.store.defaultModel || '';
    this._contextUsage = this.store.contextUsage || 0;
    // The store uses 0 as the "no limit known" sentinel; `??` preserves it
    // (a bare `||` would coerce 0 to the 100000 default and mis-render usage).
    this._contextLimit = this.store.contextLimit ?? 100000;
    this._isBusy = Boolean(this.store.isBusy);
    this._isReadOnly = Boolean(this.store.isReadOnly);
  }

  updated(changedProps) {
    if (changedProps.has('store')) {
      const oldStore = changedProps.get('store');
      if (oldStore) oldStore.removeEventListener('change', this.#onStoreChange);
      if (this.store) {
        // app.js assigns `store` after the element is connected, so the
        // listener must be registered here. The store populates availableModels
        // asynchronously from a WebSocket message, so without this the model
        // controls would never appear.
        this.store.addEventListener('change', this.#onStoreChange);
        this.#syncFromStore();
      }
    }
  }

  // Conversation action cluster (issue #1190): compact controls hosted in the
  // control bar. The context meter keeps the right end of the strip via its
  // own margin-left:auto.
  #handleTerminalClick() {
    const store = this.store;
    const convId = store?.currentConvId || '';
    if (!convId) return;
    store?.sendMessage?.(SIDE_EFFECT_COMMANDS.TERMINAL);
  }

  #handleSideQuestion() {
    this.dispatchEvent(new CustomEvent('open-side-question', { bubbles: true, composed: true }));
  }

  // The context meter summons the context inspector. The inspector is owned
  // by the sidebar, so we dispatch an event it listens for.
  #handleContextClick() {
    this.dispatchEvent(new CustomEvent('toggle-context-inspector', {
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
    const convId = this.store?.currentConvId || '';
    const canvas = this._canvasSnapshot;
    const showResummon = Boolean(
      convId && canvas && Array.isArray(canvas.tabs) && canvas.tabs.length > 0 && !canvas.visible,
    );

    return html`
      <!-- Model control -->
      ${this._modelGroups.length > 0 ? html`
        <div class="control model-control">
          <label>Model</label>
          <model-picker
            .groups=${this._modelGroups}
            .activeModel=${this._activeModel}
            .defaultModel=${this._defaultModel}
            ?disabled=${this._isBusy || this._isReadOnly}
            .convId=${convId}
          ></model-picker>
        </div>
      ` : nothing}

      <!-- Conversation actions (issue #1190): compact controls in the control
           bar. The context meter below keeps the strip's right end via its own
           margin-left:auto.

           Issue #1192: the copy-conversation-menu moved OUT of this cluster
           into the context inspector raw pane (conversation export copy now
           lives there), so the cluster keeps the canvas resummon pill, the
           terminal button, and the side-question button. -->
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
