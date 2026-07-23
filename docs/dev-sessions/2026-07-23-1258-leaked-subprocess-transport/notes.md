# Notes — #605 leaked subprocess transport

## Root cause (systematic-debugging, evidence-based)

**Symptom:** `make test` intermittently ends with `1 warning` —
`PytestUnraisableExceptionWarning: Exception ignored in: <function BaseSubprocessTransport.__del__>`.

**Localization (deterministic reproduction found):**
`uv run pytest tests/<file>.py -n0 -W error::pytest.PytestUnraisableExceptionWarning`, 5 runs each:

| file | warned |
|------|--------|
| `test_background_tools.py` | **5/5** |
| test_background_wake_integration / test_mcp / test_claude_code_output / test_claude_code_sessions / test_process_tool_media | 0/5 |

So the leak is in `test_background_tools.py` — the only tests that spawn a **real** asyncio subprocess via `BackgroundJobManager` (`asyncio.create_subprocess_shell`). No single test warns alone (0/3 each); the file warns as a whole → classic "leaks in test X, GC'd during a later test / session teardown."

**Full failing traceback** pinned the mechanism:
- `24 passed`, warning raised at `_pytest/unraisableexception.py:95 cleanup` — i.e. **session teardown**, not any test.
- Inner: `asyncio/base_subprocess.py:130 in __del__ → RuntimeError('Event loop is closed')`. (The issue observed `'PosixPath' object has no attribute '_parts_normcase_cached'` — same class of CPython-3.13 teardown-after-loop-closed noise.)

**Mechanism:** `Process.wait()` reaps the child but never closes the subprocess *transport*. asyncio *does* schedule a `transport.close()` itself (so `is_closing()` reads True mid-test — a red herring), but that scheduled close races the event loop's teardown. Under pytest-asyncio's short-lived per-test loops, the transport isn't fully closed within its loop's lifetime; it survives, and its `__del__` runs at interpreter shutdown after the loop is gone → unraisable → pytest warning, misattributed to whatever was running.

**Causality proven by A/B** (full file, 20 runs each):
- Fix disabled → **19/20** warned.
- Fix enabled → **0/20**.

## Fix

`src/decafclaw/skills/background/tools.py` — new `_close_transport(job)` helper called in `_run_reader`'s `finally` (runs inside the live loop for both natural completion and cancellation). It calls `job.process._transport.close()` (asyncio-internal; no public accessor on `Process`; `close()` is idempotent), setting `_closed` so `__del__` is a no-op. Fail-open (`log.debug` on error).

## Regression protection

`pyproject.toml [tool.pytest.ini_options]`:
```toml
filterwarnings = ["error::pytest.PytestUnraisableExceptionWarning"]
```
Any future leaked resource now fails the suite deterministically instead of flakily dirtying it. Verified the **full suite is clean under the gate**: 5/5 `make test` runs `3051 passed`, zero warnings.

## Tests

`tests/test_background_tools.py` — 3 deterministic unit tests of `_close_transport` (closes an open transport; fail-open on missing `_transport`; fail-open when `close()` raises). The end-to-end regression is caught by the warning-as-error gate (a GC-timing assertion would be flaky; `is_closing()` doesn't distinguish fixed vs unfixed since asyncio calls close itself).

## Resolution of spec open questions

- *Which test leaks?* → systemic to `test_background_tools.py`'s real subprocess spawns; not one test — fix is in production code, covers all.
- *Point fix vs systemic guard?* → point fix at the source (`_run_reader` finally) **plus** the suite-wide warning gate. Both.
- *Add the `filterwarnings=error` gate?* → yes; verified suite clean under it, reliable under `-n auto`.

## Status
Root cause fixed, regression gate added, `make check` clean, `make test` 5/5 clean. Ready for PR.
