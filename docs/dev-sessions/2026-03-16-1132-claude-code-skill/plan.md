# Claude Code Skill — Plan

## Ordering Rationale

We start with the riskiest unknown (SDK installation + API verification), then build the session manager (core state), then the permission bridge (riskiest integration), then the tools, then the SKILL.md, and finally config + docs. Each phase is testable in isolation.

The permission bridge (Phase 3) is deliberately early because it's the make-or-break integration point. If the `can_use_tool` async callback doesn't play nicely with DecafClaw's event bus, we need to know before building everything else on top of it.

Each phase ends with `make check && make test`.

---

## Phase 1: SDK Verification & Skeleton

**Goal:** Install the SDK, verify the actual package name and API, create the skill directory structure.

### Step 1.1: Install SDK and verify API

**Prompt:**
> Install the Claude Agent SDK. The package may be called `claude-agent-sdk` or `claude-code-sdk` — try both. Once installed, verify the actual import path and key classes/functions:
>
> 1. `uv add claude-agent-sdk` (or the correct package name)
> 2. Write a small test script that imports the key classes: `query`, `ClaudeSDKClient` (or whatever the client class is called), `ClaudeAgentOptions` (or equivalent options class)
> 3. Check what message types are available (AssistantMessage, TextBlock, ToolUseBlock, ResultMessage, etc.)
> 4. Check if `can_use_tool` / permission callback exists and what its signature is
> 5. Document findings in the session notes — we need the real API before proceeding
>
> Do NOT proceed with implementation until we know the actual API. If the SDK API differs significantly from what we researched, flag it so we can adjust the spec.

### Step 1.2: Create skill directory skeleton

**Prompt:**
> Create the skill directory at `src/decafclaw/skills/claude_code/` with:
>
> 1. `SKILL.md` — frontmatter with name, description, requires (ANTHROPIC_API_KEY). Body describes the tools and usage guidance. Follow the tabstack SKILL.md as a template for structure.
> 2. `tools.py` — skeleton with:
>    - `init(config)` function (reads config, stores settings in module state)
>    - Empty `TOOLS` dict and `TOOL_DEFINITIONS` list
>    - Placeholder docstrings for the four tool functions
>
> Don't implement the tools yet — just the structure so the skill can be discovered and activated (even if the tools don't do anything yet).
>
> Run `make check` to verify the skill is importable and lint-clean.

### Step 1.3: Commit

> Commit: "Add claude_code skill skeleton and SDK dependency"

---

## Phase 2: Session Manager

**Goal:** Build the core session lifecycle management — create, track, expire, destroy sessions. No SDK calls yet, just the state management.

### Step 2.1: Implement SessionManager class

**Prompt:**
> In `src/decafclaw/skills/claude_code/sessions.py`, create a `SessionManager` class that manages Claude Code session lifecycle:
>
> ```python
> @dataclass
> class Session:
>     session_id: str
>     cwd: str
>     description: str
>     model: str | None
>     budget_usd: float
>     client: object | None = None  # Will be ClaudeSDKClient later
>     created_at: float = 0  # time.monotonic()
>     last_active: float = 0
>     total_cost_usd: float = 0
>
> class SessionManager:
>     def __init__(self, timeout_sec: int, budget_default: float, budget_max: float):
>         self.sessions: dict[str, Session] = {}
>         self.cwd_to_session: dict[str, str] = {}  # cwd -> session_id
>         self.timeout_sec = timeout_sec
>         self.budget_default = budget_default
>         self.budget_max = budget_max
>
>     def create(self, cwd, description="", model=None, budget_usd=None) -> Session:
>         """Create a new session. Raises if cwd already has an active session."""
>
>     def get(self, session_id) -> Session | None:
>         """Get a session, or None if expired/not found. Lazy expiration check."""
>
>     def touch(self, session_id):
>         """Update last_active timestamp."""
>
>     def stop(self, session_id) -> Session | None:
>         """Remove and return a session. Returns None if not found."""
>
>     def list_active(self) -> list[Session]:
>         """Return all non-expired sessions."""
>
>     async def close_all(self):
>         """Close all sessions (for shutdown/skill deactivation)."""
> ```
>
> Key behaviors:
> - `create()` generates a UUID session_id, validates budget (clamp to budget_max), checks cwd uniqueness
> - `get()` checks expiration lazily — if `time.monotonic() - last_active > timeout_sec`, remove the session and return None
> - `close_all()` is async because it will eventually close SDK clients
>
> The `client` field is `None` for now — Phase 4 will populate it with the real SDK client.
>
> Run `make check` after.

