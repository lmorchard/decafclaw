# Issue #164 — Tighten exception handling (agent.py + web/websocket.py)

## Resume here

- **Goal**: implement issue #164's settled scope — narrow/split the named broad `except Exception` / `contextlib.suppress(CancelledError, Exception)` blocks in `src/decafclaw/agent.py` and `src/decafclaw/web/websocket.py`, add the named unit tests, pass `make lint` + `make typecheck` (zero errors) and the full gates.
- **Worktree**: `/Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-164-exception-handling`, branch `issue-164-exception-handling`, base `origin/main` @ `9173771da6e410635f37d7b5b6426f78ef4792ef`.
- **Status**: implementation complete (all 7 sites + 7 new tests). All gates green (see checks below). Next: commit checkpoint, publish draft PR.
- **Next action**: commit (2 code commits + notes), publish draft PR via submit-pr, report to coordinator.

## Issue site mapping (issue line → current line @ 9173771d)

Issue line refs were recorded against an earlier revision (triage at `a86729ad`); all sites verified present and materially unchanged. Function names in the issue body differ slightly from current names; the code sites are unambiguous (confirmed structurally identical in `a86729ad` via `git show`):

| Issue site | Current location @ 9173771d | Change |
|---|---|---|
| `_call_llm_with_events` ll 440 | `agent.py:496-499` (`except (asyncio.CancelledError, Exception): pass` around `await llm_task`) | suppress only `asyncio.CancelledError`; log unexpected `Exception` with `exc_info=True` |
| `_handle_widget_interaction` ll 342 | `agent.py:374-375` (`contextlib.suppress(asyncio.CancelledError, Exception)` around `await confirm_task` inside `_handle_widget_input_pause`, def at ll 308) | `contextlib.suppress(asyncio.CancelledError)` |
| `_archive` ll 110 | `agent.py:134-135` (`except Exception as e:` + `log.error` no exc_info, around `append_message`) | narrow to `(OSError, TypeError, ValueError)` + `exc_info=True` |
| `_is_context_length_exceeded` ll 85 | `agent.py:87` (`except Exception:` around `exc.response.text.lower()`) | narrow to `(AttributeError, RuntimeError, UnicodeDecodeError)` |
| `_maybe_compact` ll 167 | `agent.py:200-201` (`except Exception as e: log.error(f"Compaction failed: {e}")`) | add `exc_info=True`. (The clear-tier block at ll 182-186 already uses `log.exception` — no change.) |
| `ws_send` ll 990-995 | `websocket.py:1310-1314` (closure in `websocket_chat`) | split: `(WebSocketDisconnect, RuntimeError)` → DEBUG; `(TypeError, ValueError)` → ERROR + `exc_info=True` |
| `_handle_send_terminal` ll 517 | `websocket.py:649-655` (`_handle_terminal_command`, def at ll 593; `except Exception as exc` around `registry.spawn`) | narrow to `(OSError, RuntimeError, ValueError)` |

Notes on naming mismatches (not material, recorded for the review):
- Issue calls the widget fn `_handle_widget_interaction`; it was already named `_handle_widget_input_pause` at triage revision `a86729ad` (def ll 275 there). Issue ll 342 @ a86729ad is exactly the confirm_task suppress.
- Issue calls the terminal handler `_handle_send_terminal`; it was already named `_handle_terminal_command` at `a86729ad` (def ll 466 there).
- There is a *second* `contextlib.suppress(asyncio.CancelledError, Exception)` in `_handle_widget_input_pause` at ll 358 (around `await cancel_task`, the `asyncio.Event.wait()` task). The issue names only the confirm_task site, so ll 358 stays as-is (out of the settled scope). Recorded as an observation for review.

## Decisions

- **Confirmed by coordinator (handoff)**: scope fully settled (triage:ready) — the per-file change list, exclusions (mattermost.py, mattermost_display.py, tools/skill_tools.py), and success conditions are authoritative.
- **Test file choice**: issue allows `tests/test_ws_message_type_handlers.py` OR `tests/test_websocket.py` (does not exist). `tests/test_ws_message_type_handlers.py` exists → use it. `tests/test_agent_turn.py` exists → use it.
- **ws_send testability**: `ws_send` is a closure inside `websocket_chat`, not unit-testable directly. Plan: extract a module-level `_make_ws_send(websocket)` factory (mirrors the existing `_make_*_forwarder` pattern in the same file), `websocket_chat` uses it. Behavior-identical, minimal, enables the named unit tests.
- **llm_task unexpected-exception handling**: log ERROR + exc_info and continue with the cancelled response (do not re-raise). Rationale: the issue says "log any unexpected Exception raised by llm_task with exc_info=True" and the test criteria assert only the log, not a turn failure; re-raising would change user-visible cancel behavior.
- **ws_send other exceptions**: propagate (outer `websocket_chat` loop already logs `except Exception` with exc_info=True and ends the connection). Spec names exactly two catch groups; the outer handler covers the "unexpected failures logged with full traceback" intent.

## Environment

- `uv sync` done in worktree; `.env` copied from main clone + `HTTP_PORT=18892`.
- Baseline `make test` (worktree @ 9173771d, before any change): **4566 passed, 2 skipped in 52.44s** — clean baseline.
- Board: #164 Ready → In progress (set 2026-10-15, readback confirmed).

## Attempts and checks

All checks run in the worktree, after `make fmt`, on the final working tree (pre-commit):

- `make test` (baseline, before any change): 4566 passed, 2 skipped in 52.44s.
- New tests, targeted (`uv run pytest tests/test_agent_turn.py tests/test_ws_message_type_handlers.py -n 0 -k "call_llm_with_events or widget_input_pause or ws_send"`): 7 passed.
- **Discrimination check** (stashed `src/` changes, re-ran new tests against pre-change code):
  - `test_call_llm_with_events_unexpected_llm_task_exception_logs_error` FAILED (old code swallows — no ERROR log) ✓
  - `test_handle_widget_input_pause_unexpected_confirmation_failure_propagates` FAILED ("DID NOT RAISE" — old code swallows) ✓
  - `tests/test_ws_message_type_handlers.py` collection FAILED (`ImportError: _make_ws_send` — factory doesn't exist pre-change) ✓
  - The two normal-path tests (cancel suppression) pass on both old and new code, as intended (they guard preserved expected behavior).
- `make fmt`: 1 file reformatted (tests), then clean.
- `make lint`: "All checks passed!" (zero errors) ✓ — issue success condition 1a.
- `make typecheck`: "0 errors, 0 warnings, 0 informations" (pyright) ✓ — issue success condition 1b. (The "new pyright version available" line is a tool-version notice, present on baseline main too.)
- `make check` (full gate: install-js, check-npm-policy, check-message-types, fmt-check, lint, typecheck, check-js, check-browser-assets): passed, exit 0.
- `make test` (full suite, after changes): **4573 passed, 2 skipped in 19.76s** — baseline +7, exactly the new tests; no regressions.
- `pytest --durations=10` on the two changed test files: no new test in the top-10 slowest (fastest new tests ~0.05s; no fixed sleeps — blocking coroutines resolve via immediate cancellation).
- Docs check: no `docs/` page describes the old ws_send log wording or the changed blocks (grep for "client likely disconnected" / "ws send failed" in docs/ → no live-doc hits), so no doc update in this PR.
