# Phase 6b — Live smoke transcript

Date: 2026-06-24
Worktree: `.claude/worktrees/feat-580-workflow-skill-activation` on `feat/580-workflow-skill-activation`
Model: `vertex-gemini-flash`
Conversation: `web-lmorchard-60f2f267`

## Setup

- Worktree `.env` had `MATTERMOST_ENABLED=false` and `HTTP_PORT=18893` from earlier setup.
- **One environment fix needed**: `TABSTACK_API_KEY` was commented out in the worktree's `.env`. The tabstack SKILL.md declares `requires.env: TABSTACK_API_KEY`, which gates discovery on the actual environment variable (not the resolved value in `data/decafclaw/config.json`). Uncommented the env line before the smoke. Captured this as a learning — see "Setup gotcha" below.
- Server: `uv run decafclaw` web-only on `0.0.0.0:18893` from the worktree.
- Client: `decafclaw-client send/respond` against `http://localhost:18893`.

## Run 1 — fail-loud activation when skill is missing

Before uncommenting `TABSTACK_API_KEY`, the smoke captured the activation's fail-loud
behavior directly. With `tabstack` not in `ctx.config.discovered_skills`,
`run_workflow_turn` returned (text trimmed):

```
[error: skill activation failed: requires_skills entry 'tabstack' is not a discovered skill]
```

Journal status was set to `"error"` and `run_workflow` was never called. Exactly the
contract Phases 2 + 4 promised.

## Run 2 — happy path with tabstack discoverable

After enabling `TABSTACK_API_KEY` and restarting the server:

1. `/research kelp forest restoration` — activation succeeded silently, workflow
   suspended on `user_input` "What topic should I research?" at seq `(0,)`.
2. Responded with topic — workflow suspended on `user_input` "Any specific angle…" at
   seq `(1,)`.
3. Responded with scope — workflow ran through:
   - `wf.llm_call` (plan stage): 4 search queries generated; journal entry at seq `(2,)`.
   - `wf.parallel` with 4 thunks → 4 `tabstack_research` calls completed in parallel.
     Each child entry at seq `(3, i, 0)` carries real markdown output (4-6KB each):

   ```json
   {
     "seq": "3.0.0",
     "kind": "tool_call",
     "result": {
       "text": "Kelp forests are incredibly productive underwater ecosystems, often described as the \"rainforests of the sea,\" that are currently experiencing widespread degradation globally [1][2]. Kelp forest restoration efforts ...",
       "data": null
     }
   }
   ```

   (Three more similar entries at `(3.1.0)`, `(3.2.0)`, `(3.3.0)`.)

**This is the load-bearing proof.** `wf.tool_call("tabstack_research", query=q)` now
reaches a real tabstack invocation that returns substantive content. Compare with #574
smoke Finding 1 (PR #579), where the same call returned
`"[error: unknown tool 'tabstack_research']"`. #580's activation block fixed it.

## Hang at the same place as #574 smoke Finding 3

After the 4 child `tool_call` entries landed at `16:15:43`, the workflow stopped making
progress. Final state polled 6+ minutes later:

```json
{
  "status": "running",
  "seqs": ["0", "1", "2", "3.0.0", "3.1.0", "3.2.0", "3.3.0"],
  "kinds": ["user_input", "user_input", "llm_call",
            "tool_call", "tool_call", "tool_call", "tool_call"]
}
```

- Outer `wf.parallel` entry at seq `(3,)` is **missing**.
- Pipeline summarize entries at seq `(4, i, 0)` are **missing**.
- Server process at 0% CPU for 6+ minutes; no log activity past the last
  `Research complete`.

**This is the same hang as #574 smoke Finding 3** (`docs/dev-sessions/2026-06-10-1732-574-workflow-batch-primitives/smoke.md`).

**New evidence that rules out the #582 hypothesis.** #574/#582 framed it as possibly
"Vertex throttling on degenerate input" — the prior smoke fed `[error: …]` strings into
the summarize stage. This smoke fed 4-6KB of legitimate markdown per query. The hang
happened anyway. So the failure mode is in `wf.parallel`/`wf.pipeline` itself, not in
how Vertex handles degenerate prompts. **Update issue #582** with this evidence — the
investigation should focus on the primitive boundary between parallel completion and
pipeline startup (or parallel's outer-entry write).

## Setup gotcha

Worth a memory note for future smokes: the tabstack skill (and likely others with
`requires.env: …` declarations in SKILL.md) gates discovery on the actual environment
variable. The worktree shares `data/decafclaw/config.json` via `DATA_HOME`, so the
resolved tabstack API key is available to the tool at runtime — but `discover_skills`
runs at startup before any tool execution and checks `os.environ`. With the env var
commented out, the skill is silently excluded from `config.discovered_skills`, and
`requires_skills=("tabstack",)` fails activation.

This is precisely the fail-loud behavior I wanted for #580 — the workflow can't run
without its declared skill, and the message names the missing skill. But the setup
gotcha is that running the smoke in a fresh worktree requires enabling any env var that
gates a declared skill's discovery, even when the resolved config has the value.

## Acceptance vs scope

**#580 acceptance criteria met:**
- ✅ Workflow turns auto-activate the always-loaded set.
- ✅ `@workflow(..., requires_skills=(…))` declaration carried through to activation.
- ✅ Activation reuses the existing `activate_skill_internal` path.
- ✅ Fail-loud on declared-skill activation failure (Run 1 proves it).
- ✅ `/research` declares `requires_skills=("tabstack",)` and reaches a real
  `tabstack_research` invocation (Run 2 proves it).
- ✅ Live walk on `vertex-gemini-flash`. (The full hero workflow does NOT complete
  end-to-end because of the #582 primitive bug, but that's out of scope for #580.)

**Out of scope (filed elsewhere):**
- The pre-existing `wf.parallel`/`wf.pipeline` hang past parallel completion (#582).
  Evidence captured here updates that issue's repro from "degenerate input" to "real
  input also hangs."

## Artifacts

- Pre-restart journal snapshot: `smoke-journal-snapshot.json` (sibling file in this
  session dir).
- Server log: `/tmp/decafclaw-580-smoke.log` (last activity at `16:15:43`).
- Client log: `/tmp/decafclaw-580-final.log` (client disconnected at `16:17:42` on
  timeout).
