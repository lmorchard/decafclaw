# Redundant `conversations_dir.exists()` guard in `startup_scan` — Implementation Plan

**Goal:** Delete the redundant `conversations_dir` local and its `exists()` early-return from
`ConversationManager.startup_scan`, whose work is already done by `iter_conversation_archives`.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/586 — **Tier:** `auto-ok`
(both criteria reduce to greps that fail today; internal cleanup, no risk-gated path touched,
existing tests pin the observable behaviour)

**Approach:** Delete the two lines outright rather than rewriting them as
`conversations_root(config)`. The issue offers both and declares the choice style-not-scope; the
local has exactly one consumer (its own `exists()` check), so once the guard goes there is nothing
left for a `conversations_root(config)` call to serve. Deleting is the smaller change and both
criteria are satisfied either way.

**Criteria:** C1 the guard block is gone · C2 the now-unused local is gone with it
(Full text + checks live in `checks.md`.)

---

## Phase 0: Freeze the acceptance checks

Write `checks.md` per `references/frozen-checks.md`. No implementation in this phase.

**Files:**
- Create: `docs/dev-sessions/2026-07-24-1619-586-redundant-guard/checks.md` — criteria + checks
  copied verbatim from the issue, ids assigned
- Create: *(no acceptance-test file)* — both checks are `grep` commands over the implementation
  file. Nothing to author; `Check files` is empty. Recorded in `checks.md`, with the consequence
  (the tamper diff is vacuous for this run) stated there rather than left implicit.

**Verification — automated:**
- [x] Every criterion's check runs and **fails for the expected reason** — C1 → `1` at
      `conversation_manager.py:1809`; C2 → `1` at `:1808`. Both located by `grep -n`, so the
      failure is the guard's presence, not a bad path.
- [x] Every guard runs and **passes** — G1 `12 passed in 1.60s` (12 collected, `-k` matches);
      G2 `make test` → `3338 passed, 2 skipped in 36.58s`.
- [x] Freeze commit made; sha recorded in `checks.md` in a follow-up commit.

---

## Phase 1: Delete the redundant local and guard

Remove the two-line dead guard from `startup_scan`. Single vertical slice: the whole issue is
two lines in one function, and there is no layer below or above it to stage.

**Advances:** C1, C2 — completely; nothing remains for a later phase.

**Files:**
- Modify: `src/decafclaw/conversation_manager.py` — delete lines 1808–1810 (the local, the
  `exists()` guard, and its `return 0`) from `startup_scan`.
- Test: *(none)* — pure deletion of dead code with no behaviour change. TDD opt-out per
  `plan.md` step 8: there is no new behaviour to drive out with a failing test, and the
  behaviour that must not change is already covered by G1's 12 tests (which include the
  missing/empty-conversations-dir case that the deleted guard used to short-circuit).

**Key changes:**

Before (`src/decafclaw/conversation_manager.py:1806-1812`):

```python
        from datetime import datetime, timedelta

        conversations_dir = self.config.workspace_path / "conversations"
        if not conversations_dir.exists():
            return 0

        # Staleness threshold: ignore confirmations older than 24 hours
```

After:

```python
        from datetime import datetime, timedelta

        # Staleness threshold: ignore confirmations older than 24 hours
```

Why this is behaviour-preserving: `iter_conversation_archives`
(`src/decafclaw/conversation_paths.py:59-63`) resolves the same path via
`conversations_root(config)` and early-returns when it does not exist, so the loop yields
nothing and `startup_scan` falls through to `return recovered` with `recovered == 0` — the same
value the deleted guard returned.

**Verification — automated:**
- [x] C1's check passes: `grep -c 'if not conversations_dir.exists():' src/decafclaw/conversation_manager.py` returns `0` — observed `0`
- [x] C2's check passes: `grep -c 'conversations_dir = self.config.workspace_path' src/decafclaw/conversation_manager.py` returns `0` — observed `0`
- [x] G1 still passes: `uv run pytest tests/test_conversation_manager.py -k startup_scan -q` — observed `12 passed in 2.37s` (same 12 collected as at freeze)
- [x] G2 still passes: `make test` — observed `3338 passed, 2 skipped in 21.55s`, identical pass/skip counts to the freeze baseline
- [x] `make lint` passes — observed `All checks passed!`; `make typecheck` also clean (`0 errors`)

**Verification — manual:**
- [ ] None. No human-judgment criterion in this spec; the tier is `auto-ok` precisely because
      every criterion resolved to a command.
