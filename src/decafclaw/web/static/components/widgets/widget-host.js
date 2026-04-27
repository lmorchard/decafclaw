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
    submitted: { type: Boolean },
    response: { type: Object, attribute: false },
    mode: { type: String },
    _state: { type: String, state: true },  // 'loading' | 'ready' | 'error'
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.widgetType = '';
    this.data = null;
    this.fallbackText = '';
    this.submitted = false;
    /** @type {object|null} */
    this.response = null;
    this.mode = 'inline';
    this._lastMode = 'inline';
    this._state = 'loading';
    /** @type {Element|null} */
    this._child = null;
    this._lastType = null;
    this._lastData = null;
    this._lastSubmitted = false;
    this._lastResponse = null;
  }

  updated(changed) {
    if (changed.has('widgetType') && this.widgetType !== this._lastType) {
      this._lastType = this.widgetType;
      this._loadAndMount();
    } else if (this._child) {
      const child = /** @type {any} */ (this._child);
      if (changed.has('data') && this.data !== this._lastData) {
        this._lastData = this.data;
        child.data = this.data;
      }
      if (changed.has('submitted') && this.submitted !== this._lastSubmitted) {
        this._lastSubmitted = this.submitted;
        child.submitted = this.submitted;
      }
      if (changed.has('response') && this.response !== this._lastResponse) {
        this._lastResponse = this.response;
        child.response = this.response;
      }
      if (changed.has('mode') && this.mode !== this._lastMode) {
        this._lastMode = this.mode;
        child.mode = this.mode;
      }
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
    /** @type {any} */ (el).submitted = this.submitted;
    /** @type {any} */ (el).response = this.response;
    this._lastData = this.data;
    this._lastSubmitted = this.submitted;
    this._lastResponse = this.response;
    /** @type {any} */ (el).mode = this.mode;
    this._lastMode = this.mode;
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
