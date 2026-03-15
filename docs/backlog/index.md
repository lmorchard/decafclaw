# DecafClaw — Backlog

Future session ideas and enhancements, organized by architectural layer.

**Design principle:** Skills should be portable across agents (DecafClaw,
OpenClaw, Nanobot, Picoclaw, etc.). They define tools + prompt fragments +
workspace patterns, and rely on the host agent for context, event bus, and
channel delivery. Core modules shape how the agent loop works; skills ride
on top of it.

## Backlogs

- [core.md](core.md) — Agent loop, context, conversation management, infrastructure
- [skills.md](skills.md) — Portable skills: memory, wiki, planning, delegation, etc.
- [mattermost.md](mattermost.md) — Mattermost-specific features
- [devinfra.md](devinfra.md) — Developer tools: testing, eval, deployment, observability

