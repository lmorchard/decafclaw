import { LitElement, html, nothing } from 'lit';

/** Humanize a byte count; mirrors the backend's `_format_bytes`. */
function formatBytes(n) {
  if (!Number.isFinite(n) || n < 0) return '';
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

/** Shorten a URL for display, keeping the host and a tail of the path. */
function shortenUrl(url, maxLen = 72) {
  if (!url) return '';
  let display = url;
  try {
    const parsed = new URL(url);
    display = parsed.host + (parsed.pathname === '/' ? '' : parsed.pathname) + parsed.search;
    if (url.startsWith('https://')) display = 'https://' + display;
    else if (url.startsWith('http://')) display = 'http://' + display;
  } catch {
    // Not a parseable URL — fall through and show it as given.
  }
  if (display.length <= maxLen) return display;
  const head = display.slice(0, Math.max(0, maxLen - 12));
  return `${head}…${display.slice(-10)}`;
}

/** Split an HTTP status code into a tone class for the badge. */
function statusTone(status) {
  if (status >= 200 && status < 300) return 'ok';
  if (status >= 300 && status < 400) return 'redirect';
  return 'error';
}

/**
 * web_fetch_result widget — a compact outcome card for `web_fetch` (#1625).
 *
 * The fetched body is deliberately absent from `data`. A page's markup is
 * rarely the artifact the user wanted, and duplicating up to 50 KB of it into
 * the archived widget payload turned every fetch into a wall of source. The
 * body still reaches the model as tool-result text, and the hosting tool row
 * offers its own "Show raw result" disclosure for humans who want it.
 *
 * Data shape: {
 *   status: number,          // HTTP status code
 *   status_text?: string,    // reason phrase
 *   url: string,             // requested URL
 *   final_url?: string,      // post-redirect URL, when different
 *   content_type?: string,   // bare media type
 *   bytes?: number,          // body size
 *   elapsed_ms?: number,     // request duration
 *   truncated?: boolean,     // body capped before reaching the model
 *   title?: string,          // document title (HTML)
 * }
 */
export class WebFetchResultWidget extends LitElement {
  static properties = {
    data: { type: Object },
    mode: { type: String },
  };

  constructor() {
    super();
    this.data = {};
    this.mode = 'inline';
  }

  // Light DOM: inherit document styles, like the other bundled widgets.
  createRenderRoot() { return this; }

  #tone() {
    return statusTone(Number(this.data?.status ?? 0));
  }

  render() {
    const data = this.data ?? {};
    const status = Number(data.status ?? 0);
    const tone = this.#tone();
    const finalUrl = data.final_url && data.final_url !== data.url ? data.final_url : null;
    const facts = [];
    if (data.content_type) facts.push(data.content_type);
    if (Number.isFinite(data.bytes)) facts.push(formatBytes(data.bytes));
    if (Number.isFinite(data.elapsed_ms)) facts.push(`${data.elapsed_ms} ms`);

    return html`
      <div class="web-fetch-result">
        <div class="web-fetch-result__headline">
          <span class="web-fetch-result__status web-fetch-result__status--${tone}">
            ${status || '?'}${data.status_text ? ` ${data.status_text}` : ''}
          </span>
          ${data.title ? html`
            <span class="web-fetch-result__title" title=${data.title}>${data.title}</span>
          ` : nothing}
        </div>
        <div class="web-fetch-result__url" title=${data.url ?? ''}>
          ${shortenUrl(data.url)}
        </div>
        ${facts.length ? html`
          <div class="web-fetch-result__facts">
            ${facts.map((fact, i) => html`
              ${i > 0 ? html`<span class="web-fetch-result__sep">·</span>` : nothing}
              <span class="web-fetch-result__fact">${fact}</span>
            `)}
            ${data.truncated ? html`
              <span class="web-fetch-result__sep">·</span>
              <span class="web-fetch-result__fact web-fetch-result__fact--warn">truncated</span>
            ` : nothing}
          </div>
        ` : nothing}
        ${finalUrl ? html`
          <div class="web-fetch-result__redirect">
            redirected to <span title=${finalUrl}>${shortenUrl(finalUrl)}</span>
          </div>
        ` : nothing}
      </div>
    `;
  }
}

customElements.define('dc-widget-web-fetch-result', WebFetchResultWidget);
