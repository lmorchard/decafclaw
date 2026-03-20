# Self-Reflection

Self-reflection evaluates the agent's response before delivering it to the user. If the response doesn't adequately address the request, a critique is fed back and the agent retries. This implements the [Reflexion pattern](https://arxiv.org/abs/2303.11366) (Shinn et al., NeurIPS 2023).

## How it works

1. The agent produces a final response (no more tool calls).
2. A separate "judge" LLM call evaluates whether the response addresses the user's request.
3. If the judge says **pass**, the response is delivered normally.
4. If the judge says **fail**, the critique is injected as a user-role message and the agent retries from the LLM call step within the existing iteration loop.
5. The retried response goes through reflection again, up to `max_retries`.

The judge uses a chain-of-thought-before-verdict prompt (G-Eval pattern): it reasons about what the user asked, whether the response addresses it, and notes gaps before outputting a binary `{"pass": true/false, "critique": "..."}` verdict.

### What the judge sees

The judge receives only the current turn's context, not the full conversation history:

- The user's message that started this turn
- A condensed summary of tool calls and results (each result truncated to 500 chars)
- The agent's final response

This keeps the judge context small and focused.

### Retry mechanics

When the judge returns `pass: false`:

1. The failed response stays in history as an assistant message.
2. A user-role critique message is injected (user-role because models weight it more heavily than system messages).
3. The agent loop continues back to the LLM call step — no nested loop.
4. The new response goes through reflection again (up to `max_retries`).

Retries consume iterations from the `max_tool_iterations` budget. If that budget is exhausted during a retry, the turn ends with whatever the agent has produced.

## Quick start

Reflection is enabled by default but **invisible** — the default visibility is `hidden`, so you won't see anything happening. To verify it's working:

```bash
# Show all reflection evaluations (pass and fail)
decafclaw config set reflection.visibility debug

# Or just show failures/retries
decafclaw config set reflection.visibility visible
```

Restart the bot after changing config. In `debug` mode, every response will show the judge's evaluation. In `visible` mode, you'll only see output when the judge triggers a retry.

To use a cheaper model for the judge (recommended):

```bash
decafclaw config set reflection.model gemini-2.5-flash
```

## Configuration

All settings live under the `reflection` group in `config.json`. See [Configuration Reference](config.md#reflection) for the full table.

```json
{
  "reflection": {
    "enabled": true,
    "model": "gemini-2.5-flash",
    "max_retries": 2,
    "visibility": "hidden"
  }
}
```

### Judge model

The judge can use a separate model from the main LLM. Empty `url`/`model`/`api_key` fall back to the `llm` group values via `config.reflection.resolved(config)`. This lets you run an expensive model for the agent and a cheap/fast one as the judge.

## Visibility modes

Configured via `reflection.visibility`:

| Mode | Behavior |
|------|----------|
| `hidden` (default) | User sees only the final response. No reflection UI. |
| `visible` | Only failed reflections (retries) appear in the UI as collapsed entries with critique text. |
| `debug` | All reflections shown (pass and fail), including the full raw judge output in a collapsible detail. |

In all modes, the failed response and critique messages remain in conversation history for the agent's benefit on future turns.

Visibility is implemented via a `reflection_result` event published through `ctx.publish()`. Mattermost and web UI subscribers render based on the configured mode.

## Custom judge prompt

The default judge prompt lives in `src/decafclaw/prompts/REFLECTION.md`. To override it, place a file at `data/{agent_id}/REFLECTION.md`. If present, it replaces the bundled prompt entirely.

The template receives these variables:

| Variable | Content |
|----------|---------|
| `{user_message}` | The user's message that started this turn |
| `{tool_results_summary}` | Condensed tool call/result pairs, or "(no tools used)" |
| `{agent_response}` | The agent's final response text |

**Important:** The template uses Python `str.format()`. Any literal `{` or `}` in your prompt (e.g. JSON examples) must be escaped as `{{` and `}}`, otherwise the prompt will fail to render and reflection will silently pass.

## Skip conditions

Reflection is automatically skipped when:

- **Disabled** — `reflection.enabled = false`
- **Max iterations hit** — the agent already exhausted `max_tool_iterations`
- **Cancelled turn** — user interrupted
- **Child agent turn** — delegation subtasks aren't evaluated; the parent turn is (detected via `ctx.is_child`)
- **Empty response** — nothing to evaluate
- **Max retries exhausted** — deliver the last response as-is

## Error handling

Reflection is fail-open. The agent's response is always delivered even if reflection breaks:

- **Judge call fails** (network error, model error) — deliver as-is, log the error.
- **Unparseable judge output** — treat as pass, log warning.
- **False positives** (judge incorrectly fails a good response) — the retry budget limits damage. After `max_retries`, deliver whatever the agent has.

## Files

- `src/decafclaw/reflection.py` — judge call, prompt assembly, result parsing
- `src/decafclaw/agent.py` — reflection check after final response, retry integration
- `src/decafclaw/config_types.py` — `ReflectionConfig` dataclass
- `data/{agent_id}/REFLECTION.md` — optional custom judge prompt override
