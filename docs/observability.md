# Observability and Tracing

DecafClaw supports native **OpenTelemetry (OTLP)** tracing to provide deep visibility into the agent's internal execution pathways.

Agent frameworks are often difficult to debug, as a single prompt might yield multiple cascading tool calls, sub-agent delegations, and semantic retrieval loops. OpenTelemetry allows developers to visualize these waterfall execution graphs in tools like Jaeger, Honeycomb, or DataDog.

## What is instrumented?

When OpenTelemetry is enabled, DecafClaw automatically creates distributed trace spans for:
- **Agent Loop Iterations:** (`TurnRunner._run_iteration`) Captures the time taken for a single pass of the agent loop (LLM call + tool execution dispatch).
- **Context Composition:** (`ContextComposer.compose`) Measures the time taken to assemble the system prompt, including vault semantic search, notes injection, and token budgeting.
- **Tool Execution:** (`execute_single_tool`) Tracks individual tool executions, including failures or retries.
- **LLM Client Requests:** (`call_llm`, `call_llm_streaming`) Captures the duration and metadata (like `model_name`) for requests made to the underlying LLM providers (Vertex, OpenAI, etc.).

## Configuration

OpenTelemetry tracing is disabled by default (using a NoOp tracer). To enable it, configure an OTLP endpoint in your `config.json` or via environment variables.

### `config.json`
```json
{
  "telemetry": {
    "otlp_endpoint": "http://localhost:4317",
    "otlp_service_name": "decafclaw-local"
  }
}
```

### Environment Variables
```bash
TELEMETRY_OTLP_ENDPOINT=http://localhost:4317
TELEMETRY_OTLP_SERVICE_NAME=decafclaw-local
```

If `otlp_endpoint` is set, DecafClaw initializes an `OTLPSpanExporter` that sends trace data over gRPC or HTTP (depending on the endpoint scheme and available OTel packages) to the collector.

## Existing Homegrown Telemetry

DecafClaw also maintains homegrown, file-based telemetry for usage and cost analysis (e.g., `tool_usage.jsonl`, `retrieval.jsonl`, `reflection_metrics.jsonl`). 

OpenTelemetry tracing **supplements** rather than replaces these sidecars. The homegrown JSONL logs are used for historical reports (`make tool-usage-report`), while OpenTelemetry provides real-time performance bottleneck analysis and distributed tracing.
