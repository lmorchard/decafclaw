# Self-Reflection / Retry — Spec

## Status: Ready

## Background

The agent sometimes produces responses that don't actually answer the user's question — tool loops that lose the thread, partial answers to multi-part questions, or deflections when the agent has the tools to help. Currently there's no mechanism to catch this before the response reaches the user.

This implements the Reflexion pattern (Shinn et al., NeurIPS 2023): after the agent produces a final response, a separate "judge" LLM call evaluates whether it adequately addresses the user's request. If not, the critique is injected into the conversation and the agent retries.

Closes #16.

## References

- [Reflexion: Language Agents with Verbal Reinforcement Learning (arXiv)](https://arxiv.org/abs/2303.11366) — the core pattern: binary eval + verbal reflection + retry
- [Reflexion GitHub repo](https://github.com/noahshinn/reflexion) — reference implementation
- [Reflexion - Prompt Engineering Guide](https://www.promptingguide.ai/techniques/reflexion) — accessible overview
- [LangGraph Reflection Agents](https://blog.langchain.com/reflection-agents/) — production patterns for critique-then-revise loops
- [G-Eval](https://www.confident-ai.com/blog/g-eval-the-definitive-guide) — chain-of-thought before scoring improves judge accuracy

## Goals

1. Catch inadequate responses before they reach the user
2. Use a cheap/fast model as the judge to keep costs low
3. Retry with specific feedback so the agent can improve
4. Make the reflection process configurable and optionally visible
5. Fail gracefully — never make the user experience worse than no reflection

## Design

### Where it runs

Integrated into the agent loop in `agent.py`. When the agent produces a final response (no more tool calls), the reflection check runs before the response is delivered. This is the interception point at approximately line 456-479 of `agent.py`.

### What the judge sees

The judge receives the turn's message chain — not the full conversation history. Specifically:

1. The user's message that started this turn
2. Tool call/result pairs from this turn (condensed — tool name, arguments, result text)
3. The agent's final response

This keeps the judge context window small and focused. The judge doesn't need prior conversation history — it only evaluates whether this turn's response addresses this turn's request.

### Judge prompt

The judge uses a chain-of-thought-before-verdict prompt (G-Eval pattern):

```
You are evaluating whether an AI assistant's response adequately addresses
the user's request.

Review the interaction below, then:
1. Identify what the user asked for
2. Assess whether the response addresses it
3. Note any specific gaps or problems

Then output your verdict as JSON:
{"pass": true/false, "critique": "specific feedback if failed"}

If the response is adequate, even if imperfect, pass it.
Only fail responses that clearly miss the point, ignore the question,
or contain significant errors relative to the tool results.

---

User: {user_message}

{tool_results_summary}

Assistant response: {agent_response}
```

**Default prompt is hardcoded.** An override file at `data/{agent_id}/REFLECTION.md` replaces it entirely if present. The file receives the same `{user_message}`, `{tool_results_summary}`, and `{agent_response}` template variables.

### Retry mechanics

When the judge returns `pass: false`:

1. The agent's failed response stays in history as an assistant message
2. A `user`-role message is injected with the critique (user-role because most models weight it more heavily than system messages, matching the Reflexion pattern):
   ```
   [reflection] Your previous response may not fully address the user's request.
   Feedback: {critique}
   Please try again, addressing the feedback above.
   ```
3. The agent loop `continue`s back to the LLM call step within the existing iteration loop — no nested loop. The agent sees its failed response + the critique and generates a new response. It can make new tool calls if needed.
4. The new response goes through reflection again (up to `max_retries`).

A `reflection_retries` counter tracks how many times reflection has triggered in this turn. The reflection check is skipped when `reflection_retries >= max_retries`.

**Retry budget:** `max_retries` (default 2) counts reflection failures per turn. This is independent of `max_tool_iterations`, but retries consume iterations from the same `max_tool_iterations` budget. If the iteration budget is exhausted during a retry, the turn ends with whatever the agent has produced — no further reflection.

### Skip conditions

Reflection is automatically skipped when:

- **Reflection is disabled** (`reflection.enabled = false`)
- **Max iterations hit** — the agent already hit `max_tool_iterations`, retrying won't help
- **Cancelled turn** — user interrupted
- **Child agent turn** — delegation subtasks aren't evaluated individually; the parent turn is. Detected via a new `ctx.is_child` flag, set by `delegate.py` when forking child contexts.
- **Empty response** — nothing to evaluate (already handled by the empty retry logic)
- **Max retries exhausted** — deliver the last response as-is

### Visibility modes

Configured via `reflection.visibility`:

- **`hidden`** (default) — the user sees only the final (possibly retried) response. Reflection messages don't appear in the UI. They remain in the internal history for diagnostics.
- **`visible`** — reflection results appear in the UI like collapsed tool calls. Shows the verdict and critique text.
- **`debug`** — full details: the judge's chain-of-thought reasoning, score, retry count, model used, token usage.

In all modes, the failed response + critique messages stay in the conversation history (for both the agent's benefit on future turns and diagnostic purposes).

Visibility is implemented via a `reflection_result` event published through `ctx.publish()`. Mattermost and web UI subscribers render it based on the configured visibility mode — hidden (suppress), visible (collapsed attachment), or debug (full details).

### Judge model

The judge uses a separate model configuration that falls back to the main LLM config, following the same `resolved()` pattern as compaction and embedding:

```python
@dataclass
class ReflectionConfig:
    enabled: bool = True
    url: str = ""       # empty = resolve from llm
    model: str = ""     # empty = resolve from llm
    api_key: str = ""   # empty = resolve from llm
    max_retries: int = 2
    visibility: str = "hidden"  # hidden | visible | debug

    def resolved(self, config) -> "ReflectionConfig":
        ...
```

This lets you run Gemini Pro as the main model and Flash as the judge, for example.

### Config

New `reflection` group in config.json:

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

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `enabled` | bool | `true` | `REFLECTION_ENABLED` | |
| `url` | str | (from llm) | `REFLECTION_URL` | |
| `model` | str | (from llm) | `REFLECTION_MODEL` | |
| `api_key` | str | (from llm) | `REFLECTION_API_KEY` | yes |
| `max_retries` | int | `2` | `REFLECTION_MAX_RETRIES` | |
| `visibility` | str | `hidden` | `REFLECTION_VISIBILITY` | |

### Tool results summary format

The judge needs a condensed view of tool activity. For each tool call in the turn:

```
Tool: {tool_name}({key_args})
Result: {truncated_result}
```

Results are truncated to a reasonable length (e.g. 500 chars) to keep the judge context small. If there are many tool calls, group them:

```
Tools used this turn:
1. memory_search(query="weather API") → Found 2 results: ...
2. workspace_read(path="config.yaml") → (284 chars)
3. shell(command="curl ...") → {"temperature": 72, ...}
```

### Error handling

- **Judge call fails** (network error, model error) — deliver the response as-is, log the error. Never block the user because reflection broke.
- **Judge returns unparseable output** — treat as pass, log warning.
- **Judge false positive** (says fail when response is fine, ~10% rate per research) — the retry budget limits damage. After `max_retries`, deliver whatever the agent has.

### What this does NOT do

- **Post-delivery correction** — no follow-up messages after the response is sent
- **Multi-dimensional scoring** — binary pass/fail only, no rubrics
- **Cross-turn learning** — no persistent reflection memory across conversations
- **Evaluation of tool choice** — the judge evaluates the response, not whether the agent picked the right tools

## Files Changed

- **New**: `src/decafclaw/reflection.py` — judge call, prompt assembly, result parsing
- **Update**: `src/decafclaw/agent.py` — reflection check after final response, retry loop
- **Update**: `src/decafclaw/config_types.py` — add ReflectionConfig
- **Update**: `src/decafclaw/config.py` — load reflection config
- **New**: `data/decafclaw/REFLECTION.md` — (optional) custom judge prompt override
- **Update**: tests, docs, CLAUDE.md
