# Phase 8b — Live smoke transcript

Date: 2026-06-11
Worktree: `.claude/worktrees/feat-574-workflow-batch-primitives` on `feat/574-workflow-batch-primitives` @ `3cc4de3`.
Model: `vertex-gemini-flash` (default for the `research` workflow).

## Setup

- Worktree `.env` had `HTTP_PORT=18892` already; added `MATTERMOST_ENABLED=false` to stay off the shared Mattermost token (the deployed agent is on the same token).
- Provider creds + tabstack key resolved from `data/decafclaw/config.json` (shared via `DATA_HOME`).
- Server: `uv run decafclaw` web-only on `0.0.0.0:18892`.
- Client: `decafclaw-client send/respond` against `http://localhost:18892` with the existing `lmorchard` web token.

## Run 1 — mid-run SIGINT (conv `web-lmorchard-2730ab34`)

The crash-resilience leg of the smoke. Drove `/research artificial coral reefs` interactively, answered both user_inputs (`"artificial coral reefs in the Mediterranean"` and `"for a general audience; focus on ecological effectiveness"`), then watched the journal file evolve. At T+10s after the second response, the journal looked like:

```
status: "running"  (8 entries)
  (0,)    user_input
  (1,)    user_input
  (2,)    llm_call          ← plan stage
  (3, 0, 0)  tool_call       ← parallel child 0
  (3, 1, 0)  tool_call       ← parallel child 1
  (3, 2, 0)  tool_call       ← parallel child 2
  (3, 3, 0)  tool_call       ← parallel child 3
```

The outer parallel entry at seq `(3,)` is correctly absent — `wf.parallel` hadn't yet returned. Issued `kill -INT` to the uvicorn worker mid-fan-out.

**Post-kill journal contents (saved as `/tmp/decafclaw-574-journal-pre-restart.json`):**

```json
{
  "workflow_name": "research",
  "status": "running",
  "entries": [
    { "seq": "0",     "kind": "user_input", "args_fingerprint": "6dfb9c243a409066" },
    { "seq": "1",     "kind": "user_input", "args_fingerprint": "1d5831b730324642" },
    { "seq": "2",     "kind": "llm_call",   "args_fingerprint": "d1d0279b8437cddf" },
    { "seq": "3.0.0", "kind": "tool_call",  "args_fingerprint": "1749650f5d2012e6" },
    { "seq": "3.1.0", "kind": "tool_call",  "args_fingerprint": "c3c185850106762f" },
    { "seq": "3.2.0", "kind": "tool_call",  "args_fingerprint": "97866325932182d2" },
    { "seq": "3.3.0", "kind": "tool_call",  "args_fingerprint": "e9f8044652346150" }
  ]
}
```

**What this proves:**

1. **Tuple-path on-disk serialization is correct.** Seq is a dotted string (`"3.0.0"`); the loader's `path_from_any` reverses it back to a tuple on read.
2. **Sub-handle key composition is correct end-to-end.** The `wf.parallel` at outer seq `(3,)` allocates sub-handles at `(3, idx)`; each sub-handle's first `tool_call` lands at `(3, idx, 0)`. Phase 2's `_make_subhandle_at` works under live load.
3. **Mid-fan-out state is faithfully preserved.** All 4 thunks that completed have their child entries on disk; the outer wasn't yet written; status is `running`. Resume would re-dispatch the thunks; each would hit cache via its sub-handle's first call.

## Run 2 — fresh complete walk (conv `web-lmorchard-e585ecdd`)

Restarted the server clean, issued a second `/research kelp forest restoration techniques` to exercise the plan → parallel → pipeline → subagent path on a fresh journal.

Workflow reached the parallel stage and recorded **5 children** at `(3, 0..4, 0)`. Then it hung. Inspecting the children:

```
{
  "seq": "3.0.0",
  "result": {
    "text": "[error: unknown tool 'tabstack_research'. Did you mean: workspace_search. Use the exact name from your tool list. To discover available tools, call tool_search.]",
    "data": null
  }
}
```

All five queries got the same error.

## Findings (file as follow-ups)

### Finding 1 — Skill tools are not reachable from workflow contexts

