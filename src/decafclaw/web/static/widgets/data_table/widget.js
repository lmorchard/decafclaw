import { LitElement, html, nothing } from 'lit';

/**
 * Tabular data widget. Props:
 *   data = { columns: [{key, label}], rows: [{...}], caption?: string }
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

  /** @param {any[]} rows */
  _sortedRows(rows) {
    if (!this._sortKey || !this._sortDir) return rows;
    const key = this._sortKey;
    const dir = this._sortDir === 'asc' ? 1 : -1;
    const copy = rows.slice();
    copy.sort((a, b) => {
      const av = a?.[key];
      const bv = b?.[key];
      const an = Number(av);
      const bn = Number(bv);
      const numeric = !Number.isNaN(an) && !Number.isNaN(bn)
        && av !== '' && bv !== '' && av != null && bv != null;
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

  /** @param {any} v */
  _renderCell(v) {
    if (v == null) return '';
    if (typeof v === 'object') {
      try { return JSON.stringify(v); } catch { return String(v); }
    }
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
                  ${cols.map(c => html`<td>${this._renderCell(r?.[c.key])}</td>`)}
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
