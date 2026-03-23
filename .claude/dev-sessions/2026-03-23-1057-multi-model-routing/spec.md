# Spec: Multi-Model Routing via Effort Levels

**Issue:** #3
**Branch:** `multi-model-routing`

## Problem

The agent currently uses a single model for everything. In practice, different tasks need different models: procedural SOPs work better on compliant/fast models (Flash), while complex reasoning benefits from stronger models (Pro). There's no way to route tasks to appropriate models, and no way to escalate when the current model is struggling.

## Solution

Introduce an **effort level** abstraction that maps human-meaningful labels (`fast`, `default`, `strong`) to concrete model configs. Effort can be set at multiple levels: config defaults, skill frontmatter, delegate_task parameter, agent tool call, and user command. Reflection retries serve as an escalation signal.

## Design

### Effort Levels

Three levels to start:

| Level | Intent | Example use |
|-------|--------|-------------|
| `fast` | Cheap, compliant, procedural | SOPs, simple tool calls, heartbeat tasks |
| `default` | Normal conversation | General chat, most interactions |
| `strong` | Complex reasoning, analysis | Code review, planning, "think harder" |

Each level maps to a partial LLM config (model, and optionally url/api_key). Missing fields resolve from `config.llm`.

### Config

New top-level `models` section in config.json, stored as a freeform dict (like `skills`):

```json
{
  "models": {
    "fast": {
      "model": "gemini-2.5-flash"
    },
    "default": {
      "model": "gemini-2.5-flash"
    },
    "strong": {
      "model": "gemini-2.5-pro"
    }
  }
}
```

Each entry can optionally include `url` and `api_key` for cross-provider routing. If omitted, they fall back to `config.llm.url` / `config.llm.api_key`.

If the `models` section is absent, all effort levels resolve to `config.llm` (backward compatible — no behavior change unless you configure it).

No env var overrides per effort level — this is the kind of config where env vars lack expressiveness. Use the `env` section in config.json if needed.

**Data model:** `Config.models` is `dict[str, dict[str, str]]` — a raw dict from config.json. No typed dataclass. Resolution happens at runtime via a helper function.

### Effort resolution

A helper function resolves effort level to concrete LLM settings:

```python
def resolve_effort(config, level: str) -> LlmConfig:
    """Resolve an effort level to a concrete LLM config.

    Merges config.models[level] over config.llm defaults.
    Unknown levels or absent models section falls back to config.llm.
    """
```

**Where resolution happens:** At the start of each agent turn, before calling the LLM. The agent loop resolves the current effort level, creates a forked config with the resolved `LlmConfig` on `config.llm`, and all downstream code (including streaming) just works without changes. This matches the existing context-forking pattern.

### Effort state

Effort level is tracked on `Context` as `ctx.effort: str` (default: `"default"`). This works across all interfaces (Mattermost, web, interactive). New conversations start at `"default"`.

**Persistence:** Effort level changes are recorded in the conversation archive (JSONL) so they can be replayed on conversation reload after a restart. The `set_effort` tool writes an archive entry when it changes the level. On reload, the archive replay restores `ctx.effort` to the last-set value.

### Independent subsystems

Compaction, reflection, and embedding keep their own independent model configs. Effort levels do not affect them — they serve different purposes and have their own `.resolved()` fallback to `config.llm`.

### Where effort is specified

**Priority order (highest wins):**

1. **Per-conversation override** — `set_effort` tool or `!think-harder` / `!think-faster` command. Sticky for the conversation.
2. **Skill frontmatter** — `effort: fast` in SKILL.md. Only applies to forked contexts (`context: fork`). Inline skills ignore this field.
3. **delegate_task parameter** — `effort: "strong"` on the tool call. Sets the child agent's effort level.
4. **Config default** — `"default"` effort level, which resolves via the `models` section or falls back to `config.llm`.

### Skill effort

Skills can declare effort in SKILL.md frontmatter:

```yaml
---
name: daily-todo-migration
effort: fast
context: fork
---
```

Skill effort only applies when `context: fork` — the child turn's context gets the specified effort level. Inline skills ignore the `effort` field; only `set_effort` and user commands change conversation effort.

### delegate_task effort

The `delegate_task` tool gains an optional `effort` parameter:

```
delegate_task(task="Follow this SOP exactly", effort="fast")
delegate_task(task="Analyze this architecture", effort="strong")
```

The parent agent picks the appropriate effort for each subtask. If omitted, the child inherits the parent's current effort level.

### set_effort tool

A new tool that changes the conversation's effort level:

```
set_effort(level="strong")
```

Returns a confirmation message including the resolved model name. The change is sticky for the conversation. The tool should be always-loaded (it's tiny and the agent needs it available to self-escalate).

### User commands

- `!think-harder` — sets effort to `strong` for the conversation
- `!think-faster` — sets effort to `fast` for the conversation
- `!think-normal` — resets to `default`

Three small skill directories, each with a SKILL.md that instructs the agent to call `set_effort` with the appropriate level. Same pattern as `!health`.

### Reflection escalation

When the reflection judge exhausts its retry budget (`max_retries`) without approving the response, and the conversation is **not already at `strong`**, the agent appends a suggestion:

> "I'm not confident in this answer. Try `!think-harder` to retry with a more capable model."

Suppressed when already at `strong` (nowhere to escalate). This is a lightweight nudge — the user stays in control.

### Logging

The agent loop logs the resolved effort level and model name before each LLM call, for debugging routing issues.

## Future Enhancements

- **Web UI effort picker:** Start a new conversation with a chosen effort level from the web UI (depends on this plumbing being in place first)
- **Qualitative model categories:** Extend effort levels to include specialized models (e.g. "writing", "coding")
- **Automatic classification:** Use a classifier to auto-route based on message complexity

## Out of Scope

- Automatic classification / routing (deferred — effort level is explicit)
- Per-turn effort (effort is per-conversation)
- Qualitative model categories (future extension of the same config shape)
- Different providers per level requiring different API formats (assumes OpenAI-compatible for all)
- Effort persistence outside of conversation archives

## Acceptance Criteria

- `models` config section maps effort levels to partial LLM configs
- Absent `models` section falls back to `config.llm` (backward compat)
- `resolve_effort()` merges model entry over `config.llm` defaults
- Effort level tracked on `Context`, defaults to `"default"`
- Agent loop forks config with resolved LLM settings before each turn
- Skills can declare `effort` in SKILL.md frontmatter (forked contexts only)
- `delegate_task` accepts optional `effort` parameter, inherits parent effort if omitted
- `set_effort` tool changes conversation effort level (sticky)
- `!think-harder`, `!think-faster`, `!think-normal` user commands work
- Reflection suggests escalation when retries exhausted and not already at `strong`
- Resolved effort level and model logged per LLM call
- Compaction/reflection/embedding unaffected by effort levels
- Effort level changes persisted in conversation archive and restored on reload
- Tests cover: effort resolution, config fallback, context state, delegate inheritance, set_effort tool, archive persistence
