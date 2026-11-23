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
    FakeWebSocket.instances.push(this);
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
