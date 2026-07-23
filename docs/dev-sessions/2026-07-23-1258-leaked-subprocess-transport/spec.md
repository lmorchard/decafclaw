# Spec sketch — Fix flaky PytestUnraisableExceptionWarning (leaked subprocess transport)

**Issue:** #605 · **Branch:** `605-leaked-subprocess-transport` · **Date:** 2026-07-23

> This is the issue captured verbatim as a starting sketch. Refine into a real
> spec during brainstorm / systematic-debugging (reproduce → isolate → fix).

## Symptom

`make test` intermittently ends with `1 warning`:

```
PytestUnraisableExceptionWarning: Exception ignored in: <function BaseSubprocessTransport.__del__>
```

(The inner traceback is CPython 3.13 GC-teardown noise: `'PosixPath' object has no attribute '_parts_normcase_cached'`.)

pytest attributes it to whatever test happens to be running on the xdist worker when the orphaned transport is garbage-collected, so the *attributed* test name varies and is misleading.

## Pre-existing and flaky (evidence)

- Full suite with an unrelated new test file: 1 warning, 3/3 runs (misattributed to that test).
- Full suite excluding that file (≈ clean main): 1 warning 3/3 runs; a separate earlier run showed 0. So the warning exists independent of any one test and surfaces non-deterministically based on GC timing.
- The real source is a test that creates an asyncio subprocess and relies on GC for cleanup — likely among the `claude_code` / `background` / `mcp_client` tests (the three modules that call `asyncio.create_subprocess_*` / spawn stdio servers).

**Session observation (2026-07-23):** the warning appeared in ~every `make test` run during the instrumentation session (`3048 passed, 1 warning`), but the fresh baseline on this branch showed `3048 passed` with **0** warnings — confirming GC-timing nondeterminism.

## Why it matters

Project policy is zero-tolerance for warnings; a flaky warning erodes that signal and makes `make test` non-deterministically "dirty."

## Suggested fix (from the issue)

Find the test leaving the subprocess transport unclosed and close it deterministically (await `proc.wait()` / `transport.close()` in a finally or fixture teardown), or add a fixture that drains/closes the event loop's subprocess transports at teardown. A `filterwarnings = error::pytest.PytestUnraisableExceptionWarning` gate (once clean) would prevent regressions.

**Size:** S–M. **Priority:** P2.

## Open questions for brainstorm / debugging

- Which specific test(s) leak the transport? (Reproduction is the hard part — GC-timing flaky. Need a way to force/deterministically surface it, e.g. run the suspect modules in isolation with `gc.collect()` + warning-as-error.)
- Point fix (close the transport in the offending test/fixture) vs systemic guard (autouse fixture draining subprocess transports at loop teardown) vs both?
- Add the `filterwarnings = error::...` gate only after clean, to prevent regression — worth it, or too brittle under xdist?
