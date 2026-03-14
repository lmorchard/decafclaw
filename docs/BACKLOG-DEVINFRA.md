# Developer Infrastructure Backlog

Tools, testing, deployment, and observability.

## ~~System prompt as Markdown files~~ (DONE)

Implemented: SOUL.md + AGENT.md bundled, USER.md workspace override.

## ~~Prompts as independent Markdown files~~ (DONE)

Implemented: prompts/ package with bundled files, workspace overrides.

## Skills system with progressive resource loading

The framework for packaging and loading skills, designed for
portability across agents (DecafClaw, OpenClaw, Nanobot, Picoclaw).

**Approach: build tools first, extract framework second.**
Build 2-3 more skills as plain tools (to-do list, chain-of-thought,
wiki). See which ones the agent actually uses well and which gather
dust. The winners earn portability. Design the framework from real
usage patterns, not theory. Migration cost is low — moving a tool
into a skill directory is a refactor, not a rewrite.

**Structure (inspired by Claude Code's SKILL.md pattern):**
```
skills/
  memory/
    SKILL.md            # Manifest: name, description, frontmatter
    tools.py            # Tool functions
    reference.md        # Detailed docs (loaded on demand)
    examples.md         # Usage examples (loaded on demand)
```

**Key design elements (from Claude Code analysis):**
- `SKILL.md` as entrypoint with YAML frontmatter (name, description,
  allowed tools, invocation control)
- Description-based auto-discovery — agent sees lightweight index of
  skill descriptions in system prompt, full content loads on demand
- Supporting files loaded progressively — SKILL.md references them,
  agent reads only when needed. Keeps base context lean.
- Two invocation modes: user-invoked (slash commands in terminal) and
  model-invoked (agent decides based on description match)

**What to simplify vs Claude Code:**
- Skip subagent forking initially (we already have context.fork())
- Skip shell command injection in skill content
- Skip per-skill tool permission restrictions
- Start with: skill directory, SKILL.md manifest, tool registration,
  description-based auto-discovery

**Portability interface — what a host agent must provide:**
- Context object with user/channel/thread metadata
- Event bus for progress publishing
- Workspace directory for file storage
- Tool registry that accepts skill-provided tools
- System prompt injection point for skill descriptions

Parallels: Claude Code skills, OpenClaw progressive loading, MCP tool
discovery, Agent Skills open standard (agentskills.io).

## MCP server support

Connect external MCP servers as tool providers.

- Config lists endpoints (stdio or HTTP/SSE)
- Discover tools on startup, merge into registry
- `execute_tool` routes calls to the appropriate server
- Could be bidirectional: DecafClaw exposes its own tools via MCP

## ~~Eval loop~~ (DONE)

Implemented: YAML tests, multi-turn, failure reflection, judge model,
result bundles, allowed_tools constraints. 12+ eval cases.

## ~~Proper linting and testing~~ (MOSTLY DONE)

Implemented: glob-based py_compile, pytest with 54 tests.
Remaining: ruff/flake8, CI integration (GitHub Actions).

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

## Code cleanup

- Mattermost progress subscriber: refactor `elif` chain to dispatch dict pattern
- ~~Makefile lint target: glob instead of explicit file list~~ (DONE)

## Experiments

Pure learning value — exploring how agent behavior changes.

- Strip the system prompt to nothing — what happens?
- Tool selection as a separate LLM call
- Result verification ("did this answer the question?")
- Context window hygiene — aggressive truncation vs full history
- Move instructions between system prompt and tool descriptions
- Feed SSE stream into prompt — let agent reason about partial results
