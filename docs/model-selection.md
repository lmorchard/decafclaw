# Model Selection

DecafClaw supports multiple LLM providers and named model configurations. Users can switch models per-conversation via the web UI or WebSocket commands.

## Providers

Four provider types are supported:

| Provider | Type | Auth | Use case |
|----------|------|------|----------|
| **Vertex AI** | `vertex` | ADC or service account JSON | Direct Gemini access, no proxy |
| **OpenAI Responses** | `openai-responses` | API key | Reasoning with function tools; stateless item replay |
| **OpenAI Chat Completions** | `openai` | API key | Existing direct OpenAI connections |
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
  "default_model": "gemini-flash",
  "auxiliary_model": "gemini-flash-8b"
}
```

**Providers** define connections (credentials, endpoints). **Model configs** reference a provider and add per-model settings (timeout, context window size, streaming preference).

For OpenAI reasoning with tools, use `openai-responses` and optionally set
`reasoning_effort` on each model config. The setting follows named, default,
and auxiliary model selection in both streaming modes. See the
[Responses setup example](providers.md#openai-responses-openai-responses).

## Model discovery (#1211)

Instead of hand-writing every `model_configs` entry, DecafClaw can discover
models directly from the configured providers.

### How it works

- **OpenAI-compatible family** (`openai-compat`, `openai`, `openai-responses`):
  queries `GET {base}/models` and normalizes whatever the gateway reports.
  Metadata quality varies — LiteLLM reports mode/tool support/token limits,
  OpenRouter reports modalities and supported parameters, Ollama and vLLM
  report bare IDs.
- **Vertex AI**: no practical listing endpoint exists, so discovery returns a
  small curated static Gemini list (maintained in
  `llm/providers/vertex.py`).

Discovery runs **on demand only** — at startup and when you press **Refresh**
in the Models tab. It is never polled. If a gateway is unreachable, the error
is recorded per provider and the cached catalog keeps serving; nothing crashes
and no entries are lost.

### Capability filtering and default-on policy

Models the gateway positively identifies as non-chat (embeddings, image,
audio, realtime endpoints) are excluded entirely. Unknown capabilities are
treated as *not capable*, never assumed. A newly discovered model lands
**enabled** only when the gateway reports chat + tool support + context-window
metadata **and** the id is live-confirmed; otherwise it lands **disabled**
and you opt in. For the OpenAI-compatible family the gateway's own listing
is the confirmation. For the curated Vertex static list, ids without live
confirmation (`gemini-2.5-flash-lite`, `gemini-3.8-flash`, `gemini-4-argon`
as of this writing) land **disabled** — flip them in `llm/providers/vertex.py`
only with live evidence the project serves the id, so an unserved id never
surfaces enabled and fails on first use.

### Enabling, disabling, and the default model

All of this is managed in the web UI's **⚙ Agent Config → Models** tab:

- Toggle any discovered model on/off for the conversation picker.
- **Select all / Deselect all** buttons per provider bulk-enable or
  bulk-disable that provider's discovered catalog entries in one request.
  Catalog entries only — hand-authored `model_configs` entries (including
  one under a colliding key) are never touched.
- Providers render as collapsible sections (collapsed by default, with an
  enabled/total count in the summary), so a gateway reporting hundreds of
  models stays manageable.
- Filter the model list with the search field above the sections: typing
  filters the visible rows immediately (case-insensitive match on model
  id, catalog key, or provider name — a provider-name match shows that
  provider's whole list). Matching sections auto-expand while a filter is
  active and zero-match sections hide, with an explicit "no models match"
  note when nothing matches. It is a view-only filter: no API call, the
  summary counts stay real totals, and Refresh or a toggle keeps the
  typed text. Esc clears the field.
- Set the global default model from any enabled model (discovered or
  hand-configured), or clear the pointer to fall back to `config.json`.

State lives in the `data/{agent_id}/model_catalog.json` sidecar —
`config.json` is never written by the UI. Entries are keyed
`{provider}/{model_id}` to avoid collisions with hand-named configs.
Selecting a discovered model materializes a `ModelConfig` in memory with
safe defaults (`context_window_size` from the gateway when reported, else 0;
`timeout` 300; `streaming` true; `reasoning_effort` never guessed) and then
resolves through the normal single-`ModelConfig` path.

### Default-model resolution order

1. The sidecar's `default_model` pointer, when it names a selectable model.
2. `config.json`'s `default_model`.
3. `config.llm.model` (legacy), exactly as before.

Disabling the model the pointer names is allowed — resolution falls back to
the next step cleanly, on the next turn, without a restart.

### Shell auto-approval model (#1213)

The shell auto-approval LLM — the small structured-classification judge that
screens commands when `shell.aux_approval_enabled` is on — rides on the active
conversation model by default. A dedicated judge is optional so an expensive or
fast conversation model does not pay judgment prices it does not need.

- **Config**: `shell.aux_approval_model` (`""` = ride on the legacy aux
  chain: `auxiliary_model` → conversation model → default; when set, names a
  `model_configs` key — discovered models materialize as keys).
- **UI**: **⚙ Agent Config → Models → Auto-approval model** lists every
  enabled catalog model plus an explicit **"use conversation model"** option
  that clears the pointer and re-runs the chain. Writes land in the
  `data/{agent_id}/model_catalog.json` sidecar under `aux_approval_model`.

Resolution order for the auto-approval judge:

1. The sidecar's `aux_approval_model` pointer, when it names a selectable model.
2. `config.json`'s `shell.aux_approval_model` (the seed; a `model_configs`
   key — a hand-written raw name has no endpoint to route through, so it is
   treated as unset with a warning).
3. `config.json`'s `auxiliary_model` — the legacy aux chain the judge rode
   before dedicated pointers existed.
4. The active conversation model, then the global default — today's
   behavior when nothing is set.

An empty pointer or seed inherits step 3 onward; a pointer at a disabled or
vanished model falls back cleanly to the seed, then down the legacy chain.

The judgment is a forced-tool structured-output call, so the picker warns
when the selected model's discovery metadata reports no tool support
(`tools: false`; an unknown capability stays silent).

The agent cannot change its own auto-approval model — same user-only rule as
the default model.

### Reflection and verifier model pickers (#1212)

The **⚙ Agent Config → Models** tab also renders two judge pickers on top of
the global default:

- **Reflection model** — the judge that grades each turn
  (`reflection.model`). Lists every enabled catalog model (discovered +
  hand-configured) plus an explicit **inherit** option that clears the
  sidecar pointer so the existing resolution chain runs
  (`reflection.model` → `default_model` → `config.llm.model`).
- **Verifier model** — the optional second-opinion judge
  (`reflection.verifier_model`), same picker shape.

Both write the `reflection_model` / `verifier_model` keys of the
`data/{agent_id}/model_catalog.json` sidecar; `config.json` stays
read-only for this path.

Reflection-judge resolution mirrors the default-model order:

1. The sidecar's `reflection_model` pointer, when it names a selectable
   model (enabled discovered or hand-configured).
2. `config.json`'s `reflection.model` (the seed — it may itself be a
   `model_configs` key or a legacy raw name).
3. The legacy default chain via `reflection.resolved`.

An empty pointer means **inherit**. Changes take effect on the next
reflection run — no restart.

Disabling a model a reflection pointer references falls back cleanly, the
same rule as the default-model pointer.

Selecting a reflection judge equal to the active conversation model is
allowed but surfaces a warning in the UI — the point of a separate judge is
that the author does not grade its own homework (#591).

The agent cannot trigger discovery or change models — selection is
user-only, and the sidecar is outside the agent's writable workspace.

## Selecting a model

Model selection is **user-only** — the agent cannot change its own model (cost control).

### Web UI

The sidebar shows a dropdown picker when model configs are available. Selecting a model applies it for the rest of the conversation. The picker lists hand-authored configs plus enabled models discovered from providers — manage discovery and the global default in **⚙ Agent Config → Models** (see [Model discovery](#model-discovery-1211)).

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

At the start of each agent turn, the active model is resolved through the provider registry. The resolved provider + model are used for that turn's LLM calls, without affecting subsystem models (reflection and embeddings still use their own configured models or fall back to the default). Background operations like compaction and scoring use the `auxiliary_model` if configured, falling back to the active model or default model if not.

## Migration from effort levels

The old effort system (`fast`/`default`/`strong`, `set_effort` tool, `!think-harder` commands) has been replaced. If your `config.json` has an `llm` section but no `providers`/`model_configs`, the system auto-generates a "default" openai-compat provider and model config from the old values.

Effort levels are not model names. A leftover `model: strong` in a `SCHEDULE.md` or a forked skill's frontmatter resolves to nothing and the turn runs on `default_model`. This used to be silent; it now warns:

```
WARNING Unknown model 'strong' requested — falling back to vertex-gemini-flash.
        Configured model_configs: vertex-gemini-flash, vertex-gemini-pro
```

Grep your schedules and skill frontmatter for `fast` / `default` / `strong` after upgrading (#729).
