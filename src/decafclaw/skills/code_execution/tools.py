"""Code-execution skill.

The `code_execution` tool runs a curated subset of decafclaw tools inside
an isolated Python subprocess so the agent can do deterministic multi-step
work without paying for intermediate tool outputs in context.

Phase 3: real tool dispatch. The sandbox's `dc.<tool>(...)` RPC calls are
routed to the actual `execute_tool` against a forked ctx with confirmation
disabled — confirmation-gated tools (e.g. `vault_write` outside the agent
folder) fall through to their NON_INTERACTIVE_ERROR branch.
"""

import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.skills.code_execution import _sandbox
from decafclaw.tools import execute_tool

log = logging.getLogger(__name__)


# Curated allowlist of tools the sandboxed script can call via `dc.<name>(...)`.
# Selection criteria: read-heavy or agent-folder-only writes that won't deadlock
# on confirmation prompts. `http_request` is excluded — it's confirmation-gated
# and would block. Tabstack tools assume the skill is activated; we surface the
# init error cleanly when it isn't.
SANDBOX_ALLOWED_TOOLS: tuple[str, ...] = (
    "vault_read", "vault_search", "vault_journal_append", "vault_write",
    "workspace_read", "workspace_list",
    "notes_read", "notes_append",
    "tabstack_extract_markdown", "tabstack_extract_json", "tabstack_research",
)


@dataclass
class SkillConfig:
    timeout_seconds: float = 300.0
    max_tool_calls: int = 50
    max_stdout_bytes: int = 50_000
    max_stderr_bytes: int = 10_000
    memory_cap_bytes: int = 512 * 1024 * 1024


# Resolved at activation via init(config, skill_config). Replaced wholesale
# rather than mutated so tests can compare against a fresh SkillConfig().
_settings: SkillConfig = SkillConfig()


def init(config, skill_config: SkillConfig) -> None:
    global _settings
    _settings = skill_config
    log.info(
        "code_execution skill initialized "
        f"(timeout={_settings.timeout_seconds}s, "
        f"max_tool_calls={_settings.max_tool_calls})"
    )


def _make_tool_handler(
    parent_ctx,
) -> Callable[[str, dict], Awaitable[dict]]:
    """Build the RPC handler that dispatches sandbox `dc.<tool>(...)` calls
    against the real tool registry on a forked ctx.

    The sandbox server already enforces the allowlist (see
    `_sandbox._serve_rpc`); the duplicate check here is defense in depth
    against a misbehaving stub that submits unknown names.
    """

    async def handler(tool_name: str, args: dict) -> dict:
        if tool_name not in SANDBOX_ALLOWED_TOOLS:
            return {
                "text": "",
                "data": None,
                "error": f"tool '{tool_name}' not in sandbox allowlist",
            }

        # Fork ctx so confirmation-gated tools fall through to their
        # NON_INTERACTIVE_ERROR paths instead of blocking the script.
        # Context is a plain class (not a dataclass) — copy.copy + explicit
        # overrides is the documented idiom.
        sandbox_ctx = copy.copy(parent_ctx)
        sandbox_ctx.request_confirmation = None
        # Note: ctx.tools.allowed is per-tool-restriction within a turn;
        # we use the explicit allowlist check above instead. Don't mutate
        # ctx.tools.allowed here — it would also affect concurrent tool
        # calls in the parent.

        try:
            result = await execute_tool(sandbox_ctx, tool_name, args)
        except Exception as exc:
            # _serve_rpc doesn't wrap handler() in try/except — an unhandled
            # exception here would hang the subprocess waiting for a reply.
            log.exception("sandbox tool '%s' raised", tool_name)
            return {"text": "", "data": None, "error": str(exc)}

        text = result.text or ""
        # decafclaw convention: error tools return text starting with "[error".
        error = None
        if text.startswith("[error"):
            error = text
            text = ""
        return {"text": text, "data": result.data, "error": error}

    return handler


