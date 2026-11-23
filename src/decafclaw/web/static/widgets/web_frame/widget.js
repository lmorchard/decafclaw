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

  /**
   * Reload the embedded application without rebuilding the tab. Reassigning
   * the iframe's own src re-navigates it; reading the attribute is allowed
   * cross-origin, unlike contentWindow.location.
   */
  reload() {
    const frame = /** @type {HTMLIFrameElement|null} */ (this.querySelector('iframe'));
    if (!frame) return;
    frame.src = frame.src;
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
          <div style="padding: 1rem; color: var(--pico-del-color, #c00);">
            No URL specified for web frame.
          </div>
        </div>
      `;
    }

    // Theme tokens (not raw hex): a light titlebar on a dark canvas reads as
    // a broken panel. The header also hosts the reload control, so it
    // renders even when the tab has no title.
    const headerStyle =
      'display: flex; align-items: center; gap: 0.5rem; flex: 0 0 auto;'
      + ' padding: 0.25rem 0.5rem; font-size: 0.85rem; font-weight: 500;'
      + ' border-bottom: 1px solid var(--pico-muted-border-color, #ccc);'
      + ' background: var(--pico-card-sectioning-background-color, transparent);'
      + ' color: inherit;';

    return html`
      <div class=${wrapperClass} style=${wrapperStyle}>
        <header class="web-frame-header" style=${headerStyle}>
          <span
            class="web-frame-title"
            style="flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
          >${title || ''}</span>
          <button
            type="button"
            class="web-frame-reload"
            style="flex: 0 0 auto; width: auto; margin: 0; padding: 0.1rem 0.4rem; border: 0; background: none; color: inherit; cursor: pointer; font-size: 1rem; line-height: 1;"
            title="Reload frame"
            aria-label="Reload frame"
            @click=${() => this.reload()}
          >&#8635;</button>
        </header>
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
