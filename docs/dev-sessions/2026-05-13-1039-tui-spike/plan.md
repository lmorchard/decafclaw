# TUI Network Client Spike — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal Ink (React-for-terminal) TUI that connects to the running decafclaw bot over its existing WebSocket gateway and drives a single conversation: chat with streaming, tool activity lane, inline confirmation prompts, Ctrl+C cancel/exit. Validate Option A (hand-typed minimal Ink) from `spec.md`.

**Architecture:** Node CLI binary `decafclaw-tui` in a new `tui/` sibling directory. Connects to `/ws/chat` on the running daemon with `Cookie: decafclaw_session=<token>` (same auth as the browser). Single WS connection, per-conversation subscription via `select_conv`. No bot-side changes. Wire types hand-typed but codegen-shaped so we can graduate to generated types from `src/decafclaw/web/message_types.json` later without consumer churn.

**Tech Stack:** Node 20+, TypeScript, Ink 5, `ink-text-input`, React 18, `ws` (for cookie-header WS upgrade), `tsx` (run TS directly, no build step), Vitest (dispatcher unit test only).

**Spec:** [`spec.md`](spec.md). Read it first if you haven't.

**File layout (final state):**
```
tui/
  .gitignore
  package.json
  tsconfig.json
  README.md
  src/
    entry.tsx                # TTY gate, argv/env parsing, render <App/>
    App.tsx                  # Ink tree: transcript + activity + composer + confirm prompt
    wsClient.ts              # WS connect, send, on, reconnect w/ backoff
    types.ts                 # hand-typed WS message discriminated union
    dispatcher.ts            # pure reducer: (state, msg) -> state
    dispatcher.test.ts       # vitest unit tests for dispatcher
    conversationPicker.tsx   # initial list/create picker
```

