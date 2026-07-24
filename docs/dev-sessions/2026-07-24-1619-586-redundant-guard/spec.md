# Redundant conversations_dir.exists() guard in startup_scan

**Source:** https://github.com/lmorchard/decafclaw/issues/586

Captured from the issue body (marker line and trailing attribution footer stripped;
otherwise verbatim).

---

Follow-up to #576 / #578.

`ConversationManager.startup_scan` (`src/decafclaw/conversation_manager.py`) keeps a `conversations_dir = workspace_path / "conversations"` + `if not conversations_dir.exists(): return 0` guard, but the actual iteration now uses `iter_conversation_archives`, which already no-ops (and fails open) when the root is missing. The local + guard are redundant.

Minor cleanup: drop the local/guard (or switch to `conversations_root(config)` for consistency with the other discovery sites). Cosmetic — no behavior change.

Context: `docs/dev-sessions/2026-06-10-1728-conversation-sidecar-dirs/notes.md`.

---

## Acceptance criteria (agent-session triage)

**C1 — the redundant guard block is gone.**
- CHECK: `grep -c 'if not conversations_dir.exists():' src/decafclaw/conversation_manager.py` returns `0`.
- Verified discriminating at triage: returns `1` today (`conversation_manager.py:1809`).

**C2 — the now-unused local is gone with it.**
- CHECK: `grep -c 'conversations_dir = self.config.workspace_path' src/decafclaw/conversation_manager.py` returns `0`.
- Verified discriminating at triage: returns `1` today (line 1808).

Either resolution satisfies both criteria — deleting the guard outright, or replacing it with a `conversations_root(config)` call for consistency with `iter_conversation_archives` / `conversation_dir` / `sidecar_path`. That choice is style, not scope: it does not change which criteria apply, so it does not affect the tier.

### Regression guards (pass today; must keep passing — not criteria)

- **G1:** `uv run pytest tests/test_conversation_manager.py -k startup_scan -q` — `startup_scan` behaviour unchanged: still recovers pending confirmations, still returns 0 on an empty/missing conversations dir, still respects the 24h staleness cutoff, still skips resolved confirmations. Observed at triage: `12 passed in 3.35s`.

## Tier: `auto-ok`

Both criteria reduce to greps that fail today; internal cleanup of redundant guard logic with no risk-gated path touched, and the existing tests pin the externally observable behaviour across the empty-dir, resolved, stale, and pending-confirmation cases.

---

## Run notes (added at session setup, not part of the issue)

**Readiness checklist** (`references/spec-template.md`): items 1–5 and 7 pass. Item 6
("`What we're NOT doing` present and concrete") has no corresponding section — the issue was
produced by `triage`'s augment path, which emits marker + criteria + guards + tier only.
Proceeding under the reading that scope is bounded in substance ("Cosmetic — no behavior
change"; the resolution choice explicitly declared style-not-scope) even though the named
section is absent. Flagged as a skill finding.

**Re-confirmed at setup** (plan step 4, against `2649bfe`):
- C1 grep → `1` (line 1809). Still discriminates.
- C2 grep → `1` (line 1808). Still discriminates.
- G1 → `12 passed in 1.60s`. Passes.
- Baseline `make test` → `3338 passed, 2 skipped in 36.58s`. Green.
- The issue's central factual claim holds: `iter_conversation_archives`
  (`src/decafclaw/conversation_paths.py:61-62`) early-returns when `conversations_root(config)`
  does not exist, so the guard is genuinely redundant and `startup_scan`'s `return 0` path is
  reachable identically via the loop yielding nothing.
