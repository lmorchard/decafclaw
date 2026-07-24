# Tools Reference

All built-in tools the agent can call, grouped by module. Skills and MCP servers provide additional tools on demand.

Tools marked **critical** (✓) are always sent to the LLM — these are the minimum set needed for common tasks. Other tools are `normal` priority (filled in as budget allows) or `low` (fetched on demand via `tool_search`). See [Tool Priority System](tool-priority.md) and [Tool Search](tool-search.md).

## Core (`tools/core.py`)

| Tool | Always | What it does |
|------|:------:|--------------|
| `web_fetch` | ✓ | Fetch raw HTML from a URL |
| `current_time` | ✓ | Get current date and time |
| `wait` | | Pause the agent for a specified number of seconds |
| `debug_context` | | Dump current context as JSON file attachments |
| `context_stats` | | Show token budget breakdown and diagnostics |

## Workspace (`tools/workspace_tools.py`)

Sandboxed file operations inside `data/{agent_id}/workspace/`. See [Data Layout](data-layout.md).

| Tool | Always | What it does |
|------|:------:|--------------|
| `workspace_read` | ✓ | Read a file (supports line ranges) |
| `workspace_write` | ✓ | Write/overwrite a file, creating parents |
| `workspace_list` | | List files and directories |
| `workspace_append` | | Append content to a file |
| `workspace_edit` | | Exact string replacement in a file |
| `workspace_insert` | | Insert text at a specific line number |
| `workspace_replace_lines` | | Replace or delete a range of lines |
| `workspace_search` | | Regex search across workspace files |
| `workspace_glob` | | Find files by name/glob pattern |
| `workspace_move` | | Move or rename a file |
| `workspace_delete` | | Delete a file |
| `workspace_diff` | | Unified diff between two files |
| `file_share` | | Share a workspace file as a Mattermost attachment |

## Vault (`skills/vault/tools.py`)

Always-activated skill for the unified knowledge base. See [Vault](vault.md).

| Tool | What it does |
|------|--------------|
| `vault_read` | Read a vault page by name or path |
| `vault_write` | Create or overwrite a vault page; auto-indexes in embeddings |
| `vault_journal_append` | Append a timestamped journal entry |
| `vault_search` | Semantic + substring search across the vault |
| `vault_list` | List pages with last-modified dates |
| `vault_backlinks` | Find pages linking to a given page |

## Conversation (`tools/conversation_tools.py`)

| Tool | What it does |
|------|--------------|
| `conversation_search` | Search past conversation archives (stemmed word overlap + substring) |
| `conversation_compact` | Manually trigger conversation compaction |

## Checklist (`tools/checklist_tools.py`)

Per-conversation step-by-step execution loop. Storage is markdown checkboxes at `workspace/todos/{conv_id}.md`.

| Tool | Always | What it does |
|------|:------:|--------------|
| `checklist_create` | ✓ | Create a new checklist from a list of steps |
| `checklist_step_done` | ✓ | Mark the current step done and advance |
| `checklist_abort` | ✓ | Abort the current checklist |
| `checklist_status` | ✓ | Show current checklist state |

## Shell (`tools/shell_tools.py`)

Requires user confirmation unless pre-approved via `shell_allow_patterns.json`.

| Tool | Always | What it does |
|------|:------:|--------------|
| `shell` | ✓ | Run a shell command (requires confirmation) |
| `shell_patterns` | | Manage the approved shell command allow list |

Background process management (`shell_background_start/status/stop/list`) lives in the bundled `background` skill (auto-activates) — see [Skills](skills.md).

### Approval sources

`check_shell_approval()` is the single chokepoint — don't duplicate its checks. It approves a command if any of these hold, in order:

1. The turn is an admin heartbeat (`user_id == "heartbeat-admin"`).
2. `shell` (or the calling tool name) is in `ctx.tools.preapproved` — blanket approval from a command's `allowed-tools`.
3. The command matches a **scoped pattern** from a skill's `allowed-tools: shell(...)` (see [Skills](skills.md#environment-for-shell-based-skills)).
4. The command matches a **persisted pattern** in `data/{agent_id}/shell_allow_patterns.json`.

Otherwise it falls through to a user confirmation, which offers to save a suggested pattern.

### Wildcard patterns never match chained commands

Approving a command offers to persist a *wildcarded* pattern — approving `python foo.py --a` suggests `python foo.py *`. Because fnmatch's `*` spans `;`, `|`, `&&`, `||`, backticks, `$(`, and newlines, a naive match would let one approval authorize everything sharing that prefix, including `python foo.py --a; rm -rf ~`.

So `_command_matches_pattern` enforces: **a pattern containing a glob wildcard (`*`, `?`, `[`) will not match a command containing shell chaining tokens.** Such a command falls through to confirmation instead.

Literal patterns are exempt — they pin the command end to end, so there's no wildcard for an unapproved suffix to slip through. A user who allowlists `git log | head -20` gets exactly that command and nothing else.

