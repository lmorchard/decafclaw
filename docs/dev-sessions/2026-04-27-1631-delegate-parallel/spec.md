# Delegate parallel dispatch (`delegate_tasks`) — #397

## Problem

`delegate_task` (singular) runs one child agent at a time. Anthropic's *Effective Context Engineering for AI Agents* names parallel sub-agent fan-out as a primary benefit of the delegation pattern: multiple concurrent investigations whose results aggregate back to the parent without polluting its context. Today the agent has to sequence sibling explorations, paying latency it shouldn't have to pay.

## Goal

Add a sibling tool `delegate_tasks` (plural) that takes a list of subtask descriptions and dispatches them concurrently with a bounded gather. Each child runs in its own forked context; the parent receives an aggregated structured result.

## Non-goals

- Per-task model overrides, per-task vault flags, per-task return schemas. Batches of N similar investigations are the dominant use case; one shared set of params keeps the API tight. Promote to per-task dicts later if a real use case appears.
- Streaming child progress to the parent UI. v1 emits one parent-side `tool_status` event per child completion (3/N done, etc.) — no individual tool/skill events per child.
- A separate "join after N successes" / "abort on first failure" mode. Always wait for all, return per-task status.
- Replacing `delegate_task`. Singular stays — clearer ergonomics for one-off cases and avoids forcing a list literal in the simple case.

## Design

### New tool: `delegate_tasks`

**Signature:**

```python
tool_delegate_tasks(
    ctx,
    tasks: list[str],
    model: str = "",
    allow_vault_retrieval: bool = False,
    allow_vault_read: bool = False,
    return_schema: dict | None = None,
) -> ToolResult
```

All non-`tasks` parameters are shared across the batch — same model, same vault flags, same schema. This matches the most common use case ("investigate these N pages and pull out X for each") without forcing the agent to specify N copies of the same params.

**Validation:**

- `tasks` must be a non-empty list of non-empty strings — empty list / non-string entries / blank entries → error result.
- Length capped at `config.agent.max_tasks_per_delegate_call` (default 10) — over the cap → error result with the cap value in the message.

### Concurrency

- New config: `AgentConfig.max_parallel_delegates` (default 3).
- Internal `asyncio.Semaphore(max_parallel_delegates)` in `tool_delegate_tasks` wraps each child dispatch so we cap simultaneous in-flight children regardless of `tasks` length.
- `asyncio.gather(..., return_exceptions=True)` so one child's failure doesn't kill the rest.
- Cancellation: if the parent's tool-call cancel event fires, the gather is cancelled — `asyncio.gather` propagates `CancelledError` to the in-flight children. Already wired through `parent_ctx.cancelled` on each child.

### Refactor

The existing `_run_child_turn` already encapsulates the per-task primitive. The plural tool calls it N times under the semaphore. No structural refactor of `_run_child_turn` itself.

### Result shape

```json
{
  "summary": {"total": 3, "ok": 2, "failed": 1},
  "results": [
    {"index": 0, "ok": true, "text": "...", "data": {...}},
    {"index": 1, "ok": true, "text": "...", "data": {...}},
    {"index": 2, "ok": false, "error": "subtask timed out after 300s"}
  ]
}
```

- `ToolResult.text` is a one-line human-readable summary (`"3 tasks complete: 2 succeeded, 1 failed"`).
- `ToolResult.data` carries the structured breakdown (rendered as a fenced JSON block in the chat by the existing `ToolResult` rendering path).
- `data` is keyed in task input order — `results[i]` corresponds to `tasks[i]`.
- When `return_schema` is supplied, each successful entry's `data` field is the parsed JSON; `text` is the prose with the JSON block stripped (mirroring the singular tool's split).
- Failed entries omit `text`/`data` and carry an `error` string.

### Event routing