### Step 2.2: Tests for SessionManager

**Prompt:**
> Write tests for `SessionManager` in `tests/test_claude_code_sessions.py`:
>
> - Create a session, verify ID and fields
> - Create returns session with clamped budget
> - Create rejects duplicate cwd
> - Get returns session by ID
> - Get returns None for unknown ID
> - Get returns None and removes expired session
> - Touch updates last_active
> - Stop removes and returns session
> - Stop returns None for unknown ID
> - list_active excludes expired sessions
> - close_all clears all sessions
>
> Use `time.monotonic()` patching or short timeouts for expiration tests.
>
> Run `make check && make test` after.

### Step 2.3: Commit

> Commit: "Add SessionManager for Claude Code session lifecycle"

---

## Phase 3: Permission Bridge (Prototype)

**Goal:** Build and verify the async bridge between the SDK's `can_use_tool` callback and DecafClaw's Mattermost confirmation flow. This is the riskiest integration point.

### Step 3.1: Implement allowlist and permission callback

**Prompt:**
> In `src/decafclaw/skills/claude_code/permissions.py`, implement:
>
> 1. **Allowlist management** — same pattern as shell tool:
>    ```python
>    def _allowlist_path(config) -> Path
>    def load_allowlist(config) -> list[str]
>    def save_allowlist_entry(config, pattern: str)
>    def matches_allowlist(tool_name: str, input_data: dict, patterns: list[str]) -> bool
>    ```
>    File: `claude_code_allow_patterns.json` in agent_path
>    Auto-approve read-only tools (`Read`, `Glob`, `Grep`, `WebSearch`, `WebFetch`) by default.
>
> 2. **Permission callback factory** — creates the `can_use_tool` callback for the SDK:
>    ```python
>    def make_permission_handler(ctx, config):
>        """Create a can_use_tool callback that bridges to DecafClaw's confirmation flow."""
>        async def can_use_tool(tool_name, input_data):
>            # 1. Check allowlist
>            # 2. If not allowed, use request_confirmation() via ctx
>            # 3. If "always" approved, add to allowlist
>            # 4. Return allow/deny per SDK's expected format
>        return can_use_tool
>    ```
>
> The exact return type for allow/deny depends on what we discovered about the SDK API in Phase 1. Adapt accordingly.
>
> Run `make check` after.

### Step 3.2: Tests for permission bridge

**Prompt:**
> Write tests for the permission module in `tests/test_claude_code_permissions.py`:
>
> - Auto-approves read-only tools (Read, Glob, Grep)
> - Auto-approves tools matching allowlist patterns
> - Requests confirmation for unknown tools
> - Approved confirmation allows the tool
> - Denied confirmation blocks the tool
> - "Always" approval adds to allowlist
> - Timeout denies the tool
>
> Mock the confirmation flow by publishing tool_confirm_response events from a separate task (same pattern as test_confirmation.py).
>
> Run `make check && make test` after.

### Step 3.3: Integration smoke test

**Prompt:**
> Write a manual test script (not pytest — a script in `scripts/test-claude-code-permissions.py`) that:
>
> 1. Creates a real `ClaudeSDKClient` (requires ANTHROPIC_API_KEY)
> 2. Passes our `make_permission_handler` as `can_use_tool`
> 3. Sends a simple prompt like "list the files in the current directory"
> 4. Verifies that the permission callback fires for the `Bash` or `Glob` tool
> 5. Auto-approves (since Glob is read-only) and prints the result
>
> This validates that the async bridge works inside the SDK's event loop. Flag any issues — this is the make-or-break test.
>
> Do NOT add this to the test suite (requires API key). It's a manual verification step.

### Step 3.4: Commit

> Commit: "Add Claude Code permission bridge with allowlist"

---

## Phase 4: Output Logging

**Goal:** Build the log writer that captures full SDK output to disk.

### Step 4.1: Implement session logger

