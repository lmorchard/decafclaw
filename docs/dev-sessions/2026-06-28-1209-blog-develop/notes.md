# Dev Session Notes: blog-develop skill

## Task 4: eval downgraded to manual smoke

### Why the harness cannot guard this

`/blog-develop` is a `user-invocable: true`, `context: fork` command.

In the eval runner (`src/decafclaw/eval/runner.py`, lines 479–508), when
`dispatch_command` returns `cmd.mode == "fork"`, the runner:

1. Captures `cmd.text` (the child agent's final response) as the turn response.
2. Skips `run_agent_turn` entirely — no agent loop runs in the outer context.
3. Records **zero tool calls** in the outer history, because all tool calls
   happen inside `run_child_turn`'s isolated child context and are never
   appended to the main `history` list the runner inspects.
4. Fires assertions against `tool_names=[]`.

This means `expect_tool: delegate_task` would unconditionally FAIL: the
`delegate_task` call happens inside the forked child (which IS the
blog-develop orchestrator), invisible to the outer harness's
`_collect_tool_names`. The fork isolation that makes the skill reliable in
production is exactly what makes it untestable via the current harness's
structural assertions.

An eval case added under these conditions would be permanently broken — not
a false alarm, but structurally unable to pass. Per project convention
(CLAUDE.md: "Do NOT ship a broken/un-runnable eval"), this case is
downgraded.

### What guards the scout-via-delegate contract instead

The scout-first / delegate-not-narrate contract is instead verified by the
**Task 5 live smoke test** against a running DecafClaw instance. That smoke
test drives `/blog-develop <idea>` through the real UI or CLI, observes the
first tool call the orchestrator makes, and confirms it is `delegate_task`
(scout), not any inline web-research tool.

### Future path to an automated guard

If the harness is extended to propagate child-agent tool calls back into the
outer result bundle (e.g. a `child_tool_calls` list on `CommandResult`),
a structural eval assertion becomes possible. Until then, the smoke test is
the right boundary.
