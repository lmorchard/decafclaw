# Developer Infrastructure Backlog

Tools, testing, deployment, and observability.

## Skills system with progressive resource loading

The framework for packaging and loading skills. Skills live in a
directory with a manifest (name, description, trigger conditions,
tools, prompt fragments). Agent sees a lightweight index; loads
resources on demand.

Parallels: Claude Code skills, OpenClaw progressive loading, MCP tool discovery.

## MCP server support

Connect external MCP servers as tool providers.

- Config lists endpoints (stdio or HTTP/SSE)
- Discover tools on startup, merge into registry
- `execute_tool` routes calls to the appropriate server
- Could be bidirectional: DecafClaw exposes its own tools via MCP

## Eval loop for prompt and tool testing

Lightweight YAML-based test harness for prompt/tool changes.

- Fixtures with known memories, assertions on tool calls and responses
- `--model` flag to compare across models
- Cost-controlled: short conversations, cheapest viable model

## Proper linting and testing

- ruff or flake8 replacing per-file `py_compile`
- pytest unit tests for memory, event bus, context, tool dispatch
- CI integration (GitHub Actions)

## Model-specific prompt tuning

Different tool description verbosity per model capability.

- Flash: shorter, punchier ("try 3 query variations")
- Pro: full checklist
- Skills system could select prompt variants by model
- Experiment: quantify Flash vs Pro instruction adherence

## Observability and metrics

Event bus subscriber for metrics: response times, tool usage,
error rates, circuit breaker trips. Log-based or Prometheus.

## Audit log

Persistent event bus subscriber logging every tool call, LLM request,
and agent decision. SQLite or structured log files.

## Health check endpoint

HTTP endpoint returning uptime, active conversations, stats.
Useful once deployed as a persistent service.

## Deployment

- Proxmox VM as persistent service
- Systemd user service (like picoclaw)
- Docker container

## Experiments

Pure learning value — exploring how agent behavior changes.

- Strip the system prompt to nothing — what happens?
- Tool selection as a separate LLM call
- Result verification ("did this answer the question?")
- Context window hygiene — aggressive truncation vs full history
- Move instructions between system prompt and tool descriptions
- Feed SSE stream into prompt — let agent reason about partial results
