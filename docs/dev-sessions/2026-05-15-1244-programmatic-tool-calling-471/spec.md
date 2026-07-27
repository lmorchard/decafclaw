# Programmatic tool calling: LLM-written Python sandbox

**Goal:** Let the LLM author a Python script that calls a curated subset of decafclaw tools over local RPC, so deterministic multi-step work (read N pages → extract fields → compute → return one answer) runs as a single tool call with intermediate per-tool outputs kept off the conversation transcript.

**Source:** https://github.com/lmorchard/decafclaw/issues/471

## Current state

Today, multi-step deterministic work goes through the regular agent loop: each tool call is a round-trip, each result lands in the conversation transcript, each intermediate output consumes context. A "read 5 vault pages → extract dates → return earliest" task takes ~10+ tool messages.

Relevant existing surface:

- **Tool dispatch:** `tool_definitions.py:18-50` merges core/skill/MCP tools; `tool_execution.py:282-364` runs calls concurrently via `asyncio.Semaphore`, forks `ctx` per call (`fork_for_tool_call`), wraps in timeout (`tools/__init__.py:53-108`).
- **ToolResult shape:** `media.py:68-88` — `text` (required), optional `data` (dict for structured output), `end_turn`, `widget`. Rendered into the tool message as text + fenced JSON data block (`tool_execution.py:262-279`).
- **Skill loading:** `skills/__init__.py:215-292` discovers from workspace > agent > bundled > extra paths; `tools/skill_tools.py:42-88` loads `tools.py` via `importlib`, calls `_call_init(SkillConfig)` with merged env + `config.skills[name]`. Always-loaded bundled skills are auto-activated and exempt from deferral.
- **Subprocess pattern (reference):** `mcp_client.py:583-603` spawns stdio MCP servers via `StdioServerParameters` + `AsyncExitStack`; `mcp_client.py:628-703` wraps tool calls with `asyncio.wait_for` and `2^n` reconnect backoff. Framing is JSON-RPC line-delimited (delegated to the `mcp` SDK).
- **Confirmation gating:** `tools/confirmation.py:106-141` returns `{"approved": bool, ...}`. Only `shell`, `shell_background_start`, `send_email` (when not allowlisted), and `activate_skill` request confirmation today. Other vault/workspace/notes/http/tabstack tools complete without prompting.
- **No `read_only` metadata exists** on tool definitions today. Confirmation-vs-not is enforced inline per tool, not declared on the definition object.

Reference implementation (out-of-tree): `~/devel/hermes-agent/tools/code_execution_tool.py` — particularly `:60-68` (`SANDBOX_ALLOWED_TOOLS`), `:307-366` (UDS stub generation), `:439-557` (`_rpc_server_loop`), `:118-154` (`_scrub_child_env`).

## Desired end state

A new bundled skill at `src/decafclaw/skills/code_execution/` with:

- `SKILL.md` frontmatter declaring `always-loaded: true` and a tight description that emphasizes "for deterministic multi-step work where intermediate outputs are wasted context."
- `tools.py` exposing one tool — `code_execution` — that accepts a Python script string and returns a `ToolResult` with:
  - `text`: a structured summary — exit status, elapsed time, tool calls made (name + arg keys, no values), truncated stdout, truncated stderr.
  - `data`: structured dict with the same fields, machine-readable.
- The tool spawns a subprocess running the project's Python interpreter (via `sys.executable` from the agent process, so the worktree's venv is reused), writes a generated `decafclaw_tools.py` stub module and the LLM's `script.py` into a temp directory, and starts a Unix-domain-socket RPC server in the agent process.
- The stub module exposes a `dc` namespace; each allowlisted tool becomes `dc.<tool_name>(**kwargs) -> ToolResultProxy`. `ToolResultProxy` is a small dataclass with `.text: str`, `.data: dict | None`, `.error: str | None`. Calls serialize on a `threading.Lock` inside the subprocess (one RPC at a time).
- Sandbox-callable tools (v1):
  - **Read:** `vault_read`, `vault_search`, `workspace_read`, `workspace_list`, `notes_read`
  - **Safe writes:** `notes_append`, `vault_journal_append`, `vault_write` — `vault_write` gates on path: writes inside `agent/` are silent, writes outside return `NON_INTERACTIVE_ERROR` text (the existing path when `ctx.request_confirmation is None`); the RPC handler forces ctx into that mode.
  - **Web extraction:** `tabstack_extract_markdown`, `tabstack_extract_json`, `tabstack_research` — these live in the (lazy-loaded) `tabstack` skill, which must be activated in the conversation before scripts can use them. If not yet active, the RPC returns an error to the script (`_get_client()` raises in `skills/tabstack/tools.py:35-38`).
  - **Excluded for v1:** `http_request` (always gates on confirmation unless URL matches admin allow-pattern at `http_tools.py:99-115`; inside the sandbox this would deadlock or silently deny after 60s timeout). Tabstack covers the load-bearing web-fetch use case.
