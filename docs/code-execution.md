# Code Execution — Programmatic Tool Calling

The `code_execution` tool runs a small Python script in an isolated subprocess. The script can call a curated subset of decafclaw tools through a `dc.*` proxy and `print(...)` the values it actually wants returned to the conversation. Intermediate tool outputs stay inside the subprocess — only the script's stdout, stderr, and exit code come back into context.

Tracking issue: #471. See also [Tools Reference](tools.md) and [Skills](skills.md) — this is an always-loaded bundled skill that registers a single tool.

## Overview

The agent loop normally exchanges one tool call per LLM turn. For deterministic multi-step work — "read these 5 pages, extract one field from each, return the earliest" — that pattern is wasteful: every intermediate page body sits in conversation history, eating context, even though only the final answer matters. `code_execution` lets the LLM emit one script that drives multiple tool calls, where intermediate results stay in subprocess-local Python state and only the final `print` output crosses back.

The framing is **deterministic multi-step**, not "do anything in Python." If the work is exploratory ("not sure what I need yet"), interactive tool calls are still the right answer. If it's a single lookup, the direct tool call is the right answer. The sandbox is only worth it when intermediate per-tool outputs would be wasted context.

## Allowlist

The subprocess can call these 11 tools via `dc.<name>(...)`:

| Tool | Role inside the sandbox |
|------|-------------------------|
| `vault_read` | Read a vault page by path |
| `vault_search` | Semantic search over vault pages + journal |
| `vault_journal_append` | Append an entry to the journal |
| `vault_write` | Create/update a vault page (agent folder only — outside hits non-interactive error) |
| `workspace_read` | Read a file under the workspace |
| `workspace_list` | List workspace files / dirs |
| `notes_read` | Read the per-conversation scratchpad |
| `notes_append` | Append to the per-conversation scratchpad |
| `tabstack_extract_markdown` | Fetch a URL as readable markdown (Tabstack) |
| `tabstack_extract_json` | Fetch a URL extracted to structured JSON (Tabstack) |
| `tabstack_research` | Multi-source web research (Tabstack) |

Selection criteria: read-heavy tools and writes that are confined to the agent-trusted side of the trust boundary. Two categories are deliberately excluded:

- **Confirmation-gated tools** (`shell`, `send_email`, `vault_write` outside the agent folder, `http_request` outside admin allow-patterns). User confirmations don't route through the subprocess. Inside the sandbox these would either deadlock or fall through to a non-interactive error path. If the agent needs them, it should call them directly in a normal turn.
- **`http_request` specifically.** Even with an admin URL allow-pattern, it's a poor fit for batch use inside a script: the network surface is too broad to audit case-by-case, and the existing Tabstack tools cover the common need (fetch + extract).

Lazy-loaded skill tools (e.g. Tabstack) require the parent skill to be active in the calling turn — `dc.tabstack_research(...)` errors cleanly if `tabstack` hasn't been activated. The `vault`, `background`, and `mcp` skills (and now `code_execution` itself) are always-loaded and always available inside the sandbox.

## Caps & overrides

Defaults live on the `SkillConfig` dataclass in `src/decafclaw/skills/code_execution/tools.py`:

| Field | Default | Meaning |
|-------|---------|---------|
| `timeout_seconds` | `300.0` | Wall-clock cap on the subprocess. Tool-level outer timeout is disabled (`timeout=None`) so the skill owns its own kill switch. Expiry triggers a process-group SIGKILL — descendants of the script (if it spawned any via `subprocess`) die with it. |
| `max_tool_calls` | `50` | Cap on `dc.*` RPC invocations per script. Over-limit calls return an error response to the script; they do NOT hard-abort the subprocess. The script can catch `.error` and continue (but won't get any more dispatches). The sandbox reports `status="tool_call_limit"` when the script eventually exits if any over-limit dispatch was rejected. |
| `max_stdout_bytes` | `50_000` | Stdout truncation budget, measured in **bytes** (not code points). Over-budget output is reported as `head 40% + marker + tail` with an elided middle. The parent process reads at most ~4× the cap from the pipe and drains the rest, so a runaway `print` loop cannot OOM the agent. |
| `max_stderr_bytes` | `10_000` | Same byte-budget + bounded-read scheme for stderr. |
| `memory_cap_bytes` | `536_870_912` | 512 MB virtual-memory ceiling enforced via `RLIMIT_AS` in a `preexec_fn`. Linux only — `preexec_fn` is **not installed** on macOS because `RLIMIT_AS` can't be lowered below `RLIM_INFINITY` there (setrlimit raises). |

Override via env (`SKILLS_CODE_EXECUTION_TIMEOUT_SECONDS=...`, `SKILLS_CODE_EXECUTION_MAX_TOOL_CALLS=...`, etc.) or `config.skills.code_execution = {...}` in `data/{agent_id}/config.json`. Resolution order matches every other skill: dataclass defaults → `config.skills.code_execution` JSON → env vars.

## Security boundary

The sandbox is a *contextual* isolation, not a hardened jail. Treat the LLM that authors the script as a trusted insider — the boundary exists to keep the *tool surface* curated, not to defend against malicious code.

- **The script is an unrestricted Python interpreter.** Once launched, it can use any stdlib module — `subprocess`, `os`, `socket`, `urllib`, `pathlib`, etc. — directly, in addition to the `dc.*` proxy. There is **no** seccomp, no chroot, no network namespace, no syscall filtering. The script runs as a normal Python process under the agent's UID and shares the host's filesystem and network namespace. Anything the agent itself could do, this script can do. The sandbox is NOT a security boundary against the script — it's a *capability boundary* against accidental tool-surface expansion. Treat the LLM as a trusted author.
- **Subprocess isolation (env + cwd, not capabilities).** The script runs in a fresh `python` interpreter under a scrubbed env. The env scrub is allowlist-by-prefix (`PATH`, `HOME`, `USER`, `LANG`, `LC_*`, `TERM`, `TMPDIR`, `TZ`, `PYTHONPATH`, `VIRTUAL_ENV`, `DECAFCLAW_*`) plus a secret-substring deny (anything matching `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `PASSWD`, `AUTH` is dropped regardless of prefix). The working directory is a fresh per-call tempdir that's removed unconditionally on exit.
- **RPC over Unix-domain socket.** The subprocess talks to the parent only through newline-delimited JSON RPC over a per-call UDS (one JSON request per line; one JSON response per line). The proxy library (`_stub.py`) is the only convenient way to reach decafclaw tools from the script, but a determined script could open the same socket directly — the server enforces the allowlist regardless of what the proxy generates.
- **Confirmation bypass.** The parent's `request_confirmation` is set to `None` on the forked ctx that handles RPC. Tools that would otherwise prompt fall through to their `NON_INTERACTIVE_ERROR` path and return a `[error: ...]` result to the script, which sees it as `.error` on the proxy result.
- **Resource caps.** Wall-clock timeout, byte-budgeted stdout/stderr capture, and (on Linux) `RLIMIT_AS` memory cap. Stdout/stderr capture is *bounded in the parent* — reads at most ~4× the cap and drains the rest — so a runaway `print` loop can't OOM the agent before the truncation runs. On timeout the subprocess group is SIGKILL'd, taking any descendants with it.

Why `http_request` is excluded: it requires user confirmation unless the URL matches an admin-curated allow-pattern. Inside the sandbox, both branches misbehave — confirmation deadlocks (no prompt surface), and the non-interactive error path silently swallows the call without producing useful output. Cleaner to keep it out of the allowlist entirely.

## Adding tools to the allowlist

To add a new tool to `SANDBOX_ALLOWED_TOOLS`:

1. Add the tool name to the `SANDBOX_ALLOWED_TOOLS` tuple in `src/decafclaw/skills/code_execution/tools.py`.
2. Verify the tool does **not** call `ctx.request_confirmation(...)` — grep for `request_confirmation` in the tool's module. If it does, either skip adding it or confirm the `NON_INTERACTIVE_ERROR` path produces useful output (not a hang or empty result).
3. Verify the tool is in the active tool set when `code_execution` is invoked. Always-loaded skill tools and core priority-classified tools always are. Lazy-loaded skill tools require their parent skill to be activated in the same turn (the sandbox forks ctx but doesn't activate skills).
4. Re-run `make eval-tools` to check that the new tool's presence doesn't degrade disambiguation against the direct-call alternative — the LLM should still prefer the direct tool for single-call scenarios.

## Key files

- `src/decafclaw/skills/code_execution/SKILL.md` — agent-facing skill body (loaded into system prompt as always-loaded)
- `src/decafclaw/skills/code_execution/tools.py` — tool definition, RPC handler, `SkillConfig` defaults
- `src/decafclaw/skills/code_execution/_sandbox.py` — subprocess spawn, UDS RPC server, resource caps
- `src/decafclaw/skills/code_execution/_stub.py` — the `decafclaw_tools` proxy library injected into the subprocess
- `evals/tool_choice/code_execution.yaml` — disambiguation eval cases vs direct `vault_*` / `notes_*` calls
