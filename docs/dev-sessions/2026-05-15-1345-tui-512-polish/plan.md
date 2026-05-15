# Plan — #512 entry-point polish + dispatcher test

## Approach

Extract `parseArgs` from `entry.tsx` into its own module so it's unit-testable as a pure function (`argv`, `env`) → discriminated union. Refactor `main()` to call `parseArgs` BEFORE the TTY gate, so `--help` prints to stdout and exits 0 even on a non-TTY pipe. Add bounds-checks to flag-parsing so `--token` at end-of-argv produces a clear error rather than silently consuming `undefined`. Add a `conv_history` dispatcher test that mirrors existing test style.

The extraction is the load-bearing change — it's the prerequisite for both items 1 and 2 being testable without `child_process` shenanigans. Two files added, one modified, plus one test added to `dispatcher.test.ts`.

## File changes

| File | Action | Why |
|---|---|---|
| `tui/src/parseArgs.ts` | **new** | Pure function: `parseArgs(argv: string[], env: Record<string, string \| undefined>) → ParseResult`. Discriminated union `{kind: "ok", args} \| {kind: "help", message} \| {kind: "error", message}`. Bounds-checks flag values. |
| `tui/src/parseArgs.test.ts` | **new** | Vitest tests covering: --help / -h, valid args, missing values for each flag, missing token, token from env. |
| `tui/src/entry.tsx` | modify | Import parseArgs from new module. Reorder `main()`: parseArgs first → help (stdout, exit 0) → error (stderr, exit 1) → TTY check → client/render. |
| `tui/src/dispatcher.test.ts` | modify | Add `conv_history` test: fixture with user + assistant messages → transcript populated in order. |

## Step-by-step

### Step 1 — Extract + test `parseArgs` (TDD)

1. Write `tui/src/parseArgs.test.ts` first with tests for:
   - `parseArgs(["--help"], {})` → `{kind: "help", message: <usage>}`
   - `parseArgs(["-h"], {})` → `{kind: "help", message: <usage>}`
   - `parseArgs(["--token", "t1", "--host", "http://x", "--conv", "c1"], {})` → `{kind: "ok", args: {token: "t1", host: "http://x", conv: "c1"}}`
   - `parseArgs(["--token"], {})` → `{kind: "error", message: /--token/}` (missing value)
   - `parseArgs(["--host"], {DECAFCLAW_TOKEN: "t1"})` → `{kind: "error", message: /--host/}` (missing value)
   - `parseArgs(["--conv"], {DECAFCLAW_TOKEN: "t1"})` → `{kind: "error", message: /--conv/}` (missing value)
   - `parseArgs([], {})` → `{kind: "error", message: /token/}` (missing required)
   - `parseArgs([], {DECAFCLAW_TOKEN: "t1"})` → `{kind: "ok", args: {token: "t1", host: "http://localhost:8088", conv: null}}` (env fallback)
   - `parseArgs([], {DECAFCLAW_TOKEN: "t1", DECAFCLAW_HOST: "http://example:9000"})` → `{kind: "ok", args: {..., host: "http://example:9000"}}` (env host)
2. Watch tests fail (no module yet).
3. Create `tui/src/parseArgs.ts` exporting `parseArgs(argv, env)` returning `ParseResult`. Bounds-check via `if (i + 1 >= argv.length) return {kind: "error", message: \`Missing value for ${a}\`}`. Help message lifted from current entry.tsx usage line.
4. Watch tests pass.
5. Commit.

### Step 2 — Refactor `entry.tsx` to use new parseArgs + reorder

1. Update `entry.tsx`:
   - Remove local `parseArgs` (and `Args` interface — re-export from parseArgs.ts).
   - Import `parseArgs` from `./parseArgs.js`.
   - `main()` order: parseArgs → if `help`: console.log(message), exit 0 → if `error`: console.error(message), exit 1 → TTY check → client/render.
2. Verify `npm run typecheck` clean.
3. Live smoke (manual):
   - `echo | npx tsx src/entry.tsx --help` → prints usage to stdout, exits 0.
   - `npx tsx src/entry.tsx --token` → prints error to stderr, exits 1.
4. Commit.

### Step 3 — Add `conv_history` dispatcher test

1. Add a test to `tui/src/dispatcher.test.ts` mirroring the existing style:
   - Construct `conv_history` message with two messages (user + assistant) carrying `role` + `text` fields.
   - Dispatch on `initialState`.
   - Assert `transcript` matches `[{kind: "user", text: "hi"}, {kind: "assistant", text: "hello"}]`.
   - Bonus: a second test with three messages (user + assistant + tool) confirming tool role is skipped per spike intent.
2. Run `npm test` — all green (was 12, now 14+).
3. Commit.

### Step 4 — Verify

1. `cd tui && npm test` clean.
2. `cd tui && npm run typecheck` clean.
3. `make check` clean (no Python regression; the manifest didn't change so no codegen drift).
4. Branch self-review (per express phase 3d).

## Risks / open notes

- The `extractText` helper in `dispatcher.ts` reads from a flexible shape (`raw as Record<string, unknown>`). My `conv_history` test must use a shape that `extractText` can handle — probably `{role, text}` based on the user_message test. Verify against `extractText` source before writing the fixture.
- Help message text: I'll keep it identical to today's wording (`"Usage: decafclaw-tui [--token <t>] [--host <url>] [--conv <id>]\nEnv: DECAFCLAW_TOKEN, DECAFCLAW_HOST"`) so the change is purely about routing (stdout vs stderr) and exit code, not content.
- No new dependencies. Stays consistent with the spike's "dependency-light" intent.

## Plan self-review

- **Placeholders?** None.
- **Internal consistency?** Steps 1–3 match spec acceptance criteria; nothing dangling.
- **Scope?** XS — three files modified, one new test file, one new source file. Three independent commits possible.
- **Ambiguity?** `extractText` shape is the only point that needs verification at execute time. Noted.
