import { LitElement, html } from 'lit';

/**
 * web_frame widget. Renders an approved local or homelab companion web
 * application (such as Marimo notebooks or Television artifacts) inside
 * an iframe in canvas mode.
 *
 * Sandbox attributes:
 *   - `allow-scripts`           — required for interactive client-side execution
 *   - `allow-same-origin`       — required for WebSocket and session communication
 *   - `allow-forms`             — permits form submissions inside the companion app
 *   - `allow-popups`            — permits popups/downloads initiated by the app
 *
 * Mode: canvas mode only (fills available height).
 *
 * Data shape: { url: string, title?: string }
 */
export class WebFrameWidget extends LitElement {
  static properties = {
    data: { type: Object },
    mode: { type: String },
  };

  constructor() {
    super();
    this.data = {};
    this.mode = 'canvas';
  }

  createRenderRoot() {
    return this;
  }

  render() {
    const url = (this.data && typeof this.data.url === 'string')
      ? this.data.url
      : '';
    const title = this.data?.title;
    const iframeStyle =
      'flex: 1 1 auto; min-height: 12rem; width: 100%; height: 100%; border: none; background: white;';
    const wrapperClass = 'web-frame web-frame-canvas';
    const wrapperStyle =
      'display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; height: 100%; width: 100%;';

    if (!url) {
      return html`
        <div class=${wrapperClass} style=${wrapperStyle}>
          <div style="padding: 1rem; color: var(--error-color, #c00);">
            No URL specified for web frame.
          </div>
        </div>
      `;
    }

    return html`
      <div class=${wrapperClass} style=${wrapperStyle}>
        ${title ? html`
          <header
            class="web-frame-header"
            style="flex: 0 0 auto; padding: 0.25rem 0.5rem; font-size: 0.85rem; font-weight: 500; border-bottom: 1px solid var(--border-color, #ccc); background: var(--bg-surface, #fafafa);"
          >
            <span class="web-frame-title">${title}</span>
          </header>
        ` : ''}
        <iframe
          class="web-frame-frame"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          referrerpolicy="no-referrer"
          loading="lazy"
          .src=${url}
          style=${iframeStyle}
          title=${title || 'embedded companion web frame'}
        >
        </iframe>
      </div>
    `;
  }
}

customElements.define('dc-widget-web-frame', WebFrameWidget);