Six source files + one test (a one-file expansion past the spec's "5 files" budget — justified by pulling the dispatcher out of `App.tsx` for testability without bringing Ink rendering into the test). All other files are scaffolding.

**Reference paths in main repo:**
- Wire contract: `src/decafclaw/web/message_types.json`
- WS handler implementation: `src/decafclaw/web/websocket.py`
- Auth + token store: `src/decafclaw/web/auth.py`, `data/{agent_id}/web_tokens.json`
- Comparison transport (Python stdin/stdout): `src/decafclaw/interactive_terminal.py`

---

### Task 1: Project scaffold

**Files:**
- Create: `tui/.gitignore`
- Create: `tui/package.json`
- Create: `tui/tsconfig.json`
- Create: `tui/README.md`
- Create: `tui/src/.gitkeep` (delete after Task 2 adds first source file)

- [ ] **Step 1: Create `tui/.gitignore`**

```
node_modules/
dist/
*.log
.DS_Store
```

- [ ] **Step 2: Create `tui/package.json`**

```json
{
  "name": "decafclaw-tui",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "bin": {
    "decafclaw-tui": "./src/entry.tsx"
  },
  "scripts": {
    "dev": "tsx src/entry.tsx",
    "start": "tsx src/entry.tsx",
    "test": "vitest run",
    "typecheck": "tsc --noEmit"
  },
  "engines": {
    "node": ">=20"
  },
  "dependencies": {
    "ink": "^5.0.1",
    "ink-text-input": "^6.0.0",
    "react": "^18.3.1",
    "ws": "^8.18.0"
  },
  "devDependencies": {
    "@types/node": "^22.7.5",
    "@types/react": "^18.3.11",
    "@types/ws": "^8.5.12",
    "tsx": "^4.19.1",
    "typescript": "^5.6.3",
    "vitest": "^2.1.2"
  }
}
```

- [ ] **Step 3: Create `tui/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "allowImportingTsExtensions": true,
    "lib": ["ES2022"]
  },
  "include": ["src/**/*"]
}
```

- [ ] **Step 4: Create `tui/README.md`**

```markdown
# decafclaw-tui

Minimal Ink TUI that connects to a running decafclaw bot over its WebSocket gateway.
Spec: `docs/dev-sessions/2026-05-13-1039-tui-spike/spec.md`.

## Running

```bash
cd tui
npm install
DECAFCLAW_TOKEN=<token> npm run dev
# or
npm run dev -- --token <token> --conv <conv_id>
```

Requires the bot to be running locally (e.g. `make dev` in the repo root).

## Configuration

| Flag | Env var | Default |
|---|---|---|
| `--token <t>` | `DECAFCLAW_TOKEN` | (required) |
| `--host <url>` | `DECAFCLAW_HOST` | `http://localhost:8088` |
| `--conv <id>` | — | (picker shown if absent) |

Get a token from `data/{agent_id}/web_tokens.json` in the main clone.

## Scripts

- `npm run dev` — run the TUI
- `npm test` — run dispatcher unit tests
- `npm run typecheck` — type-check without emitting

## Status

Spike. See `docs/dev-sessions/2026-05-13-1039-tui-spike/spec.md` for scope and deferred items.
```

- [ ] **Step 5: Create `tui/src/.gitkeep` placeholder**

```
```

(empty file)

- [ ] **Step 6: Install dependencies**

```bash
cd tui && npm install
```

Expected: `node_modules/` populated, lockfile written. No errors.

- [ ] **Step 7: Verify typecheck passes on empty source tree**

```bash
cd tui && npm run typecheck
```

Expected: no output, exit 0. (No `.ts` / `.tsx` files yet; tsc has nothing to complain about.)

- [ ] **Step 8: Commit**

```bash
git add tui/.gitignore tui/package.json tui/package-lock.json tui/tsconfig.json tui/README.md tui/src/.gitkeep
git commit -m "feat(tui): TypeScript project scaffold for Ink TUI spike"
```

---

### Task 2: Wire types

**Files:**
- Create: `tui/src/types.ts`
- Delete: `tui/src/.gitkeep`

The TS types mirror `src/decafclaw/web/message_types.json` verbatim. Field names match, envelope key is `"type"`. Two unions: `ServerMessage` (received) and `ClientMessage` (sent). When we promote to codegen, this file gets replaced by `types.generated.ts` with the same exported names.

- [ ] **Step 1: Create `tui/src/types.ts`**

```typescript
/**
 * WebSocket message types for the decafclaw bot.
 *
 * Mirrors src/decafclaw/web/message_types.json. Hand-typed for the spike.
 * Codegen-shaped: when we promote, replace with generated file using the
 * same exported names. See spec for the A->B promotion path.
 */

// ---- Server -> client ----

export interface SrvBackgroundEvent { type: "background_event"; conv_id: string; event: Record<string, unknown>; }
export interface SrvCanvasUpdate { type: "canvas_update"; conv_id: string; state: Record<string, unknown>; }
export interface SrvChunk { type: "chunk"; conv_id: string; text: string; }
export interface SrvCommandAck { type: "command_ack"; conv_id: string; command: string; }
export interface SrvCompactionDone { type: "compaction_done"; conv_id: string; }
export interface SrvConfirmRequest { type: "confirm_request"; conv_id: string; request_id: string; kind: string; payload: Record<string, unknown>; }
export interface SrvConfirmationResponse { type: "confirmation_response"; conv_id: string; request_id: string; decision: string; }
export interface SrvConvHistory { type: "conv_history"; conv_id: string; messages: Array<Record<string, unknown>>; before: string | null; }
export interface SrvConvSelected { type: "conv_selected"; conv_id: string; model: string | null; }
export interface SrvError { type: "error"; message: string; conv_id: string | null; }
export interface SrvMessageComplete { type: "message_complete"; conv_id: string; message: Record<string, unknown>; }
export interface SrvModelChanged { type: "model_changed"; conv_id: string; model: string; }
export interface SrvModelsAvailable { type: "models_available"; models: string[]; }
export interface SrvNotificationCreated { type: "notification_created"; notification: Record<string, unknown>; }
export interface SrvNotificationRead { type: "notification_read"; id: string; }
export interface SrvReflectionResult { type: "reflection_result"; conv_id: string; result: Record<string, unknown>; }
export interface SrvToolEnd { type: "tool_end"; conv_id: string; tool_call_id: string; name: string; ok: boolean; result: string | Record<string, unknown>; }
export interface SrvToolStart { type: "tool_start"; conv_id: string; tool_call_id: string; name: string; input: Record<string, unknown>; }
export interface SrvToolStatus { type: "tool_status"; conv_id: string; tool_call_id: string; status: string; }
export interface SrvTurnComplete { type: "turn_complete"; conv_id: string; }
export interface SrvTurnStart { type: "turn_start"; conv_id: string; }
export interface SrvUserMessage { type: "user_message"; conv_id: string; message: Record<string, unknown>; }
export interface SrvVaultChanged { type: "vault_changed"; path: string; }

export type ServerMessage =
  | SrvBackgroundEvent
  | SrvCanvasUpdate
  | SrvChunk
  | SrvCommandAck
  | SrvCompactionDone
  | SrvConfirmRequest
  | SrvConfirmationResponse
  | SrvConvHistory
  | SrvConvSelected
  | SrvError
  | SrvMessageComplete
  | SrvModelChanged
  | SrvModelsAvailable
  | SrvNotificationCreated
  | SrvNotificationRead
  | SrvReflectionResult
  | SrvToolEnd
  | SrvToolStart
  | SrvToolStatus
  | SrvTurnComplete
  | SrvTurnStart
  | SrvUserMessage
  | SrvVaultChanged;

// ---- Client -> server ----

export interface CliCancelTurn { type: "cancel_turn"; conv_id: string; }
export interface CliConfirmResponse { type: "confirm_response"; conv_id: string; request_id: string; decision: string; extras: Record<string, unknown>; }
export interface CliLoadHistory { type: "load_history"; conv_id: string; limit: number; before: string | null; }
export interface CliSelectConv { type: "select_conv"; conv_id: string; }
export interface CliSend { type: "send"; conv_id: string; text: string; attachments: Array<Record<string, unknown>>; }
export interface CliSetEffort { type: "set_effort"; conv_id: string; effort: string; }
export interface CliSetModel { type: "set_model"; conv_id: string; model: string; }
export interface CliWidgetResponse { type: "widget_response"; conv_id: string; request_id: string; value: Record<string, unknown>; }

export type ClientMessage =
  | CliCancelTurn
  | CliConfirmResponse
  | CliLoadHistory
  | CliSelectConv
  | CliSend
  | CliSetEffort
  | CliSetModel
  | CliWidgetResponse;
```

- [ ] **Step 2: Delete the placeholder**

```bash
rm tui/src/.gitkeep
```

- [ ] **Step 3: Verify typecheck passes**

```bash
cd tui && npm run typecheck
```

Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add tui/src/types.ts
git rm tui/src/.gitkeep
git commit -m "feat(tui): hand-typed wire message types mirroring message_types.json"
```

---

### Task 3: Pure dispatcher with TDD

**Files:**
- Create: `tui/src/dispatcher.ts`
- Create: `tui/src/dispatcher.test.ts`

The dispatcher is the only test-worthy unit in the spike. It's a pure reducer `(state, msg) -> state`. Extract it from `App.tsx` so we can test without rendering Ink.

**State shape (kept inline in dispatcher.ts):**

```typescript
type TranscriptItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "system"; text: string }; // [reconnected], [compaction complete], errors

type Activity = { tool_call_id: string; name: string; status: string } | null;

type Confirm = { request_id: string; kind: string; payload: Record<string, unknown> } | null;

interface State {
  conv_id: string | null;
  transcript: TranscriptItem[];
  draft: string; // in-flight assistant message
  activity: Activity;
  confirm: Confirm;
  turnInFlight: boolean;
  model: string | null;
}
```

- [ ] **Step 1: Write the failing tests**

Create `tui/src/dispatcher.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { initialState, dispatch } from "./dispatcher.js";
import type { ServerMessage } from "./types.js";

const CONV = "conv-abc";

describe("dispatcher", () => {
  it("chunk appends to draft", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, { type: "chunk", conv_id: CONV, text: "hello " });
    const s2 = dispatch(s1, { type: "chunk", conv_id: CONV, text: "world" });
    expect(s2.draft).toBe("hello world");
  });

  it("message_complete finalizes draft into transcript", () => {
    const s0 = { ...initialState, conv_id: CONV, draft: "answer" };
    const s1 = dispatch(s0, {
      type: "message_complete",
      conv_id: CONV,
      message: { text: "answer" },
    });
    expect(s1.draft).toBe("");
    expect(s1.transcript).toEqual([{ kind: "assistant", text: "answer" }]);
  });

  it("turn_start / turn_complete toggle turnInFlight", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, { type: "turn_start", conv_id: CONV });
    expect(s1.turnInFlight).toBe(true);
    const s2 = dispatch(s1, { type: "turn_complete", conv_id: CONV });
    expect(s2.turnInFlight).toBe(false);
  });

  it("tool_start sets activity; tool_status updates it; tool_end clears it", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, {
      type: "tool_start",
      conv_id: CONV,
      tool_call_id: "tc1",
      name: "run_shell_command",
      input: {},
    });
    expect(s1.activity).toEqual({
      tool_call_id: "tc1",
      name: "run_shell_command",
      status: "",
    });
    const s2 = dispatch(s1, {
      type: "tool_status",
      conv_id: CONV,
      tool_call_id: "tc1",
      status: "running",
    });
    expect(s2.activity?.status).toBe("running");
    const s3 = dispatch(s2, {
      type: "tool_end",
      conv_id: CONV,
      tool_call_id: "tc1",
      name: "run_shell_command",
      ok: true,
      result: "done",
    });
    expect(s3.activity).toBeNull();
  });

  it("confirm_request sets confirm; we clear it on response intent", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, {
      type: "confirm_request",
      conv_id: CONV,
      request_id: "req1",
      kind: "run_shell_command",
      payload: { command: "ls" },
    });
    expect(s1.confirm).toEqual({
      request_id: "req1",
      kind: "run_shell_command",
      payload: { command: "ls" },
    });
  });

  it("user_message echo appends to transcript", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, {
      type: "user_message",
      conv_id: CONV,
      message: { text: "hi" },
    });
    expect(s1.transcript).toEqual([{ kind: "user", text: "hi" }]);
  });

  it("conv_selected stores model", () => {
    const s0 = { ...initialState };
    const s1 = dispatch(s0, {
      type: "conv_selected",
      conv_id: CONV,
      model: "claude-opus-4-7",
    });
    expect(s1.conv_id).toBe(CONV);
    expect(s1.model).toBe("claude-opus-4-7");
  });

  it("compaction_done appends system line", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, { type: "compaction_done", conv_id: CONV });
    expect(s1.transcript.at(-1)).toEqual({
      kind: "system",
      text: "[compaction complete]",
    });
  });

  it("model_changed appends system line + updates model", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, {
      type: "model_changed",
      conv_id: CONV,
      model: "claude-haiku-4-5",
    });
    expect(s1.model).toBe("claude-haiku-4-5");
    expect(s1.transcript.at(-1)?.text).toContain("claude-haiku-4-5");
  });

  it("error appends system line", () => {
    const s0 = { ...initialState };
    const s1 = dispatch(s0, {
      type: "error",
      message: "boom",
      conv_id: null,
    });
    expect(s1.transcript.at(-1)).toEqual({
      kind: "system",
      text: "[error: boom]",
    });
  });

  it("unknown type returns state unchanged (forward-compat)", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const unknown = { type: "future_message", conv_id: CONV } as unknown as ServerMessage;
    const s1 = dispatch(s0, unknown);
    expect(s1).toBe(s0); // referential equality — no copy
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd tui && npm test
```

Expected: errors importing `./dispatcher.js` (file doesn't exist). All tests fail.

- [ ] **Step 3: Implement `tui/src/dispatcher.ts`**

```typescript
/**
 * Pure reducer mapping (state, ServerMessage) -> new state.
 *
 * Exhaustive switch with TS `never` guard on default — adding a new
 * ServerMessage variant in types.ts will surface here as a compile error
 * rather than silent drift. See spec.md "Promotion path" section.
 */

import type { ServerMessage } from "./types.js";

export type TranscriptItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "system"; text: string };

export type Activity = {
  tool_call_id: string;
  name: string;
  status: string;
} | null;

export type Confirm = {
  request_id: string;
  kind: string;
  payload: Record<string, unknown>;
} | null;

export interface State {
  conv_id: string | null;
  transcript: TranscriptItem[];
  draft: string;
  activity: Activity;
  confirm: Confirm;
  turnInFlight: boolean;
  model: string | null;
}

export const initialState: State = {
  conv_id: null,
  transcript: [],
  draft: "",
  activity: null,
  confirm: null,
  turnInFlight: false,
  model: null,
};

function appendTranscript(s: State, item: TranscriptItem): State {
  return { ...s, transcript: [...s.transcript, item] };
}

function extractText(msg: Record<string, unknown>): string {
  const t = msg["text"];
  return typeof t === "string" ? t : "";
}

export function dispatch(s: State, m: ServerMessage): State {
  switch (m.type) {
    case "chunk":
      return { ...s, draft: s.draft + m.text };

    case "message_complete": {
      const text = extractText(m.message) || s.draft;
      const next = appendTranscript(s, { kind: "assistant", text });
      return { ...next, draft: "" };
    }

    case "user_message":
      return appendTranscript(s, { kind: "user", text: extractText(m.message) });

    case "turn_start":
      return { ...s, turnInFlight: true };

    case "turn_complete":
      return { ...s, turnInFlight: false };

    case "tool_start":
      return {
        ...s,
        activity: { tool_call_id: m.tool_call_id, name: m.name, status: "" },
      };

    case "tool_status":
      if (s.activity && s.activity.tool_call_id === m.tool_call_id) {
        return { ...s, activity: { ...s.activity, status: m.status } };
      }
      return s;

    case "tool_end":
      if (s.activity && s.activity.tool_call_id === m.tool_call_id) {
        return { ...s, activity: null };
      }
      return s;

    case "confirm_request":
      return {
        ...s,
        confirm: {
          request_id: m.request_id,
          kind: m.kind,
          payload: m.payload,
        },
      };

    case "confirmation_response":
      // Server replay of a prior decision (e.g., loading history). Currently no-op for live UI.
      return s;

    case "conv_selected":
      return { ...s, conv_id: m.conv_id, model: m.model };

    case "conv_history": {
      const items: TranscriptItem[] = [];
      for (const raw of m.messages) {
        const role = (raw as { role?: string }).role;
        const text = extractText(raw as Record<string, unknown>);
        if (role === "user") items.push({ kind: "user", text });
        else if (role === "assistant") items.push({ kind: "assistant", text });
        // skip tool/system roles in the spike
      }
      return { ...s, transcript: [...items, ...s.transcript] };
    }

    case "compaction_done":
      return appendTranscript(s, { kind: "system", text: "[compaction complete]" });

    case "model_changed":
      return appendTranscript(
        { ...s, model: m.model },
        { kind: "system", text: `[model: ${m.model}]` }
      );

    case "error":
      return appendTranscript(s, { kind: "system", text: `[error: ${m.message}]` });

    // Spike-deferred. Acknowledged in types.ts but not surfaced in UI.
    case "background_event":
    case "canvas_update":
    case "command_ack":
    case "models_available":
    case "notification_created":
    case "notification_read":
    case "reflection_result":
    case "vault_changed":
      return s;

    default: {
      // Forward-compat: surface unknown types as unchanged.
      // TS exhaustiveness check — if a new variant is added to ServerMessage
      // and not handled above, this will fail to compile.
      const _exhaustive: never = m;
      void _exhaustive;
      return s;
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd tui && npm test
```

Expected: all 11 tests pass.

- [ ] **Step 5: Verify typecheck passes**

```bash
cd tui && npm run typecheck
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add tui/src/dispatcher.ts tui/src/dispatcher.test.ts
git commit -m "feat(tui): pure dispatcher reducer + vitest unit tests"
```

---

### Task 4: WS transport

**Files:**
- Create: `tui/src/wsClient.ts`

`wsClient.ts` owns the socket. Dumb pipe: `connect()`, `send(msg)`, `on(handler)`, `close()`. Reconnect with exponential backoff. Surfaces a synthetic `__reconnected` event so the dispatcher / App can mark `[reconnected]` and re-issue `select_conv`.

The `ws` npm package supports cookie headers on upgrade; Node's global `WebSocket` does not. We use `ws`.

- [ ] **Step 1: Implement `tui/src/wsClient.ts`**

```typescript
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
 */

import WebSocket from "ws";
import type { ClientMessage, ServerMessage } from "./types.js";

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
  private ws: WebSocket | null = null;
  private handlers: Array<(e: WSEvent) => void> = [];
  private reconnectAttempt = 0;
  private wantClosed = false;
  private hadOpen = false;

  constructor(opts: WSClientOptions) {
    this.opts = opts;
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
    this.ws?.close();
  }

  connect(): void {
    const wsUrl = this.opts.host.replace(/^http/, "ws") + "/api/ws";
    const ws = new WebSocket(wsUrl, {
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
        // eslint-disable-next-line no-console
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
      // eslint-disable-next-line no-console
      console.error("[tui] ws error:", err.message);
      // close handler will fire after this; reconnect is scheduled there.
    });
  }

  private scheduleReconnect(): void {
    const delays = [1000, 2000, 4000, 8000, 16000, 30000];
    const idx = Math.min(this.reconnectAttempt, delays.length - 1);
    const delay = delays[idx]!;
    this.reconnectAttempt += 1;
    setTimeout(() => {
      if (!this.wantClosed) this.connect();
    }, delay);
  }

  private emit(e: WSEvent): void {
    for (const h of this.handlers) h(e);
  }
}
```

- [ ] **Step 2: Verify typecheck passes**

```bash
cd tui && npm run typecheck
```

Expected: clean.

- [ ] **Step 3: Verify dispatcher tests still pass**

```bash
cd tui && npm test
```

Expected: 11 tests still pass.

- [ ] **Step 4: Commit**

```bash
git add tui/src/wsClient.ts
git commit -m "feat(tui): WS transport with cookie auth + reconnect backoff"
```

---

### Task 5: Entry + argv

**Files:**
- Create: `tui/src/entry.tsx`

Entry point: TTY gate, argv/env parsing, construct `WSClient`, render `<App/>`. Exits early on missing token or non-TTY stdin.

- [ ] **Step 1: Implement `tui/src/entry.tsx`**

```typescript
#!/usr/bin/env -S npx tsx
/**
 * Entry point: parse argv + env, construct WSClient, render App.
 *
 * Required: token (--token / DECAFCLAW_TOKEN).
 * Optional: --host (default http://localhost:8088), --conv <id>.
 */

import React from "react";
import { render } from "ink";
import { WSClient } from "./wsClient.js";
import { App } from "./App.js";

interface Args {
  token: string;
  host: string;
  conv: string | null;
}

function parseArgs(argv: string[]): Args | { error: string } {
  const args: Partial<Args> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--token") args.token = argv[++i];
    else if (a === "--host") args.host = argv[++i];
    else if (a === "--conv") args.conv = argv[++i];
    else if (a === "--help" || a === "-h") {
      return {
        error:
          "Usage: decafclaw-tui [--token <t>] [--host <url>] [--conv <id>]\n" +
          "Env: DECAFCLAW_TOKEN, DECAFCLAW_HOST",
      };
    }
  }
  const token = args.token ?? process.env.DECAFCLAW_TOKEN;
  const host = args.host ?? process.env.DECAFCLAW_HOST ?? "http://localhost:8088";
  if (!token) {
    return { error: "Missing token. Use --token <t> or DECAFCLAW_TOKEN." };
  }
  return { token, host, conv: args.conv ?? null };
}

function main(): void {
  if (!process.stdin.isTTY) {
    // eslint-disable-next-line no-console
    console.error("decafclaw-tui requires a TTY stdin.");
    process.exit(1);
  }

  const parsed = parseArgs(process.argv.slice(2));
  if ("error" in parsed) {
    // eslint-disable-next-line no-console
    console.error(parsed.error);
    process.exit(1);
  }

  const client = new WSClient({ host: parsed.host, token: parsed.token });
  client.connect();

  render(<App client={client} initialConvId={parsed.conv} host={parsed.host} token={parsed.token} />);
}

main();
```

- [ ] **Step 2: Create a minimal `App.tsx` stub so entry typechecks**

`tui/src/App.tsx`:

```typescript
import React from "react";
import { Text } from "ink";
import type { WSClient } from "./wsClient.js";

export interface AppProps {
  client: WSClient;
  initialConvId: string | null;
  host: string;
  token: string;
}

export function App(_props: AppProps): React.JSX.Element {
  return <Text>decafclaw-tui (placeholder)</Text>;
}
```

- [ ] **Step 3: Verify typecheck passes**

```bash
cd tui && npm run typecheck
```

Expected: clean.

- [ ] **Step 4: Smoke-check the `--help` path (no WS required)**

```bash
cd tui && node --import tsx src/entry.tsx --help
```

Expected: usage text printed, exit 1.

- [ ] **Step 5: Commit**

```bash
git add tui/src/entry.tsx tui/src/App.tsx
git commit -m "feat(tui): entry point with argv/env parsing + TTY gate"
```

---

### Task 6: App — transcript + composer (basic chat)

**Files:**
- Modify: `tui/src/App.tsx`

Wire the dispatcher into App state. Render transcript + composer. Send `select_conv` on mount (if `initialConvId` is set) and dispatch incoming WS events. Composer submit → `send` message.

This task validates the chat round-trip without confirm prompts or activity lane.

- [ ] **Step 1: Replace `tui/src/App.tsx` with the basic chat view**

```typescript
import React, { useEffect, useReducer, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import TextInput from "ink-text-input";
import type { WSClient, WSEvent } from "./wsClient.js";
import { dispatch, initialState, type State } from "./dispatcher.js";
import type { ServerMessage } from "./types.js";

export interface AppProps {
  client: WSClient;
  initialConvId: string | null;
  host: string;
  token: string;
}

type Action =
  | { kind: "wire"; msg: ServerMessage }
  | { kind: "reconnected" }
  | { kind: "auth_failed"; reason: string }
  | { kind: "closed"; code: number; reason: string };

function reducer(s: State, a: Action): State {
  switch (a.kind) {
    case "wire":
      return dispatch(s, a.msg);
    case "reconnected":
      return {
        ...s,
        transcript: [...s.transcript, { kind: "system", text: "[reconnected]" }],
      };
    case "auth_failed":
      return {
        ...s,
        transcript: [
          ...s.transcript,
          { kind: "system", text: `[auth failed: ${a.reason}]` },
        ],
      };
    case "closed":
      return s;
  }
}

export function App({ client, initialConvId }: AppProps): React.JSX.Element {
  const [state, dispatchUi] = useReducer(reducer, initialState);
  const [draft, setDraft] = useState("");
  const { exit } = useApp();

  useEffect(() => {
    client.on((e: WSEvent) => {
      if (e.type === "__reconnected") {
        dispatchUi({ kind: "reconnected" });
        if (state.conv_id) client.send({ type: "select_conv", conv_id: state.conv_id });
      } else if (e.type === "__auth_failed") {
        dispatchUi({ kind: "auth_failed", reason: e.reason });
      } else if (e.type === "__closed") {
        dispatchUi({ kind: "closed", code: e.code, reason: e.reason });
      } else {
        dispatchUi({ kind: "wire", msg: e });
      }
    });

    if (initialConvId) {
      // tiny delay to let the socket open; the WSClient drops sends when not open
      // and a real reconnect-aware send would buffer — out of scope for spike.
      const t = setInterval(() => {
        client.send({ type: "select_conv", conv_id: initialConvId });
        clearInterval(t);
      }, 100);
      return () => clearInterval(t);
    }
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useInput((_input, key) => {
    if (key.ctrl && _input === "c") {
      client.close();
      exit();
    }
  });

  function onSubmit(text: string): void {
    if (!state.conv_id) return;
    if (!text.trim()) return;
    client.send({
      type: "send",
      conv_id: state.conv_id,
      text,
      attachments: [],
    });
    setDraft("");
  }

  return (
    <Box flexDirection="column">
      {state.transcript.map((item, i) => (
        <Text key={i} color={item.kind === "system" ? "yellow" : undefined}>
          {item.kind === "user" ? "you> " : item.kind === "assistant" ? "bot> " : ""}
          {item.text}
        </Text>
      ))}
      {state.draft && <Text color="cyan">bot> {state.draft}</Text>}
      <Box>
        <Text>{state.conv_id ? "> " : "(connecting...) "}</Text>
        <TextInput value={draft} onChange={setDraft} onSubmit={onSubmit} />
      </Box>
    </Box>
  );
}
```

- [ ] **Step 2: Verify typecheck passes**

```bash
cd tui && npm run typecheck
```

Expected: clean.

- [ ] **Step 3: Verify dispatcher tests still pass**

```bash
cd tui && npm test
```

Expected: 11 tests pass.

- [ ] **Step 4: Manual smoke test**

Prerequisite: `make dev` running on `http://localhost:8088`. Token from `data/{agent_id}/web_tokens.json`.

```bash
cd tui && DECAFCLAW_TOKEN=<token> npm run dev -- --conv smoke-tui-spike
```

Expected:
1. TUI launches, shows `> ` prompt.
2. Type "hello" and Enter.
3. See `bot> ...` stream in.
4. After completion the streamed line stays.
5. Ctrl+C exits cleanly.

If `--conv smoke-tui-spike` is a fresh ID, the bot creates a new conversation lazily on the first `send`.

- [ ] **Step 5: Commit**

```bash
git add tui/src/App.tsx
git commit -m "feat(tui): wire dispatcher into App; basic chat round-trip working"
```

---

### Task 7: Activity lane

**Files:**
- Modify: `tui/src/App.tsx`

Add a single-line activity indicator showing the current tool's name + status. Renders above the composer when `state.activity` is non-null.

- [ ] **Step 1: Update `App.tsx`'s render to include the activity lane**

Replace the JSX `return` in `App` with:

```typescript
  return (
    <Box flexDirection="column">
      {state.transcript.map((item, i) => (
        <Text key={i} color={item.kind === "system" ? "yellow" : undefined}>
          {item.kind === "user" ? "you> " : item.kind === "assistant" ? "bot> " : ""}
          {item.text}
        </Text>
      ))}
      {state.draft && <Text color="cyan">bot> {state.draft}</Text>}
      {state.activity && (
        <Text color="gray">
          [{state.activity.name}] {state.activity.status || "running..."}
        </Text>
      )}
      <Box>
        <Text>{state.conv_id ? "> " : "(connecting...) "}</Text>
        <TextInput value={draft} onChange={setDraft} onSubmit={onSubmit} />
      </Box>
    </Box>
  );
```

- [ ] **Step 2: Verify typecheck + tests**

```bash
cd tui && npm run typecheck && npm test
```

Expected: clean + 11 pass.

- [ ] **Step 3: Manual smoke test**

Same setup as Task 6. In the TUI, send a message that triggers a tool — e.g., ask the agent to read a file via the existing `read_file` tool (no confirmation required) or run a vault read.

Expected:
1. While the tool runs, a gray `[tool_name] running...` line appears between the streaming `bot>` line and the composer.
2. When `tool_end` arrives, the gray line disappears.

- [ ] **Step 4: Commit**

```bash
git add tui/src/App.tsx
git commit -m "feat(tui): activity lane shows in-flight tool name + status"
```

---

### Task 8: Confirm prompt + Ctrl+C cancel

**Files:**
- Modify: `tui/src/App.tsx`

When `state.confirm` is non-null, suspend the composer and show an inline prompt. Accept `y` / `n` / `a` keys and send `confirm_response` with `decision` of `approve` / `deny` / `always`. Then clear `state.confirm`.

Also: Ctrl+C while `turnInFlight` sends `cancel_turn` instead of exiting. Ctrl+C twice in a row (or while idle) exits.

- [ ] **Step 1: Add a confirm-cleared local Action variant**

Update the `Action` type and `reducer` in `App.tsx`:

```typescript
type Action =
  | { kind: "wire"; msg: ServerMessage }
  | { kind: "reconnected" }
  | { kind: "auth_failed"; reason: string }
  | { kind: "closed"; code: number; reason: string }
  | { kind: "clear_confirm" };

function reducer(s: State, a: Action): State {
  switch (a.kind) {
    case "wire":
      return dispatch(s, a.msg);
    case "reconnected":
      return {
        ...s,
        transcript: [...s.transcript, { kind: "system", text: "[reconnected]" }],
      };
    case "auth_failed":
      return {
        ...s,
        transcript: [
          ...s.transcript,
          { kind: "system", text: `[auth failed: ${a.reason}]` },
        ],
      };
    case "closed":
      return s;
    case "clear_confirm":
      return { ...s, confirm: null };
  }
}
```

- [ ] **Step 2: Update `useInput` to handle confirm + Ctrl+C semantics**

Replace the `useInput` block:

```typescript
  const [cancelArmed, setCancelArmed] = useState(false);

  useInput((input, key) => {
    // Confirm prompt active: y/n/a keys send decision.
    if (state.confirm) {
      const decision =
        input === "y" || input === "Y" ? "approve" :
        input === "n" || input === "N" ? "deny" :
        input === "a" || input === "A" ? "always" :
        null;
      if (decision && state.conv_id) {
        client.send({
          type: "confirm_response",
          conv_id: state.conv_id,
          request_id: state.confirm.request_id,
          decision,
          extras: {},
        });
        dispatchUi({ kind: "clear_confirm" });
      }
      return;
    }

    // Ctrl+C: cancel turn if busy (first press), or exit (idle or second press).
    if (key.ctrl && input === "c") {
      if (state.turnInFlight && !cancelArmed && state.conv_id) {
        client.send({ type: "cancel_turn", conv_id: state.conv_id });
        setCancelArmed(true);
        // arm for 2s; after that Ctrl+C cancels again instead of exiting
        setTimeout(() => setCancelArmed(false), 2000);
      } else {
        client.close();
        exit();
      }
    }
  });
```

- [ ] **Step 3: Render the confirm prompt above the composer**

Update the JSX return to include the prompt:

```typescript
  return (
    <Box flexDirection="column">
      {state.transcript.map((item, i) => (
        <Text key={i} color={item.kind === "system" ? "yellow" : undefined}>
          {item.kind === "user" ? "you> " : item.kind === "assistant" ? "bot> " : ""}
          {item.text}
        </Text>
      ))}
      {state.draft && <Text color="cyan">bot> {state.draft}</Text>}
      {state.activity && (
        <Text color="gray">
          [{state.activity.name}] {state.activity.status || "running..."}
        </Text>
      )}
      {state.confirm && (
        <Box flexDirection="column">
          <Text color="magenta">
            confirm ({state.confirm.kind}): {JSON.stringify(state.confirm.payload)}
          </Text>
          <Text color="magenta">[y]es / [n]o / [a]lways</Text>
        </Box>
      )}
      <Box>
        <Text>{state.conv_id ? "> " : "(connecting...) "}</Text>
        {!state.confirm && (
          <TextInput value={draft} onChange={setDraft} onSubmit={onSubmit} />
        )}
      </Box>
    </Box>
  );
```

- [ ] **Step 4: Verify typecheck + tests**

```bash
cd tui && npm run typecheck && npm test
```

Expected: clean + 11 pass.

- [ ] **Step 5: Manual smoke test (approve)**

In the TUI, send a message that triggers a confirmation. For example: "run the shell command `ls /tmp`".

Expected:
1. After a moment, magenta confirmation block appears with the command + `[y]es / [n]o / [a]lways`.
2. Press `y`. Confirmation disappears, composer reactivates, tool runs, output streams back.

- [ ] **Step 6: Manual smoke test (deny)**

Same setup, press `n` instead.

Expected: agent acknowledges denial and recovers (probably explains it won't run the command).

- [ ] **Step 7: Manual smoke test (cancel mid-turn)**

Send a message that produces a long response. Hit Ctrl+C while the response streams.

Expected: stream halts shortly after (server processes `cancel_turn` and emits `turn_complete`). Composer reactivates. A second Ctrl+C within 2s exits.

- [ ] **Step 8: Commit**

```bash
git add tui/src/App.tsx
git commit -m "feat(tui): inline confirm prompt + Ctrl+C cancel/exit"
```

---

### Task 9: Conversation picker

**Files:**
- Create: `tui/src/conversationPicker.tsx`
- Modify: `tui/src/App.tsx`

When `--conv` is absent, show a picker on launch: list recent conversations from `GET /api/conversations` with cookie auth, plus a "New conversation" option that generates a fresh `conv_id`.

- [ ] **Step 1: Verify the REST endpoint shape**

```bash
TOKEN=<token>; curl -s -H "Cookie: decafclaw_session=$TOKEN" http://localhost:8088/api/conversations | head -c 500
```

Expected: JSON array of `{conv_id, ...}` records. If the shape is something else, adjust the picker's mapping accordingly. If `/api/conversations` doesn't exist, check `web/conversations.py` for the actual route and update.

- [ ] **Step 2: Implement `tui/src/conversationPicker.tsx`**

```typescript
import React, { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";

interface ConvSummary {
  conv_id: string;
  title?: string;
  updated_at?: string;
}

interface Props {
  host: string;
  token: string;
  onPick: (convId: string) => void;
}

function newConvId(): string {
  // Random short ID; the bot creates the conversation lazily on first send.
  return "tui-" + Math.random().toString(36).slice(2, 10);
}

export function ConversationPicker({ host, token, onPick }: Props): React.JSX.Element {
  const [convs, setConvs] = useState<ConvSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    fetch(host + "/api/conversations", {
      headers: { Cookie: `decafclaw_session=${token}` },
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return (await r.json()) as ConvSummary[] | { conversations: ConvSummary[] };
      })
      .then((data) => {
        const arr = Array.isArray(data) ? data : data.conversations;
        setConvs(arr.slice(0, 20));
      })
      .catch((e: Error) => setErr(e.message));
  }, [host, token]);

  useInput((input, key) => {
    if (!convs) return;
    const max = convs.length; // +1 for "new conversation" at index `max`
    if (key.upArrow) setCursor((c) => Math.max(0, c - 1));
    else if (key.downArrow) setCursor((c) => Math.min(max, c + 1));
    else if (key.return) {
      if (cursor === max) onPick(newConvId());
      else onPick(convs[cursor]!.conv_id);
    } else if (input === "n" || input === "N") {
      onPick(newConvId());
    }
  });

  if (err) return <Text color="red">Failed to list conversations: {err}</Text>;
  if (!convs) return <Text>Loading conversations…</Text>;

  return (
    <Box flexDirection="column">
      <Text bold>Pick a conversation (Up/Down + Enter, or `n` for new):</Text>
      {convs.map((c, i) => (
        <Text key={c.conv_id} color={i === cursor ? "cyan" : undefined}>
          {i === cursor ? "> " : "  "}
          {c.title || c.conv_id}
        </Text>
      ))}
      <Text color={cursor === convs.length ? "cyan" : "gray"}>
        {cursor === convs.length ? "> " : "  "}[new conversation]
      </Text>
    </Box>
  );
}
```

- [ ] **Step 3: Wire the picker into `App.tsx`**

Modify the top of the `App` body:

```typescript
  const [pickedConv, setPickedConv] = useState<string | null>(initialConvId);

  useEffect(() => {
    if (pickedConv) {
      const t = setInterval(() => {
        client.send({ type: "select_conv", conv_id: pickedConv });
        clearInterval(t);
      }, 100);
      return () => clearInterval(t);
    }
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickedConv]);
```

Remove the old `initialConvId`-only `useEffect` (the one from Task 6). And at the top of the JSX `return`:

```typescript
  if (!pickedConv) {
    return (
      <ConversationPicker
        host={host}
        token={token}
        onPick={(id) => setPickedConv(id)}
      />
    );
  }
```

Add the import:

```typescript
import { ConversationPicker } from "./conversationPicker.js";
```

(`host` and `token` are already in `AppProps`.)

- [ ] **Step 4: Verify typecheck + tests**

```bash
cd tui && npm run typecheck && npm test
```

Expected: clean + 11 pass.

- [ ] **Step 5: Manual smoke test**

```bash
cd tui && DECAFCLAW_TOKEN=<token> npm run dev
```

(No `--conv` flag.)

Expected:
1. Picker appears listing recent conversations.
2. Arrow keys move the cursor.
3. Enter on a conversation switches to chat view with history loaded.
4. Enter on `[new conversation]` (or `n` key) starts a fresh conversation.

- [ ] **Step 6: Commit**

```bash
git add tui/src/App.tsx tui/src/conversationPicker.tsx
git commit -m "feat(tui): conversation picker on launch when --conv absent"
```

---

### Task 10: Acceptance criteria walkthrough + README polish

**Files:**
- Modify: `tui/README.md`

Final task: walk the acceptance criteria from the spec, fixing anything that surfaces. Then polish the README with what's actually working.

- [ ] **Step 1: Walk the acceptance criteria (spec.md "Acceptance criteria")**

For each box, run the smoke + check it off. Note any deviations in `notes.md`.

```bash
ls docs/dev-sessions/2026-05-13-1039-tui-spike/
# create notes.md if not present
```

Spec acceptance criteria:
- [ ] Connect with `--token <t>` and either pick a conversation or pass `--conv <id>`.
- [ ] Send a user message, see streamed assistant response, see `message_complete` finalize it.
- [ ] Trigger a confirm-gated tool (shell command), approve inline, see tool output continue.
- [ ] Trigger a confirm-gated tool, deny inline, see the agent recover.
- [ ] `Ctrl+C` mid-turn cancels via `cancel_turn`; transcript reflects the cancel.
- [ ] Kill `make dev`, restart, see TUI reconnect and resume the same conversation.
- [ ] Run `cd tui && npm test` — dispatcher unit test passes.
- [ ] Run `cd tui && npm run typecheck` — clean.

(The spec's earlier `npm run lint` line is dropped — no lint configured for the spike.)

- [ ] **Step 2: Update `tui/README.md` with anything you learned**

Examples: actual REST endpoint shape, token-finding command, any quirks. Add a "Known limitations" section listing what the spike doesn't do (cross-reference the spec's Phase 2 candidates table).

- [ ] **Step 3: Capture session retro in `notes.md`**

Per CLAUDE.md dev-session convention, write a brief retro:
- What worked / what surprised.
- Wire-protocol corrections needed beyond the spec patch.
- Whether the design held up under implementation.
- Recommended Phase 2 first-step (markdown rendering? something else?).

- [ ] **Step 4: Commit**

```bash
git add tui/README.md docs/dev-sessions/2026-05-13-1039-tui-spike/notes.md
git commit -m "docs(tui): acceptance criteria walkthrough + session retro"
```

---

## After completion

Plan complete when all task checkboxes are checked. Next steps the user may want:

1. **Promote A → B** (codegen from `message_types.json`) if the spike is keeping. Write `tui/scripts/gen-types.ts`, extend `make gen-message-types` to also emit `tui/src/types.generated.ts`.
2. **Phase 2 work** — see the spec's "Phase 2 candidates" table. Natural first step: markdown rendering of assistant text.
3. **Engage with #487** if widget / canvas work surfaces — that's the cross-cutting capability concern.
4. **Open PR** for the spike if keeping. The branch is `worktree-tui-spike` (harness-prefixed). Rename to `tui-spike` via `git branch -m tui-spike` first if preferred.
