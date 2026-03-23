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
| `effort` | No | Effort level (`fast`/`default`/`strong`). Omit to inherit parent's level. See [Effort Levels](effort-levels.md). |

## Results

- Returns the child's text response directly
- Failures returned as error text — one child failing doesn't affect siblings
- Timeouts return `[subtask timed out after Ns]`

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
