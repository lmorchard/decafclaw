import { LitElement, html, nothing } from 'lit';
import { DefaultService, ApiError } from '../lib/api-client/index.js';

/**
 * Capacity gauges for the VM DecafClaw runs on (VM 302).
 *
 * The agent spawns builds, test suites, and subagent CLIs on the same 4 vCPU
 * box as this process, so the point of this strip is to make host pressure
 * visible before it becomes a lockup. It reads the authenticated `/api/host`
 * snapshot, which is fed by a server-side sampler — never `/proc` directly —
 * because CPU needs a delta between two samples and a browser cannot take one.
 *
 * Deliberately a strip, not a dashboard: four readings, a hover for detail.
 *
 * @typedef {import('../lib/api-client/index.js').HostMetricsResponse} HostGaugeData
 */

const POLL_MS = 5000;
const MIB = 1024 * 1024;

/**
 * Format a byte count compactly. Uses binary units because memory and tmpfs
 * sizes are conventionally read that way, and treats null as unknown rather
 * than zero — a missing gauge must not look like a full disk.
 * @param {number|null|undefined} bytes
 * @returns {string}
 */
function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '—';
  const gib = bytes / (MIB * 1024);
  if (gib >= 1) return `${gib.toFixed(1)}G`;
  return `${Math.round(bytes / MIB)}M`;
}

export class HostGauges extends LitElement {
  static properties = {
    _data: { type: Object, state: true },
    _error: { type: String, state: true },
    _stale: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {HostGaugeData|null} */
    this._data = null;
    this._error = '';
    this._stale = false;
    this._timer = null;
    this._inFlight = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this.#poll();
    this._timer = setInterval(() => this.#poll(), POLL_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  async #poll() {
    // Skip a tick rather than stacking requests if the last one is still
    // outstanding — on a loaded host the reply can take longer than POLL_MS.
    if (this._inFlight) return;
    this._inFlight = true;
    try {
      const data = await DefaultService.wrapperApiHostGet();
      this._data = data;
      this._error = '';
      this._stale = false;
    } catch (err) {
      // A 401 means the session ended; app.js owns redirecting to login, so
      // leave the strip quiet and drop the stale numbers instead of shouting.
      if (err instanceof ApiError && err.status === 401) {
        this._data = null;
        this._error = '';
        return;
      }
      this._error = err instanceof Error ? err.message : String(err);
      this._stale = true;
    } finally {
      this._inFlight = false;
    }
  }

  /** Threshold band for a percentage: '', 'warn', or 'danger'. */
  #band(pct) {
    if (typeof pct !== 'number') return '';
    if (pct >= 90) return 'danger';
    if (pct >= 75) return 'warn';
    return '';
  }