**Prompt:**
> In `src/decafclaw/skills/claude_code/logging.py` (or `output.py` to avoid shadowing the `logging` module), implement:
>
> ```python
> class SessionLogger:
>     """Writes SDK output to a JSONL log file."""
>
>     def __init__(self, log_dir: Path, session_id: str):
>         self.path = log_dir / f"{session_id}.jsonl"
>         self.path.parent.mkdir(parents=True, exist_ok=True)
>         self.files_changed: list[str] = []
>         self.total_cost_usd: float = 0
>         self.errors: list[str] = []
>
>     def log_message(self, message):
>         """Append a serialized SDK message to the log file."""
>
>     def build_summary(self) -> str:
>         """Build a concise summary string for the LLM."""
> ```
>
> The `log_message` method should:
> - Serialize the message to a JSON-safe dict (handle SDK message types)
> - Append as a line to the JSONL file
> - Track files changed (from Edit/Write tool uses)
> - Track cost (from ResultMessage)
> - Track errors
>
> The `build_summary` method should return a markdown string like:
> ```
> **Claude Code completed** (session abc123, $0.45)
> - Files changed: agent.py, config.py
> - 3 tool calls (Read, Edit, Edit)
> - No errors
> - Full log: workspace/claude-code-logs/abc123.jsonl
> ```
>
> Run `make check` after.

### Step 4.2: Tests for session logger

**Prompt:**
> Write tests for SessionLogger in `tests/test_claude_code_output.py`:
>
> - Log file is created in the right location
> - Messages are appended as JSONL
> - files_changed tracked from Edit/Write tool uses
> - cost tracked from result messages
> - build_summary includes cost, files, tool count
> - build_summary handles empty session (no messages)
>
> Run `make check && make test` after.

### Step 4.3: Commit

> Commit: "Add Claude Code session output logger"

---

## Phase 5: Tool Implementations

**Goal:** Wire the SessionManager, permission bridge, and logger into the four skill tools.

### Step 5.1: Implement `init()` and `claude_code_start`

**Prompt:**
> In `src/decafclaw/skills/claude_code/tools.py`:
>
> 1. Implement `init(config)` — read config values, create the SessionManager, store in module state:
>    - Parse `CLAUDE_CODE_SESSION_TIMEOUT` using `heartbeat.parse_interval()` (or a local parser)
>    - Read budget defaults and max from config
>    - Store the session manager and config as module globals
>
> 2. Implement `tool_claude_code_start(ctx, cwd, description="", model=None, budget_usd=None)`:
>    - Validate cwd exists
>    - Create session via SessionManager
>    - Do NOT create the SDK client yet (that happens on first `send`)
>    - Return session info (ID, cwd, description)
>
> 3. Implement `tool_claude_code_sessions(ctx)`:
>    - List active sessions from SessionManager
>    - Format as readable text
>
> 4. Wire into TOOLS dict and TOOL_DEFINITIONS list (start and sessions only for now)
>
> Run `make check` after.

### Step 5.2: Implement `claude_code_send`

This is the most complex tool — it creates the SDK client (lazy), streams output, handles permissions, and logs results.

