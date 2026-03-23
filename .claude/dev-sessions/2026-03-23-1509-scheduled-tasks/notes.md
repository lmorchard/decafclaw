# Session Notes — Scheduled Tasks

## Session started: 2026-03-23

### Context
- Issue #8: Scheduled tasks (cron-like)
- Extending the existing heartbeat system with cron-style per-task scheduling
- Key insight from discussion: rather than a new subsystem, extend heartbeat
  sections to support cron expressions and point at different prompt files
