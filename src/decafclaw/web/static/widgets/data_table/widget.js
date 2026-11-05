import { LitElement, html, nothing } from 'lit';

/** Humanize a byte count for display (#1169). */
function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(2)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(2)} GB`;
}

/** Humanize a duration in seconds for display (#1169). */
function formatDuration(secs) {
  const s = Math.round(secs);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  if (m < 60) return `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h < 24) return `${h}h ${mm}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/**
 * Tabular data widget. Props:
 *   data = { columns: [{key, label, type?}], rows: [{...}], caption?: string }
 * Column `type` is one of "bytes" | "duration" | "number" | "text"
 * (default "text"). Typed numeric columns sort on raw numbers while
 * displaying formatted values; rows may carry decoupled cells
 * `{ value, display }` (#1169).
 * Click a column header to cycle sort: none → asc → desc → none.
 */
export class DataTableWidget extends LitElement {
  static properties = {
    data: { type: Object },
    _sortKey: { type: String, state: true },
    _sortDir: { type: String, state: true },  // 'asc' | 'desc' | ''
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {{columns: {key: string, label: string}[], rows: any[], caption?: string}|null} */
    this.data = null;
    this._sortKey = '';
    this._sortDir = '';
  }

  /** @param {string} key */
  _onHeaderClick(key) {
    if (this._sortKey !== key) {
      this._sortKey = key;
      this._sortDir = 'asc';
      return;
    }
    if (this._sortDir === 'asc') {
      this._sortDir = 'desc';
    } else if (this._sortDir === 'desc') {
      this._sortKey = '';
      this._sortDir = '';
    } else {
      this._sortDir = 'asc';
    }
  }

  /** Column type from a column def; defaults to "text". */
  _columnType(col) {
    const t = col?.type;
    return typeof t === 'string' ? t : 'text';
  }

  /**
   * Coerce a cell value to a number for sorting. Typed numeric columns
   * ("bytes" | "duration" | "number") and decoupled `{ value, display }`
   * cells sort on the raw numeric value; formatted strings like "100 B" or
   * "120s" are unit-stripped when a numeric column type is declared (#1169).
   * Returns null when the value is not meaningfully numeric.
   */
  _sortableNumber(v, col) {
    if (v == null) return null;
    if (typeof v === 'object' && !Array.isArray(v)) {
      const n = typeof v.value === 'number' ? v.value : null;
      return n;
    }
    if (typeof v === 'number') return v;
    const type = this._columnType(col);
    const numericType = type === 'bytes' || type === 'duration' || type === 'number';
    if (!numericType) return null;
    const s = String(v).trim();
    const cleaned = s.replace(/[^0-9.\-]/g, '');
    if (cleaned === '' || cleaned === '-' || cleaned === '.') return null;
    const n = Number(cleaned);
    if (Number.isNaN(n)) return null;
    if (type === 'bytes') {
      // Unit-aware scaling: "1.2 KB" sorts as ~1200 bytes, not 1.2 (#1169).
      const m = /^(-?[\d.]+)\s*([a-z]*)$/i.exec(s);
      if (!m) return n;
      const unit = (m[2] || '').toLowerCase();
      const mult =
        unit === '' ? 1 : unit.startsWith('k') ? 1024 : unit.startsWith('m') ? 1024 * 1024 : unit.startsWith('g') ? 1024 * 1024 * 1024 : unit === 'b' ? 1 : null;
      if (mult == null) return n;
      return n * mult;
    }
    return n;
  }

  /** Humanized display value for a cell given its column type (#1169). */
  _displayValue(v, col) {
    const type = this._columnType(col);
    if (v != null && typeof v === 'object' && !Array.isArray(v)) {
      if (typeof v.display === 'string') return v.display;
      const n = typeof v.value === 'number' ? v.value : null;
      if (n == null) return '';
      if (type === 'bytes') return formatBytes(n);
      if (type === 'duration') return formatDuration(n);
      return String(n);
    }
    const n = typeof v === 'number' ? v : this._sortableNumber(v, col);
    if (n == null) return v == null ? '' : String(v);
    if (type === 'bytes') return formatBytes(n);
    if (type === 'duration') return formatDuration(n);
    return String(v);
  }

  /** @param {any[]} rows */
  _sortedRows(rows) {
    if (!this._sortKey || !this._sortDir) return rows;
    const key = this._sortKey;
    const dir = this._sortDir === 'asc' ? 1 : -1;
    const cols = Array.isArray(this.data?.columns) ? this.data.columns : [];
    const col = cols.find(c => c?.key === key);
    const copy = rows.slice();
    copy.sort((a, b) => {
      const av = a?.[key];
      const bv = b?.[key];
      const an = this._sortableNumber(av, col);
      const bn = this._sortableNumber(bv, col);
      const numeric = an != null && bn != null;
      let cmp;
      if (numeric) {
        cmp = an - bn;
      } else {
        const as = av == null ? '' : String(av);
        const bs = bv == null ? '' : String(bv);
        cmp = as < bs ? -1 : (as > bs ? 1 : 0);
      }
      return cmp * dir;
    });
    return copy;
  }

  /** @param {string} key */
  _ariaSort(key) {
    if (this._sortKey !== key) return 'none';
    if (this._sortDir === 'asc') return 'ascending';
    if (this._sortDir === 'desc') return 'descending';
    return 'none';
  }

  /** @param {string} key */
  _sortIndicator(key) {
    if (this._sortKey !== key) return '';
    if (this._sortDir === 'asc') return '▲';
    if (this._sortDir === 'desc') return '▼';
    return '';
  }

  /** @param {any} v @param {any} col */
  _renderCell(v, col) {
    const type = this._columnType(col);
    if (v != null && typeof v === 'object' && !Array.isArray(v)) {
      // Decoupled typed cell: { value, display } — prefer the explicit display.
      if (typeof v.display === 'string') return v.display;
      const n = typeof v.value === 'number' ? v.value : null;
      if (n == null) return '';
      if (type === 'bytes') return formatBytes(n);
      if (type === 'duration') return formatDuration(n);
      return String(n);
    }
    const n = typeof v === 'number' ? v : this._sortableNumber(v, col);
    if (n == null) return v == null ? '' : String(v);
    if (type === 'bytes') return formatBytes(n);
    if (type === 'duration') return formatDuration(n);
    return String(v);
  }

  render() {
    const d = this.data;
    if (!d || !Array.isArray(d.columns) || !Array.isArray(d.rows)) {
      return html`<div class="widget-data-table widget-data-table--empty"><em>no data</em></div>`;
    }
    const cols = d.columns;
    const rows = this._sortedRows(d.rows);
    return html`
      <figure class="widget-data-table">
        ${d.caption ? html`<figcaption>${d.caption}</figcaption>` : nothing}
        <div class="widget-data-table__scroll">
          <table>
            <thead>
              <tr>
                ${cols.map(c => {
                  const indicator = this._sortIndicator(c.key);
                  const indicatorClasses = indicator
                    ? 'widget-data-table__sort-indicator'
                    : 'widget-data-table__sort-indicator widget-data-table__sort-indicator--hidden';
                  // Always render a glyph so the indicator slot has stable
                  // width; --hidden class makes it invisible when unsorted.
                  return html`
                    <th aria-sort=${this._ariaSort(c.key)}>
                      <button type="button"
                              class="widget-data-table__sort-btn"
                              @click=${() => this._onHeaderClick(c.key)}>
                        <span class="widget-data-table__sort-label">${c.label}</span>
                        <span class=${indicatorClasses}
                              aria-hidden="true">${indicator || '▼'}</span>
                      </button>
                    </th>
                  `;
                })}
              </tr>
            </thead>
            <tbody>
              ${rows.map(r => html`
                <tr>
                  ${cols.map(c => html`<td>${this._renderCell(r?.[c.key], c)}</td>`)}
                </tr>
              `)}
            </tbody>
          </table>
        </div>
      </figure>
    `;
  }
}

customElements.define('dc-widget-data-table', DataTableWidget);
