# DecafClaw Documentation

## Getting Started

- [Installation & Setup](installation.md) — Prerequisites, configuration, running
- [Configuration Reference](config.md) — Config file, env vars, CLI tool, all settings
- [Deployment](deployment.md) — Systemd service on a Debian VM

## Interfaces

- [Web UI](web-ui.md) — Browser-based chat, vault editor, conversation management, model picker
- [Files tab](files-tab.md) — Web UI workspace browser: browse, view, and edit agent workspace files
- [Streaming](streaming.md) — Stream LLM tokens as they arrive, configurable throttle
- [HTTP Server & Interactive Buttons](http-server.md) — Mattermost button confirmations, HTTP callback server

## Knowledge & Memory

- [Vault](vault.md) — Unified knowledge base: curated pages, journal entries, user notes
- [Semantic Search](semantic-search.md) — Embedding-based search over vault pages, journal, and conversations
- [Dream Consolidation](dream-consolidation.md) — Periodic journal review and vault page gardening
- [Conversations](conversations.md) — Archive, resume, and compaction of conversation history

## Tools & Skills

- [Tools Reference](tools.md) — All built-in tools, grouped by module, with one-line descriptions
- [Skills System](skills.md) — Agent Skills standard, portable skill packages, native Python + shell-based tools
- [MCP Server Support](mcp-servers.md) — Connect external MCP servers as tool providers (stdio + HTTP)
- [Tool Priority System](tool-priority.md) — Declarative critical/normal/low tiers for tool visibility
- [Tool Search / Deferred Loading](tool-search.md) — Defer tool definitions behind search when context budget exceeded
- [Pre-emptive Tool Search](preemptive-tool-search.md) — Keyword-match user message at turn start to auto-promote relevant tools
- [User Commands](commands.md) — User-invokable commands (!command / /command) with argument substitution
- [Project Skill](project-skill.md) — Structured workflow: brainstorm → spec → plan → execute for multi-step tasks
- [Postmortem Skill](postmortem-skill.md) — User-invokable blameless RCA: structured report on what went wrong, archived to the vault
- [Ingest Skill](ingest-skill.md) — User-invokable one-shot ingestion: fetch a URL/file/attachment, synthesize it into vault pages
- [File Attachments](file-attachments.md) — Upload files, MCP media, workspace image refs, rich cards

## Agent Behavior

- [Self-Reflection](reflection.md) — Binary judge + critique + retry before delivering responses (Reflexion pattern)
- [Sub-Agent Delegation](delegation.md) — Fork child agents for concurrent subtasks
- [Heartbeat](heartbeat.md) — Periodic agent wake-up for monitoring and recurring tasks
- [Scheduled Tasks](schedules.md) — Cron-style per-task scheduling with model, tool, and skill configuration
- [Eval Loop](eval-loop.md) — Test prompts and tools with real LLM calls
- [Notifications](notifications.md) — Inbox for agent-initiated events: heartbeat, schedule, background-job, compaction, reflection
- [Email](email.md) — Outbound email via SMTP: agent tool (`send_email`) + notification channel, shared SMTP core
- [Background Job Agent Wake](background-wake.md) — Agent wake turns when background processes exit: archive record, tool-result framing, sentinel, rate limiting

## LLM Configuration

- [LLM Providers](providers.md) — Multi-provider LLM support: Vertex/Gemini, OpenAI, LiteLLM-compat, service accounts
- [Model Selection](model-selection.md) — Named model configs, per-conversation model switching

## Architecture

- [Architecture Overview](architecture.md) — Narrative walkthrough: entry points, Context, EventBus, agent turn lifecycle, tool concurrency
- [Data Layout](data-layout.md) — File structure, admin vs workspace trust boundary
- [Context Composer](context-composer.md) — Context assembly, vault retrieval, relevance scoring, token budget
- [Original Agent Spec](original-agent-spec.md) — The original design sketch
