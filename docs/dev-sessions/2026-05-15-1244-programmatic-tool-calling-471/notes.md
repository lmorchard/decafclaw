# Session notes

## Status: parked, not shipped

PR [#518](https://github.com/lmorchard/decafclaw/pull/518) was **closed without merge** on 2026-05-17. The second Copilot review surfaced a framing-vs-reality gap: the "sandbox" was a subprocess runner with stdlib unrestricted, so a script could `import subprocess` and bypass the shell-confirmation gate, `import urllib.request` and bypass the `http_request` gate, or `import os` and bypass workspace-write guards. Issue [#471](https://github.com/lmorchard/decafclaw/issues/471) remains **open**.

Read the plan below as a record of an attempt, not a description of shipped behavior — no `code_execution` skill exists on `main`. The technical findings further down (macOS `RLIMIT_AS`, the `delegate.py` wire-format bug, the `copy.copy` fork race) are independent of the parked design and still hold.

**These docs are also incomplete.** They were last written 2026-05-15 14:50, and the branch continued for seven commits after that (through 2026-05-16 12:44) — allowlisting `workspace_glob`/`workspace_search`, auto-injecting the dc-proxy import, and several `ToolResult.data` corrections. None of that is reflected here, and no retro was written before the PR was parked. For the final state of the attempt, read the branch itself: `origin/programmatic-tool-calling-471` at `00c51ae`, kept alive on the remote for exactly that reason.

These four files were only ever untracked in a local worktree; they are committed here so they survive that worktree's cleanup.

## Pre-existing issues surfaced during review (out of scope — file as follow-ups)

- **`src/decafclaw/tools/delegate.py:394`** — calls `parent_ctx.publish("tool_status", {"tool": ..., "message": ...})` passing a positional dict where the rest of the codebase uses `**kwargs`. The dict is silently dropped by consumers (`web/websocket.py:555-562` reads `event.get("tool")` from the top-level event). Phase 4's `_sandbox.py:147` uses the correct `**kwargs` form; spotted by the reviewer while verifying wire-format compatibility. Worth a one-line fix in a separate PR.

## Phase 4 review notes

- Test `test_progress_events_published` could be tightened by mixing in a limit-overrun (e.g. `max_tool_calls=2`, loop 3 times, expect 2 events) — that would catch a regression where publish accidentally moves above the limit check. Not gating Phase 4, but worth doing during Phase 5 polish if time permits.

## Phase 3 design note

- `copy.copy(parent_ctx)` shares mutable subobjects (`tools.current_call_id`, `tools.extra`, `event_bus`, `composer`) with the parent. If two `code_execution` calls run concurrently in one parent turn (possible — `max_concurrent_tools=5` default), they would race on `ctx.tools` mutations. Phase 3's `_make_tool_handler` explicitly does NOT mutate `sandbox_ctx.tools.allowed` to avoid one slice of this; the rest is a deeper architectural concern for whoever next touches `Context.fork_for_tool_call`. Forward-looking only.

## RLIMIT_AS reality (vs. plan's assumption)

- The plan assumed `setrlimit(RLIMIT_AS, ...)` on macOS would silently be best-effort. In practice it raises `ValueError: current limit exceeds maximum limit` because RLIM_INFINITY is the hard cap and the kernel refuses to lower it (would propagate as `subprocess.SubprocessError` post-fork). Phase 2 gated the call behind `_RLIMIT_AS_OK = sys.platform.startswith("linux")` at module load. Documented in `_sandbox.py:147-155`. Phase 4's memory test correctly uses `pytest.mark.skipif(sys.platform == "darwin", ...)`.