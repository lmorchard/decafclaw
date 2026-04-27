# Sub-Agent Delegation

The `delegate_task` tool lets the agent fork a child agent to handle a focused subtask. For batches of similar subtasks, the `delegate_tasks` (plural) tool dispatches them in parallel under one tool call — see [Parallel dispatch](#parallel-dispatch-with-delegate_tasks).

## Usage

The agent calls `delegate_task` with a task description:

```json
{"task": "Look up the weather in Portland"}
```

For a batch of related subtasks, prefer `delegate_tasks` (plural) — see [Parallel dispatch](#parallel-dispatch-with-delegate_tasks). The agent can also emit multiple singular `delegate_task` calls in one response and they'll execute concurrently via the agent loop's tool semaphore, but the plural tool gives a single aggregated result and a fixed concurrency cap.

Each call spawns an independent child agent that:
- Gets a fresh, empty conversation history
- Inherits the parent's tools and activated skills (minus `delegate_task` to prevent recursion)
- Uses a focused system prompt ("Complete the following task. Be concise and focused. Return your result directly.")
- Shares the parent's cancel event (so user cancellation stops children too)
- Inherits skill_data (e.g. vault base path)

## Parameters

| Field | Required | Description |
|-------|----------|-------------|
| `task` | Yes | Task description — becomes the child agent's user message |
| `model` | No | Named model config for the subtask. Omit to inherit parent's model. See [Model Selection](model-selection.md). |
| `allow_vault_retrieval` | No | Opt the child INTO proactive memory retrieval at turn start. Default `false`. See [Vault access](#vault-access). |
| `allow_vault_read` | No | Opt the child INTO read-side vault tools (`vault_read`, `vault_search`, etc.). Default `false`. See [Vault access](#vault-access). |
| `return_schema` | No | JSON-schema-shaped object describing the structured return shape. When supplied, the child is instructed to emit prose followed by a fenced JSON block; the parsed object lands on `ToolResult.data`. See [Structured returns](#structured-returns). |

## Vault access

By default, child agents have **no vault access at all** — no proactive retrieval, no read tools, no write tools. The parent opts the child in via flags on `delegate_task`:

| Flag | Effect |
|---|---|
| `allow_vault_retrieval=True` | Child runs the proactive memory retrieval at turn start (`skip_vault_retrieval=False`). |
| `allow_vault_read=True` | Child can call `vault_read`, `vault_search`, `vault_list`, `vault_backlinks`, `vault_show_sections`. |

**Vault writes are categorically blocked** for children regardless of flags. The set is hardcoded in `_VAULT_WRITE_TOOLS` and includes `vault_write`, `vault_journal_append`, `vault_delete`, `vault_rename`, `vault_move_lines`, and `vault_section`. If the child's work should land in the vault, the parent does the write itself after the child returns — keeps the audit trail in the parent's conversation.

The default-deny posture means the child gets isolated context: no auto-injected memory, no vault tools. Use the flags when the subtask genuinely needs them (e.g. "research X across the vault" → `allow_vault_read=True`; "summarize my last conversation about Y given my preferences" → both flags).

This is a behavior tightening from earlier versions where children inherited every parent vault tool by default; see #396.

## Results

- Returns the child's text response wrapped in a `ToolResult`.
- When `return_schema` is supplied and the child emits valid JSON, the parsed object also lands on `ToolResult.data` — auto-rendered as a fenced JSON block in the tool result content for the parent agent.
- Failures returned as error text — one child failing doesn't affect siblings.
- Timeouts return `[subtask timed out after Ns]`.

## Structured returns

For subtasks where the parent needs specific fields (counts, lists, scores) rather than just a prose summary, supply a `return_schema` hint. The child's system prompt gets an addendum instructing it to emit prose first, then a fenced ```json block matching the shape. After the child completes, `delegate_task` parses the JSON out of the response and populates `ToolResult.data`.

```python
# Example call (LLM-emitted tool call):
delegate_task(
    task="Audit the auth module for security issues. List each issue with severity.",
    return_schema={
        "issues": [
            {"file": "string", "line": "int", "severity": "low|medium|high", "summary": "string"}
        ],
        "overall_severity": "low|medium|high"
    }
)
```

The parent receives both halves:

- `ToolResult.text` — the child's prose explanation, with the fenced JSON block stripped so it doesn't duplicate the auto-rendered structured block.
- `ToolResult.data` — the parsed object, ready for the parent to operate on directly without re-parsing.

**The schema is a hint, not enforced.** No JSON-schema library is involved; the child's emitted JSON is parsed verbatim. If you need strict schema validation, do it on `ToolResult.data` from the parent side.

**Parse failures fall through silently.** If the child forgets the JSON block, emits malformed JSON, or just ignores the instruction, the parent receives `ToolResult(text=raw_response)` with no `data` field — a debug log records the fallback. No retry is attempted (the child has already done the work; the prose half is usually still useful).

See #395 for the design rationale.

## Parallel dispatch with `delegate_tasks`

`delegate_tasks` (plural) takes a list of task descriptions and runs them as concurrent child agents under a single tool call. Use it when you have a known list of similar investigations — per page, per file, per topic — that don't need to talk to each other.

```python
# Example call (LLM-emitted tool call):
delegate_tasks(
    tasks=[
        "Summarize the README in repo A",
        "Summarize the README in repo B",
        "Summarize the README in repo C",
    ],
    return_schema={"summary": "string", "main_topic": "string"},
)
```

**Result shape:**

```json
{
  "summary": {"total": 3, "ok": 2, "failed": 1},
  "results": [
    {"index": 0, "ok": true, "text": "...", "data": {"summary": "...", "main_topic": "..."}},
    {"index": 1, "ok": true, "text": "...", "data": {"summary": "...", "main_topic": "..."}},
    {"index": 2, "ok": false, "error": "[error: subtask timed out after 300s]"}
  ]
}
```

- `ToolResult.text` — one-line summary (e.g. `"3 subtasks: 2 succeeded, 1 failed"`).
- `ToolResult.data.results` — per-task entries in input order, each with `index`, `ok`, and either `text`/`data` (success) or `error` (failure).
- `ToolResult.data.summary` — total/ok/failed counts.

**Shared params, not per-task.** `model`, `allow_vault_retrieval`, `allow_vault_read`, and `return_schema` all apply to every task in the batch. If you genuinely need per-task overrides, fall back to multiple singular `delegate_task` calls.

**Concurrency cap.** At most `agent.max_parallel_delegates` children run at once (default 3). The remaining queue waits inside the gather. The total batch size is also capped at `agent.max_tasks_per_delegate_call` (default 10) to prevent fan-out blowup — over-cap requests fail fast with a clear error.

**Failure isolation.** One child raising or timing out does not abort siblings. Each per-task entry carries its own `ok` flag and (on failure) an `error` string lifted from the child's `ToolResult.text`.

**Per-child events are suppressed from the parent UI.** Each child publishes its tool-status events to a unique override id rather than the parent's subscriber, so you don't get N concurrent tool streams flooding the parent conversation. The parent emits one aggregate `tool_status` event per child completion (`"2/3 subtasks complete"`).

**Cancellation.** The parent's cancel event flows through the gather — cancelling the parent turn cancels in-flight children too.

See #397 for the design rationale.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `CHILD_MAX_TOOL_ITERATIONS` | 10 | Max tool call rounds per child agent |
| `CHILD_TIMEOUT_SEC` | 300 | Timeout in seconds per child agent |
| `MAX_PARALLEL_DELEGATES` | 3 | Max children running simultaneously inside one `delegate_tasks` call |
| `MAX_TASKS_PER_DELEGATE_CALL` | 10 | Max batch size accepted by `delegate_tasks` per call |
| `MAX_CONCURRENT_TOOLS` | 5 | Max parallel tool calls at the agent loop's outer semaphore (applies to all tools) |

## Limitations

- No nested delegation — children cannot call `delegate_task` or `delegate_tasks`
- No streaming of child LLM text to the UI (tool progress and confirmations are visible for singular `delegate_task`; suppressed for `delegate_tasks` per the parallel-dispatch event policy)
- No persistent child conversations — results are ephemeral
- `delegate_tasks` shares one set of params across the batch; per-task overrides require multiple singular `delegate_task` calls
