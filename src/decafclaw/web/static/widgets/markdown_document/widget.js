import { LitElement, html } from 'lit';
import { renderMarkdown } from '../../lib/markdown.js';
import { getActiveConvId } from '../../lib/canvas-state.js';

const INLINE_MAX_HEIGHT = '8rem';

/**
 * markdown_document widget. Two modes set by the host:
 *   mode='inline'  → collapsed preview with Expand + Open in Canvas buttons
 *   mode='canvas'  → full content, scroll position preserved across updates
 *
 * Data shape: { content: string (markdown) }
 */
export class MarkdownDocumentWidget extends LitElement {
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

  // Light DOM so the app stylesheet styles rendered markdown correctly.
  createRenderRoot() { return this; }

  willUpdate(changed) {
    if (this.mode !== 'canvas') return;
    if (!changed.has('data')) return;
    const scroller = this.querySelector('.md-doc-scroll');
    if (scroller) {
      this._savedScroll = {
        top: scroller.scrollTop,
        left: scroller.scrollLeft,
      };
    }
  }

  updated() {
    if (this.mode !== 'canvas') return;
    if (!this._savedScroll) return;
    const scroller = this.querySelector('.md-doc-scroll');
    if (!scroller) return;
    const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    const maxLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
    scroller.scrollTop = Math.min(this._savedScroll.top, maxTop);
    scroller.scrollLeft = Math.min(this._savedScroll.left, maxLeft);
    this._savedScroll = null;
  }

  _firstH1(content) {
    if (!content) return 'Untitled';
    for (const line of content.split('\n')) {
      const stripped = line.trim();
      if (stripped.startsWith('# ')) return stripped.slice(2).trim() || 'Untitled';
    }
    return 'Untitled';
  }

  _toggleExpand() {
    this.expanded = !this.expanded;
  }

  async _openInCanvas() {
    const convId = getActiveConvId() || '';
    if (!convId) return;
    const label = this._firstH1(this.data?.content);
    try {
      const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}/set`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          widget_type: 'markdown_document',
          data: { content: this.data?.content ?? '' },
          label,
        }),
      });
      if (!resp.ok) {
        console.error('canvas set failed', resp.status, await resp.text());
      }
    } catch (err) {
      console.error('canvas set error', err);
    }
  }

  render() {
    const content = this.data?.content ?? '';
    const rendered = renderMarkdown(content);

    if (this.mode === 'canvas') {
      return html`
        <div class="md-doc md-doc-canvas">
          <div class="md-doc-scroll" .innerHTML=${rendered}></div>
        </div>
      `;
    }

    const collapsedStyle = this.expanded
      ? ''
      : `max-height: ${INLINE_MAX_HEIGHT}; overflow: hidden;`;
    return html`
      <div class="md-doc md-doc-inline ${this.expanded ? 'expanded' : 'collapsed'}">
        <div class="md-doc-body" style=${collapsedStyle} .innerHTML=${rendered}></div>
        <div class="md-doc-actions">
          <button @click=${this._toggleExpand}>${this.expanded ? 'Collapse' : 'Expand'}</button>
          <button @click=${this._openInCanvas}>Open in Canvas</button>
        </div>
      </div>
    `;
  }
}

customElements.define('dc-widget-markdown-document', MarkdownDocumentWidget);
