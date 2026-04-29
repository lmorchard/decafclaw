import { LitElement, html } from 'lit';

const INLINE_HEIGHT = '24rem';

/**
 * iframe_sandbox widget. Renders agent-authored HTML inside a sandboxed
 * iframe via `srcdoc`. The backend has already wrapped `data.body` into a
 * full HTML document with a locked-down CSP meta tag (see
 * `widgets.py::_normalize_iframe_sandbox`); this component is a dumb
 * renderer.
 *
 * Sandbox attributes:
 *   - `allow-scripts`           — required for the agent's inline scripts
 *   - NO `allow-same-origin`    — denies access to parent origin's storage
 *                                  and prevents the iframe from removing its
 *                                  own sandbox attribute
 *   - NO `allow-forms`, `allow-top-navigation`, `allow-popups`, `allow-modals`
 *
 * The combination forces the iframe into the "null" origin: scripts run,
 * but cannot reach parent cookies/storage, cannot make same-origin
 * requests, and cannot navigate the top frame.
 *
 * Modes set by the host via `mode`:
 *   inline → fixed height (24rem)
 *   canvas → fills available height
 *
 * Data shape (after backend normalization): { html: string, body?, title? }
 */
export class IframeSandboxWidget extends LitElement {
  static properties = {
    data: { type: Object },
    mode: { type: String },
  };

  constructor() {
    super();
    this.data = {};
    this.mode = 'inline';
  }

  createRenderRoot() { return this; }

  render() {
    const srcdoc = (this.data && typeof this.data.html === 'string')
      ? this.data.html
      : '';
    const isCanvas = this.mode === 'canvas';
    // Canvas mode: iframe flexes to fill remaining height after the title
    // header (if any), so it always reaches the bottom of whatever surface
    // hosts it (embedded canvas panel or standalone /canvas/{id} page).
    // Inline mode: fixed height — keeps the widget compact in chat bubbles.
    const iframeStyle = isCanvas
      ? 'flex: 1 1 auto; min-height: 12rem; width: 100%; border: 1px solid var(--border-color, #ccc); border-radius: 4px; background: white;'
      : `height: ${INLINE_HEIGHT}; width: 100%; border: 1px solid var(--border-color, #ccc); border-radius: 4px; background: white;`;
    const wrapperClass = isCanvas
      ? 'iframe-sandbox iframe-sandbox-canvas'
      : 'iframe-sandbox iframe-sandbox-inline';
    const wrapperStyle = isCanvas
      ? 'display:flex; flex-direction:column; flex:1 1 auto; min-height:0; height:100%;'
      : 'display:flex; flex-direction:column;';
    const title = this.data?.title;
    return html`
      <div class=${wrapperClass} style=${wrapperStyle}>
        ${title ? html`<header class="iframe-sandbox-header" style="flex: 0 0 auto;"><span class="iframe-sandbox-title">${title}</span></header>` : ''}
        <iframe
          class="iframe-sandbox-frame"
          sandbox="allow-scripts"
          referrerpolicy="no-referrer"
          loading="lazy"
          .srcdoc=${srcdoc}
          style=${iframeStyle}
          title=${title || 'sandboxed widget'}>
        </iframe>
      </div>
    `;
  }
}

customElements.define('dc-widget-iframe-sandbox', IframeSandboxWidget);
