# DecafClaw Documentation

## Getting Started

- [Installation & Setup](installation.md) — Prerequisites, configuration, running
- [Deployment](deployment.md) — Systemd service on a Debian VM

## Features

- [Skills System](skills.md) — Agent Skills standard, portable skill packages, native Python + shell-based tools
- [MCP Server Support](mcp-servers.md) — Connect external MCP servers as tool providers (stdio + HTTP)
- [Memory](memory.md) — Persistent memory with tags, substring and semantic search
- [Conversations](conversations.md) — Archive, resume, and compaction of conversation history
- [Semantic Search](semantic-search.md) — Embedding-based search over memories and conversations
- [Eval Loop](eval-loop.md) — Test prompts and tools with real LLM calls
- [Heartbeat](heartbeat.md) — Periodic agent wake-up for monitoring and recurring tasks
- [File Attachments](file-attachments.md) — Upload files, MCP media, workspace image refs, rich cards
- [Streaming](streaming.md) — Stream LLM tokens as they arrive, configurable throttle
- [HTTP Server & Interactive Buttons](http-server.md) — Button-based confirmations, HTTP callback server
- [Sub-Agent Delegation](delegation.md) — Fork child agents for concurrent subtasks
- [User Commands](commands.md) — User-invokable commands (!command / /command) with argument substitution
- [Tool Search / Deferred Loading](tool-search.md) — Defer tool definitions behind search when context budget exceeded

## Architecture

- [Data Layout](data-layout.md) — File structure, admin vs workspace trust boundary
- [Context Map](context-map.md) — System prompt layout, tool definitions, context assembly
- [Original Agent Spec](original-agent-spec.md) — The original design sketch

## Backlogs

- [Backlog Index](backlog/index.md) — Future session ideas organized by layer
