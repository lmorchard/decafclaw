# Plan — #511 App + picker UX rough edges

## Approach

Five logical commits, ordered to keep each one independently reviewable. Item 6 (newConvId) is already done; item 3 (JSON.stringify) is partially done. The remaining items are in two domains: WS transport (item 1) and pure UI (items 2, 3-residual, 4, 5, 7). WS transport goes first because App.tsx changes depend on its new behavior.

## File changes

| File | Action |
|---|---|
| `tui/src/wsClient.ts` | Add `outbound: ClientMessage[]` field. `send()` buffers when not OPEN. `connect()` flushes buffer on `ws.on("open")` after the existing `__reconnected` emit. |
| `tui/src/wsClient.test.ts` | New case: `send()` before open → buffered → flushed on open. |
| `tui/src/App.tsx` | (a) Drop the 250ms polling loop in the `select_conv` useEffect; replace with a single send guarded on `state.conv_id !== pickedConv`. (b) `useRef` for cancelArmed timer. (c) `[disconnected]` reducer case. (d) Confirm display includes `suggested_pattern` line when non-empty. |
| `tui/src/conversationPicker.tsx` | (a) `useApp().exit()` import. (b) `useInput` handles `key.escape` / `"q"` / Ctrl+C above the loading guard. (c) New `formatRelative(iso)` helper. (d) Render `updated_at` next to each conversation title when present. |

## Step-by-step

### Step 1 — WSClient outbound buffer (TDD)

1. Add a failing test to `wsClient.test.ts`: `send()` called before any open is buffered; after the fake WS emits `open`, the buffer flushes (mock `.send` on FakeWebSocket records calls).
2. Implement: add `private outbound: ClientMessage[] = []` field. In `send()`, if `ws?.readyState !== OPEN`, push to `outbound`; else send. In `ws.on("open")` after the existing `__reconnected` emit logic, if `outbound.length`, flush each to `ws.send(JSON.stringify(m))` and clear.
3. Run `npm test`. Commit.

### Step 2 — App.tsx: drop setInterval, track cancelArmed, [disconnected], suggested_pattern

This is the bulk of the App.tsx changes. Doing them in one commit because they're tightly co-located:

1. Replace the `useEffect([pickedConv, state.conv_id])` poller with a single guarded send. Remove `cancelled` flag and `setInterval`.
2. `cancelArmedRef = useRef<ReturnType<typeof setTimeout> | null>(null)`. In the Ctrl+C handler that arms, `clearTimeout(cancelArmedRef.current)` first, then store the new timer. Add a cleanup `useEffect(() => () => { if (cancelArmedRef.current) clearTimeout(cancelArmedRef.current); }, [])`.
3. Reducer `case "closed"` returns a transcript-appended `[disconnected: ${code}${reason ? ` ${reason}` : ""}]` system line.
4. Confirm display: if `state.confirm.suggested_pattern`, render an extra `<Text>` line below the existing magenta block: `suggested pattern: <pattern>`.
5. `npm test` + `npm run typecheck`. Commit.

### Step 3 — conversationPicker.tsx: abort + updated_at

1. Import `useApp` from `ink`; call `const { exit } = useApp();`.
2. `useInput` head:
   ```ts
   if (key.ctrl && input === "c") { exit(); return; }
   if (key.escape || input === "q" || input === "Q") { exit(); return; }
   if (!convs || creating) return;  // existing
   ```
3. Add `formatRelative(iso?: string): string` — returns `""` when undefined; otherwise computes diff in seconds and formats as `1s ago` / `42m ago` / `3h ago` / `5d ago` / ISO date if > 30d.
4. Render line: `{c.title || c.conv_id}{rel ? `  (${rel})` : ""}` with `rel` colored gray to keep it secondary.
5. `npm test` + `npm run typecheck`. Commit.

### Step 4 — Verify

1. `npm test` — all green (was 28; new buffer test brings to 29+).
2. `npm run typecheck` — clean.
3. `make check` — clean.
4. Branch self-review.

## Risks / open notes

- **Test for App.tsx changes.** Ink components are awkward to render-test without a full Ink test setup. The dispatcher already tests the `closed` reducer case path (well, didn't — we'll add one for `[disconnected]`). The cancelArmed-timer hygiene and setInterval-removal are not unit-testable without mocking Ink — defer to manual live-smoke if needed, or skip. Plan: add a dispatcher-level test for the new `[disconnected]` system line via the App reducer. Skip explicit tests for the timer-tracking and setInterval-removal.

   Wait — the `[disconnected]` line is added in App.tsx's local `reducer`, not the dispatcher. Different reducer. I'll add it to App.tsx; testing that requires Ink test setup which we don't have. Cross-check the diff carefully instead. If I want this testable, I'd need to extract App.tsx's reducer to a separate module like I did with parseArgs for #512. Decision: leave it inline (consistent with current style); rely on TS + diff review.

- **`updated_at` format.** Server-side: `updated_at` field is an ISO string per `web/conversations.py` (verify when writing the code).

- **Confirm display.** Adding a second line for `suggested_pattern` will shift the y/n/a prompt down one line. Cosmetic — acceptable.

- **`outbound` buffer unbounded.** No bound is acceptable here — a single-client TUI won't realistically queue millions of messages, and the buffer flushes on each open. Adding a bound would invite incorrect dropping behavior.

## Plan self-review

- **Placeholders?** None.
- **Internal consistency?** Steps 1–3 cover all in-scope items.
- **Scope?** Three source files modified, one test file modified. Five real items, two already-done items closed out in commit message. S.
- **Ambiguity?** `updated_at` server-side format verified at execute time (one grep).
