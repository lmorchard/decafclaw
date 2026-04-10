# Spec: Direct Multi-Provider LLM Access

Issues: #128, #248

## Problem

DecafClaw currently talks to LLMs through a LiteLLM proxy using raw httpx calls to an OpenAI-compatible endpoint. This causes:

1. **Debugging blind spots.** LiteLLM translates between provider-native formats and OpenAI format, obscuring what's actually sent/received. The `__thought__` sanitization hack (tool call IDs stuffed with Gemini thinking tokens) is one known example. PR #232 is blocked because we can't determine whether tool-calling problems are Gemini model behavior or LiteLLM translation artifacts.
2. **Deployment complexity.** Requires running a LiteLLM proxy as a sidecar alongside the bot.
3. **Rigid model routing.** The effort system (fast/default/strong) maps to models on a single provider. No easy way to route to different providers or switch models ad-hoc for testing.

## Decision: Build Our Own Provider Abstraction

We evaluated [any-llm](https://github.com/mozilla-ai/any-llm) (Mozilla AI's multi-provider SDK) but decided against it because:

- It's still an opaque abstraction layer — trades one black box (LiteLLM proxy) for another (in-process SDK)
- Heavy dependencies (pulls in `openai` + `anthropic` SDKs as hard requirements)
- We only need 2-3 providers right now
- Building our own gives full visibility, control, and learning
- The current `llm.py` is only ~150 lines — extending it is less work than integrating a framework

## Architecture

### Provider Abstraction

A thin provider interface where each adapter handles:
- Authentication (API keys, ADC tokens, etc.)
- Translating the internal tool format to provider-native format
- Translating provider-native responses to the internal response format
- Streaming (provider-native SSE/chunks normalized to existing callback types)

### Internal Formats

**Tool definitions (internal canonical format):** Flat structure — `name`, `description`, `parameters` (JSON Schema). Each provider adapter wraps this in its native envelope at the edge.

**Response format (unchanged):** `{"content": str|None, "tool_calls": [{"id", "function": {"name", "arguments"}}], "role", "usage"}`. Provider adapters normalize into this.

**Streaming callbacks (unchanged):** `text`, `tool_call_start`, `tool_call_delta`, `tool_call_end`, `done` chunk types. Provider adapters emit these from their native streaming formats.

### Providers (This Session)

1. **`openai`** — Direct OpenAI API. API key auth. Native OpenAI format (minimal translation).
2. **`vertex`** — Google Vertex AI Gemini. ADC auth (`google-auth` for token refresh). Native Gemini REST API (GenerateContent/StreamGenerateContent). No OpenAI-compat endpoint — we want to see exactly what Gemini sees.
3. **`litellm`** — Existing raw-httpx OpenAI-compatible path, pointed at any OpenAI-compat endpoint (LiteLLM proxy, Ollama, vLLM, etc.). Preserves backward compatibility.

### Config Redesign

Two-level config: **providers** define connections, **models** reference providers and add per-model parameters.

```json
{
  "providers": {
    "vertex": {
      "type": "vertex",
      "project": "my-project",
      "region": "us-central1"
    },
    "openai": {
      "type": "openai",
      "api_key": "sk-..."
    },
    "local-litellm": {
      "type": "litellm",
      "url": "http://192.168.0.199:4000/v1/chat/completions",
      "api_key": "dummy"
    }
  },
  "models": {
    "gemini-flash": {
      "provider": "vertex",
      "model": "gemini-2.5-flash",
      "context_window_size": 1000000,
      "timeout": 300
    },
    "gemini-pro": {
      "provider": "vertex",
      "model": "gemini-2.5-pro",
      "context_window_size": 1000000,
      "timeout": 300
    },
    "gpt-4o": {
      "provider": "openai",
      "model": "gpt-4o",
      "context_window_size": 128000
    }
  },
  "default_model": "gemini-flash"
}
```

### Model Selection Replaces Effort

- `set_effort` tool becomes `set_model` — directly selects a named model config
- Skills, compaction, reflection, embeddings all reference named model configs instead of carrying their own url/model/api_key triplets
- The old `effort` concept (fast/default/strong) is removed — users just pick a model
- `!think-harder` / `!think-faster` commands can map to specific model configs or be deprecated

### Subsystem Model References

Subsystems that currently have their own LLM config (compaction, reflection, embeddings) will instead reference a named model config:

```json
{
  "compaction": { "model": "gemini-flash" },
  "reflection": { "model": "gemini-flash" },
  "embedding": { "model": "gemini-flash" }
}
```

If not specified, they fall back to `default_model`.

## Out of Scope (Deferred)

- **Anthropic provider adapter** — future session, when needed
- **Native provider-specific features** — extended thinking, grounding, etc. Requires rethinking the response format
- **Reworking the agent loop response format** — keep current dict format; revisit after we have more direct-access experience
- **Reworking the streaming callback format** — same as above
- **Structured output / response_format** — not currently used, add when needed
- **Provider-specific tool schema quirks** — e.g., Gemini doesn't support all JSON Schema features; handle as discovered
- **Connection pooling / client reuse** — start with new client per call (like current code), optimize if latency is a concern
- **Retry/rate-limit unification across providers** — each provider adapter handles its own for now

## Acceptance Criteria

1. DecafClaw can talk directly to Vertex/Gemini using ADC auth, no proxy
2. DecafClaw can talk directly to OpenAI API
3. Existing LiteLLM/Ollama setups still work via the `litellm` provider
4. Tool definitions are sent in provider-native format (verifiable via debug logging)
5. Streaming works with all three providers
6. Model configs are named and referenceable from subsystems
7. `set_model` tool allows switching models mid-conversation
8. Existing tests pass (adapted to new config structure)
9. Config migration path from old `LlmConfig` format
