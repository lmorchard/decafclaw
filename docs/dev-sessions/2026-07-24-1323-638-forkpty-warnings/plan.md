# forkpty test warnings — Implementation Plan

**Goal:** exempt the two audited real-spawn tests from the accepted `forkpty` DeprecationWarning
so the suite is warning-clean, without reversing the deliberate do-not-suppress decision in
production code.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/638 — **Tier:** `auto-ok`
(both criteria reduce to concrete commands; touched paths are test files only — no auth,
secrets, data, deploy/CI config, or dependency changes).

**Approach:** per-test `@pytest.mark.filterwarnings` on the two spawn tests. The warning is
acceptable *because that fork is written safely* (`chdir` + `execvpe` only between fork and
exec) — a per-site property, so the exemption is per-site. `terminals.py` is untouched.

**Criteria:** C1 the two spawn test files emit no warnings · C2 `make test` emits no warnings.
Full text, guards, and the scoped tamper rule live in `checks.md`.

---

## Phase 0: Freeze the acceptance checks — DONE (`c4028de`)

`checks.md` written; C1/C2 observed failing for the right reason; G1/G2/G3 observed passing.

**Verification — automated:**
- [x] C1 fails at freeze: `13 passed, 2 warnings`
- [x] C2 fails at freeze: `3234 passed, 2 skipped, 2 warnings in 59.56s`
- [x] G1 passes: `make test` exit 0, counts as stated
- [x] G2 passes: both exempted tests run and pass (`2 passed`)
- [x] G3 passes: `make check` exit 0
- [x] Freeze commit made; sha recorded (follow-up commit `c1c157b`)

---

## Phase 1: Exempt the two audited spawn tests

Add a message-matched `filterwarnings` mark to each real-spawn test so the accepted warning
stops dirtying the suite, while any *other* forkpty warning still surfaces.

**Advances:** C1, C2 (both fully — there are only two warning sources).

**Files:**
- Modify: `tests/test_terminals.py` — mark on `test_real_pty_echo_and_cleanup` (line ~62,
  already stacked under `@pytest.mark.asyncio`); `import pytest` present at line 4.
- Modify: `tests/web/test_terminal_ws.py` — mark on
  `test_ws_handler_serves_real_spawned_session` (line ~84, same stacking); `import pytest`
  present at line 7.

**Key changes:** on each of the two tests, above the existing `@pytest.mark.asyncio`:

```python
# The spawn path deliberately keeps Python 3.13's forkpty DeprecationWarning
# visible in production (terminals.py:71-73); this fork is safe (chdir+execvpe
# only between fork and exec). Exempt per-site so an unaudited forkpty elsewhere
# still dirties the suite. #638
@pytest.mark.filterwarnings(
    "ignore:.*use of forkpty.*:DeprecationWarning"
)
```

Mirror the comment style of the existing `#605` filter (`pyproject.toml:66-69`): say *why* the
warning is accepted, not just that it is.

**Verification — automated:**
- [ ] C1's check passes: `uv run pytest tests/test_terminals.py tests/web/test_terminal_ws.py -q`
      emits no `warnings summary`
- [ ] C2's check passes: `make test` emits no `warnings summary`
- [ ] G1 still passes: `make test` → `3234 passed, 2 skipped`, exit 0
- [ ] G2 still passes: both exempted tests run and pass, zero skipped
- [ ] G3 still passes: `make check` green
- [ ] Scoped tamper check: `git diff c4028de -- tests/test_terminals.py
      tests/web/test_terminal_ws.py` contains only added `@pytest.mark.filterwarnings`
      decorators and their comments — no test body, assertion, signature, or skip/xfail changes

**Verification — manual:**
- [ ] The mark's message pattern matches only the forkpty warning, not DeprecationWarning
      broadly — a reviewer should be able to see the exemption is narrow.

---

## Scope discipline

Not in this plan: any `pyproject.toml` change, any `terminals.py` change, a suite-wide
warnings-as-errors gate, a blanket `DeprecationWarning` ignore. Each is recorded as out of scope
in the issue.
