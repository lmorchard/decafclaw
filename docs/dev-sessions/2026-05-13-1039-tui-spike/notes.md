# TUI Spike — Session Notes

Branch: `worktree-tui-spike` (worktree at `.claude/worktrees/tui-spike/`).
Spec: [spec.md](spec.md). Plan: [plan.md](plan.md).

## What got built

Ten tasks delivered as planned: TS scaffold (T1), hand-typed wire message types
(T2), pure dispatcher reducer with vitest TDD (T3), WS transport with cookie auth
and reconnect backoff (T4), entry point with argv/env parsing and TTY gate (T5),
App with transcript + composer + activity lane (T6-T7), inline confirm prompt +
Ctrl+C cancel/exit (T8), conversation picker on launch when `--conv` is absent
(T9), and this retro (T10). The final file layout matches the plan (six source
files + one test file). No bot-side changes were needed.

## Plan / reality deltas

A handful of things in the plan turned out to be wrong or underspecified:

- **T1: typecheck on empty src passes silently.** `tsc --noEmit` with no `.ts`/`.tsx`
  files exits 0 with no output, so the "verify typecheck" step was a no-op. Not a
  bug — just an anti-climactic first smoke test.

- **T2: `vault_changed` missing `kind` field.** The plan's `types.ts` snippet
  defined `SrvVaultChanged` with only `path: string`. The actual
  `message_types.json` has a second field `kind: string`. Fixed in T2; documented
  with a `NOTE` comment inline. Also: `CliSetEffort.effort` in the plan was wrong
  — the actual manifest field is `model: string` (same as `set_model`). `set_effort`
  is a deprecated alias. Corrected in T2 and documented with a comment.

- **T4: WSClient lifecycle edge cases.** Two gaps flagged in code review: (a)
  calling `connect()` while a socket is still open could cause double-emit of
  `__reconnected` during the overlap window; (b) `close()` did not reset `hadOpen`,
  so a re-`connect()` after an intentional close would spuriously emit
  `__reconnected`. Fixed in a follow-up commit (`fix(tui): tighten WSClient
  lifecycle`) — both are one-liners that close the windows cleanly.

- **T6: JSX text `>` must be expressed as `{"bot> "}`** when used as a literal in
  JSX — the bare `>` character is ambiguous to the parser. Fixed in App.tsx.

- **T6: Ink's `render()` defaults to `exitOnCtrlC: true`**, which preempts the
  `useInput` handler in App.tsx. The plan did not flag this. The fix is to pass
  `{ exitOnCtrlC: false }` to `render()` in `entry.tsx`, then let App own shutdown
  (which it needs to do anyway to call `client.close()` and set `wantClosed`
  before exiting). Fixed in a follow-up commit before T8 was started.

- **T8: Ctrl+C swallowed during confirm prompt.** The plan's `useInput`
  implementation checked `state.confirm` before the Ctrl+C check, so a user with
  a pending confirmation had no way to exit. Fixed by moving the Ctrl+C handler
  to the top of the `useInput` callback (so it always fires) and adding a `return`
  after each branch.

- **T9: REST shape is not a bare array.** The plan assumed `GET /api/conversations`
  returns `ConvSummary[]`. The actual endpoint returns
  `{ folder, folders, conversations }`. `conversationPicker.tsx` was updated to
  extract `data.conversations` rather than treating the response as an array.

- **T9: Stale closure on `conv_id` breaks reconnect-resume.** The WS subscription
  `useEffect` runs once on mount (empty dep array) and captures `state.conv_id`
  as null at that point. On `__reconnected`, the handler was reading the stale null
  and never re-sending `select_conv`. Fixed with a `useRef` (`activeConvIdRef`)
  that is kept current by a separate `useEffect([pickedConv])`. This is the React
  stale-closure footgun for long-lived subscriptions.

## Acceptance criteria walkthrough (for Les)

The spec's acceptance criteria require live testing against `make dev`. Walk them
in order:

