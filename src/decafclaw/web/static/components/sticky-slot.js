import { LitElement, html, nothing } from 'lit';
import { subscribe, currentSnapshot, toggleCollapsed } from '../lib/sticky-state.js';
import './widgets/widget-host.js';

/**
 * Single-slot pinned widget above the chat input. Expanded shows the widget;
 * collapsed shows a one-line summary. Hidden when nothing is pinned.
 */
class StickySlot extends LitElement {
  // Render into light DOM so global styles/styles/sticky.css apply (match canvas-panel).
  createRenderRoot() { return this; }

  static properties = { _snap: { state: true } };

  constructor() {
    super();
    this._snap = currentSnapshot();
    /** @type {(() => void)|null} */
    this._unsub = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._unsub = subscribe((snap) => { this._snap = snap; });
  }

  disconnectedCallback() {
    if (this._unsub) this._unsub();
    super.disconnectedCallback();
  }

  _summaryText() {
    const d = this._snap.data || {};
    if (typeof d.summary === 'string' && d.summary) return d.summary;
    if (typeof d.title === 'string' && d.title) return d.title;
    return (this._snap.widgetType || '').replace(/_/g, ' ');
  }

  render() {
    if (!this._snap.visible) return nothing;
    const collapsed = this._snap.collapsed;
    return html`
      <div class="sticky-slot ${collapsed ? 'sticky-collapsed' : ''}">
        <div class="sticky-header" @click=${() => toggleCollapsed()}>
          <span class="sticky-summary">${this._summaryText()}</span>
          <button type="button" class="sticky-toggle dc-icon-btn" aria-label="Toggle sticky panel"
                  @click=${(e) => { e.stopPropagation(); toggleCollapsed(); }}>
            ${collapsed ? '▸' : '▾'}
          </button>
        </div>
        ${collapsed ? nothing : html`
          <div class="sticky-body">
            <dc-widget-host
              .widgetType=${this._snap.widgetType}
              .data=${this._snap.data}
              .mode=${'sticky'}
            ></dc-widget-host>
          </div>`}
      </div>`;
  }
}

customElements.define('sticky-slot', StickySlot);
