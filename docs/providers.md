# LLM Providers

DecafClaw talks directly to LLM provider APIs — no proxy required. Each provider handles its own auth, request format, and response normalization.

## Provider types

### Vertex AI (Gemini)

Direct access to Google's Gemini models via Vertex AI. Uses native Gemini REST API (not OpenAI-compatible), so you see exactly what the model receives.

```json
{
  "providers": {
    "vertex": {
      "type": "vertex",
      "project": "my-gcp-project",
      "region": "us-central1"
    }
  },
  "model_configs": {
    "gemini-flash": { "provider": "vertex", "model": "gemini-2.5-flash" },
    "gemini-pro": { "provider": "vertex", "model": "gemini-2.5-pro" }
  }
}
```

**Auth: Application Default Credentials (ADC)**

For local development:

```bash
gcloud auth application-default login
```

This opens a browser for OAuth. The token expires periodically — re-run when you get auth errors.

**Auth: Service account (for servers)**

For persistent deployments (no browser), use a service account JSON key file:

1. Go to [GCP Console → IAM → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts)
2. Select your project (e.g., `my-gcp-project`)
3. Click **Create Service Account**
4. Name it (e.g., `decafclaw-llm`)
5. Grant the role **Vertex AI User** (`roles/aiplatform.user`)
6. Click the service account → **Keys** → **Add Key** → **Create new key** → **JSON**
7. Save the downloaded JSON file securely (e.g., `/etc/decafclaw/vertex-sa.json`)

Then configure:

```json
{
  "providers": {
    "vertex": {
      "type": "vertex",
      "project": "my-gcp-project",
      "region": "us-central1",
      "service_account_file": "/etc/decafclaw/vertex-sa.json"
    }
  }
}
```

Or set the env var (works without config changes):

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/etc/decafclaw/vertex-sa.json
```

**Regions:** Gemini models are available in `us-central1`, `us-east4`, `europe-west1`, and others. Check [Vertex AI regions](https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations) for availability.

Set `"region": "global"` to use the multi-region global endpoint. Newer Gemini models are often global-only and 404 on every regional endpoint, so `global` is the safer default unless you have a data-residency requirement. Note that `global` is a *location*, not a host prefix — the provider dispatches it to `aiplatform.googleapis.com` rather than `global-aiplatform.googleapis.com` (which does not exist).

### OpenAI Responses (`openai-responses`)

Use this provider for OpenAI reasoning models with DecafClaw's function tools.
It uses `/v1/responses`; the existing `openai` provider uses Chat Completions.

```json
{
  "providers": {
    "openai": { "type": "openai-responses", "api_key": "sk-..." }
  },
  "model_configs": {
    "luna": {
      "provider": "openai",
      "model": "gpt-6-luna",
      "reasoning_effort": "medium"
    }
  },
  "default_model": "luna"
}
```

Set the key explicitly in the provider configuration. Like `openai`, this
provider does not automatically read `OPENAI_API_KEY`. Merge the entries into
`data/{agent_id}/config.json`, restart, and select the model in the conversation
picker (an existing conversation may retain its previous selection).

`url` accepts a base URL such as `https://api.openai.com/v1` or a full
`/responses` endpoint. If your key requires a regional hostname, set it there;
for example, `https://us.api.openai.com/v1/responses`. Embeddings use the same
base path with `/embeddings`.

