/**
 * Schedule page — side-panel editor for a single scheduled task.
 *
 * Hosted inside #wiki-main alongside wiki-page, file-page, config-panel.
 * Dispatches 'close' CustomEvent when the user clicks the back button.
 * Dispatches 'schedule-saved' on window after any successful write.
 */

import { LitElement, html, nothing } from 'lit';
import { ApiError, DefaultService } from '../lib/api-client/index.js';
import './wiki-editor.js';
import './schedule-metadata.js';

/** @typedef {import('../lib/api-client/index.js').ScheduleResponse} Schedule */
/** @typedef {import('../lib/api-client/index.js').ModelListResponse['models']} ModelChoices */
/** @typedef {Parameters<typeof DefaultService.wrapperApiSchedulesNamePut>[1]} SchedulePatch */

/** @param {unknown} body */
function responseError(body) {
  if (!body || typeof body !== 'object' || !("error" in body)) return '';
  return typeof body.error === 'string' ? body.error : '';
}

export class SchedulePage extends LitElement {
  static properties = {
    name: { type: String, reflect: true },
    _data: { state: true },
    _loading: { state: true },
    _runStatus: { state: true },
    _runError: { state: true },
    _models: { state: true },
    _modelsLoaded: { state: true },
    _modelsUnavailable: { state: true },
    _saveError: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.name = '';
    /** @type {Schedule|null} */
    this._data = null;
    this._loading = false;
    /** @type {''|'running'|'started'|'error'} */
    this._runStatus = '';
    this._runError = '';
    /** @type {ModelChoices} */
    this._models = [];
    /** True once a fetch has succeeded, empty list or not. */
    this._modelsLoaded = false;
    this._modelsUnavailable = false;
    this._saveError = '';
  }

  /** @param {Map<string, unknown>} changedProps */
  updated(changedProps) {
    if (changedProps.has('name') && this.name) {
      // schedule-page is a singleton (app.js reassigns .name rather than
      // recreating the element); a save error from the previous schedule
      // must not linger and get misattributed to the newly selected one.
      this._saveError = '';
      this.#fetchSchedule();
      // Gate on "did a fetch succeed", not on "is the list non-empty" —
      // no model_configs is a legitimate 200 with an empty list, and
      // keying on length refetched on every schedule selection. A failed
      // fetch leaves this false so the next selection retries.
      if (!this._modelsLoaded) this.#fetchModels();
    }
  }

  async #fetchSchedule() {
    this._loading = true;
    try {
      const data = await DefaultService.wrapperApiSchedulesNameGet(this.name);
      this._data = data.schedule;
    } catch {
      this._data = null;
    } finally {
      this._loading = false;
    }
  }

  async #fetchModels() {
    try {
      const data = await DefaultService.wrapperApiModelsGet();
      this._models = data.models;
      // An empty list from a 200 is a real state (no model_configs), so
      // it stays "available" — the panel shows an honest one-entry
      // dropdown rather than the manual-entry fallback.
      this._modelsUnavailable = false;
      this._modelsLoaded = true;
    } catch (e) {
      // Non-fatal: the panel falls back to a free-text model field.
      this._modelsUnavailable = true;
      console.warn('schedule-page: model list fetch failed:', e);
    }
  }

  /**
   * @param {SchedulePatch} fields
   */
  async #patchFields(fields) {
    if (!this._data) return;
    try {
      const data = await DefaultService.wrapperApiSchedulesNamePut(this.name, fields);
      this._saveError = '';
      this._data = data.schedule;
      window.dispatchEvent(new CustomEvent('schedule-saved'));
    } catch (error) {
      if (error instanceof ApiError) {
        /** @type {unknown} */ const body = error.body;
        this._saveError = responseError(body) || `save failed (${error.status})`;
        console.warn('schedule-page: PUT failed:', error.status);
        return;
      }
      // Offline or a restarting server never reaches the HTTP-error
      // path above, so without this the edit vanishes with no feedback
      // at all — the same invisibility the status-code branch fixes.
      this._saveError = 'save failed: could not reach the server';
      console.warn('schedule-page: PUT error:', error);
    }
  }

  /** @param {CustomEvent<{fields: SchedulePatch}>} e */
  async #onMetadataChange(e) {
    const fields = e.detail?.fields ?? {};
    await this.#patchFields(fields);
  }

  async #resetOverlay() {
    if (!confirm(`Reset "${this.name}" to its skill default?`)) return;
    try {
      await DefaultService.wrapperApiSchedulesNameOverlayDelete(this.name, true);
      window.dispatchEvent(new CustomEvent('schedule-saved'));
      // Re-fetch (not just _data assignment) so the loading swap in
      // render() unmounts + remounts wiki-editor with the bundled body.
      // Milkdown only reads its initial content once in firstUpdated.
      await this.#fetchSchedule();
    } catch (e) {
      if (e instanceof ApiError) return;
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
      await DefaultService.wrapperApiSchedulesNameRunPost(this.name, true);
      this._runStatus = 'started';
      setTimeout(() => {
        if (this._runStatus === 'started') this._runStatus = '';
      }, 3000);
    } catch (e) {
      if (e instanceof ApiError) {
        this._runStatus = 'error';
        this._runError = `Failed (${e.status})`;
        return;
      }
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
      const data = await DefaultService.wrapperApiSchedulesNameGet(this.name);
      const preservedBody = this._data?.body ?? data.schedule.body;
      this._data = { ...data.schedule, body: preservedBody };
      window.dispatchEvent(new CustomEvent('schedule-saved'));
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
    const d = this._data;
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
        <schedule-metadata
          .data=${d}
          .models=${this._models}
          .modelsUnavailable=${this._modelsUnavailable}
          .error=${this._saveError}
          @metadata-change=${(/** @type {CustomEvent} */ e) => this.#onMetadataChange(e)}
        ></schedule-metadata>
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
