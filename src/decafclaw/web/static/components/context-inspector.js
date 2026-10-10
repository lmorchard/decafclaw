import { LitElement, html, nothing } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';
import { showToast } from '../lib/toast.js';
import { copyToClipboard } from '../lib/utils.js';

/** @typedef {import('../lib/api-client/index.js').ContextDiagnosticsResponse} Diagnostics */
/** @typedef {import('../lib/api-client/index.js').ContextSource} Source */
/** @typedef {import('../lib/api-client/index.js').ContextRawResponse} ContextRaw */

const SOURCE_COLORS = {
  system_prompt: '#4A90D9',
  history: '#888888',
  tools: '#9B59B6',
  wiki: '#27AE60',
  memory: '#E67E22',
  preempt_matches: '#C0392B',
};

const SOURCE_LABELS = {
  system_prompt: 'System Prompt',
  history: 'History',
  tools: 'Tools',
  wiki: 'Wiki Pages',
  memory: 'Memory',
  preempt_matches: 'Pre-emptive matches',
};

const TYPE_LABELS = {
  page: 'Agent page',
  user: 'User page',
  journal: 'Journal',
  graph_expansion: 'Linked page',
};

export class ContextInspector extends LitElement {
  static properties = {
    convId: { type: String },
    open: { type: Boolean, reflect: true },
    contextVersion: { type: Number },  // bumped by parent when context changes
    _tab: { type: String, state: true },
    _data: { type: Object, state: true },
    _loading: { type: Boolean, state: true },
    _error: { type: String, state: true },
    _rawData: { type: Object, state: true },
    _rawLoading: { type: Boolean, state: true },
    _rawError: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.convId = '';
    this.open = false;
    this.contextVersion = 0;
    this._tab = 'diagnostics';
    /** @type {Diagnostics|null} */
    this._data = null;
    this._loading = false;
    this._error = '';
    /** @type {ContextRaw|null} */
    this._rawData = null;
    this._rawLoading = false;
    this._rawError = '';
  }

  updated(changed) {
    if (changed.has('open') && this.open && this.convId) {
      if (this._tab === 'raw') {
        this.#fetchRawData();
      } else {
        this.#fetchData();
      }
    }
    if (changed.has('_tab') && this.open && this.convId && this._tab === 'raw' && !this._rawData && !this._rawLoading) {
      this.#fetchRawData();
    }
    if (changed.has('open')) {
      if (this.open) {
        // Defer so the current click doesn't immediately close
        requestAnimationFrame(() => {
          this._onDocClick = (e) => {
            if (!this.contains(e.target)) {
              this.dispatchEvent(new Event('close'));
            }
          };
          document.addEventListener('click', this._onDocClick, true);
        });
      } else {
        this.#removeDocClickListener();
      }
    }
    // Re-fetch when context changes while inspector is open
    if (changed.has('contextVersion') && this.open && this.convId) {
      if (this._tab === 'raw' && !this._rawLoading) {
        this.#fetchRawData();
      } else if (this._tab === 'diagnostics' && !this._loading) {
        this.#fetchData();
      }
    }
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.#removeDocClickListener();
  }