- The RPC server enforces the allowlist server-side (defense in depth — the script-side stub only generates allowlisted names, but the server rejects anything else too) and a per-script tool-call count cap.
- Hard limits enforced on the subprocess:
  - 300s wall-clock timeout (kill with SIGTERM, then SIGKILL after 2s grace).
  - 50 tool calls per script.
  - 50KB stdout cap (head 40% + tail 60% on overflow with an explicit `[... truncated N bytes ...]` marker).
  - 10KB stderr cap (same scheme).
  - 512MB address-space cap via `resource.setrlimit(RLIMIT_AS, ...)` (Linux/macOS only).
- Environment scrubbing on child env: passthrough only safe-prefix vars (PATH, HOME, USER, LANG, LC_*, TERM, TMPDIR, PYTHONPATH, VIRTUAL_ENV, DECAFCLAW_*); block any var whose name contains KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/PASSWD/AUTH.
- All caps configurable via `SkillConfig` dataclass in `tools.py`, resolved through `load_sub_config` (env-var prefix `SKILLS_CODE_EXECUTION_*`, `config.skills.code_execution` JSON override).

`ToolResult` shape returned to the LLM:

```
status: success|error|timeout|tool_call_limit
elapsed_seconds: 12.34
tool_calls: [
  {tool: "vault_read", args_keys: ["path"], duration_ms: 45, ok: true},
  ...
]
stdout: "<truncated to 50KB>"
stderr: "<truncated to 10KB>"
return_value: <last expression value if printable, else null>
```

## Design decisions

- **Decision:** Bundled, always-loaded skill (`skills/code_execution/`, `always-loaded: true`).
  - **Why:** Broadly useful enough to justify the always-loaded tool slot. The skill abstraction gives us `SkillConfig` for per-instance customization (allowlist tweaks, cap overrides) without inflating top-level `Config`. Bundled-only so `auto_approve` semantics apply if we ever need them.
  - **Rejected:** On-demand activation — adds a confirmation step per conversation for a tool whose value is "use it freely when the work fits." Core tool (no skill) — loses `SkillConfig` ergonomics and the skill discovery patterns we already have.

- **Decision:** Exclude confirmation-requiring tools (`shell`, `send_email`, `activate_skill`, etc.) from the allowlist.
  - **Why:** A subprocess can't pause for user confirmation mid-execution without elaborate RPC-driven UI pausing. The curated allowlist IS the trust boundary — anything in it must complete without prompting.
  - **Rejected:** Bridging confirmation events back via RPC (hermes doesn't do this; the LLM can call gated tools outside the sandbox when it needs them). Pre-approving a confirmation budget at sandbox entry (forces the LLM to declare write intent up front; can't branch dynamically).

- **Decision:** Subprocess sandbox via `sys.executable` + UDS, not in-process `exec()`.
  - **Why:** Isolation, kill-ability, hard wall-clock timeout, memory cap via `RLIMIT_AS`, fresh globals per call. `exec()` shares the agent's namespace and can't be timed out cleanly.
  - **Rejected:** `RestrictedPython`, custom AST walker — gives a false sense of safety and breaks many useful constructs.

- **Decision:** UDS with JSON-line framing for RPC.
  - **Why:** Stdlib-only, fast on macOS/Linux. Hand-rolled framing keeps the dependency surface flat (no JSON-RPC SDK, no MCP-style negotiation). Each request is one `{"tool": ..., "args": {...}}` line; each response is one `{"text": ..., "data": ..., "error": ...}` line.
  - **Rejected:** TCP loopback (binds a port unnecessarily, requires port allocation), shared memory (overkill), pipes (no concurrent r/w abstraction).

- **Decision:** `ToolResultProxy` dataclass returned from `dc.<tool>(...)` — `.text` / `.data` / `.error`.
  - **Why:** Mirrors decafclaw's existing `ToolResult` shape; scripts can branch cleanly on `if result.error`. Surfacing both `.text` and `.data` is important because many tools (e.g., `vault_search`) put the load-bearing payload in `.data` and only a summary in `.text`.
  - **Rejected:** Raw dict (less typed, less self-documenting in the stub), "smart" return (`.data` if present else `.text`, raises on error — too magical).

- **Decision:** Defense-in-depth allowlist check (both client stub and RPC server).
  - **Why:** The script can `import socket` and write raw bytes to the UDS — server-side enforcement is non-negotiable. The client-side stub generation just keeps `dc.*` clean for the LLM.
  - **Rejected:** Trusting the stub alone — the sandbox isn't a security boundary against the script, only against accidental tool surface expansion.