def _render_result(s: _sandbox.SandboxResult, code: str) -> ToolResult:
    lines = [
        f"**status:** {s.status}",
        f"**elapsed:** {s.elapsed_seconds:.2f}s",
        f"**tool_calls:** {len(s.tool_calls)}",
    ]
    for c in s.tool_calls:
        lines.append(
            f"  - {c['tool']}({', '.join(c['args_keys'])}) "
            f"{'ok' if c['ok'] else 'err'} {c['duration_ms']}ms"
        )
    lines.append("**code:**\n```python\n" + code + "\n```")
    if s.stdout:
        lines.append("**stdout:**\n```\n" + s.stdout + "\n```")
    if s.stderr:
        lines.append("**stderr:**\n```\n" + s.stderr + "\n```")
    # The code_block widget renders inline collapsed with an "Open in
    # Canvas" affordance — much friendlier than the fenced text block
    # for non-trivial scripts. The text fallback above stays for TUI
    # users and for the LLM's own view of the result.
    widget = WidgetRequest(
        widget_type="code_block",
        data={
            "code": code,
            "language": "python",
            "filename": "script.py",
        },
    )
    return ToolResult(
        text="\n".join(lines),
        # Script body is intentionally NOT on `data` — it's already in the
        # rendered text (for TUI / LLM context) AND in the widget payload
        # (for web UI). Adding it here would auto-render a third copy via
        # the data → fenced-JSON convention, ballooning context for every
        # subsequent turn.
        data={
            "status": s.status,
            "elapsed_seconds": s.elapsed_seconds,
            "tool_calls": s.tool_calls,
            "stdout": s.stdout,
            "stderr": s.stderr,
            "exit_code": s.exit_code,
        },
        widget=widget,
    )


async def tool_code_execution(ctx, code: str) -> ToolResult:
    result = await _sandbox.run_script(
        ctx, code, _settings,
        handler=_make_tool_handler(ctx),
        allowed=SANDBOX_ALLOWED_TOOLS,
        progress_publish=ctx.publish,
    )
    return _render_result(result, code)


TOOLS = {"code_execution": tool_code_execution}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        # Always-loaded skill tools are force-promoted to critical regardless
        # of declared priority. "normal" reflects the underlying default
        # without misleading anyone reading this file.
        "priority": "normal",
        # The skill enforces its own wall-clock cap (SkillConfig.timeout_seconds)
        # in the sandbox itself; the generic per-tool timeout would only race
        # with it.
        "timeout": None,
        "function": {
            "name": "code_execution",
            "description": (
                "Run a Python script that calls a curated set of decafclaw "
                "tools via the `dc.*` proxy. Use ONLY when ALL THREE hold: "
                "(1) the work is genuinely multi-step (3+ tool calls), "
                "(2) the sequence is deterministic (you already know which "
                "calls and in what order), AND (3) intermediate per-tool "
                "outputs would be wasted context. Example: read 5 vault "
                "pages, extract a date from each, return only the earliest.\n"
                "\n"
                "DO NOT use for:\n"
                "- single tool calls — call vault_read / vault_search / "
                "notes_read / workspace_read / web_fetch / "
                "tabstack_extract_markdown directly. A single URL fetch "
                "is NOT a code_execution job.\n"
                "- work that requires user confirmation (shell, "
                "send_email, http_request — call those directly outside "
                "the sandbox)\n"
                "- exploratory work where you don't know what to compute "
                "yet (call tools interactively first)\n"
                "\n"
                "Allowlist: vault_read, vault_search, vault_journal_append, "
                "vault_write, workspace_read, workspace_list, notes_read, "
                "notes_append, tabstack_extract_markdown, "
                "tabstack_extract_json, tabstack_research. Limits: 300s "
                "wall-clock (subprocess killed on expiry); max 50 tool calls "
                "per script (over-limit calls return an error to the script — "
                "the script continues running but no further dc.* dispatches "
                "occur). Script imports the "
                "proxy as `from decafclaw_tools import dc`. Each "
                "`dc.<tool>(...)` returns a ToolResultProxy with `.text`, "
                "`.data`, `.error`. Every allowlisted tool populates "
                "`.text` only — `.data` is always None and must be parsed "
                "from `.text` (e.g. workspace_list returns one line per "
                "entry like '<name> (<bytes>B)'; vault_search returns a "
                "numbered list with '- <page>' bullets; "
                "tabstack_extract_json returns a JSON-serialized string "
                "you can json.loads()). `print(...)` what you want "
                "returned to the conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python script. Import `from decafclaw_tools "
                            "import dc` to access tools. `print(...)` what "
                            "you want returned to the conversation."
                        ),
                    },
                },
                "required": ["code"],
            },
        },
    },
]
