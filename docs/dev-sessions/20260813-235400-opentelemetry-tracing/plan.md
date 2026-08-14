# OpenTelemetry (OTLP) Native Tracing Plan

**Goal:** Embed OpenTelemetry (OTLP) natively to trace tool calls, LLM requests, and context composition.
**Source:** https://github.com/lmorchard/decafclaw/issues/786

## Phase 1: Configuration & Tracer Setup
**Advances:** C1, C3
**Focus:** Add configuration for OTLP and initialize the tracer provider.
- [x] Add `otlp_endpoint` and `otlp_service_name` to `TelemetryConfig` in `src/decafclaw/config_types.py`.
- [x] Create `src/decafclaw/telemetry.py` to expose a `get_tracer` function.
- [x] Initialize `TracerProvider` with `OTLPSpanExporter` if `otlp_endpoint` is configured, otherwise keep NoOp.
- [x] Verify: `pytest tests/test_telemetry_otlp.py::test_no_otlp_exporter_configured`

## Phase 2: Instrument Core Execution Pathways
**Advances:** C1
**Focus:** Wrap critical pathways in the agent loop with OTLP spans.
- [x] Instrument `ContextComposer.compose` (`src/decafclaw/context_composer.py`).
- [x] Instrument `execute_single_tool` (`src/decafclaw/tool_execution.py`).
- [x] Instrument `TurnRunner._run_iteration` (`src/decafclaw/agent.py`).
- [x] Verify: `pytest tests/test_telemetry_otlp.py::test_otlp_spans_started_on_core_pathways`

## Phase 3: Instrument LLM Client
**Advances:** C2
**Focus:** Wrap LLM requests with OTLP spans.
- [x] Instrument `call_llm` and `call_llm_streaming` in `src/decafclaw/llm/__init__.py`.
- [x] Verify: `pytest tests/test_telemetry_otlp.py::test_llm_client_emits_span`

## Phase 4: Integration Verification
**Advances:** G1
**Focus:** Ensure existing telemetry continues to function without modification.
- [x] Verify: `make test`

## Unresolved Questions
None.
