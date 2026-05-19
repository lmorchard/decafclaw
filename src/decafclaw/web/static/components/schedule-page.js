/**
 * Schedule page — side-panel editor for a single scheduled task.
 *
 * Hosted inside #wiki-main alongside wiki-page, file-page, config-panel.
 * Dispatches 'close' CustomEvent when the user clicks the back button.
 * Dispatches 'schedule-saved' on window after any successful write.
 */

import { LitElement, html, nothing } from 'lit';
import './wiki-editor.js';

export class SchedulePage extends LitElement {
  static properties = {
    name: { type: String, reflect: true },
    _data: { state: true },
    _loading: { state: true },
    _runStatus: { state: true },
    _runError: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.name = '';
    /** @type {object|null} */
    this._data = null;
    this._loading = false;
    /** @type {''|'running'|'started'|'error'} */
    this._runStatus = '';
    this._runError = '';
  }

  /** @param {Map<string, any>} changedProps */
  updated(changedProps) {
    if (changedProps.has('name') && this.name) {
      this.#fetchSchedule();
    }
  }

  async #fetchSchedule() {
    this._loading = true;
    try {
      const res = await fetch(`/api/schedules/${encodeURIComponent(this.name)}`);
      if (res.ok) {
        const data = await res.json();
        this._data = data.schedule;
      } else {
        this._data = null;
      }
    } catch {
      this._data = null;
    } finally {
      this._loading = false;
    }
  }

  /**
   * @param {string} field
   * @param {unknown} value
   */
  async #patchField(field, value) {
    if (!this._data) return;
    try {
      const res = await fetch(`/api/schedules/${encodeURIComponent(this.name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value }),
      });
      if (!res.ok) {
        console.warn(`schedule-page: PUT ${field} failed:`, res.status);
        return;
      }
      const data = await res.json();
      this._data = data.schedule;
      window.dispatchEvent(new CustomEvent('schedule-saved'));
    } catch (e) {
      console.warn('schedule-page: PUT error:', e);
    }
  }

  async #resetOverlay() {
    if (!confirm(`Reset "${this.name}" to its skill default?`)) return;
    try {
      const res = await fetch(`/api/schedules/${encodeURIComponent(this.name)}/overlay`, {
        method: 'DELETE',
      });
      if (res.ok) {
        window.dispatchEvent(new CustomEvent('schedule-saved'));
        // Re-fetch (not just _data assignment) so the loading swap in
        // render() unmounts + remounts wiki-editor with the bundled body.
        // Milkdown only reads its initial content once in firstUpdated.
        await this.#fetchSchedule();
      }
    } catch (e) {
      console.warn('schedule-page: reset error:', e);
    }
  }

  #close() {
    this.dispatchEvent(new CustomEvent('close', { bubbles: true, composed: true }));
  }

  async #runNow() {
    this._runStatus = 'running';
    this._runError = '';
    try {
      const res = await fetch(
        `/api/schedules/${encodeURIComponent(this.name)}/run`,
        { method: 'POST' },
      );
      if (!res.ok) {
        this._runStatus = 'error';
        this._runError = `Failed (${res.status})`;
        return;
      }
      this._runStatus = 'started';
      setTimeout(() => {
        if (this._runStatus === 'started') this._runStatus = '';
      }, 3000);
    } catch (e) {
      console.warn('schedule-page: run-now error:', e);
      this._runStatus = 'error';
      this._runError = 'Network error';
    }
  }

  /**
   * After wiki-editor saves, refresh metadata (source_tier, has_overlay,
   * etc.) so badges and the reset button update. The existing body is
   * preserved so wiki-editor doesn't see a .content change — we
   * deliberately avoid triggering the loading-swap remount path here
   * because the user is actively editing.
   */
  async #onWikiSaved(/** @type {CustomEvent} */ e) {
    if (this._data && typeof e.detail?.modified === 'number') {
      this._data = { ...this._data, modified: e.detail.modified };
    }
    try {
      const res = await fetch(`/api/schedules/${encodeURIComponent(this.name)}`);
      if (res.ok) {
        const data = await res.json();
        const preservedBody = this._data?.body ?? data.schedule.body;
        this._data = { ...data.schedule, body: preservedBody };
        window.dispatchEvent(new CustomEvent('schedule-saved'));
      }
    } catch (err) {
      console.warn('schedule-page: post-save metadata refresh failed:', err);
    }
  }

  render() {
    // Loading-swap is load-bearing: rendering a non-editor placeholder
    // while loading causes wiki-editor to unmount + remount when the
    // selected schedule changes. Milkdown only reads its initial content
    // once (firstUpdated) and ignores subsequent .content property
    // changes, so the editor must be torn down to display a different
    // schedule's body.
    if (this._loading || !this._data) {
      return html`<div class="schedule-page-empty">${this._loading ? 'Loading…' : 'Not found.'}</div>`;
    }
    const d = /** @type {any} */ (this._data);
    return html`
      <div class="schedule-page">
        <div class="schedule-page-header dc-overlay-header">
          <button class="dc-icon-btn" @click=${this.#close} title="Back" aria-label="Back">&larr;</button>
          <span class="schedule-page-title">${d.name}</span>
          <span class="schedule-tier-badge tier-${d.source_tier}">${d.source_tier}</span>
          ${d.has_overlay ? html`<span class="schedule-overlay-badge">overridden</span>` : nothing}
          <button class="outline schedule-run-btn" @click=${this.#runNow}>Run now</button>
          ${this._runStatus === 'running' ? html`<span class="schedule-run-status">Running…</span>` :
            this._runStatus === 'started' ? html`<span class="schedule-run-status">Started ✓</span>` :
            this._runStatus === 'error' ? html`<span class="schedule-run-status error">${this._runError}</span>` :
            nothing}
          ${d.has_overlay ? html`
            <button class="outline schedule-reset-btn" @click=${this.#resetOverlay}>Reset to default</button>
          ` : nothing}
        </div>
        <div class="schedule-page-form">
          <label>
            <span>Cron</span>
            <input type="text" .value=${d.schedule}
              @change=${(/** @type {Event} */ e) => this.#patchField('schedule', /** @type {HTMLInputElement} */ (e.target).value)} />
          </label>
          <label>
            <span>Channel</span>
            <input type="text" .value=${d.channel || ''} placeholder="(default channel)"
              @change=${(/** @type {Event} */ e) => this.#patchField('channel', /** @type {HTMLInputElement} */ (e.target).value)} />
          </label>
          <label class="inline">
            <input type="checkbox" .checked=${d.enabled}
              @change=${(/** @type {Event} */ e) => this.#patchField('enabled', /** @type {HTMLInputElement} */ (e.target).checked)} />
            <span>Enabled</span>
          </label>
        </div>
        <wiki-editor
          .page=${d.name}
          .content=${d.body}
          .modified=${d.modified || 0}
          save-endpoint="/api/schedules/"
          @saved=${(/** @type {CustomEvent} */ e) => this.#onWikiSaved(e)}
        ></wiki-editor>
      </div>
    `;
  }
}

customElements.define('schedule-page', SchedulePage);
