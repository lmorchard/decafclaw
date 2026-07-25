import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Dead-session handling for the terminal widget.
 *
 * A canvas tab persists to `canvas.json`; the PTY behind it lives only in the
 * server's `TerminalRegistry`. A server restart kills every session and leaves
 * the tabs behind, so reconnecting lands on a session the server has never
 * heard of. These tests pin the two halves of the resulting protocol:
 *
 *   reason: 'no_session' → tombstone: toast + auto-close the canvas tab
 *   reason: 'exited'     → the shell really exited: keep the final output,
 *                          keep the tab, let the user close it
 */

const { toasts, closedTabs } = vi.hoisted(() => ({ toasts: [], closedTabs: [] }));

// xterm needs a real rendering surface (canvas metrics, layout) that jsdom
// doesn't provide, and contributes nothing to the lifecycle paths under test.
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    constructor() {
      this.cols = 80;
      this.rows = 24;
      this.disposed = false;
      this.written = [];
    }
    loadAddon() {}
    open() {}
    reset() {}
    write(data) { this.written.push(data); }
    onData() {}
    dispose() { this.disposed = true; }
  },
}));
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }));
vi.mock('@xterm/addon-web-links', () => ({ WebLinksAddon: class {} }));

// Mocked at the same absolute specifier the widget imports — see the
// /static/ alias in vitest.config.js for why that prefix is what ships.
vi.mock('/static/lib/toast.js', () => ({
  showToast: (/** @type {string} */ msg) => { toasts.push(msg); },
}));
vi.mock('/static/lib/canvas-state.js', () => ({
  closeTabById: (/** @type {string} */ convId, /** @type {string} */ tabId) => {
    closedTabs.push([convId, tabId]);
    return Promise.resolve();
  },
}));

/** Minimal WebSocket stand-in — the widget only uses onopen/onmessage/onclose. */
class FakeWebSocket {
  static OPEN = 1;
  /** @type {FakeWebSocket[]} */
  static instances = [];

  /** @param {string} url */
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.binaryType = '';
    /** @type {string[]} */
    this.sent = [];
    /** @type {Function|null} */ this.onopen = null;
    /** @type {Function|null} */ this.onmessage = null;
    /** @type {Function|null} */ this.onclose = null;
    /** @type {Function|null} */ this.onerror = null;
    FakeWebSocket.instances.push(this);
  }

  /** @param {string} data */
  send(data) { this.sent.push(data); }
  close() { this.readyState = 3; }

  /** Drive the handshake the way a live server would. */
  serverOpen() {
    this.readyState = FakeWebSocket.OPEN;
    if (this.onopen) this.onopen();
  }

  /** @param {object} obj */
  serverJson(obj) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) });
  }
}

// jsdom ships neither ResizeObserver nor WebSocket.
class FakeResizeObserver {
  observe() {}
  disconnect() {}
}
// @ts-ignore -- test doubles for jsdom globals
global.ResizeObserver = FakeResizeObserver;
// @ts-ignore
global.WebSocket = FakeWebSocket;

await import('./widget.js');

/**
 * Mount a terminal widget already attached to a live socket.
 * @returns {Promise<{el: any, ws: FakeWebSocket}>}
 */
async function mountConnected() {
  const el = /** @type {any} */ (document.createElement('dc-widget-terminal'));
  el.convId = 'c1';
  el.tabId = 'canvas_1';
  el.data = { session_id: 's1', cwd: '/tmp', shell: '/bin/sh' };
  document.body.appendChild(el);
  await el.updateComplete;
  const ws = FakeWebSocket.instances.at(-1);
  if (!ws) throw new Error('widget did not open a socket');
  ws.serverOpen();
  return { el, ws };
}

describe('terminal widget dead-session handling', () => {
  beforeEach(() => {
    toasts.length = 0;
    closedTabs.length = 0;
    FakeWebSocket.instances.length = 0;
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('closes the tab and alerts when the server has no such session', async () => {
    const { el, ws } = await mountConnected();

    ws.serverJson({ type: 'session_ended', reason: 'no_session', exit_status: null });
    await el.updateComplete;

    expect(closedTabs).toEqual([['c1', 'canvas_1']]);
    expect(toasts).toHaveLength(1);
    expect(toasts[0]).toMatch(/restart/i);
  });

  it('disposes the xterm surface so a dead session is not a blank pane', async () => {
    const { el, ws } = await mountConnected();
    // The surface is a manually-appended light-DOM sibling with height:100%.
    // Leaving it mounted is what made a dead terminal render as empty black.
    expect(el.querySelector('.dc-terminal-surface')).not.toBeNull();

    ws.serverJson({ type: 'session_ended', reason: 'no_session', exit_status: null });
    await el.updateComplete;

    expect(el.querySelector('.dc-terminal-surface')).toBeNull();
    expect(el.querySelector('.dc-terminal-banner')).not.toBeNull();
  });

  it('keeps the tab and the final output when the shell exits normally', async () => {
    const { el, ws } = await mountConnected();

    ws.serverJson({ type: 'session_ended', reason: 'exited', exit_status: 0 });
    await el.updateComplete;

    // No auto-close: you may want to read what the shell printed on its way out.
    expect(closedTabs).toEqual([]);
    expect(toasts).toEqual([]);
    expect(el.querySelector('.dc-terminal-surface')).not.toBeNull();
    const banner = el.querySelector('.dc-terminal-banner');
    expect(banner).not.toBeNull();
    expect(banner.textContent).toContain('exit 0');
  });

  it('offers a close button on the exited banner', async () => {
    const { el, ws } = await mountConnected();
    ws.serverJson({ type: 'session_ended', reason: 'exited', exit_status: 0 });
    await el.updateComplete;

    const btn = /** @type {HTMLButtonElement} */ (
      el.querySelector('.dc-terminal-banner button')
    );
    expect(btn).not.toBeNull();
    btn.click();
    expect(closedTabs).toEqual([['c1', 'canvas_1']]);
  });

  it('treats a reason-less session_ended as a normal exit', async () => {
    // Defensive: an older server (or a frame we failed to parse fully) must
    // not be read as a tombstone — auto-closing a tab on ambiguity is the
    // destructive direction to guess in.
    const { el, ws } = await mountConnected();

    ws.serverJson({ type: 'session_ended', exit_status: 0 });
    await el.updateComplete;

    expect(closedTabs).toEqual([]);
    expect(el.querySelector('.dc-terminal-surface')).not.toBeNull();
  });

  it('does not close the tab while merely reconnecting', async () => {
    // The session may well still be alive on a server that is just slow to
    // come back — a dropped socket alone must never remove the tab.
    const { el, ws } = await mountConnected();

    ws.readyState = 3;
    if (ws.onclose) ws.onclose();
    await el.updateComplete;

    expect(closedTabs).toEqual([]);
    expect(el.querySelector('.dc-terminal-status')?.textContent).toMatch(/reconnect/i);
  });
});
