# Remove production-orphaned `delete_conversation_uploads` — Implementation Plan

**Goal:** Delete the dead `delete_conversation_uploads` helper and the two unit tests that were
its only remaining callers.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/585 — **Tier:** `auto-ok` (both
criteria reduce to greps that fail today; no auth, secrets, migration, deploy/CI, or dependency
surface — the blast radius is one dead function and its tests)

**Approach:** Triage resolved the issue's binary decision to *remove*. Production deletion goes
through `delete_conversation_files` (`src/decafclaw/conversation_paths.py`), called from
`src/decafclaw/http_server.py:696,713`, which `rmtree`s the whole `{conv_id}/` directory —
uploads included. The helper has no other caller, so removing it changes no behaviour. Its two
tests go with it because they exercise nothing else; the remaining nine tests in
`tests/test_attachments.py` cover save / read / list / `uploads_dir` sandboxing and are untouched.

**Criteria:** C1 the orphaned function is gone · C2 its dedicated tests are gone with it
(Full text + checks live in `checks.md`. Ids are assigned there and referenced here.)

---

## Phase 0: Freeze the acceptance checks

Write `checks.md` per `references/frozen-checks.md`. No implementation in this phase.

**No acceptance tests are authored.** Both criteria are `grep` commands, so `Check files` is
empty and there is no test file to write or hold read-only. `checks.md` says so explicitly and
names the three substitutes that stand in for the tamper diff at verification time.

**Files:**
- Create: `{session-dir}/checks.md` — criteria + checks copied verbatim from the issue, ids assigned
- Create: `{session-dir}/spec.md`, `{session-dir}/notes.md`

**Verification — automated:**
- [x] Every criterion's check runs and **fails for the expected reason** — C1 returns `1`
      (function defined at `src/decafclaw/attachments.py:96`), C2 returns `2` (both test
      functions defined). Neither is a bad path: both files exist and both greps matched.
- [x] Every guard runs and **passes** — G1 `11 passed in 1.26s`, exit 0 (collected 11, not zero);
      G2 2 hits at `src/decafclaw/http_server.py:696,713`.
- [x] Freeze commit made (`c91189f`); sha recorded in `checks.md`.

---

## Phase 1: Remove the helper and its tests

Delete `delete_conversation_uploads` from `attachments.py`, and remove its import and its two
tests from `tests/test_attachments.py`. This is the whole change — a single vertical slice,
because the function has exactly one layer.

**Advances:** C1, C2 — nothing remains for a later phase.

**Files:**
- Modify: `src/decafclaw/attachments.py` — delete the `delete_conversation_uploads` function
  (lines 96–101), including its function-level `import shutil`, which exists only for this
  function and has no other user in the module.
- Modify: `tests/test_attachments.py` — drop `delete_conversation_uploads` from the
  `from decafclaw.attachments import (...)` list (line 6) and delete both
  `test_delete_conversation_uploads` (lines 79–84) and
  `test_delete_conversation_uploads_noop_if_missing` (lines 87–89).

**No new unit tests.** This slice removes behaviour rather than adding it; TDD's failing-test-first
step has nothing to assert. The frozen criteria are the coverage, and G1 is what proves the
remaining suite still collects and passes.

**Key changes:**
- `delete_conversation_uploads(config, conv_id: str) -> None` — removed
- No signature in the module changes; `uploads_dir`, `save_attachment`,
  `read_attachment_base64`, `list_conversation_attachments`, and `resolve_attachments` are
  untouched.

**Not in scope:** the sibling follow-ups #586 (redundant `startup_scan` guard) and #587
(`uploads_dir` sanitization — already landed and guarded by
`test_uploads_dir_traversal_stays_under_root`). Historical dev-session prose that mentions
`delete_conversation_uploads` is a record of what was true then and is left alone.

**Verification — automated:**
- [x] C1's check passes: `grep -c "def delete_conversation_uploads" src/decafclaw/attachments.py`
      returns `0` — observed `0`
- [x] C2's check passes: `grep -c "def test_delete_conversation_uploads" tests/test_attachments.py`
      returns `0` — observed `0`
- [x] G1 still passes: `uv run pytest tests/test_attachments.py -q` — must collect **9** tests
      (11 minus the two deleted) and pass; exit 0, not exit 5 — observed `9 passed in 1.34s`,
      exit 0
- [x] G2 still passes: `grep -n "delete_conversation_files" src/decafclaw/http_server.py` — 2 hits
      — observed lines 696, 713
- [x] `make lint` passes — `All checks passed!`
- [x] `make check` passes (lint + typecheck, Python + JS) — exit 0; pyright `0 errors, 0 warnings`
- [x] `make test` passes — observed `3467 passed, 2 skipped in 12.82s` (baseline 3469 minus the
      two deleted), exactly as predicted

**Verification — manual:**
- None. No human-judgment criterion in `checks.md`, so there is no evidence to present at the
  gate on that account.
