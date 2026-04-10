# Plan: Direct Multi-Provider LLM Access

Issues: #128, #248

## Design Decisions

**Internal tool format stays as-is.** Tool definitions currently use OpenAI envelope format (`{"type": "function", "function": {"name", "description", "parameters"}}`). The inner content (name, description, JSON Schema parameters) is universal across all three target providers. Provider adapters unwrap with `td["function"]` and rewrap for their native format. Changing every `*_TOOL_DEFINITIONS` list across ~15 modules would be a large diff for no practical gain. Revisit if a provider needs something the inner format can't represent.

**`llm.py` becomes `llm/` package.** The current single-file module becomes a package to house the provider interface, registry, and per-provider adapters. Public API surface stays the same from the caller's perspective.

**Embedding calls move into providers.** Currently `embeddings.py` makes its own httpx calls. The provider abstraction should handle embeddings too, so `embed_text()` delegates to the provider rather than duplicating HTTP/auth logic.

---

## Phase 1: Provider Interface + LLM Package Scaffold

**Context:** The llm.py module is a single file with two functions. We need to restructure it as a package with a provider protocol before adding new providers.

**Prompt:**

Restructure `src/decafclaw/llm.py` into a `src/decafclaw/llm/` package:

1. Create `src/decafclaw/llm/__init__.py` — re-export `call_llm`, `call_llm_streaming`, and a new `embed_text` function so existing imports (`from .llm import call_llm`) still work.

2. Create `src/decafclaw/llm/types.py` — define a `Provider` protocol class:
   ```python
   class Provider(Protocol):
       async def complete(self, model: str, messages: list, tools: list | None = None,
                         cancel_event: asyncio.Event | None = None,
                         on_chunk: Callable | None = None,
                         streaming: bool = False,
                         **kwargs) -> dict:
           """Call LLM. Returns {"content", "tool_calls", "role", "usage"}."""
           ...

       async def embed(self, model: str, text: str, **kwargs) -> list[float] | None:
           """Embed text. Returns vector or None on failure."""
           ...
   ```
   Also define a `ProviderConfig` TypedDict or dataclass for the per-provider connection config.

3. Create `src/decafclaw/llm/registry.py` — a registry that maps provider names to provider instances. Initialized from config at startup.

