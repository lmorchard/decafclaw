/**
 * WebSocket transport for the TUI.
 *
 * Dumb pipe: opens a connection with the same cookie auth the browser uses,
 * parses incoming JSON, surfaces parsed ServerMessage envelopes (or a synthetic
 * `__reconnected` marker after a reconnect). Caller decides what to do.
 *
 * Reconnect with exponential backoff: 1s, 2s, 4s, 8s, 16s, capped at 30s.
 * Auth-failure exits are surfaced by leaving the WS closed and emitting a
 * synthetic `__auth_failed` marker. The App treats this as fatal.
 *
 * Lifecycle: a WSClient is intended for one-shot use. After `close()`, create
 * a new instance to reconnect intentionally — don't reuse.
 */

import WebSocket from "ws";
import type { ClientMessage, ServerMessage } from "./types.generated.js";

export type WSEvent =
  | ServerMessage
  | { type: "__reconnected" }
  | { type: "__auth_failed"; reason: string }
  | { type: "__closed"; code: number; reason: string };

export interface WSClientOptions {
  host: string; // e.g. "http://localhost:8088" or "https://example.com"
  token: string;
}

export class WSClient {
  private opts: WSClientOptions;
  private readonly wsUrl: string;
  private ws: WebSocket | null = null;
  private handlers: Array<(e: WSEvent) => void> = [];
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private wantClosed = false;
  private hadOpen = false;

  constructor(opts: WSClientOptions) {
    this.opts = opts;
    this.wsUrl = opts.host.replace(/^http/, "ws") + "/ws/chat";
  }

  on(handler: (e: WSEvent) => void): void {
    this.handlers.push(handler);
  }

  send(msg: ClientMessage): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
    // else: silently drop; reconnect path will re-establish state via re-select_conv
  }

  close(): void {
    this.wantClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.hadOpen = false;
  }

  connect(): void {
    if (this.ws) {
      this.ws.removeAllListeners();
      this.ws.close();
      this.ws = null;
    }
    const ws = new WebSocket(this.wsUrl, {
      headers: {
        Cookie: `decafclaw_session=${this.opts.token}`,
      },
    });
    this.ws = ws;

    ws.on("open", () => {
      const wasReconnect = this.hadOpen;
      this.hadOpen = true;
      this.reconnectAttempt = 0;
      if (wasReconnect) {
        this.emit({ type: "__reconnected" });
      }
    });

    ws.on("message", (data: WebSocket.RawData) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data.toString());
      } catch {
        console.error("[tui] malformed WS message:", data.toString());
        return;
      }
      if (
        parsed &&
        typeof parsed === "object" &&
        "type" in parsed &&
        typeof (parsed as { type: unknown }).type === "string"
      ) {
        this.emit(parsed as ServerMessage);
      }
    });

    ws.on("unexpected-response", (_req, res) => {
      // 401/403 etc. — auth failure on upgrade.
      const reason = `HTTP ${res.statusCode} ${res.statusMessage ?? ""}`.trim();
      this.wantClosed = true;
      this.emit({ type: "__auth_failed", reason });
    });

    ws.on("close", (code: number, reasonBuf: Buffer) => {
      const reason = reasonBuf.toString();
      this.emit({ type: "__closed", code, reason });
      this.ws = null;
      if (this.wantClosed) return;
      this.scheduleReconnect();
    });

    ws.on("error", (err: Error) => {
      console.error(
        `[tui] ws error: ${err.message} url=${this.wsUrl} attempt=${this.reconnectAttempt}`,
      );
      // close handler will fire after this; reconnect is scheduled there.
    });
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    const delays = [1000, 2000, 4000, 8000, 16000, 30000];
    const idx = Math.min(this.reconnectAttempt, delays.length - 1);
    const delay = delays[idx]!;
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.wantClosed) this.connect();
    }, delay);
  }

  private emit(e: WSEvent): void {
    for (const h of this.handlers) h(e);
  }
}
