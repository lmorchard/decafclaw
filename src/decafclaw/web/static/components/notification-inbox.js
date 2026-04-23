import { LitElement, html, nothing } from 'lit';
import { formatRelativeTime } from '../lib/utils.js';

const POLL_INTERVAL_MS = 30_000;
const LIST_LIMIT = 20;

/**
 * @typedef {Object} NotificationRecord
 * @property {string} id
 * @property {string} timestamp
 * @property {string} category
 * @property {string} title
 * @property {string} body
 * @property {string} priority
 * @property {?string} link
 * @property {?string} conv_id
 * @property {boolean} read
 */

export class NotificationInbox extends LitElement {
  static properties = {
    _open: { type: Boolean, state: true },
    _count: { type: Number, state: true },
    _records: { type: Array, state: true },
    _loading: { type: Boolean, state: true },
    _error: { type: String, state: true },
    _panelStyle: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this._open = false;
    this._count = 0;
    /** @type {NotificationRecord[]} */
    this._records = [];
    this._loading = false;
    this._error = '';
    this._panelStyle = '';
    this._pollTimer = null;
  }

  connectedCallback() {
    super.connectedCallback();
    // Initial fetch + poll
    this.#refreshCount();
    this._pollTimer = window.setInterval(() => this.#refreshCount(), POLL_INTERVAL_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
    this.#removeDocClickListener();
  }

  async #refreshCount() {
    try {
      const res = await fetch('/api/notifications/unread-count');
      if (!res.ok) return;
      const data = await res.json();
      this._count = data.count || 0;
    } catch (_e) {
      // Poll quietly — network blips shouldn't break UI
    }
  }

  async #refreshList() {
    this._loading = true;
    this._error = '';
    try {
      const res = await fetch(`/api/notifications?limit=${LIST_LIMIT}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this._records = data.records || [];
    } catch (e) {
      this._error = /** @type {Error} */ (e).message || 'Failed to load';
    } finally {
      this._loading = false;
    }
  }

  #togglePanel() {
    if (this._open) {
      this.#closePanel();
    } else {
      this.#openPanel();
    }
  }

  #openPanel() {
    if (this._open) return;
    this._open = true;
    this.#positionPanel();
    this.#refreshList();
    // Defer so the current click doesn't immediately close the panel via
    // the document-level listener we're about to register. Re-check _open
    // on the other side of the frame boundary — a rapid re-close can
    // flip it back to false, and we shouldn't register an orphan listener.
    requestAnimationFrame(() => {
      if (!this._open) return;
      this._onDocClick = (e) => {
        if (!this.contains(e.target)) this.#closePanel();
      };
      document.addEventListener('click', this._onDocClick, true);
      this._onWinResize = () => this.#positionPanel();
      window.addEventListener('resize', this._onWinResize);
    });
  }

  #closePanel() {
    if (!this._open) return;
    this._open = false;
    this.#removeDocClickListener();
    if (this._onWinResize) {
      window.removeEventListener('resize', this._onWinResize);
      this._onWinResize = null;
    }
  }

  /**
   * Position the panel fixed to the viewport so it escapes the sidebar's
   * overflow:hidden and floats above the page. Panel opens to the right of
   * the bell, bottom-aligned so it rises upward from the footer.
   */
  #positionPanel() {
    const bell = this.querySelector('.notification-bell');
    if (!bell) return;
    const rect = bell.getBoundingClientRect();
    const panelWidth = Math.min(360, window.innerWidth - 16);
    const gap = 6;
    let left = rect.right + gap;
    // If the panel would overflow the right edge, nudge it back.
    if (left + panelWidth > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - panelWidth - 8);
    }
    const bottom = Math.max(8, window.innerHeight - rect.bottom);
    this._panelStyle = `left:${left}px; bottom:${bottom}px; width:${panelWidth}px;`;
  }

  #removeDocClickListener() {
    if (this._onDocClick) {
      document.removeEventListener('click', this._onDocClick, true);
      this._onDocClick = null;
    }
  }

  /** @param {NotificationRecord} rec */
  async #onRowClick(rec) {
    // Mark read locally + on server. Replace the record in the array so
    // Lit sees the reference change — don't mutate rec in place.
    if (!rec.read) {
      this._records = this._records.map(r =>
        r.id === rec.id ? { ...r, read: true } : r
      );
      this._count = Math.max(0, this._count - 1);
      try {
        await fetch(`/api/notifications/${encodeURIComponent(rec.id)}/read`, { method: 'POST' });
      } catch (_e) {
        // If the mark-read call fails the server truth will refresh on next poll.
      }
    }
    this.#navigate(rec);
  }

  /** @param {NotificationRecord} rec */
  #navigate(rec) {
    // Link is optional — fall back to conv_id when set. Producers that
    // correlate to a conversation (background, schedule, reflection) are
    // navigable even without an explicit link.
    let link = rec.link || '';
    if (!link && rec.conv_id) link = `conv://${rec.conv_id}`;
    if (!link) {
      this.#closePanel();
      return;
    }
    if (link.startsWith('conv://')) {
      const convId = link.slice('conv://'.length);
      this.dispatchEvent(new CustomEvent('navigate-conversation', {
        detail: { convId },
        bubbles: true,
        composed: true,
      }));
      this.#closePanel();
      return;
    }
    if (link.startsWith('vault://')) {
      const page = link.slice('vault://'.length);
      this.dispatchEvent(new CustomEvent('navigate-vault', {
        detail: { page },
        bubbles: true,
        composed: true,
      }));
      this.#closePanel();
      return;
    }
    if (link.startsWith('http://') || link.startsWith('https://')) {
      window.open(link, '_blank', 'noopener');
      return;
    }
    // Unknown scheme (mm://, etc.) — no-op, stay open
  }

  async #markAllRead() {
    try {
      await fetch('/api/notifications/read-all', { method: 'POST' });
      this._records = this._records.map(r => ({ ...r, read: true }));
      this._count = 0;
    } catch (_e) {
      // Ignore; next poll will re-sync.
    }
  }

