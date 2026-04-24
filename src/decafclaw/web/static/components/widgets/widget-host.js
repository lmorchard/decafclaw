import { LitElement, html, nothing } from 'lit';

import { getCatalog, getDescriptor } from '../../lib/widget-catalog.js';

/**
 * Generic host for rendering a widget by type.
 *
 * Dynamic-imports the widget's JS module by URL, then creates the
 * registered `<dc-widget-{type}>` element and sets `.data` on it. Memoizes
 * imports per URL so repeat renders don't re-fetch. Falls back to a
 * `<pre>` with fallbackText (the tool's raw result text) for unknown
 * types or failed imports.
 *
 * Phase-2 input widgets dispatch a `widget-response` CustomEvent with
 * `{bubbles: true, composed: true}` from inside the host; the event
 * bubbles past dc-widget-host naturally — callers listen on the host
 * (or any ancestor). No manual re-dispatch is needed.
 */

/** @type {Map<string, Promise<any>>} */
const _importCache = new Map();

function _memoizedImport(url) {
  let p = _importCache.get(url);
  if (!p) {
    p = import(/* @vite-ignore */ url);
    _importCache.set(url, p);
  }
  return p;
}

export class WidgetHost extends LitElement {
  static properties = {
    widgetType: { type: String },
    data: { type: Object },
    fallbackText: { type: String },
    _state: { type: String, state: true },  // 'loading' | 'ready' | 'error'
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.widgetType = '';
    this.data = null;
    this.fallbackText = '';
    this._state = 'loading';
    /** @type {Element|null} */
    this._child = null;
    this._lastType = null;
    this._lastData = null;
  }

  updated(changed) {
    if (changed.has('widgetType') && this.widgetType !== this._lastType) {
      this._lastType = this.widgetType;
      this._loadAndMount();
    } else if (changed.has('data') && this._child && this.data !== this._lastData) {
      this._lastData = this.data;
      // Update live — widget components re-render on .data change.
      /** @type {any} */ (this._child).data = this.data;
    }
  }

  async _loadAndMount() {
    this._state = 'loading';
    // Ensure catalog is loaded, then look up the descriptor.
    await getCatalog();
    const desc = getDescriptor(this.widgetType);
    if (!desc) {
      console.warn(`[widget-host] unknown widget type: ${this.widgetType}`);
      this._state = 'error';
      return;
    }
    try {
      await _memoizedImport(desc.js_url);
    } catch (err) {
      console.warn(`[widget-host] import failed for ${this.widgetType}:`, err);
      this._state = 'error';
      return;
    }
    const tag = `dc-widget-${this.widgetType.replace(/_/g, '-')}`;
    if (!customElements.get(tag)) {
      console.warn(`[widget-host] module loaded but ${tag} not registered`);
      this._state = 'error';
      return;
    }
    // Tear down any previous child, mount the new one.
    if (this._child) this._child.remove();
    const el = document.createElement(tag);
    /** @type {any} */ (el).data = this.data;
    this._lastData = this.data;
    this._child = el;
    // Render() will append it below.
    this._state = 'ready';
  }

  _renderChild() {
    if (!this._child) return nothing;
    return html`${this._child}`;
  }

  render() {
    if (this._state === 'error') {
      return html`
        <div class="widget-host widget-host--fallback">
          <div class="widget-host__note">(widget '${this.widgetType}' unavailable)</div>
          ${this.fallbackText ? html`<pre>${this.fallbackText}</pre>` : nothing}
        </div>
      `;
    }
    if (this._state === 'loading') {
      return html`<div class="widget-host widget-host--loading"><small>Loading widget…</small></div>`;
    }
    return html`<div class="widget-host">${this._renderChild()}</div>`;
  }
}

customElements.define('dc-widget-host', WidgetHost);
