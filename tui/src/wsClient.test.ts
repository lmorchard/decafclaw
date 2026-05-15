import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { EventEmitter } from "events";

// Module mock must be hoisted above the SUT import. The mock is a minimal
// EventEmitter-based stand-in for the `ws` package's WebSocket class — enough
// to let us trigger `close`/`error` events and observe instance creation.
const fakeWsState = vi.hoisted(() => {
  return { instances: [] as FakeWebSocket[] };
});

class FakeWebSocket extends EventEmitter {
  readyState = 0;
  url: string;
  opts: unknown;
  sent: string[] = [];
  static OPEN = 1;
  constructor(url: string, opts: unknown) {
    super();
    this.url = url;
    this.opts = opts;
    fakeWsState.instances.push(this);
  }
  close(): void {
    /* noop — tests drive close via emit */
  }
  send(data: string): void {
    this.sent.push(data);
  }
  // Test helper: transition to OPEN and fire the event.
  triggerOpen(): void {
    this.readyState = 1;
    this.emit("open");
  }
}

vi.mock("ws", () => ({ default: FakeWebSocket }));

// Imported after vi.mock so the mock takes effect.
const { WSClient } = await import("./wsClient.js");

describe("WSClient lifecycle hygiene", () => {
  beforeEach(() => {
    fakeWsState.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("close() after scheduleReconnect cancels the pending reconnect", () => {
    const c = new WSClient({ host: "http://localhost:8088", token: "t" });
    c.connect();
    expect(fakeWsState.instances).toHaveLength(1);

    // Simulate the server dropping us without wantClosed — triggers scheduleReconnect.
    fakeWsState.instances[0]!.emit("close", 1006, Buffer.from(""));

    // Now close the client BEFORE the 1000ms reconnect timer fires.
    c.close();

    // Advance past the initial 1000ms delay (and then some, for safety).
    vi.advanceTimersByTime(5000);

    // No second WebSocket should have been created — reconnect was cancelled.
    expect(fakeWsState.instances).toHaveLength(1);
  });

  it("reconnect timer fires and creates a new connection when not cancelled", () => {
    const c = new WSClient({ host: "http://localhost:8088", token: "t" });
    c.connect();
    expect(fakeWsState.instances).toHaveLength(1);

    fakeWsState.instances[0]!.emit("close", 1006, Buffer.from(""));

    // Advance past the initial 1000ms delay; reconnect should fire.
    vi.advanceTimersByTime(1500);

    expect(fakeWsState.instances).toHaveLength(2);

    // Clean up so the second timer doesn't leak into other tests.
    c.close();
  });

  it("send() before open buffers; flushes on open", () => {
    const c = new WSClient({ host: "http://localhost:8088", token: "t" });
    c.connect();
    const w = fakeWsState.instances[0]!;

    // Pre-open: send should buffer rather than reach the socket.
    c.send({ type: "select_conv", conv_id: "abc" });
    c.send({ type: "send", conv_id: "abc", text: "hello", attachments: [] });
    expect(w.sent).toEqual([]);

    // After open: buffer should flush in order.
    w.triggerOpen();
    expect(w.sent).toHaveLength(2);
    expect(JSON.parse(w.sent[0]!)).toMatchObject({ type: "select_conv", conv_id: "abc" });
    expect(JSON.parse(w.sent[1]!)).toMatchObject({ type: "send", text: "hello" });

    // Subsequent sends go directly (no double-flush).
    c.send({ type: "cancel_turn", conv_id: "abc" });
    expect(w.sent).toHaveLength(3);

    c.close();
  });

  it("ws error log includes URL and attempt count", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const c = new WSClient({ host: "http://localhost:8088", token: "tok" });
    c.connect();
    fakeWsState.instances[0]!.emit("error", new Error("boom"));

    expect(errSpy).toHaveBeenCalled();
    const logged = errSpy.mock.calls[0]!.join(" ");
    expect(logged).toContain("boom");
    expect(logged).toContain("ws://localhost:8088/ws/chat");
    expect(logged).toContain("attempt=0");

    c.close();
  });
});
