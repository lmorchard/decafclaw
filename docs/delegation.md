# Sub-Agent Delegation

The `delegate` tool lets the agent fork child agents to handle focused subtasks. Multiple tasks run concurrently.

## Usage

The agent calls the `delegate` tool with a list of tasks:

```json
{
  "tasks": [
    {"task": "Look up the weather in Portland", "tools": ["tabstack_research"]},
    {"task": "Search my memories for cocktail recipes", "tools": ["memory_search"]}
  ]
}
```

Each task spawns an independent child agent that:
- Gets a fresh, empty conversation history
- Only has access to the specified tools
- Uses a focused system prompt ("Complete the following task. Be concise and focused. Return your result directly.")
- Shares the parent's cancel event (so user cancellation stops children too)

## Task fields

| Field | Required | Description |
|-------|----------|-------------|
| `task` | Yes | Task description — becomes the child agent's user message |
| `tools` | Yes | List of tool names the child can use |
| `system_prompt` | No | Override the default child system prompt |

## Results

- **Single task**: returns the child's text response directly
- **Multiple tasks**: returns labeled results (`Task 1: ...\nTask 2: ...`)
- **Failures**: returned as error text per task — one child failing doesn't cancel siblings

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `CHILD_MAX_TOOL_ITERATIONS` | 10 | Max tool call rounds per child agent |
| `CHILD_TIMEOUT_SEC` | 300 | Timeout in seconds per child agent |

## Limitations (v1)

- No nested delegation — children cannot call `delegate`
- Children use the same LLM model as the parent
- No streaming of child progress to the UI
- No persistent child conversations — results are ephemeral
