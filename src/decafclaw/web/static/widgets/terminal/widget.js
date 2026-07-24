import { LitElement, html, nothing } from 'lit';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';

// Reconnect backoff schedule (ms) for unexpected WS closes. Capped at
// BACKOFF.length attempts — after that we give up and show an error banner
// (no auto-retry; reloading the page starts over).
const BACKOFF = [1000, 2000, 4000, 8000, 16000, 30000];

/**
 * Live PTY terminal widget. Canvas-only — opens a WebSocket to
 * `/ws/terminal/{convId}/{tabId}`, streams binary output into an xterm.js
 * surface, and forwards keystrokes + resizes back to the server.
 *
 * Props:
 *   data    { session_id, cwd?, shell? } — descriptive only; the socket
 *           URL is built from convId/tabId, not from data.
 *   convId  set by the canvas host (wired in a later task).
 *   tabId   set by the canvas host (wired in a later task).
 *
 * Until the host sets both convId and tabId, the widget stays in a
 * "waiting" state — it never opens a socket and never spams reconnects.
 */
export class TerminalWidget extends LitElement {
  static properties = {
    data: { attribute: false },
    convId: { attribute: false },
    tabId: { attribute: false },
    _state: { state: true },
    _ended: { state: true },
  };

  createRenderRoot() { return this; }  // light DOM, matches other widgets

  constructor() {
    super();
    /** @type {{session_id?: string, cwd?: string, shell?: string}|null} */
    this.data = null;
    /** @type {string|null} */
    this.convId = null;
    /** @type {string|null} */
    this.tabId = null;
    this._state = 'waiting';     // waiting|connecting|replaying|attached|disconnected|error|ended
    this._ended = null;          // exit status once ended
    this._attempts = 0;
    this._replaying = false;
    this._ws = null;
    this._term = null;
    this._fit = null;
    this._ro = null;
    this._surface = null;
    this._reconnectTimer = null;
    this._resizeTimer = null;    // debounce for ResizeObserver-driven fits
    this._lastCols = 0;          // last size sent to the server (resize dedup)
    this._lastRows = 0;
    this._connectedKey = null;   // `${convId}/${tabId}` of the currently-targeted session
  }

  connectedCallback() {
    super.connectedCallback();
    this._attempts = 0;   // fresh reconnect budget on (re)mount
    this._mountTerminal();
    this._maybeConnect();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._teardown();
  }

  /** @param {Map<string, any>} changed */
  updated(changed) {
    if (!(changed.has('convId') || changed.has('tabId'))) return;
    // A shared-host standalone canvas window reuses one widget instance
    // across terminal tabs (widget-host only recreates the child when the
    // *tag* changes, not the props). If convId/tabId now point at a
    // different session than the one we're connected to, the old socket
    // must be dropped and a fresh one opened — otherwise keystrokes keep
    // routing to the previous tab's shell.
    const key = (this.convId && this.tabId) ? `${this.convId}/${this.tabId}` : null;
    if (key && this._connectedKey && key !== this._connectedKey) {
      this._retarget();
      return;
    }
    this._maybeConnect();
  }

  /** Drop the old session's socket/state and reconnect to the new target. */
  _retarget() {
    this._teardownSocket();
    this._term.reset();   // clear the old session's scrollback
    this._ended = null;
    this._attempts = 0;
    this._state = 'connecting';
    this._connect();
  }

  _mountTerminal() {
    if (this._term) return;  // idempotent across disconnect/reconnect
    const host = document.createElement('div');
    host.className = 'dc-terminal-surface';
    host.style.width = '100%';
    host.style.height = '100%';
    this.appendChild(host);
    this._surface = host;
    this._term = new Terminal({ convertEol: false, fontSize: 13, theme: this._theme() });
    this._fit = new FitAddon();
    this._term.loadAddon(this._fit);
    this._term.loadAddon(new WebLinksAddon());
    this._term.open(host);
    this._fit.fit();
    this._term.onData((d) => this._send({ type: 'input', data: d }));
    this._ro = new ResizeObserver(() => this._scheduleResize());
    this._ro.observe(host);
    // No live theme-change event exists in this codebase (theme-toggle.js
    // just sets/removes the data-theme attribute) — the terminal picks up
    // the theme in effect at mount time; switching themes mid-session
    // requires remounting.
  }

  _theme() {
    const cs = getComputedStyle(document.documentElement);
    const v = (name, fallback) => (cs.getPropertyValue(name).trim() || fallback);
    return {
      background: v('--pico-card-background-color', '#11191f'),
      foreground: v('--pico-color', '#e8e8e8'),
      cursor: v('--pico-color', '#e8e8e8'),
    };
  }

  _maybeConnect() {
    if (this._ws) return;               // already connecting/connected
    if (this._state === 'ended') return;  // clean end, never reconnect
    if (!this.convId || !this.tabId) {
      this._state = 'waiting';
      return;
    }
    this._connect();
  }

