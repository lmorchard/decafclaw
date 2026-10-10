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
| `workspace_edit` | ✓ | Surgical text edits (single- or multi-line); default tool for edits where the text is in view |
| `workspace_insert` | | Insert text at a specific line number |
| `workspace_replace_lines` | | Replace or delete a range of lines |
| `workspace_search` | | Regex search across workspace files |
| `workspace_glob` | | Find files by name/glob pattern |
| `workspace_move` | | Move or rename a file |
| `workspace_delete` | | Delete a file |
| `workspace_diff` | | Unified diff between two files |
| `file_share` | | Share a workspace file as a Mattermost attachment |

## Admin Files (`tools/admin_tools.py`)

Dedicated file tools for the agent's administrative directory (`config.agent_path`). Used to manage admin skills (`skills/`), prompts (`prompts/`), schedules (`schedules/`), and agent configuration (`config.json`). Strictly excludes the workspace (`config.workspace_path` — use `workspace_*` tools instead). All mutations require interactive user confirmation with a unified diff preview, and are categorically blocked on unattended runs and child agents.

| Tool | Always | What it does |
|------|:------:|--------------|
| `admin_read` | | Read an administrative file (supports line ranges) |
| `admin_list` | | List files and directories under `config.agent_path` |
| `admin_write` | | Create or overwrite an admin file (requires confirmation) |
| `admin_replace_lines` | | Replace or delete a range of lines (requires confirmation) |
| `admin_edit` | | Exact string replacement in an admin file (requires confirmation) |
| `admin_delete` | | Delete an admin file or directory (requires confirmation) |

### Secret-file protection

The admin directory holds secret-bearing files (web/browser token
stores, provider API keys, MCP credentials, per-VM keys). The six tools consult
**one shared rule** in `decafclaw/secret_policy.py`, so a new secret file
needs a single extension point, not a per-tool fix:

- **Refuse** — the agent gets nothing. Applied to *all six tools*, before
  confirmation, reading, or mutation. The default set is:
   `web_tokens.json`, `browser_tokens.json`, `mcp_oauth/**`, `*.pem`, `*.key`,
   `**/keys/*`, `service_account*.json`. `admin_list` still shows the *name* of a
   refused file (a name is not its contents) but refuses to descend into a
   refused directory. The check runs on the path's *resolved* location
   (`subdir/../web_tokens.json`, a symlink named innocently, …), so a spelling
   that resolves into a secret store is refused too — the rule may over-match,
   but never under-match.
- **Redact** — applied to `config.json` and `mcp_servers.json` on `admin_read`.
  The agent sees the structure and every non-secret field, with secret leaf
  values replaced by the fixed marker `<redacted>`. The redacted set is the
  `secret`-annotated field names walked across `Config`
  (`config_types.py`), plus fixed structural rules
  (`env.*`, `skills.*.*`, `mcpServers.*.env.*`, `mcpServers.*.headers.*`).
  This is the same annotation `config show` uses to mask values, so the two
  surfaces stay in lockstep.
- **Extend** — both tiers are admin-widenable in `config.json`:

  ```jsonc
  {
    "secret_policy": {
      "refuse_paths": ["vault_backup/keys/**", "legacy_key.json"],
      "redact_paths": ["some_group.legacy_key", "providers.*.extra_cred"]
    }
  }
  ```

  `refuse_paths` are globs (relative to `config.agent_path`, `*`/`?` cross
  `/`); `redact_paths` are dotted JSON paths into a redactable file, with a
  single `*` segment matching any one key.

**Marker guard.** Any write payload containing `<redacted>` is refused
(`admin_write`, `admin_replace_lines`, `admin_edit`), and the mutation
*recovery* path (`AdminMutationHandler._apply_mutation`) inherits the guard —
a pending approval that arrives after a restart is still refused. The guard
exists to stop a read→modify→write of the marker from clobbering a real secret.

**Fail-closed.** If a redactable file's JSON does not parse, `admin_read`
refuses to return it (rather than leak unparseable bytes that might still hold
secrets). Fix the file's JSON, or list it under `secret_policy.refuse_paths`
if it is not actually config.

