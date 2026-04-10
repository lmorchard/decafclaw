# Notes: Adopt any-llm SDK for Direct Multi-Provider LLM Access

Issue: #128

## Phase 1: Provider interface + LLM package scaffold

- Restructured `llm.py` into `llm/` package: `__init__.py`, `types.py`, `registry.py`, `providers/litellm.py`
- Defined `Provider` protocol with `complete()` and `embed()` methods
- Moved all existing httpx/SSE code into `LiteLLMProvider` class
- `call_llm` and `call_llm_streaming` are now thin wrappers that delegate to the provider
- Added `embed()` method to the provider (will be wired to embeddings.py in Phase 3)
- All 1175 tests pass, lint clean, typecheck clean
- Backward-compatible: all existing imports (`from .llm import call_llm`) still work

## Phase 2: Config redesign — providers + models

- Added `ProviderConfig` and `ModelConfig` dataclasses to `config_types.py`
- Added `providers`, `model_configs`, `default_model` fields to `Config`
- Added `resolve_model()` function for name-based model resolution
- Added `_load_providers()` and `_load_model_configs()` config parsers
- Migration path: if no `providers` section exists but old `llm` config does, auto-generates a "default" litellm provider + model config
- Updated registry `init_providers()` to read from `config.providers`
- Old effort system (`resolve_effort`, `EFFORT_LEVELS`) still works alongside new system
- All 1175 tests pass, lint clean, typecheck clean

## Phase 3: Wire providers into agent loop

- Added `model_name` parameter to `call_llm`, `call_llm_streaming`, and `_call_llm_with_events`
- Added `embed_text` to llm package (provider-delegating, for future use)
- Updated `_resolve()` in llm/__init__.py with 3-priority resolution: model_name → legacy overrides → default provider
- Updated `_setup_turn_state()` to try model_name path first, fall back to effort
- Added "model" role archive scanning alongside "effort" for state restoration
- Compaction, reflection, eval/reflect still use legacy url/model/api_key overrides (works through existing path)
- All 1175 tests pass, lint clean, typecheck clean

## Phase 4: OpenAI provider

- Created `OpenAIProvider` as a thin subclass of `LiteLLMProvider` — same SSE format, just different URL defaults
- `_sanitize_tool_call_id` is a no-op for clean OpenAI tool call IDs, so no override needed
- Registered as `"openai"` provider type in registry
- All 1175 tests pass, lint clean, typecheck clean

## Phase 5: Vertex/Gemini provider

- Created `VertexProvider` with native Gemini REST API (not OpenAI-compat)
- ADC auth via `google-auth` (auto-refresh on credential expiry)
- Full message translation: system→systemInstruction, assistant→model, tool→user/functionResponse
- Tool definitions: unwrap OpenAI envelope to flat FunctionDeclaration format
- Streaming: Gemini sends full accumulated content per chunk; provider diffs consecutive chunks to extract deltas for callback events
- Response parsing: functionCall parts → tool_calls with generated UUIDs (Gemini has no tool call IDs)
- Embedding via Vertex predict endpoint
- 15 unit tests for message/tool/response translation
- Added `google-auth>=2.0.0` as dependency
- All 1190 tests pass, lint clean, typecheck clean

## Phase 6: set_model alongside set_effort

- Added `active_model` field to Context (alongside existing `effort`)
- Created `set_model` tool in `tools/model_tools.py` — validates model name against config.model_configs
- Archives model changes as `{"role": "model", "content": model_name}`
- `_setup_turn_state()` already scans for "model" role (added in Phase 3)
- `active_model` propagated through fork() and delegate_task()
- `set_effort` kept working for backward compat — both tools coexist
- Think-harder/faster commands still use set_effort (can be updated later)
- All 1190 tests pass, lint clean, typecheck clean
- Full replacement: removed `ctx.effort`, `set_effort` tool, `resolve_effort()`, `EFFORT_LEVELS`, think-harder/faster/normal skills
- Updated all call sites: delegate.py, schedules.py, commands.py, health.py, websocket.py, http_server.py
- Archive role changed from "effort" to "model"
- WebSocket handler renamed `_handle_set_effort` → `_handle_set_model`, kept "set_effort" message type as backward compat alias
- Rewrote all affected tests (test_effort.py, test_ws_effort.py, test_web_conversations.py, test_agent_turn.py, test_schedules.py)
- All 1190 tests pass, lint clean, typecheck clean

## Post-Phase 6: Additional cleanup

- Removed `set_model` from agent tools — model selection is user-only (closes #245)
- Replaced web UI effort radio buttons with model dropdown `<select>`
- conversation-store.js: activeModel/availableModels/defaultModel replace effort state
- Dropdown only shows when server reports available model configs

## Phase 7: Cleanup

- Removed `config.models` dict (old effort level mapping) — no callers remain
- Removed unused `replace` import from config.py
- Updated CLAUDE.md: key files list (llm/ package, provider files, model_tools), description, conventions (multi-provider LLM, model selection, scheduled task frontmatter)
- `LlmConfig` and `resolved()` pattern kept for now — still used by migration path and subsystem fallbacks (compaction, reflection, embeddings). Full removal deferred to when those subsystems use named model refs.