**Prompt:**
> Implement `tool_claude_code_send(ctx, session_id, prompt)`:
>
> 1. Get session from SessionManager — return error if expired or not found
> 2. If session has no SDK client yet, create one:
>    - Build `ClaudeAgentOptions` (or equivalent) with cwd, model, budget, and our `can_use_tool` callback
>    - Create `ClaudeSDKClient` (or use `query()` depending on SDK API)
>    - Store client on the Session object
> 3. Send the prompt to the SDK client
> 4. Stream messages:
>    - For each message, log via SessionLogger
>    - Publish progress events via ctx.publish() (so Mattermost shows "Claude Code: editing file..." etc.)
>    - Track cost and files changed
> 5. Touch the session (update last_active)
> 6. Return the summary from SessionLogger.build_summary()
>
> Handle errors gracefully — if the SDK call fails, log the error and return an error message (don't crash the session).
>
> Adapt to the actual SDK API discovered in Phase 1. The message streaming loop depends on what types the SDK yields.
>
> Run `make check` after.

### Step 5.3: Implement `claude_code_stop`

**Prompt:**
> Implement `tool_claude_code_stop(ctx, session_id)`:
>
> 1. Stop the session via SessionManager
> 2. If the session had an SDK client, close it (async)
> 3. Return confirmation with total cost
>
> Also implement `shutdown()` module function for skill deactivation:
> ```python
> async def shutdown():
>     """Close all sessions. Called on skill deactivation."""
>     if _session_manager:
>         await _session_manager.close_all()
> ```
>
> Wire all four tools into TOOLS and TOOL_DEFINITIONS.
>
> Run `make check` after.

### Step 5.4: Add shutdown hook to skill system

**Prompt:**
> The skill system currently has `init()` but no `shutdown()`. Add support:
>
> In `src/decafclaw/tools/skill_tools.py`, in `tool_activate_skill()`:
> - After loading native tools, also check for a `shutdown` function on the module
> - Store the shutdown function in the skill state (alongside extra_tools, etc.)
>
> We don't need to call shutdown on deactivation yet (skills persist for the conversation), but store the reference so we can call it during cleanup later.
>
> This is a minimal skill system evolution — just capturing the function, not adding a full deactivation flow.
>
> Run `make check && make test` after.

### Step 5.5: Commit

> Commit: "Implement Claude Code skill tools with SDK integration"

---

## Phase 6: SKILL.md & Config

**Goal:** Write the SKILL.md content and add config to the system.

### Step 6.1: Write SKILL.md content

**Prompt:**
> Write the full SKILL.md for the Claude Code skill. Follow the tabstack SKILL.md as a template:
>
> - Frontmatter: name, description (should trigger on coding requests), requires ANTHROPIC_API_KEY
> - Body: explain the tools, when to use each, session workflow (start → send → send → stop), cost awareness, permission model
> - Include a "Choosing the Right Approach" section — when to start a fresh session vs continue, when to use plan mode vs full execution
>
> The description field is critical — it's what makes DecafClaw's LLM decide to activate this skill. Make it trigger on coding requests: "fix this bug," "add a feature," "refactor," "write a test," "review this code," etc.

### Step 6.2: Add config fields

**Prompt:**
> Add the Claude Code config fields to `src/decafclaw/config.py`:
>
> ```python
> # Claude Code skill settings
> claude_code_model: str = ""  # empty = SDK default
> claude_code_budget_default: float = 2.0
> claude_code_budget_max: float = 10.0
> claude_code_session_timeout: str = "30m"
> ```
>
> Add corresponding `os.getenv()` lines in `load_config()`.
>
> Update CLAUDE.md and docs if needed.
>
> Run `make check && make test` after.

### Step 6.3: Commit

> Commit: "Add Claude Code SKILL.md and config fields"

---

## Phase 7: Integration Testing & Polish

### Step 7.1: Manual end-to-end test

**Prompt:**
> Test the full flow manually:
>
> 1. Start DecafClaw in interactive mode (`make run`)
> 2. Activate the claude_code skill
> 3. Start a session pointing at a test repo
> 4. Send a simple task ("list the files in this directory")
> 5. Verify permission flow works (or auto-approves for read-only)
> 6. Verify output log is written
> 7. Verify summary is returned
> 8. Stop the session
> 9. Verify session expiration works (start, wait, try to send)
>
> Document any issues found and fix them.

### Step 7.2: Update docs

**Prompt:**
> Update documentation:
>
> 1. `CLAUDE.md` — add to Key files list, add any new conventions
> 2. `README.md` — add Claude Code skill to skills section if applicable
> 3. `docs/skills.md` — add Claude Code skill documentation
> 4. Session notes — write summary

### Step 7.3: Final commit

> Commit: "Polish and document Claude Code skill integration"

---

## Risk Notes

- **Phase 1 is critical** — the SDK API may differ from our research. The entire plan is contingent on the SDK providing: async streaming, persistent sessions, and a `can_use_tool` callback. If any of these are missing, we need to redesign.
- **Phase 3 (permission bridge)** is the riskiest integration. The async blocking pattern inside the SDK's event loop is unproven. If it doesn't work, fallback options: (a) use `acceptEdits` mode and only confirm shell commands, (b) run the SDK in a separate thread with cross-thread event signaling.
- **Phase 5.2 (claude_code_send)** is the most complex single implementation. The message streaming loop depends heavily on the SDK's actual message types, which we'll only know after Phase 1.
- **Cost risk** — every SDK call costs real money. Keep test prompts minimal during development. Use `max_budget_usd` aggressively in testing.
