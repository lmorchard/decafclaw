import { LitElement, html, nothing } from 'lit';

const SOURCE_COLORS = {
  system_prompt: '#4A90D9',
  history: '#888888',
  tools: '#9B59B6',
  wiki: '#27AE60',
  memory: '#E67E22',
};

const SOURCE_LABELS = {
  system_prompt: 'System Prompt',
  history: 'History',
  tools: 'Tools',
  wiki: 'Wiki Pages',
  memory: 'Memory',
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
    _data: { type: Object, state: true },
    _loading: { type: Boolean, state: true },
    _error: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.convId = '';
    this.open = false;
    this.contextVersion = 0;
    this._data = null;
    this._loading = false;
    this._error = '';
  }

  updated(changed) {
    if (changed.has('open') && this.open && this.convId) {
      this.#fetchData();
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
    if (changed.has('contextVersion') && this.open && this.convId && !this._loading) {
      this.#fetchData();
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
      const res = await fetch(`/api/conversations/${encodeURIComponent(this.convId)}/context`);
      if (res.status === 404) {
        this._data = null;
        this._loading = false;
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      this._data = await res.json();
    } catch (e) {
      this._error = e.message || 'Failed to load';
    } finally {
      this._loading = false;
    }
  }

  #renderWaffle(sources, windowSize) {
    if (!windowSize || !sources?.length) return nothing;

    // Aim for ~300 cells
    const tokensPerCell = Math.max(1, Math.ceil(windowSize / 300));
    const totalCells = Math.ceil(windowSize / tokensPerCell);

    const cells = [];
    for (const s of sources) {
      const count = Math.max(1, Math.round(s.tokens_estimated / tokensPerCell));
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

  #renderStats(data) {
    return html`
      <dl class="stats">
        <dt>Estimated</dt>
        <dd>${data.total_tokens_estimated?.toLocaleString() || '—'}</dd>
        <dt>Actual</dt>
        <dd>${data.total_tokens_actual?.toLocaleString() || '—'}</dd>
        <dt>Window</dt>
        <dd>${data.context_window_size?.toLocaleString() || '—'}</dd>
        <dt>Compact at</dt>
        <dd>${data.compaction_threshold?.toLocaleString() || '—'}</dd>
      </dl>
    `;
  }

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

  #sourceDetail(s) {
    if (s.source === 'memory' && s.details) {
      const d = s.details;
      return html`scores ${d.top_score ?? '—'}–${d.min_score ?? '—'}, ${d.budget_source || ''} budget`;
    }
    if (s.source === 'tools' && s.details?.deferred_mode) {
      return 'deferred mode';
    }
    return '';
  }

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

  render() {
    if (!this.open) return nothing;

    let content;
    if (this._loading) {
      content = html`<div class="loading">Loading...</div>`;
    } else if (this._error) {
      content = html`<div class="error-msg">Error: ${this._error}</div>`;
    } else if (!this._data) {
      content = html`<div class="empty-msg">No context data yet</div>`;
    } else {
      const d = this._data;
      content = html`
        ${this.#renderWaffle(d.sources, d.context_window_size)}
        ${this.#renderStats(d)}
        ${this.#renderSourceTable(d.sources)}
        ${this.#renderCandidates(d.memory_candidates)}
      `;
    }

    return html`
      <div class="inspector">
        <div class="inspector-header">
          <h3>Context Inspector</h3>
          <button class="close-btn" @click=${() => this.dispatchEvent(new Event('close'))}>&times;</button>
        </div>
        ${content}
      </div>
    `;
  }
}

customElements.define('context-inspector', ContextInspector);
