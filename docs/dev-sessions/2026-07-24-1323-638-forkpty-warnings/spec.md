
## Observation
Since the web-terminal PTY work landed (#627 / #635), `make test` ends with `2 warnings`:

```
.../python3.13/pty.py:95: DeprecationWarning: This process (pid=…) is multi-threaded,
use of forkpty() may lead to deadlocks in the child.
```

Source: `src/decafclaw/terminals.py:77` calls `pty.fork()` to spawn the shell in its own session (needed for `login_tty` / job control / `killpg` teardown). Python 3.13 warns when forking a multi-threaded interpreter. `tests/test_terminals.py` exercises the real spawn twice → 2 warnings.

## This is a deliberate decision, not a bug
The spawn code documents the choice (`terminals.py:71-73`):

> *"Forking a multi-threaded interpreter emits a DeprecationWarning; per project decision we do NOT suppress it (it is ignored by Python's default warning filter in normal runs)."*

The fork itself is written safely — the child only `chdir` + `execvpe` between fork and exec (no async-signal-unsafe work), which is the correct mitigation for the deadlock the warning describes. `posix_spawn(setsid=True)` isn't portable (NotImplementedError on Linux/CI), so `pty.fork()` stands.

**Visibility is test-only.** In a normal server run Python's default filter ignores `DeprecationWarning`, so production terminal spawns don't surface it. Only pytest (which surfaces warnings) shows it.

## Why file it anyway
It's a standing tension with the project's **zero-tolerance-for-warnings** policy — `make test` is now non-clean (`2 warnings`), which erodes the "clean suite" signal. And it's a latent trap for the `PytestUnraisableExceptionWarning`-as-error gate added in #605/#631: if a future gate promotes `DeprecationWarning` (or all warnings) to errors suite-wide, these 2 would fail CI.

## Options (don't touch the deliberate spawn decision)
- **Test-scoped `filterwarnings` ignore** — a targeted `ignore::DeprecationWarning` matched to `pty` / this message in `pyproject.toml` (or a `@pytest.mark.filterwarnings` on the spawn tests), so the accepted warning doesn't dirty the suite. Cheapest; keeps the spawn decision intact.
- **Localized `warnings.catch_warnings()`** around the `pty.fork()` call site in `terminals.py`, suppressing just this message — but that reverses the "we do NOT suppress it" decision, so only if that decision is being revisited.

Leaning test-scoped ignore.

## Related
- Terminal work: #627 (#442), #635 (#626)
- Zero-tolerance-for-warnings gate precedent: #605 / #631
- Surfaced during #604 (PR #636), where a rebase onto the terminal work made the 2 warnings visible.

---

## Acceptance criteria (agent-session intake)

**AC1 — the sanctioned forkpty warning no longer dirties the suite (the only discriminating criterion).**
WHEN the real-spawn tests run, the suite SHALL report no warnings.
- CHECK: `uv run pytest tests/test_terminals.py tests/web/test_terminal_ws.py -q` emits no `warnings summary` section.
- Verified discriminating at intake: today this prints 2 `forkpty` DeprecationWarnings (`13 passed, 2 warnings in 4.61s`).

### Regression guards (pass today; must keep passing — deliberately not criteria)

- **G1:** `make test` → `3234 passed, 2 skipped` with no `warnings summary`.
- **G2:** both exempted tests still **run and pass** — not skipped, not deleted. Guards against the degenerate "fix" of removing real-spawn coverage.
- **G3:** `make check` (ruff + pyright + tsc) green.

These pass before any change, so they cannot distinguish done from untouched — they are guards, not acceptance criteria.

## Tier: `auto-ok`

AC1 reduces to a concrete command; touched paths are test files only (no auth/secrets/data/deploy/CI config/dependency changes). Note the interaction: the **per-test-mark mechanism is what keeps this `auto-ok`** — a `pyproject.toml` edit would touch build/CI config and pull the tier toward `needs-review`.

## Design decisions

- **Per-test `@pytest.mark.filterwarnings` on the two audited spawn tests.** The warning is acceptable *because that fork is written safely* (`chdir` + `execvpe` only between fork and exec) — a per-site property, not a global one. A blanket ignore would also hide a future, less careful spawn site.
  - *Rejected:* a single message-matched ignore in `pyproject.toml` — simpler and one place, but suite-wide, silences unaudited sites, and touches build config.
- **`terminals.py` untouched.** `terminals.py:71-73` records a deliberate decision to leave the warning visible; Python's default filter ignores it in normal runs anyway.
  - *Rejected:* `warnings.catch_warnings()` at the call site — reverses that decision.
- **No warnings-as-errors gate in this issue.** Promoting all warnings to errors is a project-wide posture change with dependency-upgrade blast radius.

Mirror the comment style of the existing `#605` filter (`pyproject.toml:66-69`) on the marks.

## Corrections to the observation above

- The two warnings come from **two different files**, one spawn each — `tests/test_terminals.py::test_real_pty_echo_and_cleanup` and `tests/web/test_terminal_ws.py::test_ws_handler_serves_real_spawned_session` — not from `test_terminals.py` spawning twice.
- **`-W error::DeprecationWarning` does not catch these**: the warning is emitted in the forked child, so the intuitive "promote to error" check passes today and would be a vacuous oracle. The check must assert on the `warnings summary` section instead.

## Scope

- **In:** exempt the two audited spawn tests so the suite is warning-clean.
- **Out (own follow-ups):** suite-wide warnings-as-errors gate; any change to the `terminals.py` do-not-suppress decision; a blanket `DeprecationWarning` ignore.
- **Not a criterion:** "a future unsanctioned forkpty still surfaces." It holds today, so it cannot discriminate; the per-site mechanism is the design-level protection.

---
*Acceptance criteria + tier added via `agent-session intake`. Original issue text preserved verbatim above.*