The guard lives inside `_command_matches_pattern` rather than at the call sites, so both the scoped and persisted branches get it automatically. It previously sat at one call site only, and the persisted branch was missing it ([#649](https://github.com/lmorchard/decafclaw/issues/649)).

## HTTP (`tools/http_tools.py`)

| Tool | What it does |
|------|--------------|
| `http_request` | General-purpose HTTP request (all methods, headers, body; URL allowlist) |

## Attachments (`tools/attachment_tools.py`)

Conversation file attachments (uploaded via Mattermost or web UI).

| Tool | What it does |
|------|--------------|
| `list_attachments` | List files attached to the current conversation |
| `get_attachment` | Read an attachment's content |

## Delegation (`tools/delegate.py`)

See [Sub-Agent Delegation](delegation.md).

| Tool | Always | What it does |
|------|:------:|--------------|
| `delegate_task` | ✓ | Fork a child agent for a subtask (call multiple times for parallel work) |

## Skills (`tools/skill_tools.py`)

See [Skills System](skills.md).

| Tool | Always | What it does |
|------|:------:|--------------|
| `activate_skill` | ✓ | Load a skill's tools into the current conversation |
| `refresh_skills` | | Re-scan skill directories without restarting |

## MCP (`skills/mcp/tools.py`)

MCP admin tools live in the bundled `mcp` skill (auto-activates). See [MCP Server Support](mcp-servers.md).

| Tool | What it does |
|------|--------------|
| `mcp_status` | Show or restart MCP server connections |
| `mcp_list_resources` | List resources exposed by MCP servers |
| `mcp_read_resource` | Read a resource from an MCP server |
| `mcp_list_prompts` | List prompts exposed by MCP servers |
| `mcp_get_prompt` | Get a prompt from an MCP server |

## Tool search (`tools/search_tools.py`)

See [Tool Search](tool-search.md).

| Tool | What it does |
|------|--------------|
| `tool_search` | Keyword or exact-name lookup for deferred tools |

## Health (`tools/health.py`)

| Tool | What it does |
|------|--------------|
| `health_status` | Uptime, MCP status, heartbeat, tool count, embeddings stats |

## Heartbeat (`tools/heartbeat_tools.py`)

See [Heartbeat](heartbeat.md).

| Tool | What it does |
|------|--------------|
| `heartbeat_trigger` | Manually fire a heartbeat cycle |

## Project skill (`skills/project/tools.py`)

Structured workflow skill. See [Project Skill](project-skill.md). Dynamic tool loading — only phase-appropriate tools are visible per turn.

| Tool | What it does |
|------|--------------|
| `project_create` | Create a new project |
| `project_status` | Check current state and progress |
| `project_list` | List all projects |
| `project_switch` | Switch to a different project |
| `project_next_task` | Get the next actionable step |
| `project_task_done` | Mark the current phase's work complete |
| `project_update_spec` | Write/update the spec |
| `project_update_plan` | Write/update the plan |
| `project_update_step` | Update a step's status |
| `project_add_steps` | Insert new steps into the plan |
| `project_advance` | Move to next phase (or backward) |
| `project_note` | Append a timestamped note |

## Bundled skills with tools

These skills ship with DecafClaw and provide tools when activated. Full details in each skill's doc.

- **[Tabstack](skills.md#tabstack)** — web browsing/research: `tabstack_extract_markdown`, `tabstack_extract_json`, `tabstack_generate`, `tabstack_automate`, `tabstack_research`
- **[Claude Code](skills.md#claude_code)** — delegate coding tasks: `claude_code_start`, `claude_code_send`, `claude_code_exec`, `claude_code_push_file`, `claude_code_pull_file`, `claude_code_stop`, `claude_code_sessions`

## Priority tiers and deferred loading

Every tool declares a priority: `critical` (✓ above), `normal` (default), or `low`. When the active tool budget is exceeded, the classifier fills tier by tier: critical first, then normal, deferring `low`-priority tools behind `tool_search`. Pre-emptive search can promote tools to critical for a single turn based on user-message keyword matches. See [Tool Priority System](tool-priority.md), [Tool Search](tool-search.md), and [Pre-emptive Tool Search](preemptive-tool-search.md).

## Tool usage telemetry (#310)

`tool_telemetry.py` is a fail-open EventBus subscriber that appends one **metadata-only** JSONL record per tool call to `{workspace}/tool_usage.jsonl`. It answers "which tools are load-bearing and which are decorative" with data instead of intuition — the first step before any [MCP-overload](tool-priority.md) consolidation.

**Record shape** (one per `tool_end` event): `timestamp`, `conv_id`, `tool`, `source` (`core`/`skill`/`mcp`), `source_detail` (owning skill / MCP server), `outcome` (`success`/`error`/`cancelled`), `duration_ms`, `input_bytes`, `output_bytes`.

**Privacy:** tool arguments and return bodies are **never** recorded — only names, sizes, counts, and the inferred outcome. Outcome is derived from the result-text prefix (`[error…]` / `[cancelled…]`), so unknown-tool calls surface as `error` records automatically.

The publish site in `tool_execution.py` enriches `tool_start`/`tool_end` with `conv_id`, `duration_ms`, and `input_bytes`; the subscriber consumes `tool_end` only. Wired in `runner.py`, guarded by `config.telemetry.tool_usage_enabled` (default on).

**Report:** `make tool-usage-report` (`python -m decafclaw.tool_telemetry`) ranks tools by calls with unique-conversation counts, error rate, and last-called time, then lists never-called core + skill tools as consolidation candidates. MCP tools are only enumerable when their servers are connected, so offline unused-detection doesn't cover them.

Config: see [config.md](config.md) `telemetry` group.
