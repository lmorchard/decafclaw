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

### 3.3a — Extract a shared streaming retry helper; adopt in `openai_compat`

**Size:** S
**Triage:** ready

**Problem.** The backoff/retry/attempt loop is near-identical across
`openai_compat`, `vertex`, and `openai_responses`. `openai_compat` owns a private
copy.

**Result.** One streaming retry helper (in `llm/retry.py`) parameterized by an
attempt coroutine and a chunk normalizer; `openai_compat` calls it.

**Scope.** In: define the helper; adopt it in `openai_compat` only. Out: the other
providers (3.3b/3.3c), the `StreamState` classes (3.4), and `embed` (3.5).

**Verify.** `make test` — `tests/test_llm_streaming.py`.

**Tests.** Existing `openai_compat` retry/cancellation tests pass unchanged.

**Deps.** none (builds on 3.1)

---

### 3.3b — Adopt the shared retry helper in `vertex`

**Size:** S
**Triage:** blocked-by #3.3a

**Result.** `vertex` replaces its inline retry loop with the helper from 3.3a.

**Scope.** In: `vertex` retry loop only. Out: chunk normalization stays in `vertex`.

**Verify.** `make test` — `tests/test_llm_streaming.py`.

**Tests.** Existing `vertex` retry/cancellation tests pass unchanged.

**Deps.** blocked-by #3.3a

---

### 3.3c — Adopt the shared retry helper in `openai_responses`

**Size:** S
**Triage:** blocked-by #3.3a

**Result.** `openai_responses` replaces its inline retry loop with the helper from
3.3a.

**Scope.** In: `openai_responses` retry loop only. Out: its error-type behavior
(unchanged from 2.5).

**Verify.** `make test` — `tests/test_llm_streaming.py`.

**Tests.** Existing `openai_responses` retry tests pass unchanged.

**Deps.** blocked-by #3.3a

---

### 3.4a — Extract a shared `StreamState` base; adopt in `openai_compat`

**Size:** S
**Triage:** ready

**Problem.** `_StreamState` / `_VertexStreamState` / `_ResponsesState` duplicate
`record_event`, diagnostic trimming, `finalize()` SSE-dump, and `emit`.

**Result.** A generic `StreamState` base; `openai_compat` subclasses it with only
its chunk-normalization path.

**Scope.** In: define the base; refactor `openai_compat`'s state class onto it.
Out: the other providers (3.4b/3.4c), retry loop (3.3), embed (3.5).

**Verify.** `make test` — `tests/test_llm_streaming.py`.

**Tests.** Existing `openai_compat` diagnostic/emit assertions pass unchanged.

**Deps.** none

---

### 3.4b — Adopt the shared `StreamState` base in `vertex`

**Size:** S
**Triage:** blocked-by #3.4a

**Result.** `vertex` replaces `_VertexStreamState` with a subclass of the 3.4a
base.

**Verify.** `make test` — `tests/test_llm_streaming.py`.

**Tests.** Existing `vertex` diagnostic/emit tests pass unchanged.

**Deps.** blocked-by #3.4a

---

### 3.4c — Adopt the shared `StreamState` base in `openai_responses`

**Size:** S
**Triage:** blocked-by #3.4a

**Result.** `openai_responses` replaces `_ResponsesState` with a subclass of the
3.4a base.

**Verify.** `make test` — `tests/test_llm_streaming.py`.

**Tests.** Existing `openai_responses` diagnostic/emit tests pass unchanged.

**Deps.** blocked-by #3.4a

---

### 3.5a — Extract a shared embedding transport; adopt in `vertex`

**Size:** S
**Triage:** ready

**Problem.** Embedding transport is triplicated across `vertex._complete`,
`openai_compat.embed`, and a legacy fallback in `embeddings.py`, with divergent
retry/swallow behavior.

**Result.** One shared embedding transport helper; `vertex` delegates to it.

**Scope.** In: define the helper; adopt in `vertex`. Out: the other providers
(3.5b/3.5c). No change to embedding inputs or callers.

**Verify.** `make test` — `tests/test_embeddings.py`.

**Tests.** Existing `vertex` embedding retry/error tests pass unchanged.

**Deps.** none

---

### 3.5b — Adopt the shared embedding transport in `openai_compat`

**Size:** S
**Triage:** blocked-by #3.5a

**Result.** `openai_compat.embed` delegates to the 3.5a helper.

**Verify.** `make test` — `tests/test_embeddings.py`.

**Tests.** Existing `openai_compat` embedding tests pass unchanged.

**Deps.** blocked-by #3.5a

---

### 3.5c — Route `embeddings.py` legacy path through the shared transport

**Size:** S
**Triage:** blocked-by #3.5a

**Result.** The `embeddings.py` fallback routes through a provider's `embed`
instead of raw httpx.

**Verify.** `make test` — `tests/test_embeddings.py`.

**Tests.** Existing legacy-embedding tests pass; update the fallback assertions.

**Deps.** blocked-by #3.5a

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