- **Decision:** Match hermes resource caps verbatim (300s / 50 calls / 50KB stdout / 10KB stderr / 512MB).
  - **Why:** Proven shape from the reference impl. Tighter caps will frustrate the load-bearing use case; looser caps blow up turn footprint.
  - **Rejected:** Tighter (120s/30 calls) — too short for "research 10 vault pages." Looser (600s/100 calls) — invites runaway scripts.

- **Decision:** Serialize tool calls within one script invocation (one in-flight RPC at a time via `threading.Lock` in the stub).
  - **Why:** The script is single-threaded by default; concurrency from inside the script would force us to fork the agent's `ctx` per concurrent call and is unlikely to be load-bearing for v1.
  - **Rejected:** Concurrent RPC — extra complexity for no clear v1 win. The outer `max_concurrent_tools` semaphore still allows multiple `code_execution` calls in parallel.

## Patterns to follow

- **Tool definition layout:** mirror an existing skill `tools.py` (e.g., `src/decafclaw/skills/vault/tools.py`). Export `TOOLS` dict, `TOOL_DEFINITIONS` list, `SkillConfig` dataclass, `init(config, skill_config)`.
- **SkillConfig resolution:** `tools/skill_tools.py:61-88` via `load_sub_config`.
- **Always-loaded frontmatter:** `skills/__init__.py:294-333` for activation contract.
- **Subprocess + AsyncExitStack:** `mcp_client.py:583-603` for spawn pattern; we won't use the `mcp` SDK but the lifecycle shape (spawn → init → call → cleanup via context manager) carries over.
- **Per-call timeout wrapping:** `tools/__init__.py:53-108` — `_run_with_cancel()` for racing the subprocess against the outer tool timeout. Note: the outer `code_execution` tool itself should override the default `tool_timeout_sec` (set `timeout=None` in its `TOOL_DEFINITIONS` entry so the 300s internal cap is authoritative), following the precedent at `tool_definitions.py` for `delegate_task` / `conversation_compact`.
- **Event publishing for progress:** `ctx.publish('tool_status', ...)` per RPC call so the UI can show a "running script… 12 tool calls so far" indicator if we want it.
- **`ToolResult.data` for structured output:** other tools that return both text + data (e.g., `vault_search`) for the rendering precedent.

## What we're NOT doing

- **Windows support.** UDS + `RLIMIT_AS` are POSIX-only. macOS + Linux for v1.
- **Network sandboxing.** The subprocess can `import urllib` and hit the network directly. The contract is "use `dc.http_get`" — we're not trying to enforce it with seccomp / network namespaces.
- **Filesystem sandboxing.** The script runs as the agent user with full FS access. No chroot, no `--restrict-to-cwd` flag. The trust boundary is the LLM-author + the allowlist, not the OS.
- **Persistent state between invocations.** Each call gets a fresh subprocess and fresh temp dir. No pickling locals across calls.
- **Streaming partial output.** stdout/stderr captured and returned at end of run. Progress events are at the per-RPC-call granularity, not per-line.
- **Concurrent RPC from inside one script.** Serialized via `threading.Lock`.
- **Bridging mid-script confirmation prompts back to the user.** Excluded tools stay excluded.
- **`shell` / `send_email` / `activate_skill` / writes-that-require-approval in the allowlist.** Period.
- **Auto-derived allowlist from tool metadata.** No `read_only: true` field on tool defs to lean on; v1 keeps the list hand-curated and inspectable in one place.
- **Class-of-bug analogues considered.** No comparable subprocess+RPC tool already lives in the agent — MCP is the closest, but its framing is delegated to the `mcp` SDK and lifecycle is server-managed. We're explicitly NOT generalizing this into a "subprocess RPC framework" — it's one tool with one purpose.

## Open questions

- **Should the `code_execution` tool description encourage or discourage use?** Default: phrasing emphasizes "for deterministic multi-step work where intermediate results are wasted context" — the LLM should NOT reach for it for one-off lookups. Tune wording during execute; run `make eval-tools` if disambiguation drifts vs. existing tools.
- **Where do we put the UDS socket + temp dir?** Default: `tempfile.mkdtemp(prefix="dc-codeexec-")` per invocation, cleaned up in a `finally`. Socket lives inside that dir as `rpc.sock`.
- **What happens if the script itself raises an unhandled exception?** Default: capture the traceback into stderr, exit non-zero, return `status: "error"` with the truncated traceback in stderr. Don't try to surface the exception object structurally to the LLM in v1.
- **How does the `dc` stub get imported in the script?** Default: the generated `decafclaw_tools.py` lives alongside `script.py` in the temp dir; the script imports as `from decafclaw_tools import dc`. The `dc` symbol is the module-level namespace object (an instance with `__getattr__` returning bound `_call(name, kwargs)` closures).
- **Logging on the agent side?** Default: log each RPC dispatch at DEBUG (tool name, arg keys, duration), summary at INFO (script start, script end with status/elapsed/call count). Errors at WARNING.