`reasoning_effort` is optional. Omit it or set it to `null` to use the model's
default. Accepted values are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`,
and `max`; support varies by model, and the API reports unsupported combinations.
DecafClaw rejects this setting on other provider types instead of silently
ignoring it. For an existing model entry, the CLI supports:

```bash
decafclaw config set model_configs.luna.reasoning_effort low
decafclaw config set model_configs.luna.reasoning_effort null
```

Requests use `store: false` and explicitly request encrypted reasoning items.
Ordered output items, including assistant phase, are archived alongside visible
history and replayed only for the same provider name, endpoint, and model. Other
providers see visible text and tool history. Switching back can reuse unchanged
items still in the working history. Compaction removes replay metadata with the
summarized messages; cleared tool results remain cleared. Browser chat history
does not include opaque replay metadata.

Both streaming modes support text, image attachments, parallel function calls,
and refusals. Tools still run through DecafClaw's execution and approval system.
OpenAI-hosted tools, hosted conversations, and background Responses are outside
this provider's scope. Incomplete, failed, or interrupted responses cannot
execute tools.

See [OpenAI's Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)
and [reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning).

### OpenAI Chat Completions (`openai`)

Direct access to OpenAI's API.

```json
{
  "providers": {
    "openai": {
      "type": "openai",
      "api_key": "sk-..."
    }
  },
  "model_configs": {
    "gpt-4o": { "provider": "openai", "model": "gpt-4o" },
    "gpt-4o-mini": { "provider": "openai", "model": "gpt-4o-mini" }
  }
}
```

The `api_key` must be set in the provider config — the provider does not auto-detect `OPENAI_API_KEY` from the environment. To keep secrets out of `config.json`, use the `env` section to load from `.env`:

```json
{
  "providers": {
    "openai": { "type": "openai", "api_key": "sk-..." }
  }
}
```

**Custom base URL:** Set `"url"` to point to an OpenAI-compatible endpoint other than `api.openai.com` (e.g., Azure OpenAI).

### OpenAI-compatible (`openai-compat`)

For any OpenAI-compatible endpoint: LiteLLM proxy, Ollama, vLLM, OpenRouter, etc.

```json
{
  "providers": {
    "local": {
      "type": "openai-compat",
      "url": "http://localhost:11434/v1/chat/completions",
      "api_key": "ollama"
    }
  },
  "model_configs": {
    "llama": { "provider": "local", "model": "llama3.2" }
  }
}
```

This is also the fallback for legacy configs — if you have an `llm` section but no `providers`, a "default" openai-compat provider is auto-created from `llm.url` and `llm.api_key`.

## Multiple providers

You can configure multiple providers simultaneously and assign different models to each:

```json
{
  "providers": {
    "vertex": { "type": "vertex", "project": "my-project", "region": "us-central1" },
    "openai": { "type": "openai", "api_key": "sk-..." },
    "ollama": { "type": "openai-compat", "url": "http://localhost:11434/v1/chat/completions" }
  },
  "model_configs": {
    "gemini-flash": { "provider": "vertex", "model": "gemini-2.5-flash" },
    "gpt-4o": { "provider": "openai", "model": "gpt-4o" },
    "llama-local": { "provider": "ollama", "model": "llama3.2" }
  },
  "default_model": "gemini-flash"
}
```

Users can switch between these via the web UI dropdown.

## Model config fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | required | Key into `providers` dict |
| `model` | string | required | Model name for the provider |
| `context_window_size` | int | 0 | Context window in tokens (0 = use compaction_max_tokens) |
| `timeout` | int | 300 | HTTP timeout in seconds |
| `streaming` | bool | true | Use streaming responses |
| `reasoning_effort` | string or null | null | Responses reasoning effort; omitted/null uses the model default |

## Provider config fields

| Field | Type | Providers | Description |
|-------|------|-----------|-------------|
| `type` | string | all | `"vertex"`, `"openai-responses"`, `"openai"`, or `"openai-compat"` (alias: `"litellm"`) |
| `api_key` | string | openai-responses, openai, openai-compat | API key (secret, masked in config show) |
| `url` | string | openai-responses, openai, openai-compat | Base URL or API endpoint |
| `project` | string | vertex | GCP project ID |
| `region` | string | vertex | GCP region (default: `us-central1`) |
| `service_account_file` | string | vertex | Path to service account JSON key file |

## Architecture

The provider abstraction lives in `src/decafclaw/llm/`:

- `types.py` — `Provider` protocol defining `complete()` and `embed()` methods
- `registry.py` — Named provider registry, initialized from config at startup
- `providers/openai_compat.py` — OpenAI-compat provider (httpx + SSE)
- `providers/openai.py` — Direct OpenAI (thin subclass of openai-compat)
- `providers/openai_responses.py` — OpenAI Responses wire adapter and stateless output-item replay
- `providers/vertex.py` — Native Gemini REST API with ADC/service account auth

All providers normalize responses to the same internal format (`content`, `tool_calls`, `role`, `usage`). Tool definitions are sent in each provider's native format — OpenAI envelope for openai-compat/openai, `FunctionDeclaration` for Vertex/Gemini.

## Integration tests

```bash
make test-integration    # run provider integration tests
make test                # unit tests only (skips integration)
make test-all            # everything
```

Integration tests require credentials (ADC for Vertex, `OPENAI_API_KEY` in `.env` for OpenAI). Tests are auto-skipped when credentials are unavailable.

For the bounded Luna Responses tests, set `OPENAI_API_KEY` in `.env` and run:

```bash
uv run pytest tests/test_responses_integration.py -m integration -n 0
```

These tests exercise an image and two tool iterations in both streaming modes,
with a 2,048-output-token cap per response. Set `OPENAI_RESPONSES_URL` for a
regional test endpoint if required. This variable applies to the tests; runtime
connections use the provider's `url` field in `config.json`.
