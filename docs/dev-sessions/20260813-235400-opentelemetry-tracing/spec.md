**Concept from opencode:**
Agent frameworks are difficult to debug. `opencode` embeds **OpenTelemetry (OTLP)** natively. Tool calls, LLM requests, and database transactions emit distributed trace spans, allowing developers to visualize waterfall execution graphs in tools like Jaeger or Honeycomb.

**How `decafclaw` could implement this:**
`decafclaw` uses a homegrown telemetry solution (subscribing to `EventBus` for `tool_telemetry` and `reflection_metrics`) and relies on `LOG_LEVEL=DEBUG`, which is noisy and hard to analyze for performance bottlenecks.

**Proposed Implementation:**
- Integrate `opentelemetry-api` and `opentelemetry-sdk` into the Python backend.
- Instrument critical pathways (e.g., `ContextComposer.compose`, `execute_single_tool`, `TurnRunner._run_iteration`, and the LLM client) with `@tracer.start_as_current_span()`.
- Provide an optional OTLP exporter configuration so users/developers can easily spin up a local Jaeger container to visually debug slow agent turns or loop-breaker triggers.

### Design decisions
- **Decision:** Proceed with adding OpenTelemetry dependencies (`opentelemetry-api` and `opentelemetry-sdk`).
- **Why:** The proposed implementation relies on these dependencies. The human reviewer explicitly approved adding them in the ratification pass.

### Acceptance Criteria

- CRITERION: WHEN an OTLP exporter is configured, the system SHALL start an OpenTelemetry trace span for each agent turn, tool execution, and context composition.
  CHECK: `pytest tests/test_telemetry_otlp.py::test_otlp_spans_started_on_core_pathways` passes (asserts using a `MemorySpanExporter` that spans are created for `run_iteration`, `execute_single_tool`, and `ContextComposer.compose`).

- CRITERION: WHEN an OTLP exporter is configured, the LLM client SHALL emit OTLP spans for its requests.
  CHECK: `pytest tests/test_telemetry_otlp.py::test_llm_client_emits_span` passes (asserts span creation wrapping the LLM provider call).

- CRITERION: WHEN an OTLP exporter is NOT configured, the system SHALL use a NoOp tracer and SHALL NOT attempt to export spans.
  CHECK: `pytest tests/test_telemetry_otlp.py::test_no_otlp_exporter_configured` passes.

### Regression Guards

- GUARD: The existing homegrown telemetry (e.g., `tool_telemetry`, `reflection_metrics`) over `EventBus` SHALL continue to function without modification.
  CHECK: The existing test suite (`make test`) stays green, meaning no `tool_telemetry` or `EventBus` tests fail or are newly skipped.

## Tier: auto-ok
**Reason:** The human reviewer (lmorchard) explicitly approved adding `opentelemetry-api` and `opentelemetry-sdk` dependencies in the issue comments. All acceptance criteria have runnable `pytest` checks.

