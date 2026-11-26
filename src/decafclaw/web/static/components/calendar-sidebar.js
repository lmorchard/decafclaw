import { LitElement, html, nothing } from 'lit';
import { DefaultService } from '../lib/api-client/index.js';

/** @typedef {import('../lib/api-client/index.js').CalendarEventOut} CalendarEvent */

/** Calendar tab: upcoming events grouped by date. */
export class CalendarSidebar extends LitElement {
  static properties = {
    active: { type: Boolean },
    _events: { type: Array, state: true },
    _loading: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.active = false;
    /** @type {CalendarEvent[]} */
    this._events = [];
    this._loading = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._onChanged = () => { if (this.active) this.#fetch(); };
    window.addEventListener('calendar-changed', this._onChanged);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('calendar-changed', this._onChanged);
  }

  /** @param {Map<string, unknown>} changed */
  updated(changed) {
    if (changed.has('active') && this.active && !changed.get('active')) this.#fetch();
  }

  async #fetch() {
    this._loading = true;
    try {
      const data = await DefaultService.wrapperApiCalendarEventsGet(undefined, undefined, 'pending', undefined);
      this._events = data.events || [];
    } catch {
      this._events = [];
    } finally {
      this._loading = false;
    }
  }

  #dateKey(iso) {
    return new Date(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  #time(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  render() {
    /** @type {Map<string, CalendarEvent[]>} */
    const groups = new Map();
    for (const event of this._events) {
      const key = this.#dateKey(event.fire_at);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    }
    return html`
      <div class="calendar-sidebar-body">
        <button class="calendar-new-btn" @click=${() => this.dispatchEvent(new CustomEvent('calendar-new', { bubbles: true }))}>
          + New event
        </button>
        ${this._loading ? html`<p class="calendar-empty">Loading…</p>` : nothing}
        ${!this._loading && this._events.length === 0 ? html`<p class="calendar-empty">No upcoming events.</p>` : nothing}
        ${[...groups.entries()].map(([date, events]) => html`
          <div class="calendar-group">
            <div class="calendar-group-date">${date}</div>
            ${events.map(event => html`
              <div class="calendar-row" @click=${() => this.dispatchEvent(new CustomEvent('calendar-open', { bubbles: true, detail: { eventId: event.event_id } }))}>
                <span class="calendar-row-time">${this.#time(event.fire_at)}</span>
                <span class="calendar-row-title">${event.title}</span>
                <span class="calendar-row-target">${event.target_title || event.target_conv_id}</span>
              </div>
            `)}
          </div>
        `)}
      </div>
    `;
  }
}

customElements.define('calendar-sidebar', CalendarSidebar);
