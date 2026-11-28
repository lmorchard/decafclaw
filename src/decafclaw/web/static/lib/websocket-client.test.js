import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { WebSocketClient } from './websocket-client.js';

/** Minimal WebSocket stand-in that lets a test drive open/drop timing. */
class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  /** @type {FakeWebSocket[]} */
  static instances = [];

  /** @param {string} url */
  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    /** @type {Array<() => void>} */
    this.onopen = null;
    /** @type {Array<(event: MessageEvent) => void>} */
    this.onmessage = null;
    /** @type {Array<() => void>} */
    this.onclose = null;
    /** @type {Array<(event: Event) => void>} */
    this.onerror = null;
    /** @type {string[]} frames written via send() */
    this.sent = [];
    /** When true, send() throws like a socket that died mid-send. */
    this.failSends = false;
    FakeWebSocket.instances.push(this);
  }

  /** Mirrors the browser: throws unless the socket is OPEN. */
  send(data) {
    if (this.readyState !== FakeWebSocket.OPEN || this.failSends) {
      throw new Error('WebSocket is not open');
    }
    this.sent.push(data);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  /** Simulate the server dropping the socket, or a failed connection. */
  drop() {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  /** Closing an already-closed socket is a no-op with no event, per spec. */
  close() {
    this.drop();
  }
}

describe('WebSocketClient reconnect narration', () => {
  /** @type {WebSocketClient} */
  let client;
  /** @type {Array<{type: string, detail: any}>} */
  let events;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    client = new WebSocketClient('ws://test/ws');
    events = [];
    for (const type of ['open', 'close', 'reconnecting']) {
      client.addEventListener(type, (event) =>
        events.push({ type, detail: /** @type {CustomEvent} */ (event).detail }));
    }
  });

  afterEach(() => {
    client.disconnect();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('reports each scheduled retry with its 1-based attempt and delay', () => {
    client.connect();
    FakeWebSocket.instances[0].drop();
    expect(events.at(-1)).toEqual({
      type: 'reconnecting',
      detail: { attempt: 1, delayMs: 1000 },
    });

    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    FakeWebSocket.instances[1].drop();
    expect(events.at(-1)).toEqual({
      type: 'reconnecting',
      detail: { attempt: 2, delayMs: 2000 },
    });
  });

  it('caps the retry delay at 30 seconds', () => {
    client.connect();
    for (let i = 0; i < 8; i++) {
      FakeWebSocket.instances.at(-1).drop();
      vi.advanceTimersByTime(30000);
    }
    expect(events.at(-1).detail).toEqual({ attempt: 8, delayMs: 30000 });
  });

  it('restarts the attempt count after a successful open', () => {
    client.connect();
    FakeWebSocket.instances[0].drop();
    vi.advanceTimersByTime(1000);
    FakeWebSocket.instances[1].open();
    FakeWebSocket.instances[1].drop();
    expect(events.at(-1).detail).toEqual({ attempt: 1, delayMs: 1000 });
  });

  it('stops scheduling retries after disconnect()', () => {
    client.connect();
    client.disconnect();
    const created = FakeWebSocket.instances.length;
    vi.advanceTimersByTime(60000);
    expect(FakeWebSocket.instances).toHaveLength(created);
  });
});

describe('WebSocketClient outbound queue (#1416)', () => {
  /** @type {WebSocketClient} */
  let client;
  /** @type {Array<{type: string, detail: any}>} */
  let events;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    client = new WebSocketClient('ws://test/ws');
    events = [];
    for (const type of ['open', 'send-failed']) {
      client.addEventListener(type, (event) =>
        events.push({ type, detail: /** @type {CustomEvent} */ (event).detail }));
    }
  });

  afterEach(() => {
    client.disconnect();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('queues sends while the socket is not open and flushes them in order on open', () => {
    client.connect(); // socket is CONNECTING, not OPEN yet
    const m1 = { type: 'send', conv_id: 'c1', text: 'one' };
    const m2 = { type: 'select_conv', conv_id: 'c1' };
    expect(client.send(m1)).toBe(true); // queued, not delivered
    expect(client.send(m2)).toBe(true);
    const socket = FakeWebSocket.instances[0];
    expect(socket.sent).toHaveLength(0);
    expect(client.pendingCount).toBe(2);

    socket.open(); // flush happens on open
    expect(socket.sent.map((raw) => JSON.parse(raw))).toEqual([m1, m2]);
    expect(client.pendingCount).toBe(0);
  });

  it('sends directly when the socket is open', () => {
    client.connect();
    FakeWebSocket.instances[0].open();
    const m = { type: 'send', conv_id: 'c1', text: 'live' };
    expect(client.send(m)).toBe(false); // delivered directly, not queued
    expect(FakeWebSocket.instances[0].sent.map((raw) => JSON.parse(raw))).toEqual([m]);
    expect(client.pendingCount).toBe(0);
  });

  it('keeps queued messages across reconnect attempts until one opens', () => {
    client.connect();
    const m = { type: 'send', conv_id: 'c1', text: 'queued' };
    client.send(m);
    FakeWebSocket.instances[0].drop(); // first attempt fails; retry scheduled
    vi.advanceTimersByTime(1000); // second attempt created
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(client.pendingCount).toBe(1); // still buffered, not dropped

    FakeWebSocket.instances[1].open();
    expect(FakeWebSocket.instances[1].sent.map((raw) => JSON.parse(raw))).toEqual([m]);
    expect(client.pendingCount).toBe(0);
  });

  it('surfaces a failed flush as send-failed instead of dropping', () => {
    client.connect();
    const socket = FakeWebSocket.instances[0];
    const m1 = { type: 'send', conv_id: 'c1', text: 'one' };
    const m2 = { type: 'send', conv_id: 'c1', text: 'two' };
    client.send(m1);
    client.send(m2);
    socket.failSends = true; // the socket dies mid-flush
    socket.open();

    expect(events.filter((e) => e.type === 'send-failed').map((e) => e.detail)).toEqual([m1, m2]);
    expect(client.pendingCount).toBe(0); // attempted once, not re-queued
  });

  it('surfaces queued messages as send-failed on disconnect()', () => {
    client.connect(); // socket never opens; messages stay queued
    const m1 = { type: 'send', conv_id: 'c1', text: 'one' };
    const m2 = { type: 'send', conv_id: 'c1', text: 'two' };
    client.send(m1);
    client.send(m2);

    client.disconnect(); // deliberate: no open will ever flush them
    expect(events.filter((e) => e.type === 'send-failed').map((e) => e.detail)).toEqual([m1, m2]);
    expect(client.pendingCount).toBe(0);
  });

  it('does not re-surface the queue after a disconnect', () => {
    client.connect();
    const m = { type: 'send', conv_id: 'c1', text: 'one' };
    client.send(m);
    client.disconnect();
    const failures = events.filter((e) => e.type === 'send-failed').length;
    client.disconnect(); // second call must not re-fire the discarded messages
    expect(events.filter((e) => e.type === 'send-failed')).toHaveLength(failures);
  });
});