  _wsUrl() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/ws/terminal/`
      + `${encodeURIComponent(this.convId)}/${encodeURIComponent(this.tabId)}`;
  }

  _connect() {
    this._state = 'connecting';
    this._connectedKey = `${this.convId}/${this.tabId}`;
    // Force the post-replay resize to send: each socket registers its viewport
    // server-side on its first `resize` frame (keyed per-connection), so the
    // dedup must not swallow the initial size after a (re)connect.
    this._lastCols = 0;
    this._lastRows = 0;
    const ws = new WebSocket(this._wsUrl());
    ws.binaryType = 'arraybuffer';
    this._ws = ws;
    ws.onopen = () => {
      this._attempts = 0;
      this._state = 'replaying';
      this._replaying = true;
      this._term.reset();   // replayed scrollback REPLACES, not appends to, on-screen content
    };
    ws.onmessage = (e) => this._onMessage(e);
    ws.onclose = () => this._onClose();
    ws.onerror = () => { /* onclose follows; reconnect handled there */ };
  }

  /** @param {MessageEvent} e */
  _onMessage(e) {
    if (typeof e.data !== 'string') {            // binary → terminal output
      this._term.write(new Uint8Array(e.data));
      return;
    }
    let msg;
    try {
      msg = JSON.parse(e.data);
    } catch {
      return;  // ignore malformed control frame
    }
    if (msg.type === 'buffer_replay_done') {
      this._replaying = false;
      this._state = 'attached';
      // Debounced, NOT immediate: a (re)connect lands while the canvas panel
      // is still animating its width open (canvas.css `width 0.25s`), so an
      // immediate fit grabs a garbage intermediate width (e.g. 2 cols) and
      // ships it to the PTY — the shell reprints its prompt catastrophically
      // wrapped. Debouncing coalesces this with the animation's resize burst
      // and sends exactly one size, after the layout settles.
      this._scheduleResize();                     // send initial size once settled
    } else if (msg.type === 'session_ended') {
      this._ended = msg.exit_status;
      this._state = 'ended';
      this._teardownSocket();
    }
    // size_changed: server letterbox hint — xterm already fit locally,
    // nothing to do client-side.
  }

  _onClose() {
    this._ws = null;
    if (this._state === 'ended') return;          // clean end, no reconnect
    if (this._attempts >= BACKOFF.length) {
      this._state = 'error';
      return;
    }
    const delay = BACKOFF[this._attempts++];
    this._state = 'disconnected';
    this._reconnectTimer = setTimeout(() => this._connect(), delay);
  }

  /**
   * Coalesce ResizeObserver bursts. The canvas panel animates its width
   * (`transition: width 0.25s` in canvas.css) whenever a conversation is
   * switched, so the observer fires ~once per animation frame. Fitting +
   * sending a resize on each frame walks the PTY through every intermediate
   * width — and each genuine size change makes the shell reprint its prompt
   * into the scrollback (a cascade of prompt lines at ramping widths). Debounce
   * so we fit + send exactly once, after the layout settles.
   */
  _scheduleResize() {
    clearTimeout(this._resizeTimer);
    this._resizeTimer = setTimeout(() => this._onResize(), 150);
  }

  _onResize() {
    if (!this._fit || !this._term) return;
    this._fit.fit();
    const { cols, rows } = this._term;
    // Dedup: only tell the server when the fitted size actually changed.
    // A hidden panel fits to 0 (no-op) and sub-pixel jitter re-fires the
    // observer without changing cols/rows — neither should hit the PTY.
    if (cols === this._lastCols && rows === this._lastRows) return;
    // Advance the dedup baseline ONLY when the frame actually goes out. A
    // debounced resize can fire mid-replay (`_send` is gated on !_replaying);
    // updating the baseline there would let the dedup swallow the required
    // post-`buffer_replay_done` resize, leaving the socket with no size.
    if (this._send({ type: 'resize', cols, rows })) {
      this._lastCols = cols;
      this._lastRows = rows;
    }
  }

  /** @param {Object} obj @returns {boolean} whether the frame was transmitted */
  _send(obj) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN && !this._replaying) {
      this._ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  _teardownSocket() {
    if (this._ws) {
      this._ws.onclose = null;
      this._ws.onerror = null;
      this._ws.onmessage = null;
      this._ws.close();
      this._ws = null;
    }
    clearTimeout(this._reconnectTimer);
    this._reconnectTimer = null;
  }

  _teardown() {
    this._teardownSocket();
    clearTimeout(this._resizeTimer);
    this._resizeTimer = null;
    if (this._ro) { this._ro.disconnect(); this._ro = null; }
    if (this._term) { this._term.dispose(); this._term = null; }
    this._fit = null;
    if (this._surface) { this._surface.remove(); this._surface = null; }
  }

  render() {
    if (this._state === 'ended') {
      return html`<div class="dc-terminal-banner">[session ended · exit ${this._ended ?? '?'}]</div>`;
    }
    if (this._state === 'error') {
      return html`<div class="dc-terminal-banner">[disconnected — reload to retry]</div>`;
    }
    if (this._state === 'waiting') {
      return html`<div class="dc-terminal-status">[waiting for session]</div>`;
    }
    if (this._state === 'disconnected') {
      return html`<div class="dc-terminal-status">[reconnecting…]</div>`;
    }
    return nothing;  // xterm renders into the manually appended surface div
  }
}

customElements.define('dc-widget-terminal', TerminalWidget);
