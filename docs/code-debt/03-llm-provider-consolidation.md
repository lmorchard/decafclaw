# Epic 3 — LLM provider consolidation

Retry/SSE/embed machinery is copy-pasted across three providers and owned by
private names in one of them.

---

### 3.1 — Move retry primitives to `llm/retry.py`

**Size:** S
**Triage:** ready

**Problem.** `_RetryableError`, `_cancellable_sleep`, and `_MAX_RETRIES` live in
`openai_compat.py` but are domain-wide retry infrastructure; `vertex.py` and
`openai_responses.py` import them via private names.

**Result.** The primitives live in a new `llm/retry.py` and `openai_compat.py`
imports them as public names.

**Scope.** In: move the three symbols; update `openai_compat.py`. Out: do not yet
change the other providers' imports (3.2).

**Verify.** `make check` + `make test` — `tests/test_llm_streaming.py`.

**Tests.** None required for a pure move; existing provider tests still pass.

**Deps.** none

---

### 3.2 — Update provider imports to public names

**Size:** XS
**Triage:** blocked-by #3.1

**Problem.** `vertex.py` and `openai_responses.py` still import the retry
primitives from `openai_compat` by private name.

**Result.** Both import from `llm/retry.py`.

**Scope.** In: import statements in `vertex.py` and `openai_responses.py`. Out:
nothing else.

**Verify.** `make check` + `make test`.

**Tests.** None; existing provider tests pass.

**Deps.** blocked-by #3.1

---

### 3.3 — Extract a shared streaming retry loop

**Size:** M
**Triage:** ready

**Problem.** The backoff/retry/attempt loop is near-identical across
`openai_compat`, `vertex`, and `openai_responses`, differing only in logging prefix.

**Result.** One streaming retry helper parameterized by an attempt coroutine and a
chunk normalizer; all three providers call it.

**Scope.** In: the retry loop only. Out: do not yet merge the `StreamState`
classes (3.4) or `embed` (3.5).

**Verify.** `make test` — `tests/test_llm_streaming.py` +
`tests/test_provider_registry.py`.

**Tests.** Existing streaming retry/cancellation tests pass across all providers.

**Deps.** none (builds on 3.1)

---

### 3.4 — Extract a shared `StreamState` base

**Size:** M
**Triage:** ready

**Problem.** `_StreamState` / `_VertexStreamState` / `_ResponsesState` duplicate
`record_event`, diagnostic trimming, `finalize()` SSE-dump, and `emit`.

**Result.** One generic `StreamState` base, with each provider subclassing only
its chunk-normalization path.

**Scope.** In: the three state classes. Out: retry loop (3.3) and embed (3.5).

**Verify.** `make test` — `tests/test_llm_streaming.py`.

**Tests.** Existing diagnostic/emit assertions pass unchanged.

**Deps.** none

---

### 3.5 — Deduplicate `embed()`

**Size:** M
**Triage:** ready

**Problem.** Embedding transport is triplicated across `vertex._complete`,
`openai_compat.embed`, and a legacy fallback in `embeddings.py`, with divergent
retry/swallow behavior.

**Result.** One shared embedding transport; the providers delegate to it, and
`embeddings.py` routes through a provider's `embed` instead of raw httpx.

**Scope.** In: the three embed paths. Out: no change to embedding *inputs* or
callers.

**Verify.** `make test` — `tests/test_embeddings.py`.

**Tests.** Existing embedding retry/error tests pass; update the provider tests.

**Deps.** none

---

### 3.6 — Use `FINISH_REASON_*` constants in providers

**Size:** S
**Triage:** ready

**Problem.** `FINISH_REASON_*` (`llm/types.py`) are declared but only the scripted
test provider uses them; providers write raw `"length"`/`"stop"`/`"cancelled"` etc.

**Result.** All providers reference the constants, eliminating the typo class
(`"cancelled"` vs `"canceled"`).

**Scope.** In: finish-reason literals in `vertex.py`, `openai_compat.py`,
`openai_responses.py`. Out: no mapping change.

**Verify.** `make test`.

**Tests.** Existing finish-reason tests pass unchanged.

**Deps.** none

---

### 3.7 — Deduplicate `vertex.py` finish-reason / tool-call construction

**Size:** S
**Triage:** ready

**Problem.** Finish-reason mapping and tool-call dict construction are duplicated
across `vertex.py`'s streaming (`process_chunk`/`finalize`) and non-streaming
(`_parse_response`) paths, including 4× `thought_signature` chains.

**Result.** Shared `_finish_reason(raw)` and `_tool_call_from_fc(fc)` helpers used
by both paths.

**Scope.** In: `vertex.py`. Out: no wire-protocol change.

**Verify.** `make test` — `tests/test_vertex_translation.py` /
`tests/test_vertex_endpoint.py`.

**Tests.** Existing translation tests pass unchanged.

**Deps.** none

---

### 3.8 — Normalize `get_model_info`

**Size:** M
**Triage:** ready

**Problem.** `openai_compat.get_model_info` returns `None` unconditionally (dead),
while generic `ensure_model_context_window` reads provider-specific raw wire keys
(`inputTokenLimit`/`context_window_size`/...).

**Result.** `get_model_info` returns a normalized typed dict per provider, and
`ensure_model_context_window` consumes it instead of the raw-key fallback chain.

**Scope.** In: `get_model_info` implementations + the generic resolver. Out: no
catalog behavior change.

**Verify.** `make test` — `tests/test_model_catalog.py` +
`tests/test_provider_registry.py`.

**Tests.** Update/add cases asserting each provider's normalized info drives the
context-window resolution.

**Deps.** none

---

### 3.9 — Unify empty-response classification

**Size:** S
**Triage:** ready

**Problem.** `vertex.py` classifies a no-candidate response as `"error"` in
`_parse_response` but leaves it `None` in the streaming `finalize` path.

**Result.** Both paths classify an empty/absent-candidate response identically.

**Scope.** In: `vertex.py` finish-reason handling. Out: no agent-loop change.

**Verify.** `make test` — `tests/test_vertex_translation.py`.

**Tests.** Add a case asserting both streaming and non-streaming empty responses
produce the same finish reason.

**Deps.** none (builds on 3.7)