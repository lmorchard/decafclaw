# Tools Reference

All built-in tools the agent can call, grouped by module. Skills and MCP servers provide additional tools on demand.

Tools marked **critical** (✓) are always sent to the LLM — these are the minimum set needed for common tasks. Other tools are `normal` priority (filled in as budget allows) or `low` (fetched on demand via `tool_search`). See [Tool Priority System](tool-priority.md) and [Tool Search](tool-search.md).

Tools can return rich visual presentations in the web UI via DecafClaw's widget framework (`dc-widget-host`). See [Widgets](widgets.md) and [Tool Widget Opportunities](tool-widget-opportunities.md) for the survey and integration patterns.

## Core (`tools/core.py`)

| Tool | Always | What it does |
|------|:------:|--------------|
| `web_fetch` | ✓ | Fetch raw HTML from a URL |
| `current_time` | ✓ | Get current date and time |
| `wait` | | Pause briefly within current turn before checking output (max 300s; exempt from loop-breaker repeat limits; prefer `schedule_turn` for cross-turn delays) |
| `debug_context` | | Dump current context as JSON file attachments |
| `context_stats` | | Show token budget breakdown and diagnostics |

## Workspace (`tools/workspace_tools.py`)

Sandboxed file operations inside `data/{agent_id}/workspace/`. See [Data Layout](data-layout.md).