This is best-effort harm reduction, not watertightness — the admin is the
final arbiter. See [data-layout.md → Secret files](data-layout.md#secret-files).

## Vault (`skills/vault/tools.py`)

Always-activated skill for the unified knowledge base. See [Vault](vault.md).

| Tool | Always | What it does |
|------|:------:|--------------|
| `vault_read` | ✓ | Read a vault page by name or path |
| `vault_write` | ✓ | Create or overwrite a vault page; auto-indexes in embeddings |
| `vault_journal_append` | ✓ | Append a timestamped journal entry |
| `vault_search` | ✓ | Semantic + substring search across the vault |
| `vault_list` | ✓ | List pages with last-modified dates |
| `vault_backlinks` | | Find pages linking to a given page |

## Conversation (`tools/conversation_tools.py`)

| Tool | What it does |
|------|--------------|
| `conversation_search` | Search past conversation archives (stemmed word overlap + substring; optional `days` filter) |
| `conversation_compact` | Manually trigger conversation compaction |

## Checklist (`tools/checklist_tools.py`)

Per-conversation step-by-step execution loop. Storage is markdown checkboxes at `workspace/todos/{conv_id}.md`.

| Tool | Always | What it does |
|------|:------:|--------------|
| `checklist_create` | ✓ | Create a new checklist from a list of steps |
| `checklist_step_done` | ✓ | Mark the current step done and advance |
| `checklist_abort` | ✓ | Abort the current checklist |
| `checklist_status` | ✓ | Show current checklist state |

`checklist_create`/`checklist_step_done`/`checklist_abort` also mirror the checklist into the sticky slot above the chat input as a `progress_tracker` widget (fail-open), so the user sees live progress without the agent calling `widget_pin_sticky` directly. The slot clears once all steps are done or on abort. See [widgets.md](widgets.md#progress_tracker-widget).

## Shell (`tools/shell_tools.py`)

Requires user confirmation unless pre-approved via `shell_allow_patterns.json`.

| Tool | Always | What it does |
|------|:------:|--------------|
| `shell` | ✓ | Run a shell command (requires confirmation) |
| `shell_patterns` | | Manage the approved shell command allow list |
| `shell_guidance` | | Manage aux-LLM auto-approval prompt guidance and presets |

### Situational Auto-Approval Presets (`developer`, `github`)

`shell_guidance` provides built-in presets to streamline common workflows without relaxing security globally:
- **`developer`**: Auto-approves test runners (`pytest`, `npm test`), linters/formatters (`ruff`, `eslint`), typecheckers (`pyright`, `mypy`), environment task runners (`uv run`, `poetry run`, `npm run`, `cargo test/check/build`), non-destructive local git operations (`git status`, `git diff`, `git add`, `git commit`, `git checkout`), non-destructive remote operations (`git fetch`, `git pull`, pushing to `feat/*`, `fix/*`, `test/*`), and safe sequential `&&` chaining. Rejects destructive operations and package installations.
- **`github`**: Auto-approves GitHub CLI inspection and workflow management (`gh issue list/view/create`, `gh pr list/view/diff/checkout/create/checks`, `gh run list/view/watch`). Rejects destructive operations like repo deletion.

Background process management (`shell_background_start/status/stop/list`) lives in the bundled `background` skill (auto-activates) — see [Skills](skills.md).

### Approval sources

`check_shell_approval()` is the single chokepoint — don't duplicate its checks. It evaluates commands through the following stages, in order:

1. **Catastrophic safety filter (Tier 1 regex):** Commands matching dangerous destructive signatures (`rm -rf /`, `rm -rf ~`, `mkfs`, raw block device writes, fork bombs, shutdown/reboot) are immediately **blocked** (`SecurityStatus.BLOCK`), regardless of allowlists or permissions.
2. **Scoped pattern allowlist:** The command matches a **scoped pattern** from a skill's `allowed-tools: shell(...)` (see [Skills](skills.md#environment-for-shell-based-skills)). These bypass Tier 2 LLM analysis and auto-approve immediately.
3. **Persisted pattern allowlist:** The command matches a user-approved **persisted pattern** in `data/{agent_id}/shell_allow_patterns.json`. These bypass Tier 2 LLM analysis and auto-approve immediately without repeating interactive confirmation prompts (#966).
4. **Security monitor evaluation:** If not explicitly allowlisted, `evaluate_command_llm()` evaluates the command:
   - Tier 1 sensitive command checks (`npm/pip/cargo install`, `git push`, sensitive filesystem modifications) and Tier 2 LLM classification require human confirmation (`SecurityStatus.ASK`), or are denied outright on unattended turns.
   - Any malicious construct classified as `SecurityStatus.BLOCK` by Tier 2 is blocked.
5. **Blanket tool pre-approval:** If the command passed the security monitor checks and `shell` (or the tool name) is in `ctx.tools.preapproved` (blanket approval from `allowed-tools`), it is approved. Placing this after the security monitor ensures that blanket tool approvals cannot run unvetted package installations or out-of-workspace file mutations without confirmation.
6. **Auxiliary LLM auto-approval:** (If `config.shell.aux_approval_enabled` is true) The **auxiliary LLM** analyzes the command and its risk against the default policy and any active situational presets or prompt guidance (configured statically via `config.shell`, or activated per-conversation with user confirmation via `shell_guidance`). Approved commands are cached in `ctx.tools.llm_approved_shell_patterns` for the conversation.

Otherwise it falls through to a user confirmation prompt. When the auxiliary reviewer declines auto-approval, it returns a decline reason and drafts a suggested natural-language exception rule (`suggested_rule`). The confirmation dialog then displays the decline reason and an **"Approve + remember why"** option (#982):
- In the **Web UI**, the drafted rule appears in an editable field. Choosing "Approve + remember why" executes the command and adds the (optionally edited) rule to the conversation's active guidance (`ctx.tools.aux_approval_guidance`).
- In **Mattermost**, the post shows the decline reason and suggested rule, offering an "Approve + remember why" button (and `:memo:` reaction).
- Subsequent commands in that conversation are evaluated against the newly added guidance rule.
- Users can also choose standard "Approve", "Deny", or "Allow Pattern" (which persists a glob pattern).

Active conversation rules can also be snapshotted or appended to a custom situational preset on disk via `shell_guidance(action="save_preset", preset="<name>")` (#1031). This stores custom named presets in `data/{agent_id}/shell_approval_presets.json` without auto-enabling them globally, allowing future conversations to reuse them on demand via `shell_guidance(action="enable_preset", preset="<name>")`.

**Unattended turns get the same allowlist and no prompt (#649).** Heartbeat and scheduled turns
(`ctx.is_unattended`, i.e. `task_mode` in `{"heartbeat", "scheduled"}`) traverse exactly the branches
above — there is no bypass for them. On a miss they are **denied outright** instead of falling through
to a confirmation: the prompt would only reach subscribers of an ephemeral `conv_id`, so it would
block for the 60s timeout and be synthesized into that same denial. Unattended automation is granted
shell access by adding a persisted or scoped pattern, never by virtue of who is running.

Until #649 this list began with "the turn is an admin heartbeat", which auto-approved *any* command
on the least-supervised turn kind — the capability ladder inverted.

### Wildcard patterns never match chained commands

Approving a command offers to persist a *wildcarded* pattern — approving `python foo.py --a` suggests `python foo.py *`. Because fnmatch's `*` spans every shell chaining operator, a naive match would let one approval authorize everything sharing that prefix, including `python foo.py --a; rm -rf ~`.

So `_command_matches_pattern` enforces: **a pattern containing a glob wildcard (`*`, `?`, `[`) will not match a command containing shell chaining tokens.** Such a command falls through to confirmation instead.

The chaining tokens (`_SHELL_CHAIN_TOKENS`) are a minimal covering set — each is a substring of every operator it catches:

| Token | Catches |
|---|---|
| `;` | sequence |
| `&` | background, and `&&` |
| `\|` | pipe, and `\|\|` |
| `` ` `` | command substitution (legacy) |
| `$(` | command substitution |
| `<(` | process substitution (input) |
| `>(` | process substitution (output) |
| `\n` | newline as statement separator |

Don't add `&&` or `||` back as separate entries — they're already covered, and the redundancy invites the mistake of thinking `&&` is handled while bare `&` isn't. That exact gap shipped once: `&` was missing while `&&` was present, so `python foo.py --a & rm -rf ~` backgrounded the approved command and ran an unapproved one.

#### Quote-aware token scanning and interpreter handling (#966)

The chaining token scanner is quote-aware:
- Newlines, semicolons, and pipes embedded within quoted strings (`"..."` or `'...'`) are treated as literal argument data rather than statement separators. This enables multi-line commit messages (`git commit -m "title\n\nbody"`) and multi-line issue bodies (`gh issue create --body "line 1\nline 2"`) to match wildcard allow patterns.
- Command substitutions (`$()`, `` ` ``) and process substitutions (`<()`, `>()`) remain active in shell execution and are always detected as chaining/execution tokens, even inside double quotes.
- Wildcard patterns targeting shell interpreters, execution primitives, or privilege wrappers (`sh`, `bash`, `zsh`, `dash`, `ksh`, `eval`, `exec`, `command`, `sudo`, `env`, etc.) are categorically ineligible for wildcard pattern matching; `_suggest_pattern()` never wildcards them, requiring explicit literal approvals for specific scripts.

This covers command *chaining* and execution only. Plain file redirection (`>`, `<`) is deliberately not blocked — it can't introduce a command, and rejecting it would break common pipeline invocations. A wildcard pattern therefore still permits plain file redirection in its arguments (while process substitution `<(...)` / `>(...)` is blocked).

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
| `activate_skill` | ✓ | Load a skill's tools into the current conversation; re-imports `tools.py` if the skill is already active |
| `refresh_skills` | | Re-scan skill directories without restarting (catalog only — not the loaded tools) |
| `skill_validate` | | Pre-flight lint one workspace skill directory, including whether discovery scans its location |

## Tool error messages

Every tool call is wrapped by `execute_tool`, which turns exceptions into
`ToolResult(text="[error: ...]")` rather than propagating them. Two error shapes
are worth knowing because they say *where* the bug is:

- **`Expected parameters: a, b, c`** — the arguments didn't bind to the tool's
  signature. The call is wrong; fix the arguments.
- **`This TypeError was raised inside the tool's own code`** — the arguments
  bound fine and the tool's implementation raised. Calling it differently will
  not help; fix the tool. When the tool belongs to a skill, the message names
  the skill.

`execute_tool` distinguishes them with `signature.bind()`. Both once shared the
first wording, so a `TypeError` from inside a tool body was reported as a
bad-argument error — and for a tool taking only `ctx`, it rendered as an empty
`Expected parameters: `, pointing the author at a call site that was correct.

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

## Tool-owned prompt guidance (`prompt_guidelines`) (#928 & #998)

Tool definitions can optionally declare top-level `prompt_guidelines: list[str]`. When a tool is in the active set, its guidelines are rendered into an authoritative `<tool_guidance>` system message injected into the prompt.

### Division of guidance responsibilities

To avoid instruction duplication, drift, and prompt bloat:
1. **Tool descriptions** (`function.description`): describe *what* the tool does, its arguments, and parameter constraints for the model's function-calling schema.
2. **`prompt_guidelines`**: concise operational steering rules that are only relevant when the tool is callable (e.g. "Use workspace_read to see exact current content and line numbers before modifying a file"). When the tool is deferred or unavailable, its guidelines do not consume prompt tokens. Guidelines across active tools are deduplicated in stable order.
3. **`AGENT.md`**: global behavioral invariants (e.g. "prefer surgical edits to full rewrites", never use shell to edit files, reflexivity guards) and workspace directory organization.
4. **Skill bodies**: step-by-step procedures, multi-step workflows, and domain guidance.

### Trust boundary

Prompt guidance runs with system-level instruction authority. To prevent untrusted prompt injection:
- **Core tools** and **trusted skills** (`bundled`, `admin`, `extra` passing `skills.grants_capability(info)`) may contribute `prompt_guidelines`.
- **Workspace-tier skills** (`data/{agent_id}/workspace/skills/`) are agent-writable and **never** contribute prompt guidance, even when activated.
- **MCP tools** (`mcp__*`) never contribute prompt guidance.

### Dynamic lifecycle

In `TurnRunner`, mid-turn tool loadout changes (such as fetching a deferred tool via `tool_search`, or dynamic tools refreshed via `get_tools(ctx)`) trigger `ContextComposer.update_iteration_tools()`. This updates both `<deferred_tools>` and `<tool_guidance>` in-place on the very next LLM iteration.

## Tool usage telemetry (#310)

`tool_telemetry.py` is a fail-open EventBus subscriber that appends one **metadata-only** JSONL record per tool call to `{workspace}/tool_usage.jsonl`. It answers "which tools are load-bearing and which are decorative" with data instead of intuition — the first step before any [MCP-overload](tool-priority.md) consolidation.

**Record shape** (one per `tool_end` event): `timestamp`, `conv_id`, `tool`, `source` (`core`/`skill`/`mcp`), `source_detail` (owning skill / MCP server), `outcome` (`success`/`error`/`cancelled`), `duration_ms`, `input_bytes`, `output_bytes`.

**Privacy:** tool arguments and return bodies are **never** recorded — only names, sizes, counts, and the inferred outcome. Outcome is derived from the result-text prefix (`[error…]` / `[cancelled…]`), so unknown-tool calls surface as `error` records automatically.

The publish site in `tool_execution.py` enriches `tool_start`/`tool_end` with `conv_id`, `duration_ms`, and `input_bytes`; the subscriber consumes `tool_end` only. Wired in `runner.py`, guarded by `config.telemetry.tool_usage_enabled` (default on).

**Report:** `make tool-usage-report` (`python -m decafclaw.tool_telemetry`) ranks tools by calls with unique-conversation counts, error rate, and last-called time, then lists never-called core + skill tools as consolidation candidates. MCP tools are only enumerable when their servers are connected, so offline unused-detection doesn't cover them.

Config: see [config.md](config.md) `telemetry` group.
