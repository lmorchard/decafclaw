import { LitElement, html, nothing } from 'lit';
import { DefaultService } from '../lib/api-client/index.js';
import { responseError } from '../lib/utils.js';

/** @typedef {import('../lib/api-client/index.js').CalendarEventOut} CalendarEvent */

function toLocalInput(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function fromLocalInput(value) {
  if (!value) return '';
  return new Date(value).toISOString();
}

/** Side-panel calendar editor and list (#1899). */
export class CalendarPage extends LitElement {
  static properties = {
    eventId: { type: String, reflect: true },
    _event: { type: Object, state: true },
    _loading: { type: Boolean, state: true },
    _saving: { type: Boolean, state: true },
    _error: { type: String, state: true },
    _message: { type: String, state: true },
    _dedicated: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.eventId = '';
    /** @type {CalendarEvent|null} */
    this._event = null;
    this._loading = false;
    this._saving = false;
    this._error = '';
    this._message = '';
    this._dedicated = false;
  }

  /** @param {Map<string, unknown>} changed */
  updated(changed) {
    if (changed.has('eventId')) {
      this._error = '';
      this._message = '';
      if (this.eventId) this.#fetchEvent();
      else this._event = null;
    }
  }

  async #fetchEvent() {
    this._loading = true;
    try {
      // An explicit epoch `from` asks for the whole history, including fired
      // events. The route's default bound is "upcoming only", which would hide
      // the very events whose snooze/cancel actions this editor offers (#1952).
      const data = await DefaultService.wrapperApiCalendarEventsGet(
        new Date(0).toISOString(), undefined, 'all', undefined,
      );
      this._event = (data.events || []).find((event) => event.event_id === this.eventId) || null;
      if (!this._event) this._error = 'Event not found.';
      this._dedicated = this._event?.session_mode === 'dedicated';
    } catch (err) {
      this._error = responseError(err) || 'Failed to load the event.';
    } finally {
      this._loading = false;
    }
  }

  #currentConvId() {
    const sidebar = /** @type {any} */ (document.querySelector('conversation-sidebar'));
    return sidebar?.store?.currentConvId || '';
  }

  #notifyChanged() {
    window.dispatchEvent(new CustomEvent('calendar-changed'));
  }

  async #save(event) {
    event.preventDefault();
    this._saving = true;
    this._error = '';
    this._message = '';
    const form = new FormData(event.currentTarget);
    /** @type {'current'|'dedicated'} */
    const sessionMode = this._dedicated ? 'dedicated' : 'current';
    const body = {
      title: String(form.get('title') || '').trim(),
      reason: String(form.get('reason') || '').trim(),
      fire_at: fromLocalInput(String(form.get('fire_at') || '')),
      timezone: String(form.get('timezone') || '').trim(),
      recurrence: String(form.get('recurrence') || '').trim(),
      recurrence_until: fromLocalInput(String(form.get('recurrence_until') || '')),
      session_mode: sessionMode,
      session_key: String(form.get('session_key') || '').trim(),
      session_title: String(form.get('session_title') || '').trim(),
    };
    try {
      if (this.eventId) {
        const result = await DefaultService.wrapperApiCalendarEventsEventIdPut(this.eventId, body);
        this._event = result.event;
      } else {
        const convId = this.#currentConvId();
        if (!convId) throw new Error('Select a conversation first.');
        const result = await DefaultService.wrapperApiCalendarEventsPost({ conv_id: convId, ...body });
        this._event = result.event;
        this.eventId = result.event.event_id;
      }
      this._message = 'Saved.';
      this.#notifyChanged();
    } catch (err) {
      this._error = responseError(err) || String(err);
    } finally {
      this._saving = false;
    }
  }

  async #snooze(seconds) {
    if (!this.eventId) return;
    this._saving = true;
    this._error = '';
    try {
      const result = await DefaultService.wrapperApiCalendarEventsEventIdSnoozePost(this.eventId, { duration_seconds: seconds });
      this._event = result.event;
      this._message = 'Snoozed.';
      this.#notifyChanged();
    } catch (err) {
      this._error = responseError(err) || String(err);
    } finally {
      this._saving = false;
    }
  }

  /**
   * Run the event now, as a rehearsal.
   *
   * The backend deliberately does not advance the schedule, so this is safe to
   * press repeatedly on a recurring event. It also cannot tell us how the run
   * ended — the wake turn is enqueued and this request returns immediately, so
   * the message says the run *started* and points at the session.
   */
  async #runNow() {
    if (!this.eventId) return;
    this._saving = true;
    this._error = '';
    this._message = '';
    try {
      const result = await DefaultService.wrapperApiCalendarEventsEventIdRunPost(this.eventId);
      this._event = result.event;
      this._message = 'Run started. Open its conversation to watch it.';
      this.#notifyChanged();
    } catch (err) {
      this._error = responseError(err) || String(err);
    } finally {
      this._saving = false;
    }
  }

  #openTargetConversation() {
    const convId = this._event?.target_conv_id;
    if (!convId) return;
    const sidebar = /** @type {any} */ (document.querySelector('conversation-sidebar'));
    sidebar?.store?.selectConversation(convId);
  }

  async #cancel() {
    if (!this.eventId) return;
    this._saving = true;
    this._error = '';
    try {
      await DefaultService.wrapperApiCalendarEventsEventIdDelete(this.eventId);
      this._message = 'Cancelled.';
      this.#notifyChanged();
      this.dispatchEvent(new CustomEvent('close', { bubbles: true }));
    } catch (err) {
      this._error = responseError(err) || String(err);
    } finally {
      this._saving = false;
    }
  }

  render() {
    if (this._loading) return html`<div class="calendar-page"><p>Loading…</p></div>`;
    const event = this._event;
    const fireAt = toLocalInput(event?.fire_at) || toLocalInput(new Date(Date.now() + 3600_000).toISOString());
    const timezone = event?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    return html`
      <div class="calendar-page">
        <div class="calendar-page-header">
          <button class="dc-icon-btn" @click=${() => this.dispatchEvent(new CustomEvent('close', { bubbles: true }))} title="Close">‹</button>
          <strong>${event ? event.title : 'New calendar event'}</strong>
        </div>
        ${this._error ? html`<p class="calendar-error">${this._error}</p>` : nothing}
        ${this._message ? html`<p class="calendar-message">${this._message}</p>` : nothing}
        <form class="calendar-form" @submit=${(e) => this.#save(e)}>
          <label>Title <input name="title" required .value=${event?.title || ''}></label>
          <label>Reason <textarea name="reason" rows="3" required .value=${event?.reason || ''}></textarea></label>
          <label>When <input type="datetime-local" name="fire_at" required .value=${fireAt}></label>
          <label>Timezone <input name="timezone" .value=${timezone}></label>
          <label>Repeat (RRULE) <input name="recurrence" placeholder="FREQ=DAILY" .value=${event?.recurrence || ''}></label>
          <label>Repeat until <input type="datetime-local" name="recurrence_until" .value=${toLocalInput(event?.recurrence_until)}></label>
          <label class="calendar-target">
            <input type="checkbox" .checked=${this._dedicated} @change=${(e) => { this._dedicated = e.currentTarget.checked; }}> Dedicated session
          </label>
          ${this._dedicated ? html`
            <label>Session key <input name="session_key" required .value=${event?.session_key || ''}></label>
            <label>Session title <input name="session_title" required .value=${event?.session_title || ''}></label>
          ` : nothing}
          <button type="submit" ?disabled=${this._saving}>${event ? 'Save' : 'Create'}</button>
        </form>
        ${event ? html`
          <div class="calendar-target-link">
            <span class="calendar-target-label">Runs in</span>
            <button
              class="calendar-target-chip"
              aria-label="Open the conversation this event runs in"
              @click=${() => this.#openTargetConversation()}
            >${event.target_title || event.target_conv_id}<span aria-hidden="true"> ↗</span></button>
          </div>
          <div class="calendar-actions">
            <button class="calendar-run-now" @click=${() => this.#runNow()} ?disabled=${this._saving || event.status !== 'pending'}>Run now</button>
            <button @click=${() => this.#snooze(900)} ?disabled=${this._saving}>Snooze 15m</button>
            <button @click=${() => this.#snooze(3600)} ?disabled=${this._saving}>1h</button>
            <button @click=${() => this.#snooze(86400)} ?disabled=${this._saving}>1d</button>
            <button class="calendar-cancel" @click=${() => this.#cancel()} ?disabled=${this._saving}>Cancel event</button>
          </div>
          <p class="calendar-run-note">Run now rehearses this event without moving its schedule.</p>
        ` : nothing}
      </div>
    `;
  }
}

customElements.define('calendar-page', CalendarPage);