Shared file-tool helpers (cancellation check, error conversion, mini-diff) live in `tools/_file_common.py` (#1340); the admin file tools keep their own copies until 6.4b adopts the module.

| Tool | Always | What it does |
|------|:------:|--------------|
| `workspace_read` | ✓ | Read a file (supports line ranges and targeted section retrieval). Returns a content version token per read; pass it as `expected_version` to `workspace_edit` to reject edits against a stale read |
| `workspace_outline` | | Structural outline of classes, functions, and headings, or targeted section retrieval |
| `workspace_write` | ✓ | Write/overwrite a file, creating parents |
| `workspace_list` | | List files and directories |
| `workspace_append` | | Append content to a file |
| `workspace_edit` | ✓ | Surgical text edits (single- or multi-line); default tool for edits where the text is in view. Preserves the file's line endings (CRLF or LF), and when old_text is not found the error hints at a whitespace-only or near match with its line number. Accepts optional `expected_version` from a fresh read; a stale version rejects the edit (error, no write) and a success result reports the new version |
| `workspace_insert` | | Insert text at a specific line number |
| `workspace_replace_lines` | | Replace or delete a range of lines |
| `workspace_search` | | Regex search across workspace files |
| `workspace_glob` | | Find files by name/glob pattern |
| `workspace_move` | | Move or rename a file |
| `workspace_delete` | | Delete a file |
| `workspace_diff` | | Unified diff between two files |
| `file_share` | | Share a workspace file as a Mattermost attachment |

### Content versions for read→edit staleness (#1126)

`workspace_read` returns a content version token (sha256 over LF-normalized file content) in its tool data on every read — including partial line-range reads, which report the full-file version. `workspace_edit` accepts an optional `expected_version`; when the file content no longer matches that version, the edit is rejected with a stale-version error **before any write happens**, and a successful edit reports the new version via a trailing `Version: …` line in its result.

Without `expected_version`, edits keep the historical behavior (no staleness guard). The guard is process-local (per-file locks within this process); it is not a filesystem-wide compare-and-swap and does not coordinate with arbitrary external writers or other mutation paths, so callers relying on cross-process freshness must re-read.

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
| `admin_restart` | | Request a service restart (requires confirmation) |

### Post-restart wake (`admin_restart`, #1554)

`admin_restart` restarts the daemon on idle (`on_idle=true`) or immediately
(`on_idle=false`). With `wake_on_restart=true` (the default), it first writes
a single record to `data/{agent_id}/restart_wakes/{conv_id}.json` capturing the
conversation, reason, transport addresses, and request time. That restores the
turn the restart would otherwise cut short: the calling conversation can make a
change, request a restart, and then verify the change after the daemon is back.

On startup, after transports are listening, `ConversationManager.startup_scan_restart_wakes()`
reads and deletes each record, then enqueues a `TurnKind.WAKE` turn in that
conversation with the restart reason and elapsed time. Deletion happens before
the enqueue, so a failing wake cannot replay on a later restart. Cancelling a
pending restart (`/restart cancel`) deletes its record. The records live outside
the workspace so workspace tools cannot forge a wake in another conversation.

### Secret-file protection

The admin directory holds secret-bearing files (web/browser token
stores, provider API keys, MCP credentials, per-VM keys). The six admin tools
and `update_system_prompt` (the only other agent-facing writer under this
directory) consult **one shared rule** in `decafclaw/secret_policy.py`, so a
new secret file needs a single extension point, not a per-tool fix:

- **Refuse** — the agent gets nothing. Applied to *all seven tools*, before
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
(`admin_write`, `admin_replace_lines`, `admin_edit`, `update_system_prompt`),
and the mutation *recovery* paths (`AdminMutationHandler._apply_mutation`,
`UpdateSystemPromptHandler.on_approve`) inherit the guard — a pending approval
that arrives after a restart is still refused. The guard exists to stop a
read→modify→write of the marker from clobbering a real secret.

**Fail-closed.** If a redactable file's JSON does not parse, `admin_read`
refuses to return it (rather than leak unparseable bytes that might still hold
secrets). Fix the file's JSON, or list it under `secret_policy.refuse_paths`
if it is not actually config.

This is best-effort harm reduction, not watertightness — the admin is the
final arbiter. See [data-layout.md → Secret files](data-layout.md#secret-files).

## Prompt Sections (`tools/prompt_tools.py`)

The system prompt is assembled from markdown files under
`config.agent_path` ([Context Composer → System prompt](context-composer.md#system-prompt)).
Two of those files are the agent's own "living document" sections and can be
rewritten by the agent — with the same protections as the admin file tools
(interactive confirmation with a diff preview, blocked on unattended turns and
child agents, the shared secret-file rule, the marker guard, snapshot
revalidation).

| Tool | Always | What it does |
|------|:------:|--------------|
| `update_system_prompt` | | Rewrite `USER.md` (user context) or `CUSTOM.md` (custom standing instructions) in `data/{agent_id}/` (requires confirmation). `SOUL.md` is guarded and rejected; any other name is rejected as an invalid section |

Updated text is picked up by `load_system_prompt` on the next server restart
or skill refresh — system prompt assembly is not per-turn
([Context Composer → System prompt](context-composer.md#system-prompt)).

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

`checklist_create`/`checklist_step_done`/`checklist_abort` also mirror the checklist into the sticky slot above the conversation controls as a `progress_tracker` widget (fail-open), so the user sees live progress without the agent calling `widget_pin_sticky` directly. The slot clears once all steps are done or on abort. See [widgets.md](widgets.md#progress_tracker-widget).

## Turn Scheduling (`tools/schedule_tools.py`)

Tools for scheduling asynchronous wake turns in long-running workflows (#1103). Triggers an autonomous wake turn (`TurnKind.WAKE`) in the current conversation after a specified delay without blocking the active turn. State is persisted per-conversation in `data/{agent_id}/scheduled_wakes/{conv_id}.json` outside the agent-writable workspace, and resumes across server restarts. Wakes run as unattended turns (`is_unattended = True`), executing automated/allow-listed commands while failing-fast on interactive confirmations.

| Tool | Always | What it does |
|------|:------:|--------------|
| `schedule_turn` | | Schedule a future wake turn in this conversation after a delay (seconds) |
| `schedule_cancel` | | Cancel a pending scheduled wake turn by schedule_id |
| `schedule_list` | | List all pending scheduled wake turns for the current conversation |

## File Watches (`tools/watch_tools.py`)

One-shot file watches (#1560). The agent arms a watch on a workspace file; the first matching filesystem event (or the timeout) disarms it and enqueues a `TurnKind.WAKE` turn. State is persisted per-conversation in `data/{agent_id}/file_watches/{conv_id}.json` outside the agent-writable workspace, so it resumes across restarts — a change that landed while the server was down fires on the startup scan from the persisted baseline signature. The watcher polls a stat signature (mtime/size/inode) at `file_watch.poll_interval_sec`, so no new runtime dependency is required. Use this instead of polling with `wait` or guessing a delay with `schedule_turn` when an external app or person writes a known file.

| Tool | Always | What it does |
|------|:------:|--------------|
| `watch_file` | | Arm a one-shot watch on a workspace file (`event`: `modify`, `create`, `delete`, `any`; `timeout` seconds) |
| `watch_cancel` | | Disarm a pending file watch by watch_id |
| `watch_list` | | List all pending file watches for the current conversation |

## Tool Output (`tools/tool_output_tools.py`)

| Tool | Always | What it does |
|------|:------:|--------------|
| `tool_output_read` | | Read a saved tool output by its stable `tool_output://` handle, one page at a time |

Saved outputs are addressed by handle rather than path, so they stay readable after truncation, context clearing, compaction, and restart. See [Tool Output References](tool-output-references.md).

## Shell (`tools/shell_tools.py`)

Requires user confirmation unless pre-approved via `shell_allow_patterns.json`.

| Tool | Always | What it does |
|------|:------:|--------------|
| `shell` | ✓ | Run a shell command (requires confirmation) |
| `shell_patterns` | | Manage the approved shell command allow list |
| `shell_guidance` | | Manage aux-LLM auto-approval prompt guidance and presets |

### Working directory (`cwd`, #1455)

`shell` takes an optional `cwd`: a directory **inside the workspace**, relative to the workspace root (an absolute path inside the workspace also works). It defaults to the workspace root. The path is resolved (symlinks included) and must exist and be a directory; escapes outside the workspace are refused **before** approval, so nothing is confirmed or executed for an invalid directory.

Use `cwd` instead of `cd <path> && <command>`: chained commands bypass atomic allow-pattern matching and force manual confirmation, while a single invocation in a subdirectory (worktree, nested repo, subproject) matches patterns normally. Commands starting with `cd ... &&` or standalone `cd` are rejected fail-fast (#1467) with guidance to set `cwd` instead, and `cwd` is positioned first in the tool schema to encourage setting the working directory before generating the command string. The security monitor still enforces the **workspace root** as the boundary — `cwd` moves the starting point, not the authorized area. The effective directory is passed to the aux-LLM judge and shown in the web and Mattermost confirmation cards when it differs from the workspace root. The auto-background handoff path (`#1136`) runs the job in the same `cwd`.

### Purpose and denial feedback (#1559)

`shell` and `shell_background_start` take an optional `purpose`: a short statement of what the agent is trying to accomplish. The purpose is **untrusted metadata**, not a permission grant — Tier 1 blocks and sensitive-command confirmations are unaffected no matter how benign the stated purpose is. It feeds three places:

- **Aux auto-approval judge.** The prompt includes `Stated Purpose`, the conversation's derived activity, and instructs the judge to treat a command that contradicts its purpose as high risk. It also carries a **native tool index** generated from the live registry for that turn (`workspace_read`, `workspace_search`, `workspace_outline`, `workspace_edit`, `web_fetch`, `vault_search`, MCP tools, activated skill tools, …). A native-tool suggestion is **advisory** (#1641): the judge sets `auto_approve: true` and names the tool in `feedback`, and the tool result carries an `[advisory: …]` line after the command output. It is never a decline reason. Active tools are grouped by category; deferred tools (not loaded this turn) are listed separately so the judge can say "call `tool_search` first". Unknown tools land under "Other" rather than disappearing.
- **Tier 2 classifier.** `evaluate_command_llm(purpose=...)` shows the stated purpose to the security classifier and asks it to flag command/purpose conflicts as high risk.
- **Human confirmation.** The web card and the Mattermost post show the purpose next to the command, collapsed to one bounded line (mention-neutralized in Mattermost) so agent-supplied text cannot inject prompt lines or forge card content.

A denial may carry `feedback` — the judge's safer-alternative advice, or the human's. The tool result becomes `[error: command denied: <reason>. Feedback: <feedback>]`, so the agent can self-correct in the same turn. The standing guidance in `AGENT.md` tells it to treat denial feedback as guidance, not a failure.

#### Direction-aware approval (#1641)

A majority of human prompts came from commands that were safe *for what the conversation was doing*: the judge is a stateless classifier, so it had to guess whether `make deploy` was plausible and declined 31.7% of the time, mostly over style (a native tool could do the job). Direction is state the conversation already has. Two stages use it:

- **Deterministic read-only tier.** `shell_readonly.is_read_only_command` proves a command is read-only before any model call. Every pipeline/sequence segment must be a known read-only verb (`ls`, `cat`, `head`, `tail`, `grep`, `rg`, `find`, `fd`, `pwd`, `which`, `jq`, `wc`, `du`, `file`, `stat`, `echo`, `diff`, `cut`, `sort`, `uniq`, `tree`, the non-patch `git log`/`branch`/`tag -l`/`config --get`/`rev-parse`/`ls-files`/`cat-file`/`grep`/`blame`/`stash list`/`worktree list` queries, `gh … list|view|diff|checks`, `curl -I`), every path argument must resolve inside the workspace, and redirection, command substitution, subshells, expansion, and bare `&` disqualify it. A read-only verb with `> file` is **not** read-only. False negatives fall through to the existing pipeline; the rules are conservative because a false positive runs silently.

  Adversarial hardening (#1641 review): the command must be a **bare PATH name** — `./tools/ls` or `/tmp/evil/ls` is never a trusted read-only verb. Symlink-following recursion is refused (`grep -R`, `rg -L`, `fd -L`, `find -L`, `tree -l`, `du/file/stat/ls -L`, `diff -r`), as are file-list indirection (`--files0-from`, `file -f`), `sort -T`, and curl credential-file flags (`--netrc`, `--cert`, `--key`, `--cacert`, `--engine`, `@file` values). Repository config can run programs, so **`git status` (fsmonitor), `git diff`/`git show` (textconv), `git fetch`/`ls-remote` (upload-pack/transport), and patch-producing `git log` (textconv/gpg) are deliberately excluded** from the silent tier; they fall through to the judge, which can still allow them without a human prompt. The same applies to `git grep --open-files-in-pager`, `git cat-file --filters`, `git branch --edit-description`, and `git describe --dirty`.
- **Activity derivation.** `activity.derive_activity` labels a command with a coarse domain from its `cwd` (primary) and domain tokens in the command text (`homelab`, `blog`, `decafclaw`, `vault`, `television`, `me2md`, `handwriting`, `weeknotes`). Worktree paths (`…/worktrees/<branch>/…`) normalize to the parent repo. A domain is **established** when every domain the command touches appeared in the most recent `shell.activity_window` shell calls (default 50). The window is recomputed from `ctx.history`, so compaction that drops history also drops stale direction — there is no in-memory activity to reset, and a restart loses nothing history did not lose.

The decision order is: Tier 1 block → scoped and persisted allow patterns → security monitor → blanket tool pre-approval → **read-only allow** → **new activity prompts once** → **aux judge** → human prompt. A new-activity command never reaches the judge; it asks once, and the confirmation states the direction ("first `homelab` command in this conversation; recent activity: `blog`"). Answering establishes the activity, so drift costs exactly one prompt, never a block. On an unattended turn (heartbeat, scheduled, background wake) a new-activity command is denied with that reason instead of blocking on a prompt nobody can answer.

The judge sees only in-direction commands and may decline only for a concrete `risk_category`: `data_loss`, `exfiltration`, `privilege_escalation`, `outside_workspace`, or `irreversible`. A decline without one of those categories is treated as an allow with advisory feedback. `suggested_rule` is phrased scoped to the activity so accepted rules do not re-learn per conversation.

Both vocabularies are operator-editable without a restart:

- **Activity domains** — `data/{agent_id}/activity_domains.jsonl`, one `{"domain": "...", "pattern": "<regex>"}` per line (case-insensitive). It is admin-managed, not workspace-writable, because it steers approval. On first start it is seeded from the bundled `src/decafclaw/activity_domains.jsonl` examples; an existing file is authoritative (edit it to add or remove domains). Malformed lines are logged and skipped; an unusable file falls back to the bundled defaults.
- **Judge prompt** — bundled `src/decafclaw/prompts/SHELL_APPROVAL.md`, overridden per agent by `data/{agent_id}/SHELL_APPROVAL.md` (also admin-managed). The template uses `{{COMMAND}}`, `{{WORKING_DIR}}`, `{{STATED_PURPOSE}}`, `{{DIRECTION}}`, `{{NATIVE_TOOL_INDEX}}`, and `{{POLICY_BLOCK}}`; substitution is single-pass, so untrusted values are never expanded as placeholders.

**Telemetry.** Every decision publishes `approval_decision` (`command`, `cwd`, `decision`, `tier`, `reason`, `activity`, `activity_established`, `conv_id`); every human prompt publishes `approval_human` (`command`, `approved`, `latency_ms`, `tier`, …). The audit-log subscriber writes both to `audit.jsonl` when `audit_log.enabled`. Summarize them with:

```sh
decafclaw approvals report              # last 7 days, text
decafclaw approvals report --days 30 --json
```

#### Redirect before interrupting the human (#1626)

A judge decline **with a concrete risk category and usable `feedback`** does not prompt a human on the first offense. `check_shell_approval` returns a redirect `ToolResult` immediately:

```
[error: command not run — no approval requested yet. Feedback: <judge feedback>
This is the only redirect for this command; if you repeat it, a human will be asked.]
```

- **Budget.** One redirect per `(command, cwd, purpose)` fingerprint per turn (reusing `loop_breaker.fingerprint`), capped at 3 redirects per turn as a backstop. A repeated command, or a fourth unrelated one, falls through to the normal confirmation. The budget lives on `ctx.tools.shell_redirect_fingerprints`, which `fork_for_tool_call` shares across concurrent `shell` / `shell_background_start` calls; a new turn builds a new `Context`, so it never persists.
- **Unattended turns.** A redirect-eligible decline returns the redirect instead of the hard "unattended turn" denial, so the agent can self-correct with nobody present. A repeat denies with the judge's feedback attached.
- **Second offense.** The confirmation card carries both the judge's `decline_reason` and its `feedback` (`action_data.feedback`) — the web card renders "Suggested alternative:", the Mattermost card renders `> **Suggested alternative:**` — so the human sees what was already suggested and can overrule it.
- The redirect keeps the `[error` prefix, so `agent.py` still counts it toward the loop breaker's error/thrash accounting. A stated purpose never buys a redirect past a Tier 1 block or a security-monitor `ASK` confirmation; those still prompt.

Feedback collection by transport:
- **Web UI:** the confirmation card has an optional feedback field. `Deny` sends it as `feedback` on `confirm_response`.
- **Mattermost:** a reply that **starts a thread on a top-level confirmation card** is treated as a denial carrying the reply text as feedback (the turn is parked at the prompt, so no other interpretation applies). Mattermost links replies by thread root, so a card posted inside an existing thread cannot distinguish a reply to it from any other message there; those keep normal routing. The `🔧` reaction and the buttons still work everywhere.

### Capability gaps from shell review (#1559, #1482 data contract)

When a command exists only because no native tool covers the operation, the review pipeline records it in `workspace/improvement_inbox.jsonl`:

- The aux judge returns an optional `tool_gap: {proposed_tool, rationale}`; a decline carrying one appends an item with `trigger: "shell_tool_review"`, `subsystem: "tools"`, `severity: "low"`.
- A human can flag it while denying: the web card's **"Log as a missing native tool"** checkbox, or the Mattermost `🔧` reaction.

Writes are fail-open: an inbox failure never changes the command's outcome. The `improvement-inbox` contrib skill reads the same file.

### Auto-backgrounding on a foreground time budget (#1136)

By default `shell` runs the command with a blocking call and a hard timeout (`shell.max_runtime_sec`, default 30s — the command's **total runtime limit**). An opt-in **foreground waiting budget** (`shell.foreground_wait_budget_sec`, default `None` = disabled) changes that: the command is started through the same managed runner as background jobs and awaited up to the budget.

- **Finishes within the budget** → ordinary foreground result (same output shape as before). The job is dropped from the manager; no archive record, notification, or wake fires.
- **Exceeds the budget** → the **same process** continues as a managed background job and the tool returns its handle (job ID, PID) with pointers to `shell_background_status` / `shell_background_stop`. The existing completion path (archive record + inbox notification + WAKE turn) fires exactly once when it exits. The agent no longer has to predict foreground vs background up front.

Three distinct knobs, deliberately separate: the **wait budget** (how long to wait before handing off), the **total runtime limit** (`max_runtime_sec`, which becomes a handed-off job's `max_lifetime` and is enforced by the existing background-job expiry policy), and the **explicit-background default lifetime** (600s for `shell_background_start`, unchanged).

Approval happens **once, before process creation** (the usual `check_shell_approval()`); the handoff neither bypasses nor repeats it. Cancellation ownership: a turn cancelled **before** the handoff kills the process with the turn (nothing is left unmanaged, no completion sequence); a job **after** handoff is owned by the manager like any explicitly started background job — stopping it is `shell_background_stop` or expiry. Output collection stays bounded: foreground results are built from the same 500-line deques background jobs use. If the background runner is unavailable, `shell` fails open to the legacy blocking path with a warning.

### Session Modes (`default`, `dev`, `research`, `admin`)

Session Modes (#959) bundle a conversation's **shell auto-approval presets** together with its **promoted situational tools** into a switchable loadout:

- **`default`**: Baseline assistant configuration with standard interactive confirmation for shell commands and the default critical tool floor.
- **`dev`**: Software engineering and pairing mode. Enables `["developer", "github"]` shell auto-approval presets, and promotes coding tools (`shell`, `workspace_diff`, `workspace_search`, `workspace_glob`, `workspace_list`, `workspace_edit`) to the front of the active tool budget.
- **`research`**: Deep research and knowledge collection. Strict shell protection (no auto-approval presets), promoting research tools (`web_fetch`, `vault_search`, `vault_recent`, `vault_tags`, `vault_journal_append`).
- **`admin`**: Agent management and diagnostics. Default shell confirmation, promoting administrative tools (`admin_read`, `admin_list`, `admin_edit`, `admin_write`, `mcp_status`, `health_status`, `heartbeat_trigger`).

Modes can be selected in the Web UI sidebar dropdown above the Model picker, or requested by the agent via `shell_guidance(action="set_mode", mode="<name>")` (which requires user confirmation and is denied on unattended turns). Custom modes or overrides can be configured in `data/{agent_id}/config.json` under `modes: { "<name>": { "description": "...", "presets": [...], "promoted_tools": [...] } }`.

### Situational Auto-Approval Presets (`developer`, `github`)

`shell_guidance` provides built-in presets to streamline common workflows without relaxing security globally:
- **`developer`**: Auto-approves test runners (`pytest`, `npm test`), linters/formatters (`ruff`, `eslint`), typecheckers (`pyright`, `mypy`), environment task runners (`uv run`, `poetry run`, `npm run`, `cargo test/check/build`), non-destructive local git operations (`git status`, `git diff`, `git add`, `git commit`, `git checkout`), non-destructive remote operations (`git fetch`, `git pull`, pushing to `feat/*`, `fix/*`, `test/*`), and safe sequential `&&` chaining. Rejects destructive operations and package installations.
- **`github`**: Auto-approves GitHub CLI inspection and workflow management (`gh issue list/view/create`, `gh pr list/view/diff/checkout/create/checks`, `gh run list/view/watch`). Rejects destructive operations like repo deletion.

Background process management (`shell_background_start/status/stop/list`) lives in the bundled `background` skill (auto-activates) — see [Skills](skills.md). With `pin_sticky=True` on `shell_background_start`, a job streams live progress into the web UI sticky slot without conversational turns — see [Background Job Agent Wake](background-wake.md#pin_sticky-parameter).

### Approval sources

`check_shell_approval()` is the single chokepoint — don't duplicate its checks. It evaluates commands through the following stages, in order:

1. **Catastrophic safety filter (Tier 1 regex):** Commands matching dangerous destructive signatures (`rm -rf /`, `rm -rf ~`, `mkfs`, raw block device writes, fork bombs, shutdown/reboot) are immediately **blocked** (`SecurityStatus.BLOCK`), regardless of allowlists or permissions.
2. **Scoped pattern allowlist:** The command matches a **scoped pattern** from a skill's `allowed-tools: shell(...)` (see [Skills](skills.md#environment-for-shell-based-skills)). These bypass Tier 2 LLM analysis and auto-approve immediately.
3. **Persisted pattern allowlist:** The command matches a user-approved **persisted pattern** in `data/{agent_id}/shell_allow_patterns.json`. These bypass Tier 2 LLM analysis and auto-approve immediately without repeating interactive confirmation prompts (#966).
4. **Security monitor evaluation:** If not explicitly allowlisted, `evaluate_command_llm()` evaluates the command:
   - Tier 1 sensitive command checks (`npm/pip/cargo install`, `git push`, sensitive filesystem modifications) and Tier 2 LLM classification require human confirmation (`SecurityStatus.ASK`), or are denied outright on unattended turns.
   - Any malicious construct classified as `SecurityStatus.BLOCK` by Tier 2 is blocked.
5. **Blanket tool pre-approval:** If the command passed the security monitor checks and `shell` (or the tool name) is in `ctx.tools.preapproved` (blanket approval from `allowed-tools`), it is approved. Placing this after the security monitor ensures that blanket tool approvals cannot run unvetted package installations or out-of-workspace file mutations without confirmation.
6. **Deterministic read-only tier (#1641):** A command that `shell_readonly.is_read_only_command` proves read-only — every segment a read-only verb, every path inside the workspace, no redirection/substitution/expansion — is approved without a model call.
7. **Direction (#1641):** A command that starts a **new activity** for the conversation is confirmed once with the direction stated, without consulting the judge. Failure to answer on an unattended turn denies with the reason. Once the activity is established, the command proceeds to the judge.
8. **Auxiliary LLM auto-approval:** (If `config.shell.aux_approval_enabled` is true) The **auxiliary LLM** analyzes the command against the activity and may decline only for a concrete `risk_category` (§ [Direction-aware approval](#direction-aware-approval-1641)); a style preference such as a native tool is advisory. Approved commands are cached in `ctx.tools.llm_approved_shell_patterns` for the conversation. The judge runs on the resolved auto-approval model: sidecar `aux_approval_model` pointer → `shell.aux_approval_model` → the legacy aux chain (`auxiliary_model` → active conversation model → default); empty everywhere means the same chain the judge has always ridden (#1213).

Otherwise it falls through to a user confirmation prompt. The message explains why approval is requested: the security-monitor ASK path appends the monitor's `reason`, and when the auxiliary reviewer declines auto-approval it appends its `risk` and `reason` (#90). A decline also drafts a suggested natural-language exception rule (`suggested_rule`). The confirmation dialog then displays the decline reason and an **"Approve + remember why"** option (#982):
- In the **Web UI**, the drafted rule appears in an editable field. Choosing "Approve + remember why" executes the command and adds the (optionally edited) rule to the conversation's active guidance (`ctx.tools.aux_approval_guidance`).
- In **Mattermost**, the post shows the decline reason and suggested rule, offering an "Approve + remember why" button (and `:memo:` reaction).
- Subsequent commands in that conversation are evaluated against the newly added guidance rule.
- Users can also choose standard "Approve", "Deny", or "Allow Pattern" (which persists a glob pattern).

**What survives a server restart (#1160).** A conversation's "remember why" rules (`aux_approval_guidance`), its preset toggles (`active_aux_approval_presets`, `disabled_aux_approval_presets`), and its session mode are saved to `data/{agent_id}/approval_state/{conv_id}.json` whenever they change (at turn end and on `set_flag` writes such as a web UI mode switch). Rules added during a turn are saved when that turn ends, including a cancelled or errored turn, so a restart in the middle of a turn loses only that turn's additions. `ConversationManager` loads that file when it first creates state for the conversation, so every transport (web, Mattermost) gets the restored state. The file sits in the admin data dir rather than the agent-writable workspace, because a rule written there would steer the reviewer. A missing or malformed file falls back to defaults. The reviewer's approval cache (`llm_approved_shell_patterns`) is **not** saved: after a restart, each command pattern is approved again with one reviewer call. Conversations created before #1160 have no such file; for those only, the web UI and the agent turn setup still read the session mode from the archived `role: "mode"` record (a legacy fallback), and the web UI writes the file on first load. Once the file exists, its mode wins, even when it records `default`.

Active conversation rules can also be snapshotted or appended to a custom situational preset on disk via `shell_guidance(action="save_preset", preset="<name>")` (#1031). This stores custom named presets in `data/{agent_id}/shell_approval_presets.json` through a shared serialized store (`ShellPresetStore`, #1158) without auto-enabling them globally, allowing future conversations to reuse them on demand via `shell_guidance(action="enable_preset", preset="<name>")`.

Custom presets can also be viewed, created, edited, and deleted in the Web UI preset manager (reachable via the Agent Config panel's "Shell Presets" tab). Edits made through the Web UI write through the same serialized store and invalidate in-memory conversation pattern caches for conversations where the affected preset is active.

The Web UI also exposes a per-conversation shell approval panel (`<shell-approval-panel>`, #1159) so the human can manage that conversation's own "remember why" rules without leaving the chat surface. It reads and writes through the conversation-scoped endpoints (`GET/PUT/DELETE /api/conversations/{id}/shell-approval`, `PUT .../rules/replace`, `POST .../condense`, `DELETE /api/conversations/{id}/shell-approval/patterns`, `POST /api/conversations/{id}/shell-approval/promote`). Rule add/remove and full-list replace clear the cached reviewer approval pattern server-side; edits are rejected with 409 while the conversation is `busy` or `waiting`. Promote copies the rule into a custom preset (append-without-duplicate or create-new; built-in or config preset names are rejected with 400). The panel's edit mode bulk-edits the rule list (save replaces it atomically) and offers a **Condense** action that proposes fewer, more general rules via the resolved condensation model — a proposal only; state changes when the user saves. See [Web UI](web-ui.md#shell-approval-panel) for the panel layout.
**Unattended turns get the same allowlist and no prompt (#649).** Heartbeat, scheduled, and
background-wake turns (`ctx.is_unattended`, i.e. `ctx.task_mode` in
`{TaskMode.HEARTBEAT, TaskMode.SCHEDULED, TaskMode.BACKGROUND_WAKE}`) traverse exactly the branches
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

## LLM Models & Query (`tools/llm_tools.py`)

Model discovery and one-shot completions against a named model — no tool schemas, no conversation history (#1258, #1444).

| Tool | What it does |
|------|--------------|
| `llm_models` | List `config.model_configs` entries — the names `llm_query(model=...)` and `delegate_task(model=...)` accept — with provider, underlying model id, and active/default flags. Optional `provider` and `query` filters |
| `llm_query` | Single-shot completion (or a batch of up to 25, concurrency 5) against a `config.model_configs` entry. For narrow text transformation — extraction, summarization, classification — especially against small local models |

- `llm_models` is read-only and local: no network probe or remote provider ping. It reports the effective default model (#1211: catalog pointer when set, else `config.default_model`) and the conversation's active model; a stale active name that is no longer configured is not reported as active. `provider` matches the provider key (exact or case-insensitive substring); `query` matches the config name or underlying model id. Structured output lives on `ToolResult.data` as `{default_model, active_model, total, models: [{name, provider, model, is_default, is_active}]}`.
- `llm_query` `model=""` inherits the conversation's active model; an unknown name is a hard error that lists configured models (no silent fallback).
- `return_schema` requests a fenced JSON block via prompt-level instruction; a compliant block parses to `ToolResult.data`. `strict=true` turns non-compliant output into an error with field-level feedback.
- Each item runs under its own per-item timeout (default 60s); the tool opts out of the global watchdog. Usage accumulates into the turn's token counters, same as agent LLM calls.

For a subtask that needs tools or multi-turn reasoning, use `delegate_task` instead.

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

### Watchdog timeouts and in-flight progress (#1569)

A tool that exceeds its per-call timeout is killed by the watchdog in
`tools/__init__.py`, which logs `[watchdog] Tool '<name>' timed out after Ns`
and returns a `ToolResult` tagged `timed_out=True` with the limit in
`timeout_sec`. `tool_execution.py` tags every `tool_end` event (and the archived
tool message) with structured `is_error`/`timed_out`/`timeout_sec` fields, so
transports render failures without parsing `result_text`: the web UI shows a
warning badge and auto-expands the error text; Mattermost edits the tool post to
`⚠️ Tool <name> timed out after Ns` (or `⚠️ <name> failed: ...`).

Tools without native progress reporting also emit a `tool_status` heartbeat
every `TOOL_HEARTBEAT_INTERVAL_SEC` (15s) carrying the elapsed time. A tool
that publishes its own `tool_status` suppresses the heartbeat for the next
interval; `Context.publish` records the timestamp on the per-call
`ToolState.last_status_at`.

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

Every tool definition also declares a top-level `writes` boolean: `true` when a call can change state outside the turn (file writes, shell, outbound messages, external service calls, admin/vault mutations, …); `false` for read-only tools. The flag drives delegated read-only filtering (`delegate_task(read_only=true)`) and backs the declaration invariant in tests. A tool whose definition lacks a `writes` key is treated as a write (fail-closed).

## Batch execution policy: shared vs exclusive (#1124)

When the model emits several tool calls in one assistant message, they run as a **batch**: concurrently under the global semaphore (`max_concurrent_tools`), each on its own forked context. Within a batch, every call also carries an **execution policy** that orders calls against each other:

- **`shared`** — the call may overlap with other shared calls, subject only to the global semaphore budget.
- **`exclusive`** — the call waits for every preceding call in the batch to complete before it starts, and blocks every later call until it completes. A batch with one exclusive call in the middle therefore runs as: preceding calls concurrently → the exclusive call alone → later calls concurrently.

**Resolution order** (first resolvable definition wins, mirroring `execute_tool` dispatch: skill extras → core registry → MCP):

1. An explicit top-level `execution` key on the tool definition (`"shared"` or `"exclusive"`) — use it to override the `writes`-derived policy, e.g. for a mutating tool whose shared-state surface is already serialized by per-file locks.
2. The `writes` flag: `false` → shared, `true` → exclusive.
3. Resolvable but unannotated (contrib/workspace skill tools, MCP tools without `writes`) → shared — today's concurrent behavior is preserved.
4. **Unresolvable name** (no definition in any source) → the whole batch falls back to serial execution, one call at a time in assistant order (fail-safe).

**What the policy does and doesn't coordinate.** The batch policy orders calls *within one conversation's batch only*. It does not serialize across conversations, shell processes, or external editors — per-file mutation locks in `workspace_tools.py` still coordinate concurrent workspace file edits, and confirmations are unaffected. Exceptions and cancellation release the scheduling barriers: a failing or cancelled exclusive call still unblocks its successors, so a batch can never hang on the barrier. Results always retain assistant call order and `tool_call_id` attribution regardless of completion order.

Policy resolution is static per tool definition (v1); argument-dependent policies — a tool with both independent and shared-state operations choosing per call — are not supported yet. The `execution` key is the extension point for that.

**Workspace file-mutation tools are declared `execution: shared` (#1240).** The per-file mutation locks in `workspace_tools.py` / `file_locks.py` already serialize same-file contention, so the batch-exclusive default (derived from `writes: true`) was redundant for cross-file mutation batches. The workspace mutation tools (`workspace_write`, `workspace_append`, `workspace_edit`, `workspace_insert`, `workspace_replace_lines`, `workspace_move`, `workspace_mkdir`, `workspace_copy`, `workspace_delete`, `file_share`) now carry an explicit top-level `execution: shared` on their definitions, so an LLM batch that writes several *different* workspace files can run them concurrently while same-file edits still serialize through the per-file locks.

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
- **Attribution is by provenance, not by name.** `tool_definitions.collect_trusted_tool_guidelines` builds a name → guidelines map from the definitions the owners themselves declared: in precedence order: core `TOOL_DEFINITIONS`, the current `get_tools(ctx)` output of trusted dynamic providers (kept per skill in `ctx.tools.dynamic_provider_definitions`), what trusted skills registered on activation (`ctx.tools.skill_contributions`, which reflects reloads and collision renames like `skill__tool`), and finally trusted skills' pre-loaded static definitions. The active definition only decides *which* names are in play; its own `prompt_guidelines` are never read. So an activated workspace skill that declares a tool with the same name as a core or trusted-skill tool wins dispatch (`_dedupe_by_name`) but contributes no text. The `<tool_guidance>` block shows the trusted owner's guidance instead.

### Dynamic lifecycle

In `TurnRunner`, mid-turn tool loadout changes (such as fetching a deferred tool via `tool_search`, or dynamic tools refreshed via `get_tools(ctx)`) trigger `ContextComposer.update_iteration_tools()`. This updates both `<deferred_tools>` and `<tool_guidance>` in-place on the very next LLM iteration.

## Tool usage telemetry (#310)

`tool_telemetry.py` is a fail-open EventBus subscriber that appends one **metadata-only** JSONL record per tool call to `{workspace}/tool_usage.jsonl`. It answers "which tools are load-bearing and which are decorative" with data instead of intuition — the first step before any [MCP-overload](tool-priority.md) consolidation.

**Record shape** (one per `tool_end` event): `timestamp`, `conv_id`, `tool`, `source` (`core`/`skill`/`mcp`), `source_detail` (owning skill / MCP server), `outcome` (`success`/`error`/`cancelled`), `duration_ms`, `input_bytes`, `output_bytes`.

**Privacy:** tool arguments and return bodies are **never** recorded — only names, sizes, counts, and the inferred outcome. Outcome is derived from the result-text prefix (`[error…]` / `[cancelled…]`), so unknown-tool calls surface as `error` records automatically.

The publish site in `tool_execution.py` enriches `tool_start`/`tool_end` with `conv_id`, `duration_ms`, and `input_bytes`; the subscriber consumes `tool_end` only. Wired in `runner.py`, guarded by `config.telemetry.tool_usage_enabled` (default on).

**Report:** `make tool-usage-report` (`python -m decafclaw.tool_telemetry`) ranks tools by calls with unique-conversation counts, error rate, and last-called time, then lists never-called core + skill tools as consolidation candidates. MCP tools are only enumerable when their servers are connected, so offline unused-detection doesn't cover them.

Config: see [config.md](config.md) `telemetry` group.