  #removeDocClickListener() {
    if (this._onDocClick) {
      document.removeEventListener('click', this._onDocClick, true);
      this._onDocClick = null;
    }
  }

  async #fetchData() {
    if (!this.convId) return;
    this._loading = true;
    this._error = '';
    this._data = null;
    try {
      this._data = await DefaultService.getContextDiagnosticsApiConversationsIdContextGet(this.convId);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        this._error = e instanceof ApiError ? `HTTP ${e.status}` : e.message || 'Failed to load';
      }
    } finally {
      this._loading = false;
    }
  }

  async #fetchRawData() {
    if (!this.convId) return;
    this._rawLoading = true;
    this._rawError = '';
    this._rawData = null;
    try {
      this._rawData = await DefaultService.getContextRawApiConversationsIdContextRawGet(this.convId);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        this._rawError = e instanceof ApiError ? `HTTP ${e.status}` : e.message || 'Failed to load';
      }
    } finally {
      this._rawLoading = false;
    }
  }

  async #copyRaw() {
    if (!this._rawData) return;
    try {
      const text = JSON.stringify(this._rawData, null, 2);
      await copyToClipboard(text);
      showToast('Copied raw context');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Copy failed: ${msg}`);
    }
  }

  /** @param {Diagnostics["sources"]} sources
   * @param {Diagnostics["context_window_size"]} windowSize
   * @param {Diagnostics["total_tokens_actual"]} totalActual */
  #renderWaffle(sources, windowSize, totalActual) {
    if (!windowSize || !sources?.length) return nothing;

    // Aim for ~300 cells
    const tokensPerCell = Math.max(1, Math.ceil(windowSize / 300));
    const totalCells = Math.ceil(windowSize / tokensPerCell);

    // When actual token count is available, scale estimated source sizes
    // proportionally so the waffle reflects real usage, not estimates.
    const totalEstimated = sources.reduce((sum, s) => sum + (s.tokens_estimated || 0), 0);
    const scale = (totalActual && totalEstimated > 0)
      ? totalActual / totalEstimated
      : 1;

    const cells = [];
    for (const s of sources) {
      const scaledTokens = (s.tokens_estimated || 0) * scale;
      const count = Math.max(1, Math.round(scaledTokens / tokensPerCell));
      const color = SOURCE_COLORS[s.source] || '#ccc';
      for (let i = 0; i < count && cells.length < totalCells; i++) {
        cells.push(color);
      }
    }
    // Fill remaining with unused
    while (cells.length < totalCells) {
      cells.push('var(--pico-muted-border-color, #eee)');
    }

    return html`
      <div class="waffle">
        ${cells.map(c => html`<div class="waffle-cell" style="background:${c}"></div>`)}
      </div>
      <div class="legend">
        ${Object.entries(SOURCE_COLORS).map(([key, color]) => html`
          <span class="legend-item">
            <span class="legend-swatch" style="background:${color}"></span>
            ${SOURCE_LABELS[key] || key}
          </span>
        `)}
        <span class="legend-item">
          <span class="legend-swatch" style="background:var(--pico-muted-border-color, #eee)"></span>
          Unused
        </span>
      </div>
    `;
  }

  /** @param {Diagnostics} data */
  #renderStats(data) {
    return html`
      <dl class="stats">
        <dt>Estimated</dt>
        <dd>${data.total_tokens_estimated?.toLocaleString() || '—'}</dd>
        <dt>Actual</dt>
        <dd>${data.total_tokens_actual?.toLocaleString() || '—'}</dd>
        <dt>Cached</dt>
        <dd>
          ${data.cached_prompt_tokens != null
            ? html`${data.cached_prompt_tokens.toLocaleString()} (${Math.round((data.cache_hit_rate || 0) * 100)}%)`
            : '—'}
        </dd>
        <dt>Window</dt>
        <dd>${data.context_window_size?.toLocaleString() || '—'}</dd>
        <dt>Compact at</dt>
        <dd>${data.compaction_threshold?.toLocaleString() || '—'}</dd>
      </dl>
    `;
  }

  /** @param {Diagnostics["sources"]} sources */
  #renderSourceTable(sources) {
    if (!sources?.length) return nothing;
    return html`
      <table class="source-table">
        <thead>
          <tr><th>Source</th><th>Tokens</th><th>Items</th><th>Detail</th></tr>
        </thead>
        <tbody>
          ${sources.map(s => html`
            <tr>
              <td>
                <span class="source-swatch" style="background:${SOURCE_COLORS[s.source] || '#ccc'}"></span>
                ${SOURCE_LABELS[s.source] || s.source}
              </td>
              <td>${s.tokens_estimated?.toLocaleString()}</td>
              <td>${s.items_included}${s.items_truncated ? html` <span style="color:var(--pico-muted-color)">(+${s.items_truncated} dropped)</span>` : ''}</td>
              <td>${this.#sourceDetail(s)}</td>
            </tr>
          `)}
        </tbody>
      </table>
    `;
  }

  /** @param {Source} s */
  #sourceDetail(s) {
    if (s.source === 'memory' && s.details) {
      const d = s.details;
      return html`scores ${d.top_score ?? '—'}–${d.min_score ?? '—'}, ${d.budget_source || ''} budget`;
    }
    if (s.source === 'tools' && s.details?.deferred_mode) {
      return 'deferred mode';
    }
    if (s.source === 'preempt_matches' && s.details?.matches?.length) {
      const matches = s.details.matches;
      const summary = matches.map(m => `${m.name}(${m.score})`).join(', ');
      return html`<span title="input tokens: ${s.details.input_tokens?.join(', ')}">${summary}</span>`;
    }
    return '';
  }

  /** @param {Diagnostics["memory_candidates"]} candidates */
  #renderCandidates(candidates) {
    if (!candidates?.length) return nothing;

    const maxBarWidth = 40; // px

    return html`
      <div class="candidates-header">Memory Candidates (${candidates.length})</div>
      ${candidates.map(c => {
        const path = c.file_path?.replace(/^agent\/pages\//, '') || '?';
        const typeLabel = TYPE_LABELS[c.source_type] || c.source_type;
        return html`
          <div class="candidate">
            <div class="candidate-path">${path}</div>
            <div class="candidate-meta">
              <span>${typeLabel}</span>
              <span>score: ${c.composite_score?.toFixed(2)}</span>
              <span>~${c.tokens_estimated?.toLocaleString() || '?'} tok</span>
            </div>
            <div class="score-bars" title="sim=${c.similarity?.toFixed(2)} rec=${c.recency?.toFixed(2)} imp=${c.importance?.toFixed(2)}">
              <div class="score-bar" style="width:${(c.similarity || 0) * maxBarWidth}px;background:#4A90D9"></div>
              <div class="score-bar" style="width:${(c.recency || 0) * maxBarWidth}px;background:#27AE60"></div>
              <div class="score-bar" style="width:${(c.importance || 0) * maxBarWidth}px;background:#E67E22"></div>
              <span class="score-label">s/r/i</span>
            </div>
            ${c.linked_from ? html`<div class="candidate-provenance">\u2190 linked from ${c.linked_from.replace(/^agent\/pages\//, '')}</div>` : ''}
          </div>
        `;
      })}
    `;
  }

  #renderRaw() {
    if (this._rawLoading) {
      return html`<div class="loading">Loading...</div>`;
    }
    if (this._rawError) {
      return html`<div class="error-msg">Error: ${this._rawError}</div>`;
    }
    if (!this._rawData) {
      return html`<div class="empty-msg">No raw request data yet</div>`;
    }
    const d = this._rawData;
    return html`
      <div class="raw-view">
        <div class="raw-actions">
          <div class="raw-meta">
            Model: <strong>${d.model || '—'}</strong>
          </div>
          <button
            type="button"
            class="copy-raw-btn dc-small-btn"
            @click=${this.#copyRaw}
          >Copy JSON</button>
        </div>
        <div class="raw-section">
          <h4>Messages (${d.messages?.length || 0})</h4>
          <pre class="raw-pre"><code>${JSON.stringify(d.messages || [], null, 2)}</code></pre>
        </div>
        <div class="raw-section">
          <h4>Tools (${d.tools?.length || 0})</h4>
          <pre class="raw-pre"><code>${JSON.stringify(d.tools || [], null, 2)}</code></pre>
        </div>
      </div>
    `;
  }

  render() {
    if (!this.open) return nothing;

    let content;
    if (this._tab === 'raw') {
      content = this.#renderRaw();
    } else if (this._loading) {
      content = html`<div class="loading">Loading...</div>`;
    } else if (this._error) {
      content = html`<div class="error-msg">Error: ${this._error}</div>`;
    } else if (!this._data) {
      content = html`<div class="empty-msg">No context data yet</div>`;
    } else {
      const d = this._data;
      content = html`
        ${this.#renderWaffle(d.sources, d.context_window_size, d.total_tokens_actual)}
        ${this.#renderStats(d)}
        ${this.#renderSourceTable(d.sources)}
        ${this.#renderCandidates(d.memory_candidates)}
      `;
    }

    return html`
      <div class="inspector">
        <div class="inspector-header">
          <div class="inspector-title-row">
            <h3>Context Inspector</h3>
            <div class="inspector-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                class="tab-btn ${this._tab === 'diagnostics' ? 'active' : ''}"
                aria-selected=${this._tab === 'diagnostics'}
                @click=${() => { this._tab = 'diagnostics'; }}
              >Diagnostics</button>
              <button
                type="button"
                role="tab"
                class="tab-btn ${this._tab === 'raw' ? 'active' : ''}"
                aria-selected=${this._tab === 'raw'}
                @click=${() => { this._tab = 'raw'; }}
              >Raw</button>
            </div>
          </div>
          <button class="close-btn dc-icon-btn" @click=${() => this.dispatchEvent(new Event('close'))} title="Close context inspector" aria-label="Close context inspector">&times;</button>
        </div>
        ${content}
      </div>
    `;
  }
}

customElements.define('context-inspector', ContextInspector);