- [ ] Connect with `--token <t>` and either pick a conversation or pass `--conv <id>`.
- [ ] Send a user message, see streamed assistant response, see `message_complete` finalize it.
- [ ] Trigger a confirm-gated tool (shell command), approve inline, see tool output continue.
- [ ] Trigger a confirm-gated tool, deny inline, see the agent recover.
- [ ] `Ctrl+C` mid-turn cancels via `cancel_turn`; transcript reflects the cancel.
- [ ] Kill `make dev`, restart, see TUI reconnect and resume the same conversation. (Fixed via `useRef` in T9 follow-up; verify the fix works in practice.)
- [ ] Run `cd tui && npm test` — dispatcher unit tests pass (11/11).
- [ ] Run `cd tui && npm run typecheck` — clean.

Commands for the live tests:

```bash
# Grab a token key from the agent's web_tokens.json.
# Replace <agent_id> with the subdirectory name under data/ for the bot you want.
TOKEN=$(jq -r 'keys[0]' /Users/lorchard/devel/decafclaw/data/<agent_id>/web_tokens.json)

# Launch with picker:
cd tui && DECAFCLAW_TOKEN="$TOKEN" npm run dev

# Launch with explicit conversation:
cd tui && DECAFCLAW_TOKEN="$TOKEN" npm run dev -- --conv smoke-tui-spike

# For a confirm-gated tool: ask the agent to run a shell command,
# e.g. "run the shell command: ls /tmp"
```

## Retro candidates (Phase 2 follow-ups)

Items flagged during code reviews but explicitly deferred. See the spec's
"Phase 2 candidates" table for the big-ticket items (markdown rendering,
multi-line composer, persistent input history, tab completion, theme, model
picker, widgets, canvas, vault sidebars). Smaller items that surfaced during
reviews:

- **WSClient:** `setTimeout` reference in `scheduleReconnect` is untracked — can't
  be cancelled if `close()` is called in the same tick before the timer fires.
- **WSClient:** error logs lack URL and attempt-count context (hard to debug
  reconnect loops without them).
- **WSClient:** no jitter in backoff. Irrelevant for a single-client terminal tool;
  matters at scale.
- **Dispatcher:** `conv_history` handler has no test. Intentional per plan (history
  load is hard to unit-test without fixtures); note it for Phase 2 coverage.
- **App.tsx:** `setInterval` send-on-mount hack (100 ms before `select_conv`) would
  benefit from a proper send-buffer in WSClient rather than polling.
- **App.tsx:** `cancelArmed` timeout is untracked (process lifetime is short enough
  that it doesn't matter in practice).
- **App.tsx:** `JSON.stringify(payload)` confirm display is ugly for complex
  payloads — a compact pretty-printer would help.
- **App.tsx:** `[disconnected]` system line (on `__closed`) could complement the
  existing `[reconnected]` for clearer connection state during the drop window.
- **Picker:** no Ctrl+C / Escape / `q` to abort during the fetch loading state.
- **Picker:** `updated_at` field is declared in `ConvSummary` but never displayed
  (the plan included it for possible sorting/display; dropped from scope).
- **Entry:** TTY check fires before `--help` parsing — `--help` on a non-TTY exits
  with "requires a TTY stdin" rather than showing usage. Minor but non-standard.
- **Entry:** argv parsing has no missing-value detection — `--token` at end of
  argv silently gets `undefined` as the token value.
- **types.ts:** `decision: string` on `CliConfirmResponse` could be
  `"approve" | "deny" | "always"` literal union for tighter wire safety.

## Capability gap (Issue #487)

The spec's cross-cutting concern about transport capabilities is tracked at
https://github.com/lmorchard/decafclaw/issues/487 — not blocking the spike but
relevant for Phase 2 widget/canvas work. The TUI is the first new transport that
can't render the full web UI surface, making the implicit capability assumption
visible.

## Whether the spike "validated"

Pending Les's manual smoke walkthrough. The implementation passes typecheck +
unit tests + spec-compliance reviews + code-quality reviews. Every acceptance
criterion that doesn't require a live environment is satisfied. The only
validation gap is live behavior against `make dev`.

## Recommended Phase 2 first step

Per the spec's Phase 2 sequence: **markdown rendering of assistant text**. The
plain-text transcript gets old quickly and users expect at minimum code-block
rendering. Hermes's Ink markdown renderer (`@inkjs/ui` or a small custom
component) is the reference. After that: **multi-line composer + persistent input
history** — the single-line `ink-text-input` composer is the next most visible
friction point.
