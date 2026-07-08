# Dev Session Notes: blog-develop skill

## Task 4: eval status (UPDATED after the fork→inline switch)

> **Update (commit 63ca046):** Task 4 was originally downgraded because the skill
> was `context: fork`, which the eval harness can't drive (see the superseded
> reasoning below). The skill now ships as **`context: inline`**, which *reverses*
> that conclusion — the eval is viable again. Read this section first; the
> fork-era reasoning below is kept only as a record.

### Inline makes the scout-via-delegate eval viable

In inline mode the orchestrator runs in the main turn via `run_agent_turn`, so its
own `delegate_task` tool call (the scout) lands in the outer history the eval
runner inspects. So `expect_tool: delegate_task` would now pass when the skill
behaves correctly and fail if it narrates-instead-of-delegates — exactly the
#557 guard we want.

Two useful properties:
- **Resilient to missing tabstack creds.** The assertion is on the *parent-level*
  `delegate_task` call. Even if the scout child can't actually web-search (no
  creds in the eval env), `delegate_task` still returns (with an error string)
  and counts as called — so the structural guard holds without a live web
  dependency. Keep `max_tool_errors` lenient enough that a child-side failure
  surfaced as a parent tool result doesn't fail the case spuriously.
- **Cost caveat.** The `delegate_task` call still blocks on a real child turn, so
  the case is not as cheap as a pure `tool_choice` case. Bound it tightly
  (`max_tool_calls: 4`).

Still NOT assertable via evals: anything that happens *inside* the child agents
(e.g. "the deep-research child called tabstack_research") — child-internal tool
calls are not propagated to the outer history. Only the orchestrator's own calls
are visible. That's fine; the scout-via-delegate contract is an orchestrator-level
property.

### Decision

The eval was **not added in this session** — adding it correctly means running it
once against a live model to validate the exact schema and behavior, which pairs
naturally with the Task 5 live-smoke session (running app + model available).
Recommended follow-up: add `evals/blog_develop.yaml` with a
`blog_develop_scouts_via_delegate` case (`input: "/blog-develop the sovereignty
ladder as a mental model"`, `expect_tool: delegate_task`, `max_tool_calls: 4`),
run it once to confirm green, then commit. Until then, the contract is guarded by
the live smoke test (Task 5).

---

## Superseded (fork-era) reasoning — kept for the record

When the skill was `context: fork`: in the eval runner
(`src/decafclaw/eval/runner.py`, lines 479–508), when `dispatch_command` returned
`cmd.mode == "fork"`, the runner captured `cmd.text` as the response, skipped
`run_agent_turn`, recorded zero outer tool calls (all calls happened inside the
isolated child context), and fired assertions against `tool_names=[]`. So
`expect_tool: delegate_task` would have unconditionally failed. That is why Task 4
was downgraded at the time. The switch to `context: inline` removes this blocker.

---

## Task 5: live smoke test (pending — needs Les + running app)

Not yet run. Checklist in `plan.md` Task 5. Verify: scout-via-`delegate_task`
first (not inline research); one-question-at-a-time interview that waits;
deep-research child; draft written to `blog/drafts/<slug>.md` with the frontmatter
contract + `## Research notes`; take-or-leave summary with a link. Capture results
and any prompt-tuning here.
