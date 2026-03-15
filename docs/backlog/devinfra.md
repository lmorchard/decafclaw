# Developer Infrastructure Backlog

Tools, testing, deployment, and observability.

## Scheduled tasks

Whereas HEARTBEAT.md runs periodically, scheduled tasks can run at specific
times. We could even use the unix system cron for this, but might be
useful to keep this self-contained. Each scheduled task should consist
of its own distinct prompt file fed to the agend at the scheduled time, 
and each should be able to define it's own communication channel for
reporting.

## Linting and CI

Remaining: ruff/flake8 integration, CI integration (GitHub Actions).

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

## Code cleanup

- Mattermost progress subscriber: refactor `elif` chain to dispatch dict pattern

## Experiments

Pure learning value — exploring how agent behavior changes.

- Strip the system prompt to nothing — what happens?
- Tool selection as a separate LLM call
- Result verification ("did this answer the question?")
- Context window hygiene — aggressive truncation vs full history
- Move instructions between system prompt and tool descriptions
- Feed SSE stream into prompt — let agent reason about partial results
