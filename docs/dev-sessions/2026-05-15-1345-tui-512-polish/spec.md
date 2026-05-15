# TUI #512 — entry-point polish + missing dispatcher test

**Issue:** [#512](https://github.com/lmorchard/decafclaw/issues/512)
**Branch:** `tui-512-polish` (worktree at `.claude/worktrees/tui-512-polish/`)
**Type:** Bug-fix bundle. XS.

## Context

Three small items flagged during the TUI spike retro (`docs/dev-sessions/2026-05-13-1039-tui-spike/notes.md`, "Retro candidates"). All in the TUI client surface, none affecting the bot. PR #493's manifest typed codegen already addressed the wire-shape concerns in the original issue; the remaining three items are pure client-side polish.

## Goals

Three independent fixes:

1. **TTY check should let `--help` through.** Today, `decafclaw-tui --help` on a non-TTY (piped stdin) exits with "requires a TTY stdin" before usage prints. Cause: in `tui/src/entry.tsx`, the TTY check at `main()` line 44 runs before `parseArgs()` at line 49, where `--help` is recognized. Fix: parse argv (or at least scan for `--help`/`-h`) first, exit-with-usage if present, then TTY-gate.

2. **argv parsing: detect missing values.** `--token` (or `--host`/`--conv`) at end of argv silently assigns `undefined` to the flag's value, because `argv[++i]` blindly increments past the end of the array. Result: `decafclaw-tui --token` falls through to the `DECAFCLAW_TOKEN` env-var lookup, masking the user's intent. Fix: when `++i >= argv.length`, exit with a clear "missing value for --X" error.

3. **Add `conv_history` dispatcher test.** The dispatcher's `conv_history` case (`tui/src/dispatcher.ts:133`) has no unit test — explicitly deferred during the spike. Add a fixture with two messages → assert transcript populates with both, in order.

## Non-goals

- Refactoring `parseArgs` into a third-party argv library (e.g. `arg`, `commander`). Hand-rolled detection is sufficient and keeps the TUI dependency-light. (Originally tabled during the spike for the same reason.)
- Restructuring the TUI surface area or wire types — those are separate issues.
- Tightening `CliConfirmResponse.decision` to a literal union — obsolete; the field no longer exists after #493.
- Help-text overhaul. Just ensure existing usage prints in the right place; don't expand it.

## Acceptance criteria

- [ ] `decafclaw-tui --help | cat` shows usage and exits 0.
- [ ] `decafclaw-tui --help > /dev/null` shows usage and exits 0.
- [ ] `decafclaw-tui --token` (no value) exits non-zero with a clear error mentioning `--token`.
- [ ] `decafclaw-tui --host` (no value) — same.
- [ ] `decafclaw-tui --conv` (no value) — same.
- [ ] `tui/src/dispatcher.test.ts` has at least one test for `conv_history`.
- [ ] `cd tui && npm test` clean (13+ tests, was 12).
- [ ] `cd tui && npm run typecheck` clean.
- [ ] `make check` clean (no Python or JS regression).

## Files affected

- `tui/src/entry.tsx` — fix #1 and #2.
- `tui/src/dispatcher.test.ts` — fix #3.
- No production-code change in `dispatcher.ts`; the existing `conv_history` case already works (live-smoked during the spike).
