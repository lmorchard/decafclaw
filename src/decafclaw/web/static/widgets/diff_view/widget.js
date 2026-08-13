import { LitElement, html } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import hljs from 'hljs';
import * as Diff from 'diff';
import { getActiveConvId } from '/static/lib/canvas-state.js';

const INLINE_MAX_HEIGHT = '20rem';

/**
 * diff_view widget.
 *   mode='inline'  → collapsed preview
 *   mode='canvas'  → full split view
 */
export class DiffViewWidget extends LitElement {
  static properties = {
    data: { type: Object },
    mode: { type: String },
    expanded: { type: Boolean, state: true },
    viewMode: { type: String, state: true },
  };

  constructor() {
    super();
    this.data = {};
    this.mode = 'inline';
    this.expanded = false;
    this.viewMode = 'unified';
  }

  createRenderRoot() { return this; } // Use light DOM to inherit CSS

  connectedCallback() {
    super.connectedCallback();
    this.viewMode = this.data?.view || 'unified';
  }

  willUpdate(changed) {
    if (changed.has('data')) {
      if (this.data?.view && !changed.has('viewMode')) {
        this.viewMode = this.data.view;
      }
    }
  }

  _headerLabel() {
    return this.data?.filename || 'Diff';
  }

  _toggleExpand() {
    this.expanded = !this.expanded;
  }

  _toggleViewMode() {
    this.viewMode = this.viewMode === 'unified' ? 'split' : 'unified';
  }

  async _openInCanvas() {
    const convId = getActiveConvId() || '';
    if (!convId) return;
    try {
      await fetch(`/api/canvas/${encodeURIComponent(convId)}/new_tab`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          widget_type: 'diff_view',
          data: {
            ...this.data,
            view: this.viewMode,
          },
          label: this._headerLabel(),
        }),
      });
    } catch (err) {
      console.error('canvas new_tab error', err);
    }
  }

  _highlight(text, lang) {
    if (!text) return '';
    try {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(text, { language: lang }).value;
      }
      return hljs.highlightAuto(text).value;
    } catch (err) {
      return text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
  }

  _renderUnified() {
    const { before = '', after = '', language } = this.data;
    const diff = Diff.diffLines(before, after);
    let lines = [];
    
    diff.forEach(part => {
      // DiffLines leaves a trailing newline on the chunk, so strip it before splitting
      // unless it's just a single empty line added
      const partLines = part.value.replace(/\n$/, '').split('\n');
      partLines.forEach(line => {
        let type = 'unchanged';
        let marker = ' ';
        if (part.added) { type = 'added'; marker = '+'; }
        if (part.removed) { type = 'removed'; marker = '-'; }
        
        const highlighted = this._highlight(line, language);
        lines.push(html`
          <div class="diff-line ${type}">
            <span class="diff-marker">${marker}</span>
            <span class="diff-content">${unsafeHTML(highlighted || ' ')}</span>
          </div>
        `);
      });
    });

    return html`<div class="diff-unified"><pre><code>${lines}</code></pre></div>`;
  }

  _renderSplit() {
    const { before = '', after = '', language } = this.data;
    const diff = Diff.diffLines(before, after);
    
    let leftLines = [];
    let rightLines = [];
    
    diff.forEach(part => {
      const partLines = part.value.replace(/\n$/, '').split('\n');
      partLines.forEach(line => {
        const highlighted = this._highlight(line, language);
        const lineHtml = html`<span class="diff-content">${unsafeHTML(highlighted || ' ')}</span>`;
        
        if (part.added) {
          leftLines.push(html`<div class="diff-line empty"><span class="diff-marker"> </span></div>`);
          rightLines.push(html`<div class="diff-line added"><span class="diff-marker">+</span>${lineHtml}</div>`);
        } else if (part.removed) {
          leftLines.push(html`<div class="diff-line removed"><span class="diff-marker">-</span>${lineHtml}</div>`);
          rightLines.push(html`<div class="diff-line empty"><span class="diff-marker"> </span></div>`);
        } else {
          leftLines.push(html`<div class="diff-line unchanged"><span class="diff-marker"> </span>${lineHtml}</div>`);
          rightLines.push(html`<div class="diff-line unchanged"><span class="diff-marker"> </span>${lineHtml}</div>`);
        }
      });
    });

    return html`
      <div class="diff-split">
        <div class="diff-pane left-pane"><pre><code>${leftLines}</code></pre></div>
        <div class="diff-pane right-pane"><pre><code>${rightLines}</code></pre></div>
      </div>
    `;
  }

  render() {
    const headerLabel = this._headerLabel();
    const isCanvas = this.mode === 'canvas';
    
    const content = this.viewMode === 'split' ? this._renderSplit() : this._renderUnified();

    if (isCanvas) {
      return html`
        <div class="diff-view-widget code-block-canvas canvas-mode">
          <header class="code-block-header">
            <span class="code-block-label">${headerLabel}</span>
            <span class="code-block-spacer"></span>
            <button class="code-block-copy" type="button" @click=${this._toggleViewMode}>
              ${this.viewMode === 'unified' ? 'Split View' : 'Unified View'}
            </button>
          </header>
          <div class="code-block-scroll">
            ${content}
          </div>
        </div>
      `;
    }

    // Inline
    const collapsedStyle = this.expanded ? '' : `max-height: ${INLINE_MAX_HEIGHT}; overflow: hidden;`;
    return html`
      <div class="diff-view-widget code-block-inline ${this.expanded ? 'expanded' : 'collapsed'}">
        <header class="code-block-header">
          <span class="code-block-label">${headerLabel}</span>
          <span class="code-block-spacer"></span>
          <button class="code-block-copy" type="button" @click=${this._toggleViewMode}>
            ${this.viewMode === 'unified' ? 'Split View' : 'Unified View'}
          </button>
        </header>
        <div class="code-block-body diff-view-body" style=${collapsedStyle}>
          ${content}
        </div>
        <div class="code-block-actions">
          <button type="button" @click=${this._toggleExpand}>${this.expanded ? 'Collapse' : 'Expand'}</button>
          <button type="button" @click=${this._openInCanvas}>Open in Canvas</button>
        </div>
      </div>
    `;
  }
}

customElements.define('dc-widget-diff-view', DiffViewWidget);
