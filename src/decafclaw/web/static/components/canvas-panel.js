import { LitElement, html } from 'lit';
import {
  subscribe, currentSnapshot, dismiss, getActiveConvId,
  switchToTab, closeTabFromUi,
} from '../lib/canvas-state.js';
import { getDescriptor } from '../lib/widget-catalog.js';
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
 *
 * Phase 4: renders a horizontal tab strip above the header with
 * ARIA tab pattern (tablist/tab/tabpanel/region) and keyboard nav
 * (ArrowLeft/Right, Home/End, Delete).
 */
export class CanvasPanel extends LitElement {
  static properties = {
    _snapshot: { state: true },
    _mobileListOpen: { state: true },
  };

  constructor() {
    super();
    this._snapshot = currentSnapshot();
    this._mobileListOpen = false;
    /** @type {(() => void) | null} */
    this._unsubscribe = null;
  }

  createRenderRoot() { return this; }

  connectedCallback() {
    super.connectedCallback();
    this.setAttribute('role', 'region');
    this.setAttribute('aria-label', 'Canvas');
    this._unsubscribe = subscribe(snap => {
      this._snapshot = snap;
      this._reflectVisibility();
    });
    this._reflectVisibility();
    // Resize handle lives outside this component in index.html — set ARIA once.
    const resizeHandle = document.getElementById('canvas-resize-handle');
    if (resizeHandle) {
      resizeHandle.setAttribute('role', 'separator');
      resizeHandle.setAttribute('aria-orientation', 'vertical');
      resizeHandle.setAttribute('aria-label', 'Resize canvas panel');
    }
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
    // Use canvas-collapsed (transform/width transition) instead of the
    // global .hidden (display:none) so the panel can animate in.
    wrap.classList.toggle('canvas-collapsed', !visible);
    handle.classList.toggle('hidden', !visible);
    if (visible && window.matchMedia('(max-width: 639px)').matches) {
      document.getElementById('wiki-main')?.classList.add('hidden');
    }
  }

  _onClose() { dismiss(); }

  /** @param {string} tabId */
  _openTabInWindow(tabId) {
    const convId = getActiveConvId();
    if (!convId) return;
    const url = `/canvas/${encodeURIComponent(convId)}/${encodeURIComponent(tabId)}`;
    window.open(url, '_blank', 'noopener');
  }

  /** @param {string} tabId @param {string} label */
  _confirmAndClose(tabId, label) {
    const name = label || tabId;
    if (!window.confirm(`Close tab "${name}"?`)) return;
    closeTabFromUi(tabId);
  }

