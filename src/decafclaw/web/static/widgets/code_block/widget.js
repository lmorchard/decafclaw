import { LitElement, html } from 'lit';
import hljs from 'hljs';
import { getActiveConvId } from '/static/lib/canvas-state.js';

const INLINE_MAX_HEIGHT = '12rem';

/**
 * code_block widget. Two modes set by the host:
 *   mode='inline'  → collapsed preview with Expand + Open in Canvas buttons
 *   mode='canvas'  → full file, scroll preserved across data updates
 *
 * Data shape: { code: string, language?: string, filename?: string }
 */
export class CodeBlockWidget extends LitElement {
  static properties = {
    data: { type: Object },
    mode: { type: String },
    expanded: { type: Boolean, state: true },
  };

  constructor() {
    super();
    this.data = {};
    this.mode = 'inline';
    this.expanded = false;
  }

  createRenderRoot() { return this; }

  willUpdate(changed) {
    if (this.mode !== 'canvas') return;
    if (!changed.has('data')) return;
    const scroller = this.querySelector('.code-block-scroll');
    if (scroller) {
      this._savedScroll = {
        top: scroller.scrollTop,
        left: scroller.scrollLeft,
      };
    }
  }

  updated() {
    // Lit renders an empty `<code>` and we own its content imperatively
    // (textContent + hljs.highlightElement). Going through Lit interpolation
    // would let hljs mutate the part that Lit also tracks, which can prevent
    // future updates after the first highlight.
    const codeEl = /** @type {HTMLElement | null} */ (this.querySelector('pre code'));
    if (codeEl) {
      const code = this.data?.code ?? '';
      // Always reset to plain text first; this also wipes any previous
      // highlight spans so a data update re-highlights cleanly.
      codeEl.textContent = code;
      delete codeEl.dataset.highlighted;
      try {
        hljs.highlightElement(codeEl);
      } catch (err) {
        console.warn('hljs failed for code_block:', err);
      }
    }
    if (this.mode === 'canvas' && this._savedScroll) {
      const scroller = this.querySelector('.code-block-scroll');
      if (scroller) {
        const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
        const maxLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
        scroller.scrollTop = Math.min(this._savedScroll.top, maxTop);
        scroller.scrollLeft = Math.min(this._savedScroll.left, maxLeft);
      }
      this._savedScroll = null;
    }
  }

  _headerLabel() {
    return this.data?.filename
      || (this.data?.language ? `${this.data.language} snippet` : 'code');
  }

  _toggleExpand() {
    this.expanded = !this.expanded;
  }

  _copyCode() {
    const code = this.data?.code ?? '';
    const btn = /** @type {HTMLButtonElement | null} */ (this.querySelector('.code-block-copy'));
    navigator.clipboard.writeText(code).then(
      () => {
        if (btn) {
          const original = btn.textContent;
          btn.textContent = 'Copied!';
          setTimeout(() => { if (btn.textContent === 'Copied!') btn.textContent = original; }, 2000);
        }
      },
      (err) => {
        console.warn('clipboard write failed:', err);
        if (btn) {
          const original = btn.textContent;
          btn.textContent = 'Copy failed';
          setTimeout(() => { if (btn.textContent === 'Copy failed') btn.textContent = original; }, 2000);
        }
      },
    );
  }

  async _openInCanvas() {
    const convId = getActiveConvId() || '';
    if (!convId) return;
    const label = this._headerLabel();
    try {
      const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}/new_tab`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          widget_type: 'code_block',
          data: {
            code: this.data?.code ?? '',
            language: this.data?.language ?? undefined,
            filename: this.data?.filename ?? undefined,
          },
          label,
        }),
      });
      if (!resp.ok) {
        console.error('canvas new_tab failed', resp.status, await resp.text());
      }
    } catch (err) {
      console.error('canvas new_tab error', err);
    }
  }

  render() {
    const lang = this.data?.language ?? 'plaintext';
    const headerLabel = this._headerLabel();

    if (this.mode === 'canvas') {
      return html`
        <div class="code-block code-block-canvas">
          <header class="code-block-header">
            <span class="code-block-label">${headerLabel}</span>
            <span class="code-block-spacer"></span>
            <button class="code-block-copy" type="button"
                    aria-label="Copy code to clipboard"
                    @click=${this._copyCode}>Copy</button>
          </header>
          <div class="code-block-scroll">
            <pre><code class="language-${lang}"></code></pre>
          </div>
        </div>
      `;
    }

    // inline
    const collapsedStyle = this.expanded
      ? ''
      : `max-height: ${INLINE_MAX_HEIGHT}; overflow: hidden;`;
    return html`
      <div class="code-block code-block-inline ${this.expanded ? 'expanded' : 'collapsed'}">
        <header class="code-block-header">
          <span class="code-block-label">${headerLabel}</span>
          <span class="code-block-spacer"></span>
          <button class="code-block-copy" type="button"
                  aria-label="Copy code to clipboard"
                  @click=${this._copyCode}>Copy</button>
        </header>
        <div class="code-block-body" style=${collapsedStyle}>
          <pre><code class="language-${lang}"></code></pre>
        </div>
        <div class="code-block-actions">
          <button type="button" @click=${this._toggleExpand}>${this.expanded ? 'Collapse' : 'Expand'}</button>
          <button type="button" @click=${this._openInCanvas}>Open in Canvas</button>
        </div>
      </div>
    `;
  }
}

customElements.define('dc-widget-code-block', CodeBlockWidget);
