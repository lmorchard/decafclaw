# Plan — #510 WSClient lifecycle hygiene

## Approach

Add a `reconnectTimer` field on `WSClient`. Store the timer ID when `scheduleReconnect` arms a reconnect; clear it in `close()` and at the top of `scheduleReconnect()` (defensive). Compute `wsUrl` once in the constructor and store on the instance so the error handler can include it. Enhance the `ws.on("error")` log to include URL + `reconnectAttempt`.

Test the close-cancel-reconnect behavior with a vitest fake-timers + module-mocked `ws` setup. The mock is a minimal EventEmitter subclass that records its instances; the test simulates the ws `close` event to trigger reconnect scheduling, then calls `WSClient.close()` and advances timers to verify no second connect happens.

## File changes

| File | Action | Why |
|---|---|---|
| `tui/src/wsClient.ts` | modify | (a) add `private reconnectTimer: NodeJS.Timeout \| null`. (b) store `wsUrl` on instance (derived from `opts.host`). (c) `scheduleReconnect`: clear pending timer, assign new one, clear inside the callback. (d) `close()`: clear timer. (e) enhance error log. |
| `tui/src/wsClient.test.ts` | **new** | `vi.mock("ws", ...)` + `vi.useFakeTimers()`. One primary test: `close()` after `scheduleReconnect` prevents a second connect. Bonus: verify error log includes URL + attempt. |

## Step-by-step

### Step 1 — Implementation

1. In `tui/src/wsClient.ts`:
   - Add `private reconnectTimer: NodeJS.Timeout | null = null;` field.
   - Add `private readonly wsUrl: string;` field; initialize in constructor as `opts.host.replace(/^http/, "ws") + "/ws/chat"`.
   - In `connect()`: use `this.wsUrl` instead of computing locally.
   - In `scheduleReconnect()`: before scheduling, `if (this.reconnectTimer) clearTimeout(this.reconnectTimer);`. Assign result of `setTimeout(...)` to `this.reconnectTimer`. Inside the callback, set `this.reconnectTimer = null` before calling `connect()`.
   - In `close()`: `if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }`.
   - In `ws.on("error", ...)`: change log to `console.error("[tui] ws error:", err.message, "url=", this.wsUrl, "attempt=", this.reconnectAttempt);`.
2. Verify `npm run typecheck` clean.

### Step 2 — Tests (TDD-ish; test added after impl because the mock setup is non-trivial and the impl is small)

1. Create `tui/src/wsClient.test.ts`.
2. Module-mock `ws` with a fake `EventEmitter`-based class that tracks instances and exposes `close()`/`send()`.
3. Tests:
   - **close cancels pending reconnect:** `connect()` → simulate `ws.emit("close", 1006, Buffer.from(""))` → assert `reconnectTimer` is set (one FakeWebSocket exists). Call `close()`. `vi.advanceTimersByTime(2000)`. Assert no new FakeWebSocket instance was created.
   - **error log includes URL and attempt:** spy on `console.error`. `connect()` → simulate `ws.emit("error", new Error("boom"))`. Assert the error log was called with arguments containing the wsUrl string and the attempt count.
4. Run `npm test`.

### Step 3 — Verify

1. `cd tui && npm test` — all green (was 25, now 27+).
2. `cd tui && npm run typecheck` — clean.
3. `make check` — clean.
4. (No live-smoke needed; this is pure internal hygiene with no observable behavior change in the normal-operation path. The change is only observable in the close-during-pending-reconnect path, which is what the unit test covers.)
5. Branch self-review.

## Risks / open notes

- **Mocking `ws`.** vitest's `vi.mock("ws")` needs to run before the import of `wsClient` resolves. Standard pattern: top-level `vi.mock` call before the SUT import. Use `vi.hoisted` if needed.
- **`NodeJS.Timeout` type.** `setTimeout` in Node returns `NodeJS.Timeout`; in DOM it returns a number. tsc with default lib settings will pick the right one; verify with `npm run typecheck`. If TS complains, use `ReturnType<typeof setTimeout>` for portability.
- **Behavioral compatibility.** The existing `if (!this.wantClosed)` guard inside the timer callback stays — defense-in-depth. Removing it would tighten the code but rely solely on `clearTimeout` working, and the redundant check costs nothing.

## Plan self-review

- **Placeholders?** None.
- **Internal consistency?** Steps map 1:1 to acceptance criteria.
- **Scope?** Single file change + single test file. Genuinely S.
- **Ambiguity?** The mock setup details surface during execution; planned approach is the standard vitest pattern.