  /** @param {KeyboardEvent} e */
  _onTabKeyDown(e) {
    const tabs = this._snapshot.tabs;
    if (tabs.length === 0) return;
    const currentIdx = tabs.findIndex(t => t.id === this._snapshot.activeTabId);
    let nextIdx = currentIdx;
    if (e.key === 'ArrowLeft') nextIdx = Math.max(0, currentIdx - 1);
    else if (e.key === 'ArrowRight') nextIdx = Math.min(tabs.length - 1, currentIdx + 1);
    else if (e.key === 'Home') nextIdx = 0;
    else if (e.key === 'End') nextIdx = tabs.length - 1;
    else if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      const target = /** @type {HTMLElement | null} */ (e.target);
      const targetId = target?.dataset?.tabId;
      if (targetId) closeTabFromUi(targetId);
      return;
    } else if (e.key === 'Enter' || e.key === ' ') {
      // Activate focused tab. `<div role="tab">` doesn't get this for free.
      e.preventDefault();
      const target = /** @type {HTMLElement | null} */ (e.target);
      const targetId = target?.dataset?.tabId;
      if (targetId) switchToTab(targetId);
      return;
    } else {
      return;
    }
    e.preventDefault();
    if (nextIdx !== currentIdx && tabs[nextIdx]) {
      switchToTab(tabs[nextIdx].id);
      // Move focus to the newly active tab.
      requestAnimationFrame(() => {
        const btn = this.querySelector(`[data-tab-id="${tabs[nextIdx].id}"]`);
        if (btn instanceof HTMLElement) btn.focus();
      });
    }
  }

  _toggleMobileList() {
    this._mobileListOpen = !this._mobileListOpen;
  }

  _onMobileTabClick(tabId) {
    switchToTab(tabId);
    this._mobileListOpen = false;
  }

  _renderMobileList() {
    if (!this._mobileListOpen) return html``;
    return html`
      <div class="canvas-mobile-list" role="tablist" aria-label="Canvas tabs">
        ${this._snapshot.tabs.map(t => html`
          <div class="canvas-mobile-tab ${t.id === this._snapshot.activeTabId ? 'active' : ''}"
               role="tab"
               id=${`canvas-mobile-tab-${t.id}`}
               aria-selected=${t.id === this._snapshot.activeTabId ? 'true' : 'false'}
               aria-controls="canvas-tabpanel"
               tabindex=${t.id === this._snapshot.activeTabId ? 0 : -1}
               @click=${() => this._onMobileTabClick(t.id)}>
            <span class="canvas-mobile-tab-label">${t.label || t.id}</span>
            <button class="canvas-mobile-tab-open" type="button"
                    aria-label="Open tab ${t.label || t.id} in new window"
                    @click=${(e) => { e.stopPropagation(); this._openTabInWindow(t.id); }}>↗</button>
            <button class="canvas-mobile-tab-close" type="button"
                    aria-label="Close tab ${t.label || t.id}"
                    @click=${(e) => { e.stopPropagation(); this._confirmAndClose(t.id, t.label); }}>×</button>
          </div>
        `)}
      </div>
    `;
  }

  _renderTabStrip() {
    const tabs = this._snapshot.tabs;
    if (tabs.length === 0) return html``;
    return html`
      <div class="canvas-tab-strip" role="tablist" aria-label="Canvas tabs">
        ${tabs.map(t => html`
          <div class="canvas-tab ${t.id === this._snapshot.activeTabId ? 'active' : ''}"
               role="tab"
               id=${`canvas-tab-${t.id}`}
               tabindex=${t.id === this._snapshot.activeTabId ? 0 : -1}
               aria-selected=${t.id === this._snapshot.activeTabId ? 'true' : 'false'}
               aria-controls="canvas-tabpanel"
               data-tab-id=${t.id}
               @click=${() => switchToTab(t.id)}
               @keydown=${(e) => this._onTabKeyDown(e)}>
            <span class="canvas-tab-label">${t.label || t.id}</span>
            <button class="canvas-tab-open" type="button"
                    title="Open in new window"
                    aria-label="Open tab ${t.label || t.id} in new window"
                    @click=${(e) => { e.stopPropagation(); this._openTabInWindow(t.id); }}>↗</button>
            <button class="canvas-tab-close" type="button"
                    title="Close tab"
                    aria-label="Close tab ${t.label || t.id}"
                    @click=${(e) => { e.stopPropagation(); this._confirmAndClose(t.id, t.label); }}>×</button>
          </div>
        `)}
      </div>
    `;
  }

  render() {
    const active = this._snapshot.activeTab;
    if (!active) {
      return html`<div class="canvas-empty">No canvas content yet.</div>`;
    }
    const descriptor = getDescriptor(active.widget_type);
    return html`
      <div class="canvas-tab-strip-desktop">
        ${this._renderTabStrip()}
        <button class="canvas-btn canvas-close-panel" type="button"
                title="Close canvas panel" aria-label="Close canvas panel"
                @click=${this._onClose}>×</button>
      </div>
      <header class="canvas-header canvas-header-mobile dc-overlay-header">
        <button class="canvas-btn canvas-mobile-disclosure dc-floating-btn" type="button"
                aria-label="Show tab list" aria-expanded=${this._mobileListOpen ? 'true' : 'false'}
                @click=${this._toggleMobileList}>
          Tabs (${this._snapshot.tabs.length}) ▼
        </button>
        <span class="canvas-spacer"></span>
        <button class="canvas-close dc-overlay-close-x" type="button"
                title="Close" aria-label="Close canvas panel"
                @click=${this._onClose}>×</button>
      </header>
      ${this._renderMobileList()}
      <main class="canvas-body" id="canvas-tabpanel"
            role="tabpanel" aria-labelledby=${`canvas-tab-${active.id}`}>
        <dc-widget-host
          .widgetType=${active.widget_type}
          .descriptor=${descriptor}
          .data=${active.data}
          .mode=${'canvas'}
          fallbackText="Canvas widget unavailable">
        </dc-widget-host>
      </main>
    `;
  }
}

customElements.define('canvas-panel', CanvasPanel);
