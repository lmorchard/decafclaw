# Notes: Generic Tool Execution Timeout

## Outcome

Shipped a wall-clock per-call timeout wrapper around non-MCP tools in `execute_tool`, with per-tool overrides declared inline in `TOOL_DEFINITIONS`. Four commits, 1582 tests passing (10 new).

- `feat(tools): add agent.tool_timeout_sec config field (#7)` — scaffolding.
- `feat(tools): opt long-running tools out of generic timeout (#7)` — pre-landed overrides.
- `feat(tools): enforce per-call timeout in execute_tool (#7)` — wrapper + resolver + wiring.
- `test(tools): coverage for execute_tool timeout (#7)` — 10 tests.
- Docs update to `CLAUDE.md` Conventions → Tools section.

## Audit summary (captured during planning)

Opted out (`timeout: None`) because internal bound exceeds 180s default or is unbounded:

- `delegate_task` — owns `wait_for(child_timeout_sec=300)`.
- `conversation_compact` — LLM summarization bounded by model.timeout (300s default).
- `claude_code_send` — streams a Claude Code subprocess session; multi-minute runs are normal.

Considered and left at default (180s):

- `shell` — subprocess.run already bounded to 30s inside `_execute_command`; 180s default is comfortably above.
- `web_fetch`, `http_request` — own HTTP-level timeouts well below 180s.
- `background` skill tools — start a subprocess and return fast; job itself runs async.
- Other `claude_code_*` tools (exec, push_file, pull_file, stop, sessions, start) — internal `wait_for` bounds or fast ops.

## Plan tweaks vs. original spec

- **Env var name**: corrected to `TOOL_TIMEOUT_SEC` (not `AGENT_TOOL_TIMEOUT_SEC`) to follow the nearby `CHILD_TIMEOUT_SEC` precedent in `config.py`.
- **Phase order**: swapped so per-tool `timeout: None` overrides land before the wrapper enforcement commit. Avoids a regression window where the three long-runners would transiently be cut off at 180s.
- **Resolver sentinel**: used `_MISSING = object()` to distinguish "tool found, no `timeout` key" (use default) from "tool found, `timeout: None`" (opt-out). Important edge.
- **Shell 3600s override removed**: the original spec proposed `timeout: 3600` on shell based on a mistaken reading that shell takes a user-supplied timeout. Shell has a hardcoded 30s inner cap; a 180s wrapper is fine.

## Surprises during execution

- **Sync-tool test teardown cost.** `test_sync_tool_timeout` originally used `time.sleep(10)` in a sync tool; the thread kept running past the test, and pytest waited ~9s at teardown for the worker to free. Shortened to `time.sleep(2)`, teardown down to ~1s. Documented the caveat in the test comment.
- **Resolver loop semantics.** First draft had `continue` that silently fell through to the next source when a tool was found without a `timeout` key. Rewrote with a nested inner helper + explicit first-match-wins loop to keep the semantic unambiguous: finding the tool anywhere means "use default", not "keep searching".

## Follow-ups

- Items 2–4 from issue #7 remain open: #324 (cancel-scope isolation), #325 (per-tool circuit breaker), #326 (graceful degradation). Not in scope here.
- If the 180s default ever bites a legitimate non-audited tool, the fix is a one-line `timeout: None` or larger override on its def — no wrapper changes required.
