import { LitElement, html, nothing } from 'lit';
import './context-inspector.js';
import './model-picker.js';

/**
 * The conversation controls: the model picker, the context usage meter, and
 * the context inspector the meter summons. Extracted from the sidebar shell so
 * the controls are not coupled to list rendering (#1407).
 *
 * The composer's control strip (`chat-controls`) hosts its own model picker and
 * context meter; when it is present this element renders only the inspector,
 * which that strip's meter (and the composer's conversation menu) summon via
 * `toggle-context-inspector`. The parent {@link ConversationSidebar} forwards
 * the shared store; this component reads straight from it on change events
 * rather than mirroring values into reactive state (#1408).
 */
export class ConversationControls extends LitElement {
  static properties = {
    store: { type: Object, attribute: false },
    _contextInspectorOpen: { type: Boolean, state: true },
    _contextVersion: { type: Number, state: true },
  };

  createRenderRoot() { return this; }

  // Change detection for #onStoreChange only — the rendered values are read
  // straight from the store, never mirrored into reactive state (#1408). The
  // #last* fields detect transitions; they are bookkeeping, not rendered state.
  /** @type {string|null} */
  #lastConvId = null;
  /** @type {number} */
  #lastContextUsage = 0;

  constructor() {
    super();
    /** @type {import("../lib/conversation-store.js").ConversationStore | null} */
    this.store = null;
    this._contextInspectorOpen = false;
    this._contextVersion = 0;
    // The composer's control strip hosts its own model picker and context
    // meter; when present, this element renders only the inspector.
    /** @type {boolean} */
    this._hasControlsStrip = document.querySelector('chat-controls') !== null;
  }

  connectedCallback() {
    super.connectedCallback();
    // The context meter (here or in chat-controls) and the composer's
    // conversation menu summon the inspector with this event; the inspector is
    // owned by this element, so it listens on document.
    this._onToggleInspector = () => {
      this._contextInspectorOpen = !this._contextInspectorOpen;
    };
    document.addEventListener('toggle-context-inspector', this._onToggleInspector);
    // The store is the single source of truth: render() reads its getters
    // directly, so this handler only runs the per-change side effects and
    // asks Lit to re-render (#1408).
    this._onStoreChange = () => {
      const newId = this.store?.currentConvId;
      if (newId !== this.#lastConvId) {
        // Switching conversations closes the inspector: its diagnostics belong
        // to the previous conversation.
        this._contextInspectorOpen = false;
      }
      this.#lastConvId = newId;
      // Bump the inspector's version only when the usage actually changed,
      // so a turn's streaming frames do not refetch diagnostics.
      const newUsage = this.store?.contextUsage || 0;
      if (newUsage !== this.#lastContextUsage) {
        this._contextVersion++;
        this.#lastContextUsage = newUsage;
      }
      this.requestUpdate();
    };
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener('toggle-context-inspector', this._onToggleInspector);
    this.store?.removeEventListener('change', this._onStoreChange);
  }

  /** @param {Map} changedProps */
  updated(changedProps) {
    if (changedProps.has('store')) {
      const oldStore = changedProps.get('store');
      if (oldStore) oldStore.removeEventListener('change', this._onStoreChange);
      if (this.store) {
        // app.js assigns `store` after the element is connected, so the
        // listener must be registered here.
        this.store.addEventListener('change', this._onStoreChange);
        this.#lastConvId = null; // force the first change to run side effects
        this._onStoreChange();
      }
    }
  }

  /** Selected conversation id, read straight from the store ('' when none). */
  get #activeId() {
    return this.store?.currentConvId || '';
  }

  // The context meter summons the inspector. It dispatches the same event as
  // chat-controls' meter; this element's document listener owns the toggle.
  #handleContextClick() {
    this.dispatchEvent(new CustomEvent('toggle-context-inspector', {
      detail: { convId: this.#activeId },
      bubbles: true,
      composed: true,
    }));
  }

  render() {
    return html`
      ${!this._hasControlsStrip && (this.store?.availableModels || []).length > 0 ? html`
        <div class="model-picker">
          <label class="model-picker-label">Model</label>
          <model-picker
            .groups=${this.store?.modelGroups || []}
            .activeModel=${this.store?.activeModel || ''}
            .defaultModel=${this.store?.defaultModel || ''}
            ?disabled=${Boolean(this.store?.isBusy || this.store?.isReadOnly)}
            .convId=${this.#activeId}
          ></model-picker>
        </div>
      ` : nothing}
      ${!this._hasControlsStrip && (this.store?.contextLimit || 0) > 0 ? html`
        ${(() => {
          const limit = this.store?.contextLimit || 0;
          const pct = Math.round((this.store?.contextUsage || 0) / limit * 100);
          const cls = pct >= 100 ? 'full' : pct >= 80 ? 'warn' : '';
          return html`
            <div class="context-usage ${cls}" title="Click for details"
                 style="cursor:pointer;position:relative"
                 role="button"
                 tabindex="0"
                 aria-expanded=${this._contextInspectorOpen}
                 @click=${() => { this.#handleContextClick(); }}
                 @keydown=${(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.#handleContextClick(); }}}>
              <div class="context-usage-label">
                <span>Context</span>
                <span>${pct}%</span>
              </div>
              <div class="context-usage-bar">
                <div class="context-usage-fill" style="width: ${Math.min(100, pct)}%"></div>
              </div>
            </div>
          `;
        })()}
      ` : nothing}
      <context-inspector
        .convId=${this.#activeId}
        .open=${this._contextInspectorOpen}
        .contextVersion=${this._contextVersion}
        @close=${() => { this._contextInspectorOpen = false; }}
      ></context-inspector>
    `;
  }
}

customElements.define('conversation-controls', ConversationControls);