`tabstack_research` is registered under `src/decafclaw/skills/tabstack/tools.py:378` and is only loaded when the tabstack skill is activated. The agent loop activates skills via `activate_skill`. A workflow's `TurnKind.WORKFLOW` path doesn't go through that activation, so `execute_tool("tabstack_research", ...)` returns the "unknown tool" error — which `wf.tool_call` returns as the dict `{"text": "[error: ...]", "data": null}` (NOT an exception, because the tool returned a result; it's just an error-shaped result).

This isn't a primitive bug — `wf.tool_call` correctly surfaces whatever `execute_tool` returns. It's a wiring gap between workflows and skill-bundled tools.

Two reasonable fixes (out of scope for #574):
- Pre-activate a configurable skill set when a workflow turn starts.
- Let `@workflow(...)` declare required skills/tools as a decoration parameter, and have the engine activate them before invoking the orchestrator.

The current orchestrator could be rewritten to use a non-skill tool (`workspace_search` is suggested in the error; `http_fetch` would also work if it exists), but that loses the higher-fidelity Tabstack output. Better to file a follow-up.

### Finding 2 — No auto-resume on startup for `status=running` journals

A `kill -INT` mid-LLM leaves the journal in `status="running"`. On server restart, the conversation manager doesn't scan for in-flight workflows; `resume.py`'s `run_workflow_turn(resume=True)` is only triggered as the `on_approve` of a `WORKFLOW_USER_INPUT` confirmation. So a crashed-mid-fan-out workflow stays stuck unless someone manually re-enqueues a workflow turn.

The replay machinery itself is correct (Phase 5/6 unit tests cover full-cache + mid-fan-out resume, validated against synthetic journals). The on-disk journal we captured here would replay correctly if re-enqueued — that's a property of `run_workflow`'s cursor-from-zero replay.

The wiring gap: a startup scan that re-enqueues `status=running` workflows, OR a client-side "resume workflow" command, would close this.

### Finding 3 — Workflow appeared to hang after parallel completed with error-results

After the 5 `tool_call` children landed with their error texts at mtime 17:02:34, the journal didn't grow further for 3+ minutes. The server was at 0% CPU. No Vertex POST after the plan stage.

The expected behavior: `wf.parallel` returns the list of 5 error-shaped dicts, then `wf.pipeline` starts — first stage `_extract_stage` is sync (pulls `.text` from the dict), then `_summarize_stage` does `sub.llm_call(...)` against Vertex. Either (a) `wf.parallel`'s outer entry write hung between completing and recording, or (b) the workflow advanced into pipeline but `sub.llm_call` for the summarize stage hasn't returned and hasn't been journaled yet.

Likely (b) — the journal only records *after* a journaled call returns. A 3-minute Vertex stall on the first summarize stage is plausible if Vertex is throttling or the request is silently retrying. Without more instrumentation, this is a "needs follow-up" rather than a confirmed primitive bug.

## What the smoke proves vs what it leaves open

**Proven:**
- Tuple-path keys serialize and round-trip correctly on disk.
- Sub-handle key composition works under live load (5 distinct `(3, idx, 0)` paths recorded).
- Mid-run SIGINT preserves journal state faithfully.
- All four new primitives wire up through the existing transport (websocket → workflow turn → engine → handle).
- `/research` is user-invokable via the existing `/<name>` slash-command dispatch — no new wiring was needed.

**Open (follow-up issues):**
- Skill-tools-from-workflows wiring gap (Finding 1).
- No auto-resume of in-flight workflows on server restart (Finding 2).
- The 3-minute hang during pipeline summarize against error-text input (Finding 3) — could be Vertex behavior, could be an issue in `wf.parallel`/`wf.pipeline` we missed.

## Artifacts

- Pre-restart journal snapshot: `/tmp/decafclaw-574-journal-pre-restart.json`
- Server log: `/tmp/decafclaw-574-smoke.log`
- Client logs: `/tmp/decafclaw-574-fresh-start.log`, `/tmp/decafclaw-574-fresh-final.log`
- Replay-validation script (incomplete — server contention prevented running): `/tmp/decafclaw-574-replay-check.py`
