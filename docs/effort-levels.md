# Effort Levels (Multi-Model Routing)

Effort levels let you route different tasks to different models. Procedural work can use a fast, cheap model while complex reasoning uses a stronger one.

## Levels

| Level | Intent | Example use |
|-------|--------|-------------|
| `fast` | Cheap, compliant, procedural | SOPs, simple tool calls, heartbeat tasks |
| `default` | Normal conversation | General chat, most interactions |
| `strong` | Complex reasoning, analysis | Code review, planning, "think harder" |

Each level maps to a partial LLM config (model, and optionally url/api_key). Missing fields fall back to the main `llm` config.

## Configuration

Add a `models` section to `config.json`:

```json
{
  "llm": {
    "url": "http://localhost:4000/v1/chat/completions",
    "model": "gemini-2.5-flash"
  },
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

Each entry can include `model`, `url`, and `api_key`. Omitted fields inherit from the `llm` section. This means you can route effort levels to entirely different providers if needed.

If the `models` section is absent, all effort levels resolve to `config.llm` — no behavior change unless you configure it.

## Setting effort

### User commands

- **`!think-harder`** — switch to `strong` for this conversation
- **`!think-faster`** — switch to `fast` for this conversation
- **`!think-normal`** — reset to `default`

In the web UI, use `/think-harder`, `/think-faster`, `/think-normal`.

### Agent tool

The `set_effort` tool lets the agent change its own effort level:

```json
{"level": "strong"}
```

Returns a confirmation with the resolved model name. The change is sticky for the rest of the conversation.

### Skill frontmatter

Skills can declare a preferred effort level in SKILL.md:

```yaml
---
name: daily-todo-migration
effort: fast
context: fork
---
```

Skill effort only applies to **forked** execution (`context: fork`). Inline skills ignore this field. This prevents a skill from accidentally changing the conversation's model.

### Delegate task

The `delegate_task` tool accepts an optional `effort` parameter:

```json
{"task": "Follow this SOP exactly", "effort": "fast"}
{"task": "Analyze this architecture", "effort": "strong"}
```

If omitted, the child inherits the parent's current effort level.

## Priority order

When multiple sources specify effort (highest wins):

1. **Per-conversation override** — `set_effort` tool or `!think-harder` command
2. **Skill frontmatter** — `effort` field (forked contexts only)
3. **delegate_task parameter** — explicit `effort` on the tool call
4. **Config default** — the `default` effort level

## Persistence

Effort level changes are recorded as events in the conversation archive (JSONL). On reload, the agent scans the archive for the last effort event and restores the level. This means effort history is preserved alongside the conversation.

## Reflection escalation

When the [self-reflection](reflection.md) judge can't approve a response after exhausting its retry budget, and the conversation isn't already at `strong`, the agent appends a suggestion:

> *I'm not confident in this answer. Try `!think-harder` to retry with a more capable model.*

This is a nudge, not an automatic escalation — you stay in control.

## How it works

At the start of each agent turn, the effort level is resolved to a concrete LLM config. The resolved model/url/api_key are passed as per-call overrides to the LLM, without modifying the base `config.llm`. This ensures that compaction, reflection, and embedding subsystems — which fall back to `config.llm` via their `.resolved()` methods — are **not affected** by effort levels and keep their own independent model configs.

## Example: mixed-effort workflow

```
User: !think-faster
Agent: Effort level set to fast (model: gemini-2.5-flash).

User: Run my daily standup SOP
Agent: [runs procedural SOP quickly on Flash]

User: !think-harder
Agent: Effort level set to strong (model: gemini-2.5-pro).

User: Review this PR and suggest architectural improvements
Agent: [does deep analysis on Pro]
```
