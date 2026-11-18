# Metrics (#10)

Runtime counters and latency for the agent loop, exposed at `GET /metrics` for
a Prometheus scrape. In-memory only — the scrape target owns retention. Fed
entirely by EventBus events the loop already publishes, so there are no metric
call sites in the agent loop itself.

Complements [Observability](observability.md): OpenTelemetry answers "where did
this one turn spend its time," metrics answer "how has the agent behaved in
aggregate" — the latter only for as long as something is scraping, since
nothing here is durable.

## What's measured

`metrics.py` subscribes to three events and derives the per-call and per-tool
counters from them; the token counters arrive from the diagnostics extension
at turn settle.

| Metric | Type | Labels | Source |
|---|---|---|---|
| `llm_calls_total` | counter | `model` | `llm_end` event |
| `llm_call_latency_ms` | histogram | `model` | `llm_end` event |
| `llm_tokens_total` | counter | `model`, `kind` (`prompt`/`cached`/`completion`) | `DiagnosticsAndTelemetryExtension` at `ON_TURN_SETTLE` |
| `tool_calls_total` | counter | `tool`, `outcome` | `tool_end` event |
| `tool_duration_ms` | histogram | `tool`, `outcome` | `tool_end` event |
| `errors_total` | counter | `component`, `name` | `tool_end` event (on error) |
| `loop_breaker_trips_total` | counter | `action` | `loop_breaker` event |

The first two and the tool/loop-breaker metrics come from the event
subscriber. `llm_tokens_total` comes from
[`DiagnosticsAndTelemetryExtension`](extensions.md#diagnostics-and-telemetry-default-on):
it records the turn's final `ctx.tokens` totals once at turn settle (so a
multi-call turn is counted once), and only when `telemetry.metrics_enabled` is
true.

`outcome` comes from `infer_outcome()` in `tool_telemetry.py`, so it matches
what the tool-usage report shows. `action` is the loop-breaker escalation rung
(see [loop-breaker.md](loop-breaker.md)).

The `model` label is the model actually used for the call: the explicit
per-turn override if there was one, else the conversation's pinned model
config, else `config.default_model`. It is deliberately *not* `ctx.active_model`
alone — that is empty unless the conversation pinned a named model config, so
every default-model call would land under `model=""`, which Prometheus cannot
distinguish from the label being absent.

## Scraping

```
GET /metrics
```

Prometheus text format, served from in-memory state by `http_server.py`. The
in-memory counters are updated synchronously as events arrive, so a scrape
immediately after a turn reflects that turn.

Like `/health`, this endpoint is **not** behind the `@_authenticated`
decorator. With the default `http.host = "0.0.0.0"` that means anyone who can
reach the port can read the tool inventory, model-config names, per-tool error
counts, and loop-breaker trips. Bind to localhost or put it behind a reverse
proxy if that matters for your deployment.

Label values are escaped for the text format (backslash, double quote, line
feed). Model-config names are user-supplied, and Prometheus fails the *entire*
scrape on a single parse error, so one oddly-named model would otherwise drop
every metric rather than just its own. The escaping is exposition-only — the
durable records keep values verbatim.

Counters are process-lifetime only: a restart resets them to zero. Prometheus
handles that correctly for `rate()`/`increase()` via counter-reset detection,
but absolute totals are not durable — that is what the JSONL sidecar is for.

## Retention and restarts

There is no sidecar. Metrics live only in process memory, and **Prometheus owns
retention and querying** on its side of the scrape — a local file here would be
a second copy of data the scrape target already stores, which is why #848
removed the JSONL and SQLite surfaces this feature originally shipped with.

The consequence is that counters reset when the process restarts. Prometheus
handles that correctly for `rate()` and `increase()` via counter-reset
detection, so dashboards and alerts built on rates are unaffected. Absolute
lifetime totals across restarts are not available, by design.

If you need history without running Prometheus, the thing to add is a scrape
target — not a sidecar here. The sibling instrumentation files under
`telemetry/` ([tool usage](tools.md#tool-usage-telemetry-310),
[retrieval](context-composer.md#retrieval-telemetry-197), loop breaker) are
per-event records for offline reports, a different job from aggregate counters.

## Recording path

`EventBus.publish` awaits its subscribers inline on the publishing coroutine,
so anything slow in `record_metric` stalls the agent loop — and with it every
concurrent tool call, stream chunk, and WebSocket send. `record_metric` is
therefore allocation-only: it renders the label string and folds the value into
a dict. No I/O, no locks, no thread offload needed.

Every path is fail-open: a metrics error is logged at debug and swallowed.

## Configuration

See [config.md#telemetry](config.md#telemetry) for the full table.

```json
{
  "telemetry": {
    "metrics_enabled": true
  }
}
```

One knob. Set `metrics_enabled: false` (or `TELEMETRY_METRICS_ENABLED=false`)
to skip wiring the subscriber entirely, after which `/metrics` reports nothing.
`retention_days` does not apply — there is nothing on disk to rotate.

## Known gaps

- **Histograms expose no buckets.** Only `_sum` and `_count` are emitted, so
  only an average is derivable. `histogram_quantile()` over
  `llm_call_latency_ms_bucket` returns empty, and the `# TYPE ... histogram`
  line currently overstates what is there.
- **Cancelled LLM calls count as completed.** The cancellation path still
  publishes `llm_end` with the full wall-clock duration, so cancelling a long
  turn inflates the latency average.
- **The OpenAPI schema declares `application/json`** for this endpoint's
  `text/plain` response, so the generated TS client types it as `any` and would
  fail to parse it. Latent until the client is actually imported (#843).
- **No durable totals across restarts.** See above — deliberate, but it means
  `/metrics` is only useful with something actually scraping it on an interval.