  #renderBadge() {
    if (!this._count) return nothing;
    const label = this._count > 99 ? '99+' : String(this._count);
    return html`<span class="notification-badge">${label}</span>`;
  }

  #renderRow(rec) {
    const rel = formatRelativeTime(rec.timestamp);
    return html`
      <button
        class="notification-row ${rec.read ? 'read' : 'unread'}"
        @click=${() => this.#onRowClick(rec)}
        type="button"
      >
        <span class="notification-dot" aria-hidden="true">${rec.read ? '' : '●'}</span>
        <span class="notification-content">
          <span class="notification-title">${rec.title}</span>
          ${rec.body ? html`<span class="notification-body">${rec.body}</span>` : nothing}
          <span class="notification-meta">
            <span class="notification-category">${rec.category}</span>
            <span class="notification-time">${rel}</span>
          </span>
        </span>
      </button>
    `;
  }

  #renderPanel() {
    if (!this._open) return nothing;
    let body;
    if (this._loading && !this._records.length) {
      body = html`<div class="notification-empty">Loading…</div>`;
    } else if (this._error) {
      body = html`<div class="notification-empty">Error: ${this._error}</div>`;
    } else if (!this._records.length) {
      body = html`<div class="notification-empty">No notifications yet.</div>`;
    } else {
      body = html`
        <div class="notification-list">
          ${this._records.map(r => this.#renderRow(r))}
        </div>
      `;
    }
    return html`
      <div class="notification-panel" role="dialog" aria-label="Notifications"
           style=${this._panelStyle}>
        <div class="notification-panel-header">
          <span>Notifications</span>
          <button
            class="notification-mark-all"
            type="button"
            ?disabled=${this._count === 0}
            @click=${() => this.#markAllRead()}
          >Mark all read</button>
        </div>
        ${body}
      </div>
    `;
  }

  render() {
    const hasUnread = this._count > 0;
    return html`
      <button
        class="notification-bell ${hasUnread ? 'has-unread' : ''}"
        type="button"
        aria-label=${hasUnread ? `Notifications (${this._count} unread)` : 'Notifications'}
        aria-expanded=${this._open}
        @click=${() => this.#togglePanel()}
      >
        <span class="notification-bell-icon" aria-hidden="true">🔔</span>
        ${this.#renderBadge()}
      </button>
      ${this.#renderPanel()}
    `;
  }
}

customElements.define('notification-inbox', NotificationInbox);
