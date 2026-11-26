import { LitElement, html, nothing } from 'lit';
import { DefaultService } from '../lib/api-client/index.js';

/** @typedef {import('../lib/api-client/index.js').CalendarOccurrenceOut} Occurrence */

const VIEWS = ['day', 'week', 'month', 'agenda'];

function pad(n) { return String(n).padStart(2, '0'); }
function dateKey(date) { return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`; }
function parseDate(value) {
  const [y, m, d] = String(value || '').split('-').map(Number);
  if (!y || !m || !d) return new Date();
  return new Date(y, m - 1, d);
}
function startOfWeek(date) {
  const copy = new Date(date);
  const day = (copy.getDay() + 6) % 7; // Monday = 0
  copy.setDate(copy.getDate() - day);
  copy.setHours(0, 0, 0, 0);
  return copy;
}
function addDays(date, n) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + n);
  return copy;
}

/** Calendar tab: day/week/month/agenda views over the occurrence API (#1903). */
export class CalendarSidebar extends LitElement {
  static properties = {
    active: { type: Boolean },
    _view: { type: String, state: true },
    _date: { type: String, state: true },
    _occurrences: { type: Array, state: true },
    _loading: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.active = false;
    this._view = 'agenda';
    this._date = dateKey(new Date());
    /** @type {Occurrence[]} */
    this._occurrences = [];
    this._loading = false;
  }

  connectedCallback() {
    super.connectedCallback();
    const params = new URLSearchParams(window.location.search);
    const view = params.get('calendar_view');
    const date = params.get('calendar_date');
    if (view && VIEWS.includes(view)) this._view = view;
    if (date) this._date = date;
    this._onChanged = () => { if (this.active) this.#fetch(); };
    this._onPopState = () => {
      const p = new URLSearchParams(window.location.search);
      const v = p.get('calendar_view');
      const d = p.get('calendar_date');
      if (v && VIEWS.includes(v)) this._view = v;
      if (d) this._date = d;
      if (this.active) this.#fetch();
    };
    window.addEventListener('calendar-changed', this._onChanged);
    window.addEventListener('popstate', this._onPopState);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('calendar-changed', this._onChanged);
    window.removeEventListener('popstate', this._onPopState);
  }

  /** @param {Map<string, unknown>} changed */
  updated(changed) {
    if (changed.has('active') && this.active && !changed.get('active')) this.#fetch();
  }

  #range() {
    const date = parseDate(this._date);
    if (this._view === 'day') {
      const start = new Date(date); start.setHours(0, 0, 0, 0);
      return [start, addDays(start, 1)];
    }
    if (this._view === 'week') {
      const start = startOfWeek(date);
      return [start, addDays(start, 7)];
    }
    if (this._view === 'month') {
      const start = new Date(date.getFullYear(), date.getMonth(), 1);
      return [start, new Date(date.getFullYear(), date.getMonth() + 1, 1)];
    }
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    return [start, addDays(start, 30)];
  }

  async #fetch() {
    this._loading = true;
    const [start, end] = this.#range();
    try {
      const data = await DefaultService.wrapperApiCalendarOccurrencesGet(
        start.toISOString(), end.toISOString(), undefined, false,
      );
      this._occurrences = data.occurrences || [];
    } catch {
      this._occurrences = [];
    } finally {
      this._loading = false;
    }
  }

  #setView(view) {
    if (!VIEWS.includes(view)) return;
    this._view = view;
    this.#syncUrl();
    this.#fetch();
  }

  #move(delta) {
    const date = parseDate(this._date);
    if (this._view === 'day') date.setDate(date.getDate() + delta);
    else if (this._view === 'week') date.setDate(date.getDate() + 7 * delta);
    else if (this._view === 'month') date.setMonth(date.getMonth() + delta);
    else date.setDate(date.getDate() + 30 * delta);
    this._date = dateKey(date);
    this.#syncUrl();
    this.#fetch();
  }

  #today() {
    this._date = dateKey(new Date());
    this.#syncUrl();
    this.#fetch();
  }

  #syncUrl() {
    const url = new URL(window.location.href);
    url.searchParams.set('calendar_view', this._view);
    url.searchParams.set('calendar_date', this._date);
    window.history.replaceState(null, '', url);
  }

  #open(eventId) {
    this.dispatchEvent(new CustomEvent('calendar-open', { bubbles: true, detail: { eventId } }));
  }

  #openConversation(event, ev) {
    ev.stopPropagation();
    const sidebar = /** @type {any} */ (this.closest('conversation-sidebar'));
    sidebar?.store?.selectConversation(event.target_conv_id);
  }

  #time(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  #group() {
    /** @type {Map<string, Occurrence[]>} */
    const groups = new Map();
    for (const occurrence of this._occurrences) {
      const key = new Date(occurrence.occurrence_at).toLocaleDateString(undefined, {
        weekday: 'short', month: 'short', day: 'numeric',
      });
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(occurrence);
    }
    return groups;
  }

  #renderList() {
    const groups = this.#group();
    if (groups.size === 0) return html`<p class="calendar-empty">No occurrences in this range.</p>`;
    return [...groups.entries()].map(([date, occurrences]) => html`
      <div class="calendar-group">
        <div class="calendar-group-date">${date}</div>
        ${occurrences.map(occurrence => html`
          <div class="calendar-row" @click=${() => this.#open(occurrence.event_id)}>
            <span class="calendar-row-time">${this.#time(occurrence.occurrence_at)}</span>
            <span class="calendar-row-title">${occurrence.title}${occurrence.recurrence ? ' ↻' : ''}</span>
            <span class="calendar-row-target">${occurrence.target_title || occurrence.target_conv_id}
              <button class="calendar-open-conv" title="Open conversation" @click=${(ev) => this.#openConversation(occurrence, ev)}>↗</button>
            </span>
          </div>
        `)}
      </div>
    `);
  }

  #renderMonth() {
    const date = parseDate(this._date);
    const first = new Date(date.getFullYear(), date.getMonth(), 1);
    const start = startOfWeek(first);
    const byDay = new Map();
    for (const occurrence of this._occurrences) {
      const key = dateKey(new Date(occurrence.occurrence_at));
      if (!byDay.has(key)) byDay.set(key, []);
      byDay.get(key).push(occurrence);
    }
    const cells = [];
    for (let i = 0; i < 42; i += 1) {
      const day = addDays(start, i);
      const key = dateKey(day);
      const inMonth = day.getMonth() === first.getMonth();
      cells.push(html`
        <div class="calendar-month-cell ${inMonth ? '' : 'calendar-month-outside'}"
             @click=${() => { this._view = 'day'; this._date = key; this.#syncUrl(); this.#fetch(); }}>
          <div class="calendar-month-day">${day.getDate()}</div>
          ${(byDay.get(key) || []).slice(0, 3).map(occurrence => html`
            <div class="calendar-month-chip" title=${occurrence.title}>${occurrence.title}</div>
          `)}
        </div>
      `);
    }
    return html`<div class="calendar-month-grid">${cells}</div>`;
  }

  render() {
    return html`
      <div class="calendar-sidebar-body">
        <div class="calendar-view-switch">
          ${VIEWS.map(view => html`
            <button class="calendar-view-btn ${this._view === view ? 'active' : ''}"
              @click=${() => this.#setView(view)}>${view[0].toUpperCase() + view.slice(1)}</button>
          `)}
        </div>
        <div class="calendar-range-controls">
          <button @click=${() => this.#move(-1)} title="Previous">‹</button>
          <button @click=${() => this.#today()}>Today</button>
          <button @click=${() => this.#move(1)} title="Next">›</button>
          <span class="calendar-range-label">${this._date}</span>
        </div>
        <button class="calendar-new-btn" @click=${() => this.dispatchEvent(new CustomEvent('calendar-new', { bubbles: true }))}>
          + New event
        </button>
        ${this._loading ? html`<p class="calendar-empty">Loading…</p>` : nothing}
        ${!this._loading && this._view === 'month' ? this.#renderMonth() : nothing}
        ${!this._loading && this._view !== 'month' ? this.#renderList() : nothing}
      </div>
    `;
  }
}

customElements.define('calendar-sidebar', CalendarSidebar);