- For parallel children, **don't** route per-child events to the parent UI subscriber — would flood the UI with N concurrent tool-status streams.
- Implementation: in the per-task `setup` callback, set `child_ctx.event_context_id` to a unique per-child id (the child's own `child_conv_id`) instead of the parent's `event_context_id`. Each child still publishes events, but they don't land in the parent's subscriber.
- Parent emits ONE `tool_status` event per child completion: `"delegate_tasks: 2/3 complete"`. This gives the user visible progress without per-child noise. Aggregated by a small counter inside `tool_delegate_tasks` — the gather waits on `asyncio.as_completed` so we publish in the order children finish, not the order they were dispatched.

### Failure isolation

`asyncio.gather(return_exceptions=True)` treats `CancelledError` like any other exception — bad for parent-cancellation propagation. Use `asyncio.gather(*coros, return_exceptions=True)` only for non-cancel exceptions: catch `CancelledError` from the children and re-raise from the parent. In practice: each child task already returns a `ToolResult` on failure (the existing wrapper around `_run_child_turn` catches everything), so the gather mostly just sees clean returns. Use `return_exceptions=True` defensively for any unexpected raise.

### Backwards compat

`delegate_task` (singular) is unchanged. `delegate_tasks` (plural) is purely additive. Both registered, both deferred-discoverable, both `priority: critical`.

## Configuration

```python
class AgentConfig:
    max_parallel_delegates: int = 3
    max_tasks_per_delegate_call: int = 10
```

Standard env aliases: `MAX_PARALLEL_DELEGATES`, `MAX_TASKS_PER_DELEGATE_CALL`.

## Tool description

The agent picks between singular and plural; the descriptions need to disambiguate. Singular description already nudges "for parallel work, call delegate_task multiple times" — change that line to point at `delegate_tasks` for ≥2-task fan-outs.

Plural description leads with "Dispatch a batch of N independent subtasks concurrently. Use when you have a known list of similar investigations (per-page, per-file, per-topic) where the children don't need to talk to each other." Mentions the cap.

## Files touched

- `src/decafclaw/tools/delegate.py` — new `tool_delegate_tasks` + definition; minor description tweak on `delegate_task`.
- `src/decafclaw/config_types.py` — `AgentConfig` adds two fields.
- `tests/test_delegate.py` — new test class for parallel dispatch (success batch, mixed-failure batch, cap enforcement, concurrency cap, structured-return parsing, cancellation propagation).
- `docs/delegation.md` — new section on `delegate_tasks` + cap config.
- `docs/config.md` — add the two fields to `agent` table.
- `CLAUDE.md` — no mention needed beyond what's there (tool already covered).

## Test plan

1. **Happy path:** 3 tasks all succeed; result shows `ok: 2/3` worth of `True` entries, indices 0/1/2 in input order, parent emits 3 progress events.
2. **Mixed failures:** 1 of 3 children raises (mock `_run_child_turn`); failed entry has `error` string, others succeed unchanged.
3. **Empty list:** error result.
4. **Over cap:** N+1 tasks where cap is N → error result mentioning the cap.
5. **Concurrency cap honored:** Patch `_run_child_turn` to register start/end timestamps; with cap 2 and 4 tasks, max simultaneous in-flight is 2.
6. **Structured return parses:** Each child returns prose + JSON block; per-entry `data` has the parsed object, `text` has prose stripped.
7. **Cancellation propagates:** Set the parent cancel event mid-flight; gather raises, in-flight children see the cancel signal.
8. **Singular tool unchanged:** Existing `delegate_task` test class still passes without modification.

## Historical context: Gemini schema compatibility (#71)

A previous attempt at multi-delegation was unwound in [`2026-03-18-1017-concurrent-tools-delegate`](../2026-03-18-1017-concurrent-tools-delegate/spec.md). The original `delegate` tool used an **array-of-objects-with-properties** schema:

```
tasks: array → items: object → { task: string, tools: array, system_prompt: string }
```

This caused Gemini 2.5 Flash to emit `finish_reason: malformed_function_call` (0 completion tokens). The fix was to drop the array entirely, ship a flat singular `delegate_task(task: str)`, and push concurrency to the agent loop's `max_concurrent_tools` semaphore (the model emits N singular tool calls in one response).

This PR re-introduces multi-delegation with a deliberately simpler schema: **array-of-strings only**, no per-item objects. That pattern is already used successfully by `checklist_create.steps`, `vault_write.tags`, `email_send.to`, `vault_show_sections.sections`, and others — all on Gemini in production. The failure mode from #71 is sidestepped by construction.

If `malformed_function_call` reappears in logs after this lands, the fallback is the existing pattern: agents emit N singular `delegate_task` calls in one response. The plural tool's incremental value (structured aggregation, single-tool-call mental model, independent concurrency cap) doesn't justify keeping it on if Gemini chokes.

## Open questions resolved

- **Share or suppress events?** Suppress per-child events from the parent UI; emit aggregate progress from the parent. (Issue's preferred direction.)
- **Failure isolation?** Yes — `return_exceptions=True`, per-task status in result.
- **Circuit-breaker accounting?** One tool call from parent's perspective.
- **Per-task params?** No — shared across the batch in v1. Revisit if a real need surfaces.
- **`max_tokens` per call?** No explicit total cap — relies on per-child `child_max_tool_iterations` and the `max_tasks_per_delegate_call` length cap. Add later if real use surfaces blowup.
