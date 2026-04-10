# DecafClaw Documentation

## Getting Started

- [Installation & Setup](installation.md) — Prerequisites, configuration, running
- [Configuration Reference](config.md) — Config file, env vars, CLI tool, all settings
- [Deployment](deployment.md) — Systemd service on a Debian VM

## Features

- [Skills System](skills.md) — Agent Skills standard, portable skill packages, native Python + shell-based tools
- [MCP Server Support](mcp-servers.md) — Connect external MCP servers as tool providers (stdio + HTTP)
- [Vault](vault.md) — Unified knowledge base: curated pages, journal entries, user notes
- [Proactive Memory Context](memory-context.md) — Automatically surface relevant vault content per turn
- [Relevance Scoring](relevance-scoring.md) — Three-factor scoring, graph expansion, dynamic budget allocation
- [Dream Consolidation](dream-consolidation.md) — Periodic journal review and vault page gardening
- [Conversations](conversations.md) — Archive, resume, and compaction of conversation history
- [Semantic Search](semantic-search.md) — Embedding-based search over memories and conversations
- [Eval Loop](eval-loop.md) — Test prompts and tools with real LLM calls
- [Heartbeat](heartbeat.md) — Periodic agent wake-up for monitoring and recurring tasks
- [Scheduled Tasks](schedules.md) — Cron-style per-task scheduling with model, tool, and skill configuration
- [File Attachments](file-attachments.md) — Upload files, MCP media, workspace image refs, rich cards
- [Streaming](streaming.md) — Stream LLM tokens as they arrive, configurable throttle
- [HTTP Server & Interactive Buttons](http-server.md) — Button-based confirmations, HTTP callback server
- [Sub-Agent Delegation](delegation.md) — Fork child agents for concurrent subtasks
- [Project Skill](project-skill.md) — Structured workflow: brainstorm → spec → plan → execute for multi-step tasks
- [User Commands](commands.md) — User-invokable commands (!command / /command) with argument substitution
- [Tool Search / Deferred Loading](tool-search.md) — Defer tool definitions behind search when context budget exceeded
- [Self-Reflection](reflection.md) — Binary judge + critique + retry before delivering responses (Reflexion pattern)
- [LLM Providers](providers.md) — Multi-provider LLM support: Vertex/Gemini, OpenAI, LiteLLM-compat, service accounts
- [Model Selection](model-selection.md) — Named model configs, per-conversation model switching, migration from effort levels

## Architecture

- [Data Layout](data-layout.md) — File structure, admin vs workspace trust boundary
- [Context Composer](context-composer.md) — Unified context assembly pipeline for agent turns
- [Context Map](context-map.md) — System prompt layout, tool definitions, context assembly
- [Original Agent Spec](original-agent-spec.md) — The original design sketch

## Backlogs

- [Backlog Index](backlog/index.md) — Future session ideas organized by layer