  /**
   * Load is only meaningful against the core count: 4.0 is saturated on this
   * VM's 4 vCPUs, whereas the same number on a 16-core box is idle.
   */
  #loadBand(load, cpuCount) {
    if (typeof load !== 'number' || !cpuCount) return '';
    const ratio = load / cpuCount;
    if (ratio >= 1) return 'danger';
    if (ratio >= 0.75) return 'warn';
    return '';
  }

  #renderMemory() {
    const mem = this._data?.memory;
    if (!mem) return this.#renderUnknown('MEM', 'Memory unavailable');
    const band = this.#band(mem.used_percent);
    const title = [
      `Memory: ${formatBytes(mem.used_bytes)} used of ${formatBytes(mem.total_bytes)}`,
      `${mem.used_percent}% used, ${formatBytes(mem.available_bytes)} available`,
      mem.swap_total_bytes > 0
        ? `Swap: ${formatBytes(mem.swap_used_bytes)} of ${formatBytes(mem.swap_total_bytes)} (${mem.swap_used_percent}%)`
        : 'Swap: none configured',
    ].join('\n');
    return html`
      <span class="gauge ${band}" title="${title}" aria-label="${title}">
        <span class="gauge-label">MEM</span>
        <span class="gauge-value">${formatBytes(mem.available_bytes)}</span>
      </span>
    `;
  }

  #renderCpu() {
    const pct = this._data?.cpu_percent;
    const count = this._data?.cpu_count;
    if (typeof pct !== 'number') {
      // Honest cold start: the first sampling window has no delta yet.
      return this.#renderUnknown('CPU', 'CPU: waiting for second sample');
    }
    const title = `CPU: ${pct}% busy across ${count ?? '?'} vCPU`;
    return html`
      <span class="gauge ${this.#band(pct)}" title="${title}" aria-label="${title}">
        <span class="gauge-label">CPU</span>
        <span class="gauge-value">${Math.round(pct)}%</span>
      </span>
    `;
  }

  #renderLoad() {
    const load = this._data?.load;
    const count = this._data?.cpu_count;
    if (!load) return this.#renderUnknown('LOAD', 'Load average unavailable');
    const title = [
      `Load: ${load.one} (1m) / ${load.five} (5m) / ${load.fifteen} (15m)`,
      count ? `${count} vCPU — ${(load.one / count * 100).toFixed(0)}% of capacity` : '',
      load.running !== null && load.running !== undefined
        ? `${load.running} runnable of ${load.total} tasks`
        : '',
    ].filter(Boolean).join('\n');
    return html`
      <span class="gauge ${this.#loadBand(load.one, count)}" title="${title}" aria-label="${title}">
        <span class="gauge-label">LOAD</span>
        <span class="gauge-value">${load.one.toFixed(2)}</span>
      </span>
    `;
  }

  #renderDisk() {
    const disk = this._data?.disk_root;
    if (!disk) return this.#renderUnknown('DISK', 'Disk unavailable');
    const band = this.#band(disk.used_percent);
    // Surface free space, not used: the number that predicts a failed build
    // is what is left, not what is spent.
    const title = [
      `Disk ${disk.path}: ${formatBytes(disk.available_bytes)} free of ${formatBytes(disk.total_bytes)}`,
      `${disk.used_percent}% used`,
    ].join('\n');
    return html`
      <span class="gauge ${band}" title="${title}" aria-label="${title}">
        <span class="gauge-label">DISK</span>
        <span class="gauge-value">${formatBytes(disk.available_bytes)}</span>
      </span>
    `;
  }

  #renderTmpfs() {
    const tmp = this._data?.disk_tmpfs;
    // /tmp is RAM-backed on this host, so a filling tmpfs is memory pressure
    // wearing a disk costume. Only worth showing when there is one.
    if (!tmp || !tmp.total_bytes) return nothing;
    const band = this.#band(tmp.used_percent);
    const title = [
      `tmpfs ${tmp.path} (RAM-backed): ${formatBytes(tmp.used_bytes)} of ${formatBytes(tmp.total_bytes)}`,
      `${tmp.used_percent}% used — this counts against available memory`,
    ].join('\n');
    return html`
      <span class="gauge ${band}" title="${title}" aria-label="${title}">
        <span class="gauge-label">TMP</span>
        <span class="gauge-value">${formatBytes(tmp.used_bytes)}</span>
      </span>
    `;
  }

  #renderProcs() {
    const count = this._data?.process_count;
    if (typeof count !== 'number') return nothing;
    // Agent litter is visible here: shell jobs, subagent CLIs, language servers.
    const title = `${count} processes on the host`;
    return html`
      <span class="gauge" title="${title}" aria-label="${title}">
        <span class="gauge-label">PROC</span>
        <span class="gauge-value">${count}</span>
      </span>
    `;
  }

  #renderUnknown(label, title) {
    return html`
      <span class="gauge unknown" title="${title}" aria-label="${title}">
        <span class="gauge-label">${label}</span>
        <span class="gauge-value">—</span>
      </span>
    `;
  }

  render() {
    if (this._error && !this._data) {
      return html`
        <div class="host-gauges error" title="${this._error}">
          <span class="gauge-label">HOST</span>
          <span class="gauge-value">—</span>
        </div>
      `;
    }
    if (!this._data) {
      return html`<div class="host-gauges loading" aria-hidden="true"></div>`;
    }
    return html`
      <div class="host-gauges ${this._stale ? 'stale' : ''}" role="group" aria-label="Host capacity">
        ${this.#renderMemory()}
        ${this.#renderCpu()}
        ${this.#renderLoad()}
        ${this.#renderDisk()}
        ${this.#renderTmpfs()}
        ${this.#renderProcs()}
      </div>
    `;
  }
}

customElements.define('host-gauges', HostGauges);
