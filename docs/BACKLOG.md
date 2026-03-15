# DecafClaw — Backlog

Future session ideas and enhancements, organized by architectural layer.

**Design principle:** Skills should be portable across agents (DecafClaw,
OpenClaw, Nanobot, Picoclaw, etc.). They define tools + prompt fragments +
workspace patterns, and rely on the host agent for context, event bus, and
channel delivery. Core modules shape how the agent loop works; skills ride
on top of it.

## Backlogs

- [BACKLOG-CORE.md](BACKLOG-CORE.md) — Agent loop, context, conversation management, infrastructure
- [BACKLOG-SKILLS.md](BACKLOG-SKILLS.md) — Portable skills: memory, wiki, planning, delegation, etc.
- [BACKLOG-MATTERMOST.md](BACKLOG-MATTERMOST.md) — Mattermost-specific features
- [BACKLOG-DEVINFRA.md](BACKLOG-DEVINFRA.md) — Developer tools: testing, eval, deployment, observability

