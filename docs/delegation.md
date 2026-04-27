# Sub-Agent Delegation

The `delegate_task` tool lets the agent fork a child agent to handle a focused subtask. For parallel work, the agent calls `delegate_task` multiple times in the same response — the agent loop runs them concurrently.

## Usage

The agent calls `delegate_task` with a task description:

```json
{"task": "Look up the weather in Portland"}
```

For parallel subtasks, the model emits multiple `delegate_task` calls in one response:

```json
// tool_call 1
{"task": "Look up the weather in Portland"}
// tool_call 2
{"task": "Search my memories for cocktail recipes"}
```

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

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `CHILD_MAX_TOOL_ITERATIONS` | 10 | Max tool call rounds per child agent |
| `CHILD_TIMEOUT_SEC` | 300 | Timeout in seconds per child agent |
| `MAX_CONCURRENT_TOOLS` | 5 | Max parallel tool calls (applies to all tools, including concurrent delegate_task calls) |

## Limitations

- No nested delegation — children cannot call `delegate_task`
- No streaming of child LLM text to the UI (tool progress and confirmations are visible)
- No persistent child conversations — results are ephemeral
