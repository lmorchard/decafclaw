import { LitElement, html, nothing } from 'lit';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import hljs from 'hljs';
import * as Diff from 'diff';
import { getActiveConvId } from '/static/lib/canvas-state.js';
import { ApiError, DefaultService } from '/static/lib/api-client/index.js';

const INLINE_MAX_HEIGHT = '20rem';
/** Inline preview row cap; canvas renders every row. */
const MAX_DIFF_RENDER_LINES = 500;
/** Unchanged lines kept on each side of a change. */
const CONTEXT_LINES = 3;

/** Split a jsdiff part into lines without a phantom line from a trailing \n. */
function partLines(part) {
  if (!part.value) return [];
  return part.value.replace(/\n$/, '').split('\n');
}

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function pushGap(rows, count) {
  if (count > 0) rows.push({ type: 'gap', count });
}

/**
 * diff_view widget.
 *   mode='inline'  → collapsed preview with Expand + Open in Canvas
 *   mode='canvas'  → full scrollable diff
 *
 * Rows collapse long unchanged runs to CONTEXT_LINES around each change so
 * the inline preview shows the edit rather than the whole file. The widget
 * chrome is rendered once here; the per-view renderers return content only.
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
    /** Non-reactive: whether the collapsed inline body clips. */
    this._needsExpand = false;
    /** Measure the body, not the inline-layout host, for clipping. */
    this._resizeObserver = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(() => this._measureExpand());
    /** @type {Element | null} */
    this._observedBody = null;
  }

  createRenderRoot() { return this; } // Use light DOM to inherit CSS

  connectedCallback() {
    super.connectedCallback();
    this.viewMode = this.data?.view || 'unified';
  }

  disconnectedCallback() {
    this._resizeObserver?.disconnect();
    this._observedBody = null;
    super.disconnectedCallback();
  }

  willUpdate(changed) {
    if (changed.has('data')) {
      if (this.data?.view && !changed.has('viewMode')) {
        this.viewMode = this.data.view;
      }
    }
  }

  updated() {
    // A widget mounted while the chat view is hidden measures zeros; observing
    // the body catches the size change when the view is shown.
    const body = this.querySelector('.diff-view-body');
    if (this._resizeObserver && body !== this._observedBody) {
      this._resizeObserver.disconnect();
      this._observedBody = body;
      if (body) this._resizeObserver.observe(body);
    }
    this._measureExpand();
  }

  /** Offer Expand only when the collapsed inline body actually clips. */
  _measureExpand() {
    // Canvas has no collapse, and jsdom reports zero heights.
    if (this.mode === 'canvas' || this.expanded) return;
    const body = this.querySelector('.diff-view-body');
    if (!body) return;
    const needs = body.scrollHeight > body.clientHeight + 1;
    if (needs !== this._needsExpand) {
      this._needsExpand = needs;
      this.requestUpdate();
    }
  }

  /** @returns {string} */
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
      await DefaultService.postCanvasNewTabApiCanvasConvIdNewTabPost(
        convId, {
          widget_type: 'diff_view',
          data: {
            ...this.data,
            view: this.viewMode,
          },
          label: this._headerLabel(),
        }, true);
    } catch (err) {
      if (!(err instanceof ApiError)) console.error('canvas new_tab error', err);
    }
  }

  _highlight(text, lang) {
    if (!text) return '';
    try {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(text, { language: lang }).value;
      }
    } catch (err) {
      console.warn('[diff_view] highlight failed:', err);
    }
    // Without a known language, plain text. Auto-detecting one language per
    // line is unreliable and prohibitively slow for full-file diffs.
    return escapeHtml(text);
  }

  /**
   * Build diff rows, collapsing long unchanged runs into gap rows.
   * @returns {Array<{type: string, text?: string, count?: number}>}
   */
  _buildRows() {
    const { before = '', after = '' } = this.data || {};
    const parts = Diff.diffLines(before, after);
    const rows = [];

    parts.forEach((part, index) => {
      const lines = partLines(part);
      if (part.added || part.removed) {
        const type = part.added ? 'added' : 'removed';
        for (const text of lines) rows.push({ type, text });
        return;
      }

      const isFirst = index === 0;
      const isLast = index === parts.length - 1;
      if (isFirst && isLast) {
        for (const text of lines) rows.push({ type: 'unchanged', text });
        return;
      }
      if (isFirst) {
        // Trailing context before the first change.
        const kept = lines.slice(-CONTEXT_LINES);
        pushGap(rows, lines.length - kept.length);
        for (const text of kept) rows.push({ type: 'unchanged', text });
        return;
      }
      if (isLast) {
        // Leading context after the last change.
        const kept = lines.slice(0, CONTEXT_LINES);
        for (const text of kept) rows.push({ type: 'unchanged', text });
        pushGap(rows, lines.length - kept.length);
        return;
      }
      if (lines.length <= CONTEXT_LINES * 2) {
        for (const text of lines) rows.push({ type: 'unchanged', text });
        return;
      }
      for (const text of lines.slice(0, CONTEXT_LINES)) {
        rows.push({ type: 'unchanged', text });
      }
      pushGap(rows, lines.length - CONTEXT_LINES * 2);
      for (const text of lines.slice(-CONTEXT_LINES)) {
        rows.push({ type: 'unchanged', text });
      }
    });

    return rows;
  }

  _renderHeader(label) {
    return html`
      <header class="code-block-header">
        <span class="code-block-label">${label}</span>
        <span class="code-block-spacer"></span>
        <button class="code-block-copy" type="button" @click=${this._toggleViewMode}>
          ${this.viewMode === 'unified' ? 'Split View' : 'Unified View'}
        </button>
      </header>
    `;
  }

  _renderGap(count) {
    const label = `${count} unchanged line${count === 1 ? '' : 's'}`;
    return html`<div class="diff-line gap"><span class="diff-marker">\u22ef</span><span class="diff-content">${label}</span></div>`;
  }

  _renderHighlighted(type, highlighted, marker) {
    return html`<div class="diff-line ${type}"><span class="diff-marker">${marker}</span><span class="diff-content">${unsafeHTML(highlighted || ' ')}</span></div>`;
  }

  _renderLine(type, text, marker, language) {
    return this._renderHighlighted(type, this._highlight(text, language), marker);
  }

  _renderUnified(rows) {
    const language = this.data?.language;
    return html`
      <div class="diff-unified"><pre><code>${rows.map(row => {
        if (row.type === 'gap') return this._renderGap(row.count);
        const marker = row.type === 'added' ? '+' : row.type === 'removed' ? '-' : ' ';
        return this._renderLine(row.type, row.text, marker, language);
      })}</code></pre></div>
    `;
  }

  _renderSplit(rows) {
    const language = this.data?.language;
    const leftRows = [];
    const rightRows = [];
    const empty = () => html`<div class="diff-line empty"><span class="diff-marker"> </span></div>`;

    for (const row of rows) {
      if (row.type === 'gap') {
        const gap = this._renderGap(row.count);
        leftRows.push(gap);
        rightRows.push(gap);
      } else if (row.type === 'added') {
        leftRows.push(empty());
        rightRows.push(this._renderLine('added', row.text, '+', language));
      } else if (row.type === 'removed') {
        leftRows.push(this._renderLine('removed', row.text, '-', language));
        rightRows.push(empty());
      } else {
        const highlighted = this._highlight(row.text, language);
        leftRows.push(this._renderHighlighted('unchanged', highlighted, ' '));
        rightRows.push(this._renderHighlighted('unchanged', highlighted, ' '));
      }
    }

    return html`
      <div class="diff-split">
        <div class="diff-pane left-pane"><pre><code>${leftRows}</code></pre></div>
        <div class="diff-pane right-pane"><pre><code>${rightRows}</code></pre></div>
      </div>
    `;
  }

  render() {
    const isCanvas = this.mode === 'canvas';
    const rows = this._buildRows();
    const totalRows = rows.length;
    const truncated = !isCanvas && totalRows > MAX_DIFF_RENDER_LINES;
    const visibleRows = truncated ? rows.slice(0, MAX_DIFF_RENDER_LINES) : rows;
    const content = this.viewMode === 'split'
      ? this._renderSplit(visibleRows)
      : this._renderUnified(visibleRows);

    /** @type {import('lit').TemplateResult | typeof nothing} */
    let notice = nothing;
    if (truncated) {
      notice = html`
        <div class="diff-view-notice">
          Showing first ${MAX_DIFF_RENDER_LINES} of ${totalRows} diff lines.
          Open in Canvas for the full diff.
        </div>
      `;
    }

    if (isCanvas) {
      return html`
        <div class="diff-view-widget code-block-canvas canvas-mode">
          ${this._renderHeader(this._headerLabel())}
          <div class="code-block-scroll">
            ${content}
          </div>
        </div>
      `;
    }

    // Inline
    const collapsedStyle = this.expanded
      ? ''
      : `max-height: ${INLINE_MAX_HEIGHT}; overflow: hidden;`;
    return html`
      <div class="diff-view-widget code-block-inline ${this.expanded ? 'expanded' : 'collapsed'}">
        ${this._renderHeader(this._headerLabel())}
        <div class="code-block-body diff-view-body" style=${collapsedStyle}>
          ${notice}
          ${content}
        </div>
        <div class="code-block-actions">
          ${this._needsExpand ? html`
            <button type="button" @click=${this._toggleExpand}>${this.expanded ? 'Collapse' : 'Expand'}</button>
          ` : nothing}
          <button type="button" @click=${this._openInCanvas}>Open in Canvas</button>
        </div>
      </div>
    `;
  }
}

customElements.define('dc-widget-diff-view', DiffViewWidget);
