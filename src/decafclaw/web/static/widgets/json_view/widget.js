import { LitElement, html, css } from 'lit';
import { getActiveConvId } from '/static/lib/canvas-state.js';

export class JsonViewWidget extends LitElement {
  static properties = {
    data: { type: Object },
    mode: { type: String },
    expanded: { type: Boolean, state: true },
  };

  static styles = css`
    :host {
      display: block;
      font-family: var(--pico-font-family-monospace);
      font-size: 0.85em;
    }
    .json-view-container {
      background: var(--pico-code-background-color);
      color: var(--pico-code-color);
      border-radius: var(--pico-border-radius);
      padding: 0.5rem;
    }
    /* Inline literal, not an interpolated constant: lit's css tag throws on any
       interpolation that is not a number or CSSResult, and static styles runs at
       class-definition time — so a plain string here broke the module at load.
       unsafeCSS() would also work, but not worth it for a hardcoded value. */
    .json-view-inline.collapsed .json-view-body {
      max-height: 20rem;
      overflow: hidden;
    }
    .json-view-inline.expanded .json-view-body {
      max-height: none;
    }
    .json-view-canvas .json-view-body {
      overflow: auto;
      height: 100%;
    }
    .json-view-actions {
      display: flex;
      gap: 0.5rem;
      margin-top: 0.5rem;
    }
    .json-view-actions button {
      padding: 0.25rem 0.5rem;
      font-size: 0.8rem;
      width: auto;
      margin: 0;
    }
    .json-node {
      margin-left: 1rem;
    }
    details > summary {
      cursor: pointer;
      list-style: none;
      display: inline-block;
    }
    details > summary::-webkit-details-marker {
      display: none;
    }
    details > summary::before {
      content: '▶';
      display: inline-block;
      width: 1rem;
      font-size: 0.8em;
      transition: transform 0.1s;
    }
    details[open] > summary::before {
      transform: rotate(90deg);
    }
    .json-key {
      color: var(--pico-primary);
      font-weight: bold;
    }
    .json-string { color: #10a37f; }
    .json-number { color: #d97706; }
    .json-boolean { color: #2563eb; }
    .json-null { color: #6b7280; font-style: italic; }
    .path-highlight {
      background-color: var(--pico-mark-background-color);
      color: var(--pico-mark-color);
    }
    .copy-path-btn {
      opacity: 0;
      background: none;
      border: none;
      padding: 0;
      margin-left: 0.25rem;
      font-size: 0.7em;
      cursor: pointer;
      color: var(--pico-muted-color);
    }
    .json-line:hover .copy-path-btn {
      opacity: 1;
    }
  `;

  constructor() {
    super();
    this.data = {};
    this.mode = 'inline';
    this.expanded = false;
  }

  _toggleExpand() {
    this.expanded = !this.expanded;
  }

  async _openInCanvas() {
    const convId = getActiveConvId() || '';
    if (!convId) return;
    try {
      const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}/new_tab`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          widget_type: 'json_view',
          data: this.data,
          label: 'JSON View',
        }),
      });
      if (!resp.ok) {
        console.error('canvas new_tab failed', resp.status, await resp.text());
      }
    } catch (err) {
      console.error('canvas new_tab error', err);
    }
  }

  _copyPath(path, e) {
    e.preventDefault();
    e.stopPropagation();
    navigator.clipboard.writeText(path).then(() => {
      const btn = e.target;
      const orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = orig; }, 1500);
    });
  }

  _matchesPath(path, filter) {
    if (!filter) return true;
    // Simple substring or regex match
    try {
      if (path.includes(filter)) return true;
      const re = new RegExp(filter, 'i');
      return re.test(path);
    } catch (e) {
      return path.includes(filter);
    }
  }

  _renderValue(value, path, depth, expandDepth, pathFilter) {
    if (value === null) return html`<span class="json-null">null</span>`;
    if (typeof value === 'boolean') return html`<span class="json-boolean">${value}</span>`;
    if (typeof value === 'number') return html`<span class="json-number">${value}</span>`;
    if (typeof value === 'string') return html`<span class="json-string">"${value}"</span>`;

    const isArray = Array.isArray(value);
    const keys = Object.keys(value);
    if (keys.length === 0) return html`<span>${isArray ? '[]' : '{}'}</span>`;

    const isOpen = depth < expandDepth;
    const highlight = pathFilter && this._matchesPath(path, pathFilter);

    const children = keys.map(key => {
      const childValue = value[key];
      const childPath = isArray ? `${path}[${key}]` : `${path}.${key}`;
      // Check if child or its descendants match the filter
      // For simplicity, if we have a pathFilter, we only render nodes that match or have children that match.
      // Wait, full JSON path filtering is complex. Let's just highlight matched paths and auto-expand them.
      return html`
        <div class="json-node">
          <div class="json-line">
            <span class="json-key">${key}</span>: 
            ${this._renderValue(childValue, childPath, depth + 1, expandDepth, pathFilter)}
            <button class="copy-path-btn" @click=${(e) => this._copyPath(childPath, e)}>🔗</button>
          </div>
        </div>
      `;
    });

    const summaryClass = highlight ? 'path-highlight' : '';

    return html`
      <details ?open=${isOpen || highlight}>
        <summary class=${summaryClass}>${isArray ? '[' : '{'} <span style="color:var(--pico-muted-color);font-size:0.8em">${keys.length} items</span></summary>
        ${children}
        <div>${isArray ? ']' : '}'}</div>
      </details>
    `;
  }

  render() {
    const value = this.data?.value ?? null;
    const expandDepth = this.data?.expand_depth ?? 2;
    const pathFilter = this.data?.path_filter ?? '';

    const rootContent = html`
      <div class="json-line">
        <span class="json-key">$</span>: 
        ${this._renderValue(value, '$', 0, expandDepth, pathFilter)}
        <button class="copy-path-btn" @click=${(e) => this._copyPath('$', e)}>🔗</button>
      </div>
    `;

    if (this.mode === 'canvas') {
      return html`
        <div class="json-view-container json-view-canvas">
          <div class="json-view-body">
            ${rootContent}
          </div>
        </div>
      `;
    }

    // inline
    return html`
      <div class="json-view-container json-view-inline ${this.expanded ? 'expanded' : 'collapsed'}">
        <div class="json-view-body">
          ${rootContent}
        </div>
        <div class="json-view-actions">
          <button type="button" class="secondary" @click=${this._toggleExpand}>${this.expanded ? 'Collapse' : 'Expand'}</button>
          <button type="button" class="secondary" @click=${this._openInCanvas}>Open in Canvas</button>
        </div>
      </div>
    `;
  }
}

customElements.define('dc-widget-json-view', JsonViewWidget);
