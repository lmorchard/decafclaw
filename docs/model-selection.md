# Model Selection

DecafClaw supports multiple LLM providers and named model configurations. Users can switch models per-conversation via the web UI or WebSocket commands.

## Providers

Three provider types are supported:

| Provider | Type | Auth | Use case |
|----------|------|------|----------|
| **Vertex AI** | `vertex` | ADC or service account JSON | Direct Gemini access, no proxy |
| **OpenAI** | `openai` | API key | Direct OpenAI API |
| **OpenAI-compatible** | `openai-compat` | API key (or none) | LiteLLM, Ollama, vLLM, OpenRouter, etc. |

See [LLM Providers](providers.md) for detailed setup.

## Configuration

Model config has two layers in `config.json`:

```json
{
  "providers": {
    "vertex": {
      "type": "vertex",
      "project": "my-gcp-project",
      "region": "us-central1"
    },
    "openai": {
      "type": "openai",
      "api_key": "sk-..."
    }
  },
  "model_configs": {
    "gemini-flash": {
      "provider": "vertex",
      "model": "gemini-2.5-flash"
    },
    "gpt-4o": {
      "provider": "openai",
      "model": "gpt-4o"
    }
  },
  "default_model": "gemini-flash"
}
```

**Providers** define connections (credentials, endpoints). **Model configs** reference a provider and add per-model settings (timeout, context window size, streaming preference).

## Selecting a model

Model selection is **user-only** — the agent cannot change its own model (cost control).

### Web UI

The sidebar shows a dropdown picker when model configs are available. Selecting a model applies it for the rest of the conversation.

### WebSocket

Send a `set_model` message:

```json
{"type": "set_model", "conv_id": "...", "model": "gpt-4o"}
```

### Delegate task

The `delegate_task` tool accepts an optional `model` parameter:

```json
{"task": "Analyze this architecture", "model": "gemini-pro"}
```

If omitted, the child inherits the parent's active model.

### Skill frontmatter

Skills can declare a preferred model in SKILL.md:

```yaml
---
name: daily-todo-migration
model: gemini-flash
context: fork
---
```

This only applies to **forked** execution (`context: fork`). Inline skills ignore this field.

## Persistence

Model changes are recorded as `{"role": "model", "content": "model-name"}` events in the conversation archive. On reload, the agent scans the archive for the last model event and restores the selection.

## Reflection escalation

When [self-reflection](reflection.md) can't approve a response after exhausting retries, the agent suggests switching models:

> *I'm not confident in this answer. Try switching to a more capable model in the web UI model picker.*

This is a suggestion, not automatic — you stay in control.

## How it works

At the start of each agent turn, the active model is resolved through the provider registry. The resolved provider + model are used for that turn's LLM calls, without affecting subsystem models (compaction, reflection, embeddings still use their own configured models or fall back to the default).

## Migration from effort levels

The old effort system (`fast`/`default`/`strong`, `set_effort` tool, `!think-harder` commands) has been replaced. If your `config.json` has an `llm` section but no `providers`/`model_configs`, the system auto-generates a "default" openai-compat provider and model config from the old values.
