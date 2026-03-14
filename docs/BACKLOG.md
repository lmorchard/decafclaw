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

## Done

- ~~Live tool progress in placeholder messages~~ — event bus, runtime context, async agent loop
- ~~User memory (basic)~~ — file-based markdown memories with save/search/recent tools
- ~~Conversation compaction~~ — archive to JSONL, summarize via compaction LLM
- ~~Conversation resume~~ — replay archive on restart
- ~~Graceful shutdown~~ — SIGTERM/SIGINT, wait for in-flight turns
- ~~Eval loop~~ — YAML tests, failure reflection, model comparison
- ~~Semantic search~~ — embeddings via text-embedding-004, SQLite cosine similarity
- ~~Think tool~~ — hidden reasoning scratchpad
- ~~Per-conversation to-do list~~ — markdown checkboxes on disk
- ~~Conversation search~~ — semantic search over archived messages
- ~~Prompt files~~ — SOUL.md + AGENT.md + USER.md split
- ~~Data layout refactor~~ — admin/workspace boundary
- ~~LOG_LEVEL wired up~~ — env var works
- ~~Glob-based lint~~ — auto-discovers .py files
- ~~pytest test suite~~ — 54 tests
- ~~Per-agent memory~~ — dropped per-user directory
- ~~Entry-aware memory search~~ — returns whole entries
