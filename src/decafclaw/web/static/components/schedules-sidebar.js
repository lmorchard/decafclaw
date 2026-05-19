import { LitElement, html, nothing } from 'lit';

/**
 * @typedef {{
 *   name: string,
 *   source_tier: string,
 *   source_path: string,
 *   has_overlay: boolean,
 *   enabled: boolean,
 *   schedule: string,
 *   channel: string,
 *   model: string,
 *   allowed_tools: string[],
 *   required_skills: string[],
 *   body: string,
 *   modified: number,
 *   next_run_iso: string|null,
 *   last_run_iso: string|null,
 * }} ScheduleEntry
 */

export class SchedulesSidebar extends LitElement {
  static properties = {
    active: { type: Boolean },
    openName: { type: String },
    _schedules: { type: Array, state: true },
    _loading: { type: Boolean, state: true },
    _runStatus: { type: Object, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.active = false;
    this.openName = '';
    /** @type {ScheduleEntry[]} */
    this._schedules = [];
    this._loading = false;
    /** Per-schedule transient run state: 'running' | 'started' | 'error'.
     * Reassigned (not mutated) on each change so Lit detects it.
     * @type {Record<string, 'running'|'started'|'error'>} */
    this._runStatus = {};
  }

  connectedCallback() {
    super.connectedCallback();
    this._onScheduleSaved = () => {
      if (this.active) this.#fetchSchedules();
    };
    window.addEventListener('schedule-saved', this._onScheduleSaved);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener('schedule-saved', this._onScheduleSaved);
  }

  /** @param {Map} changedProps */
  updated(changedProps) {
    // Re-fetch on every false→true transition of `active`.
    if (changedProps.has('active') && this.active && !changedProps.get('active')) {
      this.#fetchSchedules();
    }
  }

  async #fetchSchedules() {
    this._loading = true;
    try {
      const res = await fetch('/api/schedules');
      if (res.ok) {
        const data = await res.json();
        this._schedules = data.schedules || [];
      } else {
        this._schedules = [];
      }
    } catch {
      this._schedules = [];
    } finally {
      this._loading = false;
    }
  }

  /**
   * @param {string} name
   * @param {boolean} currentEnabled
   */
  async #toggleEnabled(name, currentEnabled) {
    const res = await fetch(`/api/schedules/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !currentEnabled }),
    });
    if (!res.ok) {
      console.warn('schedules-sidebar: toggle failed:', res.status);
    }
    this.#fetchSchedules();
  }

  /**
   * Fire a schedule immediately via POST /api/schedules/{name}/run.
   * Shows a transient inline status indicator next to the row's button.
   * @param {string} name
   */
  async #runNow(name) {
    this._runStatus = { ...this._runStatus, [name]: 'running' };
    try {
      const res = await fetch(
        `/api/schedules/${encodeURIComponent(name)}/run`,
        { method: 'POST' },
      );
      if (!res.ok) {
        console.warn(`schedules-sidebar: run failed for ${name}:`, res.status);
        this._runStatus = { ...this._runStatus, [name]: 'error' };
        return;
      }
      this._runStatus = { ...this._runStatus, [name]: 'started' };
      // Clear after a short window unless a subsequent run replaced it.
      setTimeout(() => {
        if (this._runStatus[name] === 'started') {
          const copy = { ...this._runStatus };
          delete copy[name];
          this._runStatus = copy;
        }
      }, 2500);
    } catch (e) {
      console.warn(`schedules-sidebar: run error for ${name}:`, e);
      this._runStatus = { ...this._runStatus, [name]: 'error' };
    }
  }

  /** @param {string} name */
  #openSchedule(name) {
    this.dispatchEvent(new CustomEvent('schedule-open', {
      detail: { name },
      bubbles: true,
      composed: true,
    }));
  }

  /** Format an ISO datetime string as a short human-readable relative time. */
  #formatNextRun(isoStr) {
    if (!isoStr) return null;
    try {
      const dt = new Date(isoStr);
      const diffMs = dt.getTime() - Date.now();
      if (diffMs < 0) return 'overdue';
      const diffMin = Math.round(diffMs / 60000);
      if (diffMin < 60) return `in ${diffMin}m`;
      const diffH = Math.round(diffMin / 60);
      if (diffH < 24) return `in ${diffH}h`;
      const diffD = Math.round(diffH / 24);
      return `in ${diffD}d`;
    } catch {
      return isoStr;
    }
  }

  /** @param {ScheduleEntry} s */
  #renderRow(s) {
    const isOpen = this.openName === s.name;
    const nextRun = this.#formatNextRun(s.next_run_iso);
    const runStatus = this._runStatus[s.name];
    return html`
      <div class="schedule-row ${isOpen ? 'open' : ''}">
        <div class="schedule-row-header"
             @click=${() => this.#openSchedule(s.name)}>
          <span class="schedule-name">${s.name}</span>
          <span class="schedule-tier-badge tier-${s.source_tier}">${s.source_tier}</span>
          ${s.has_overlay ? html`<span class="schedule-overlay-badge">overridden</span>` : nothing}
          <button class="dc-icon-btn schedule-row-run"
                  @click=${(/** @type {Event} */ e) => { e.stopPropagation(); this.#runNow(s.name); }}
                  title="Run now"
                  aria-label=${`Run ${s.name} now`}>
            ▶
          </button>
          ${runStatus === 'running' ? html`<span class="schedule-row-run-status">…</span>` :
            runStatus === 'started' ? html`<span class="schedule-row-run-status ok">✓</span>` :
            runStatus === 'error' ? html`<span class="schedule-row-run-status error">!</span>` :
            nothing}
          <input type="checkbox"
                 class="schedule-enabled-toggle"
                 .checked=${s.enabled}
                 aria-label=${`Toggle ${s.name} enabled`}
                 @click=${(/** @type {Event} */ e) => e.stopPropagation()}
                 @change=${() => this.#toggleEnabled(s.name, s.enabled)}
                 title=${s.enabled ? 'Disable schedule' : 'Enable schedule'} />
        </div>
        <div class="schedule-row-meta">
          <code class="schedule-cron">${s.schedule}</code>
          ${nextRun ? html`<span class="schedule-next">${nextRun}</span>` : nothing}
        </div>
      </div>
    `;
  }

  render() {
    if (this._loading && this._schedules.length === 0) {
      return html`
        <div class="conv-list">
          <p style="padding: 1rem; color: var(--pico-muted-color);">Loading schedules…</p>
        </div>
      `;
    }
    if (this._schedules.length === 0) {
      return html`
        <div class="conv-list">
          <p style="padding: 1rem; color: var(--pico-muted-color);">No schedules found.</p>
        </div>
      `;
    }
    return html`
      <div class="conv-list schedules-list">
        <div class="vault-action-btns">
          <button class="outline" @click=${() => this.#fetchSchedules()} title="Refresh">Refresh</button>
        </div>
        ${this._schedules.map(s => this.#renderRow(s))}
      </div>
    `;
  }
}

customElements.define('schedules-sidebar', SchedulesSidebar);