4. Move the existing httpx code from `llm.py` into `src/decafclaw/llm/providers/litellm.py` as the first provider implementation. Keep `_sanitize_tool_call_id` there (it's LiteLLM-specific).

5. Update `__init__.py` so `call_llm` and `call_llm_streaming` delegate to the appropriate provider via the registry. For now, hardwire to the litellm provider so existing behavior is unchanged.

6. Run `make check && make test` to verify nothing broke.

**After this step:** The llm package exists, the provider protocol is defined, existing code works through the litellm provider. No config changes yet.

---

## Phase 2: Config Redesign — Providers + Models

**Context:** The current config has `LlmConfig` (single endpoint), `CompactionConfig`/`ReflectionConfig`/`EmbeddingConfig` each with their own url/model/api_key + `resolved()` fallback, and a `models` dict for effort levels. We're replacing all of this with a two-level providers + models structure.

**Prompt:**

Redesign the config system for multi-provider support:

1. In `config_types.py`, add new dataclasses:
   ```python
   @dataclass
   class ProviderConfig:
       type: str = ""  # "vertex", "openai", "litellm"
       # Common fields
       api_key: str = field(default="", metadata={"secret": True})
       # Provider-specific fields
       url: str = ""           # litellm/openai base URL
       project: str = ""       # vertex GCP project
       region: str = ""        # vertex region

   @dataclass
   class ModelConfig:
       provider: str = ""      # key into providers dict
       model: str = ""         # model name for the provider
       context_window_size: int = 0
       timeout: int = 300
       streaming: bool = True
       temperature: float | None = None
       max_tokens: int | None = None
   ```

2. Add to `Config`:
   ```python
   providers: dict[str, ProviderConfig]
   model_configs: dict[str, ModelConfig]  # "model_configs" to avoid collision with old "models"
   default_model: str = ""
   ```

3. Add a `resolve_model(config, name: str) -> tuple[ProviderConfig, ModelConfig]` function that looks up a model config by name and returns both the provider and model config. Falls back to `default_model` if name is empty.

4. Update `load_config()` in `config.py` to parse the new `providers` and `model_configs` sections from config.json. Keep the old `LlmConfig` and `models` sections working for now (backward compat during migration).

5. Add a migration path: if old-style `llm` config exists but no `providers`/`model_configs`, auto-generate a `litellm` provider + default model config from the old values so existing setups don't break.

6. Update `CompactionConfig`, `ReflectionConfig`, `EmbeddingConfig` to have a `model: str = ""` field that references a named model config. Keep `resolved()` working for now but mark it as deprecated internally.

7. Run `make check && make test`.

**After this step:** Config can express providers + named models. Old config still works via migration. Subsystem configs have a `model` field but don't use it yet.

---

## Phase 3: Wire Providers into Agent Loop

**Context:** The provider interface and config exist but aren't connected. The agent loop still calls `call_llm`/`call_llm_streaming` with url/model/api_key overrides. We need to wire the new provider registry into the agent loop.

**Prompt:**

Connect the provider abstraction to the agent loop:

1. In `src/decafclaw/llm/registry.py`, implement `init_providers(config)` that reads `config.providers` and instantiates provider objects. Store in module-level registry. Call this from the startup path (runner.py or `__init__.py`).

2. Update `call_llm` and `call_llm_streaming` in `llm/__init__.py` to accept a `model_name: str` parameter instead of `llm_url`/`llm_model`/`llm_api_key`. They resolve the model name → provider + model config, then call `provider.complete()`.

3. Add `embed_text` to `llm/__init__.py` that resolves a model name → provider and calls `provider.embed()`.

4. Update `_call_llm_with_events()` in `agent.py` to pass a model name instead of url/model/api_key overrides.

5. Update `_setup_turn_state()` in `agent.py` — instead of building `effort_overrides` dict with url/model/api_key, just return the resolved model name.

6. Update `compaction.py` — instead of `config.compaction.resolved(config)` + url/model/api_key kwargs, resolve the compaction model name and pass it.

7. Update `reflection.py` — same pattern as compaction.

8. Update `embeddings.py` — `embed_text()` delegates to `llm.embed_text(config, model_name, text)` instead of making its own httpx calls. Keep retry logic in the provider.

9. Update `eval/reflect.py` — pass model name instead of `llm_model` override.

10. Run `make check && make test`.

**After this step:** The entire codebase routes LLM calls through the provider abstraction. Only the litellm provider exists, so behavior is identical. But the wiring is in place for new providers.

---

## Phase 4: OpenAI Provider

**Context:** The litellm provider handles OpenAI-compatible endpoints. The OpenAI provider talks directly to OpenAI's API — same format, but with direct auth rather than going through a proxy.

**Prompt:**

Create `src/decafclaw/llm/providers/openai.py`:

1. Implement the `Provider` protocol using direct httpx calls to `https://api.openai.com/v1/chat/completions` and `https://api.openai.com/v1/embeddings`.

2. Auth: Bearer token from `ProviderConfig.api_key`.

3. The request/response format is identical to the litellm provider (both are OpenAI format). The difference is: fixed base URL, no proxy, and the api_key is an OpenAI key.

4. Streaming: same SSE format as litellm provider. Factor out shared SSE parsing logic into a `src/decafclaw/llm/providers/_openai_compat.py` helper that both litellm and openai providers use.

5. Tool definitions: pass through as-is (OpenAI native format).

6. Retry logic: same 429/5xx retry pattern, factored into the shared helper.

7. Register the provider type as `"openai"` in the registry.

8. Add a test config example in a comment or test fixture showing how to configure it.

9. Run `make check && make test`.

**After this step:** DecafClaw can talk directly to OpenAI without a proxy. The litellm and openai providers share SSE parsing code.

---

## Phase 5: Vertex/Gemini Provider

**Context:** This is the key provider — native Gemini API access via Vertex AI with ADC auth. No OpenAI-compatible wrapper. This is where we'll finally see if tool-calling issues are LiteLLM artifacts.

**Prompt:**

Create `src/decafclaw/llm/providers/vertex.py`:

1. **Auth:** Use `google-auth` library for ADC token refresh. Get credentials via `google.auth.default()`, refresh as needed, pass as Bearer token. Add `google-auth` to project dependencies.

2. **Endpoint:** `https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/publishers/google/models/{model}:generateContent` (non-streaming) and `:streamGenerateContent?alt=sse` (streaming).

3. **Message translation:** Convert from internal format (OpenAI-style `messages` list with `role`/`content`/`tool_calls`/`tool_call_id`) to Gemini's native `contents` format:
   - `role: "user"` → `role: "user"`, `parts: [{"text": ...}]`
   - `role: "assistant"` → `role: "model"`, `parts: [{"text": ...}]`
   - `role: "assistant"` with `tool_calls` → `role: "model"`, `parts: [{"functionCall": {"name", "args"}}]`
   - `role: "tool"` → `role: "user"`, `parts: [{"functionResponse": {"name", "response": {"result": ...}}}]`
   - `role: "system"` → `system_instruction: {"parts": [{"text": ...}]}`

4. **Tool translation:** Convert from OpenAI tool format to Gemini `FunctionDeclaration`:
   ```python
   # From: {"type": "function", "function": {"name", "description", "parameters"}}
   # To: {"name", "description", "parameters"}  (just unwrap)
   ```
   Wrap in `tools: [{"functionDeclarations": [...]}]`.

5. **Response translation:** Convert Gemini response to internal format:
   - `candidates[0].content.parts[0].text` → `content`
   - `candidates[0].content.parts[].functionCall` → `tool_calls` (generate unique IDs)
   - `usageMetadata` → `usage` (map `promptTokenCount`/`candidatesTokenCount`)

6. **Streaming:** Gemini streaming returns SSE with full candidate objects per chunk (not deltas like OpenAI). Parse these and emit the standard callback events (`text`, `tool_call_start`, `tool_call_end`, `done`).

7. **Embedding:** Hit `https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/publishers/google/models/{model}:predict` with Vertex embedding format.

8. **Debug logging:** Log the raw request body and response at DEBUG level so we can see exactly what Gemini receives. This is the whole point of going native.

9. **No `_sanitize_tool_call_id` needed** — that was a LiteLLM artifact. Generate clean IDs ourselves.

10. Register as `"vertex"` provider type.

11. Run `make check && make test`. Manually test against a real Vertex endpoint if possible.

**After this step:** DecafClaw talks directly to Gemini via Vertex AI. Tool definitions are sent in Gemini-native format. We can finally see exactly what the model receives and returns.

---

## Phase 6: set_model Replacing set_effort

**Context:** The effort system (fast/default/strong) is being replaced by direct model selection. Users pick a named model config instead of an abstract effort level.

**Prompt:**

Replace the effort system with model selection:

1. In `tools/effort_tools.py`, rename to `tools/model_tools.py`. Replace `set_effort` with `set_model`:
   - Takes `model: str` parameter — a named model config
   - Validates the name exists in `config.model_configs`
   - Sets `ctx.model = model` (rename `ctx.effort`)
   - Archives as `{"role": "model", "content": model_name}`
   - Returns confirmation showing provider + model

2. Update `_setup_turn_state()` in `agent.py`:
   - Scan archive for last `"model"` role message (keep backward compat scanning for `"effort"` too)
   - Resolve model name → pass to `_call_llm_with_events`

3. Update the think-harder/think-faster/think-normal skills to call `set_model` instead of `set_effort`. These can map to specific model configs (e.g., think-harder → "gemini-pro", think-faster → "gemini-flash") or we can deprecate them if direct model selection is sufficient.

4. Remove `EFFORT_LEVELS` constant and `resolve_effort()` from `config.py`.

5. Remove old `models` dict from config (the effort-level mapping).

6. Update `tools/__init__.py` imports.

7. Update Context dataclass — rename `effort` field to `model` (or `active_model`).

8. Run `make check && make test`.

**After this step:** Users select models by name. The effort abstraction is gone. Think-harder/faster commands still work but delegate to model selection.

---

## Phase 7: Cleanup + Remove Old Config

**Context:** Everything is wired through the new system. Remove deprecated code paths and update documentation.

**Prompt:**

Clean up deprecated code:

1. Remove `LlmConfig` dataclass from `config_types.py` (or reduce to just `streaming` default if still needed as a fallback).

2. Remove `resolved()` methods from `CompactionConfig`, `ReflectionConfig`, `EmbeddingConfig`. These now reference named model configs.

3. Remove the backward-compat migration code from `load_config()` once we're confident the new format works (or keep it for one release cycle — discuss with Les).

4. Remove `_sanitize_tool_call_id` from the litellm provider if it's no longer needed (test with actual LiteLLM to confirm).

5. Remove the raw httpx embedding call from `embeddings.py` — it now delegates to the provider.

6. Clean up any remaining references to `config.llm.url`, `config.llm.model`, `config.llm.api_key` throughout the codebase.

7. Update documentation:
   - `CLAUDE.md` — key files list (llm package), conventions (provider pattern), config section
   - `README.md` — config table, project structure
   - `docs/` — create `docs/providers.md` documenting the provider system
   - Update any existing docs that reference LlmConfig or effort levels

8. Run `make check && make test`.

**After this step:** No deprecated code remains. Docs are current. The codebase is clean.

---

## Phase 8: Integration Testing + Validation

**Context:** Everything is built. Time to verify it works end-to-end with real providers.

**Prompt:**

Validate the full system:

1. Test litellm provider — existing LiteLLM proxy setup still works (completions + embeddings + streaming + tool calls).

2. Test openai provider — direct OpenAI API (completions + streaming + tool calls). Needs an API key.

3. Test vertex provider — direct Gemini via Vertex AI (completions + streaming + tool calls + embeddings). Uses ADC auth.

4. Test model switching — `set_model` mid-conversation, verify the next turn uses the new provider.

5. Test subsystem routing — compaction/reflection/embedding each using their configured model.

6. Compare tool call behavior between litellm-proxied Gemini and direct Vertex Gemini — this is the key diagnostic for PR #232's blocked issues.

7. Run the eval suite against each provider to establish baselines.

8. Write up findings in session notes.

**After this step:** We know the system works, have baselines per provider, and can finally answer whether tool-calling issues are LiteLLM or Gemini.

---

## Deferred (Future Sessions)

- **Anthropic provider adapter** — when needed for direct Anthropic API access
- **Extended thinking / native provider features** — may require response format changes
- **Connection pooling / client reuse** — optimize if latency is a problem
- **Retry/rate-limit unification** — each provider handles its own for now
- **Structured output / response_format** — add when a use case arises
- **Gemini JSON Schema quirks** — handle as discovered during testing
- **Internal tool format change** — if a future provider can't be served by unwrapping the OpenAI envelope
