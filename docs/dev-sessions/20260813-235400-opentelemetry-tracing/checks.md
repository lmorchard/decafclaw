# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/786
**Frozen at:** 2a51bee218665527abefbf88d7cc2d85667c1b9a (2026-08-13)
**Check files — read-only from Phase 1 onward:**
- `tests/test_telemetry_otlp.py`

## C1
CRITERION: WHEN an OTLP exporter is configured, the system SHALL start an OpenTelemetry trace span for each agent turn, tool execution, and context composition.
CHECK: `pytest tests/test_telemetry_otlp.py::test_otlp_spans_started_on_core_pathways` passes (asserts using a `MemorySpanExporter` that spans are created for `run_iteration`, `execute_single_tool`, and `ContextComposer.compose`).
AT FREEZE: fails - FileNotFoundError or similar because file doesn't exist yet.

## C2
CRITERION: WHEN an OTLP exporter is configured, the LLM client SHALL emit OTLP spans for its requests.
CHECK: `pytest tests/test_telemetry_otlp.py::test_llm_client_emits_span` passes (asserts span creation wrapping the LLM provider call).
AT FREEZE: fails

## C3
CRITERION: WHEN an OTLP exporter is NOT configured, the system SHALL use a NoOp tracer and SHALL NOT attempt to export spans.
CHECK: `pytest tests/test_telemetry_otlp.py::test_no_otlp_exporter_configured` passes.
AT FREEZE: fails

## Guards
- G1: `make test` — existing test suite stays green, meaning no tool_telemetry or EventBus tests fail or are newly skipped. Passed at freeze.

## Adjudication
- C1: accepted - writing tests with MemorySpanExporter is the standard way to verify OTLP span emission.
- C2: accepted - standard test wrapping the provider complete method and verifying trace emission.
- C3: accepted - verifies the tracer falls back to NoOp and memory exporter remains empty if not configured.
- G1: accepted - ensures we don't break existing telemetry.

## Amendments
