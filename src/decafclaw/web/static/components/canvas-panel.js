import { LitElement, html } from 'lit';
import { subscribe, currentSnapshot, dismiss, getActiveConvId } from '../lib/canvas-state.js';
import './widgets/widget-host.js';

/**
 * Canvas panel — right-side surface displaying the current canvas tab.
 *
 * Subscribes to canvas-state for snapshot updates. Toggles
 * #canvas-main / #canvas-resize-handle visibility based on whether the
 * snapshot's `visible` flag is set (state exists AND not dismissed).
 *
 * On mobile (≤639px), opening the canvas auto-closes #wiki-main —
 * see canvas.css and the sidebar-tab-change handler in app.js.
 */
export class CanvasPanel extends LitElement {
  static properties = {
    _snapshot: { state: true },
  };

  constructor() {
    super();
    this._snapshot = currentSnapshot();
    /** @type {(() => void) | null} */
    this._unsubscribe = null;
  }

  createRenderRoot() { return this; }

  connectedCallback() {
    super.connectedCallback();
    this._unsubscribe = subscribe(snap => {
      this._snapshot = snap;
      this._reflectVisibility();
    });
    this._reflectVisibility();
  }

  disconnectedCallback() {
    if (this._unsubscribe) this._unsubscribe();
    super.disconnectedCallback();
  }

  _reflectVisibility() {
    const wrap = document.getElementById('canvas-main');
    const handle = document.getElementById('canvas-resize-handle');
    if (!wrap || !handle) return;
    const visible = this._snapshot.visible;
    wrap.classList.toggle('hidden', !visible);
    handle.classList.toggle('hidden', !visible);
    if (visible) {
      // Mobile: opening canvas closes wiki.
      if (window.matchMedia('(max-width: 639px)').matches) {
        document.getElementById('wiki-main')?.classList.add('hidden');
      }
    }
  }

  _onClose() { dismiss(); }

  _onOpenInTab() {
    const id = getActiveConvId();
    if (!id) return;
    window.open(`/canvas/${encodeURIComponent(id)}`, '_blank', 'noopener');
  }

  render() {
    const tab = this._snapshot.tab;
    if (!tab) {
      return html`<div class="canvas-empty">No canvas content yet.</div>`;
    }
    return html`
      <header class="canvas-header">
        <span class="canvas-label">${tab.label || 'Canvas'}</span>
        <span class="canvas-spacer"></span>
        <button class="canvas-btn" type="button"
                title="Open in new tab" aria-label="Open canvas in new tab"
                @click=${this._onOpenInTab}>↗</button>
        <button class="canvas-btn canvas-close" type="button"
                title="Close" aria-label="Close canvas panel"
                @click=${this._onClose}>×</button>
      </header>
      <main class="canvas-body">
        <dc-widget-host
          .widgetType=${tab.widget_type}
          .data=${tab.data}
          .mode=${'canvas'}
          fallbackText="Canvas widget unavailable">
        </dc-widget-host>
      </main>
    `;
  }
}

customElements.define('canvas-panel', CanvasPanel);
