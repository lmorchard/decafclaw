"""Shell tool with confirmation — requires user approval before execution."""

import asyncio
import codecs
import fnmatch
import io
import json
import logging
import os
import re
import shlex
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from decafclaw.conversation_paths import conversation_dir
from decafclaw.output_truncation import format_size, tail_cut_to_bytes, truncate_tail, truncation_note
from decafclaw.security_monitor import SecurityStatus, evaluate_command, evaluate_command_llm
from decafclaw.shell_presets import (
    DEFAULT_AUX_APPROVAL_PRESETS,
    ShellPresetStore,
    format_rules,
    split_rules,
)

from ..media import ToolResult
from .confirmation import request_confirmation

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _allow_patterns_path(config) -> Path:
    """Path to the shell allow patterns file (outside workspace, admin-managed)."""
    return config.agent_path / "shell_allow_patterns.json"


def _load_allow_patterns(config) -> list[str]:
    """Load shell allow patterns from disk. Returns [] if missing or corrupt."""
    try:
        path = _allow_patterns_path(config)
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return [p for p in data if isinstance(p, str)]
        if isinstance(data, dict):
            raw = data.get("patterns")
            if isinstance(raw, list):
                return [p for p in raw if isinstance(p, str)]
        return []
    except (json.JSONDecodeError, OSError, TypeError, AttributeError) as e:
        log.warning(f"Could not read shell allow patterns: {e}")
        return []


def _save_allow_pattern(config, pattern: str) -> None:
    """Add a pattern to the allow list. Called by host-side confirmation handler."""
    path = _allow_patterns_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    patterns = _load_allow_patterns(config)
    if pattern not in patterns:
        patterns.append(pattern)
        path.write_text(json.dumps(patterns, indent=2) + "\n")
        log.info(f"Added shell allow pattern: {pattern}")


# Shell metacharacters that could chain additional commands. This tuple is a
# security boundary, so it is kept as a minimal *covering* set — each entry is
# a substring of every operator it needs to catch:
#   ";"   sequence
#   "&"   background, and covers "&&"
#   "|"   pipe, and covers "||"
#   "`"   command substitution (legacy)
#   "$("  command substitution
#   "<("  process substitution (input)
#   ">("  process substitution (output)
#   "\n"  newline as a statement separator
# Note this covers command *chaining* and execution only. Plain file redirection (`>`, `<`)
# is not blocked: it cannot introduce a command, and rejecting it would break common pipelines.
_SHELL_CHAIN_TOKENS = (";", "&", "|", "`", "$(", "<(", ">(", "\n")
_UNWILDCARDABLE_COMMANDS = {
    "sh",
    "bash",
    "zsh",
    "dash",
    "ksh",
    "csh",
    "tcsh",
    # Shell execution builtins
    "eval",
    "exec",
    "command",
    "builtin",
    # Privilege escalation & execution wrappers
    "sudo",
    "su",
    "doas",
    "env",
    "nohup",
    "xargs",
}
_VAR_ASSIGN_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*=.*$")
# fnmatch wildcards. A pattern containing any of these matches a *class* of
# commands rather than one literal command.
_GLOB_CHARS = ("*", "?", "[")


def _has_shell_metacharacters(command: str) -> bool:
    """Check if a command contains shell chaining/injection tokens.

    Detects command chaining operators (;, &&, ||, |, &, newline) and command
    substitutions (backticks, $()) outside quotes. Newlines and statement
    separators embedded inside quoted arguments ("..." or '...') are treated as
    literal data, not chaining tokens. Command substitution (`...` or $(...))
    inside double quotes is still detected since the shell evaluates it.
    """
    state = "normal"
    i = 0
    n = len(command)

    while i < n:
        ch = command[i]

        if state == "single":
            if ch == "'":
                state = "normal"
            i += 1
            continue

        if state == "double":
            if ch == "\\":
                if i + 1 >= n:
                    return True
                i += 2
                continue
            if ch == '"':
                state = "normal"
                i += 1
                continue
            if ch == "`":
                return True
            if ch in ("$", "<", ">") and i + 1 < n and command[i + 1] == "(":
                return True
            i += 1
            continue

        if ch == "\\":
            if i + 1 >= n:
                return True
            i += 2
            continue

        if ch == "'":
            state = "single"
            i += 1
            continue

        if ch == '"':
            state = "double"
            i += 1
            continue

        if ch in (";", "&", "|", "`", "\n"):
            return True
        if ch in ("$", "<", ">") and i + 1 < n and command[i + 1] == "(":
            return True

        i += 1

    if state != "normal":
        return True

    return False


def _get_first_command_token(command_or_pattern: str) -> str | None:
    """Extract the first command executable name, skipping leading env assignments and quotes."""
    try:
        tokens = shlex.split(command_or_pattern, posix=True)
    except ValueError:
        tokens = command_or_pattern.strip().split()

    if not tokens:
        return None

    for token in tokens:
        if _VAR_ASSIGN_RE.match(token):
            continue
        return Path(token).name

    return None


def _is_glob_pattern(pattern: str) -> bool:
    """Check if a pattern contains fnmatch wildcards."""
    return any(ch in pattern for ch in _GLOB_CHARS)


def _is_ineligible_wildcard_pattern(pattern: str) -> bool:
    """Check if a wildcard pattern is too dangerous to allow class matching (#966).

    Wildcard patterns targeting shell interpreters, execution primitives, or
    privilege wrappers (sh, bash, eval, sudo, env, etc.) are categorically
    ineligible: arbitrary commands must never be auto-approved via wildcards.
    Exact literal patterns (without *, ?, [) remain eligible for specific vetted scripts.
    """
    if not _is_glob_pattern(pattern):
        return False
    exe = _get_first_command_token(pattern)
    return exe in _UNWILDCARDABLE_COMMANDS


def _command_matches_pattern(command: str, patterns: list[str]) -> bool:
    """Check if a command matches any allow pattern (glob-style).

    Wildcard patterns never match a command carrying shell chaining tokens.
    ``_suggest_pattern`` mints wildcarded patterns from a single approved
    command (``python foo.py --a`` -> ``python foo.py *``), and fnmatch's
    ``*`` spans ``;``, ``|``, ``&&``, backticks and newlines — so without
    this guard, approving one command silently approves everything sharing
    its prefix, including ``python foo.py --a; rm -rf ~`` (#649).

    Literal patterns are exempt: they pin the command end to end, so there
    is no wildcard for an attacker-controlled suffix to slip through, and
    a user who allowlists ``git log | head -20`` means exactly that.

    The guard lives here rather than at the call sites so no future caller
    can forget it — the persisted-allowlist branch was missing it while the
    scoped-pattern branch had it.
    """
    chained = _has_shell_metacharacters(command)
    for pattern in patterns:
        if _is_ineligible_wildcard_pattern(pattern):
            continue
        if chained and _is_glob_pattern(pattern):
            continue
        if fnmatch.fnmatch(command, pattern):
            return True
    return False


def _split_raw_tokens(command: str, max_tokens: int = 3) -> list[str]:
    """Split command into up to max_tokens raw token substrings, preserving quotes."""
    tokens = []
    i = 0
    n = len(command)
    while i < n and len(tokens) < max_tokens:
        while i < n and command[i].isspace():
            i += 1
        if i >= n:
            break
        if len(tokens) == max_tokens - 1:
            tokens.append(command[i:].strip())
            break
        start = i
        state = "normal"
        while i < n:
            ch = command[i]
            if state == "single":
                if ch == "'":
                    state = "normal"
                i += 1
                continue
            if state == "double":
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    state = "normal"
                    i += 1
                    continue
                i += 1
                continue
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "single"
                i += 1
                continue
            if ch == '"':
                state = "double"
                i += 1
                continue
            if ch.isspace():
                break
            i += 1
        tokens.append(command[start:i])
    return tokens


def _suggest_pattern(command: str) -> str:
    """Generate a suggested allow pattern from a command.

    Heuristic: keep the executable and script/subcommand path, wildcard the args.
    Preserves raw token substrings so quotes are not stripped in the suggested pattern.
    Shell interpreters and eval are never wildcarded (#966).
    """
    exe = _get_first_command_token(command)
    # Never wildcard interpreters, execution primitives, or wrappers — keep exact command
    if exe in _UNWILDCARDABLE_COMMANDS:
        return command
    raw_tokens = _split_raw_tokens(command, max_tokens=3)
    if not raw_tokens:
        return command

    first = raw_tokens[0]

    # If second part looks like a file path or subcommand, keep it
    if len(raw_tokens) >= 2:
        second = raw_tokens[1]
        # Keep the second part if it looks like a path or known subcommand
        if "/" in second or "." in second:
            if len(raw_tokens) > 2:
                return f"{first} {second} *"
            return f"{first} {second}"
        if len(raw_tokens) <= 2:
            return command
        return f"{first} {second} *"

    return command


def _suggest_aux_approval_pattern(command: str, has_guidance: bool) -> str:
    """Generate cache pattern for aux-LLM approvals.

    When guidance/presets are active, state-changing commands (e.g. git checkout,
    git add, git commit) must not be wildcarded into dangerous catch-alls (like
    'git checkout *' which would auto-approve destructive 'git checkout -- .').
    Safe read-only commands (e.g. pytest, ruff, git status, git diff) can still
    use suggested wildcard patterns.
    """
    if not has_guidance:
        return _suggest_pattern(command)

    safe_wildcard_prefixes = (
        "pytest",
        "ruff check",
        "vitest",
        "npm test",
        "npm run test",
        "cargo test",
        "git status",
        "git diff",
        "git log",
        "git show",
    )
    cmd_stripped = command.strip()
    first_part = cmd_stripped.split()[0] if cmd_stripped.split() else ""
    first_two = " ".join(cmd_stripped.split()[:2]) if len(cmd_stripped.split()) >= 2 else ""

    if first_part in safe_wildcard_prefixes or first_two in safe_wildcard_prefixes:
        return _suggest_pattern(command)

    return cmd_stripped


DEFAULT_AUX_APPROVAL_PRESETS = DEFAULT_AUX_APPROVAL_PRESETS


def _saved_presets_path(config) -> Path:
    """Path to custom saved presets file in the agent data directory."""
    return config.agent_path / "shell_approval_presets.json"


def _get_preset_store(config) -> ShellPresetStore:
    """Get or create ShellPresetStore for the given config."""
    return ShellPresetStore(config)


def _load_saved_presets(config) -> dict[str, str]:
    """Load custom saved presets from disk. Returns {'preset_name': 'guidance text'}.

    Raises ValueError if file exists but contains invalid or corrupt data.
    """
    store = _get_preset_store(config)
    return store._load_saved_unlocked()


def _save_custom_preset(config, preset_name: str, rules: list[str]) -> tuple[bool, str]:
    """Save or append rules to a custom preset on disk via ShellPresetStore."""
    store = _get_preset_store(config)
    return store._save_custom_preset_sync(preset_name, rules)


def _get_all_presets(config) -> dict[str, str]:
    """Merge built-in presets with user-defined presets from config and saved presets."""
    store = _get_preset_store(config)
    return store.get_all_presets_dict_sync()


_warned_deprecated_guidance_files: set[str] = set()


def _warn_deprecated_guidance_file(config) -> None:
    """Warn once if legacy shell_approval_guidance.json is present on disk (#986)."""
    path = config.agent_path / "shell_approval_guidance.json"
    path_str = str(path)
    if path_str not in _warned_deprecated_guidance_files and path.exists():
        _warned_deprecated_guidance_files.add(path_str)
        log.warning(
            f"Found deprecated {path}. Dynamic persistent guidance has been removed (#986). "
            "Please configure global presets and guidance in config.json via "
            "'shell.active_aux_approval_presets' and 'shell.aux_approval_guidance'."
        )


def _load_guidance_text(ctx: "Context", raw: str) -> str:
    """Resolve guidance text, reading from a file if raw is an existing path.

    Guidance files must reside in admin-controlled locations (agent_path).
    Resolving inside workspace_path is forbidden because workspace files
    are agent-writable and would allow bypassing confirmation-gated policy.
    """
    text = raw.strip()
    if not text:
        return ""
    if "\n" not in text and len(text) < 512:
        p = Path(text)
        candidate: Path | None = None
        if p.is_absolute():
            try:
                p.resolve().relative_to(ctx.config.workspace_path.resolve())
                log.warning(f"Rejecting shell guidance file inside workspace: {text}")
                return text
            except ValueError:
                candidate = p
        else:
            candidate = ctx.config.agent_path / text

        if candidate and candidate.is_file():
            try:
                return candidate.read_text().strip()
            except OSError:
                pass
    return text


def resolve_aux_approval_guidance(ctx: "Context") -> list[str]:
    """Collect active aux-LLM approval guidance from presets, config, and session state."""
    _warn_deprecated_guidance_file(ctx.config)
    guidelines: list[str] = []

    # 1. Resolve presets (config + session - disabled)
    active_preset_names: list[str] = []
    config_active = getattr(ctx.config.shell, "active_aux_approval_presets", [])
    if isinstance(config_active, str):
        active_preset_names.extend(p.strip() for p in config_active.split(",") if p.strip())
    elif isinstance(config_active, (list, tuple, set)):
        active_preset_names.extend(str(p).strip() for p in config_active if str(p).strip())

    session_active = getattr(ctx.tools, "active_aux_approval_presets", [])
    if isinstance(session_active, str):
        active_preset_names.extend(p.strip() for p in session_active.split(",") if p.strip())
    elif isinstance(session_active, (list, tuple, set)):
        active_preset_names.extend(str(p).strip() for p in session_active if str(p).strip())

    disabled_presets = set(getattr(ctx.tools, "disabled_aux_approval_presets", []))

    all_presets = _get_all_presets(ctx.config)
    seen_presets = set()
    for name in active_preset_names:
        if name in seen_presets or name in disabled_presets:
            continue
        seen_presets.add(name)
        if name in all_presets:
            preset_text = all_presets[name].strip()
            if preset_text:
                guidelines.append(preset_text)
        else:
            log.warning(f"Unknown shell aux approval preset: {name}")

    # 2. Configured guidance string / file
    raw_guidance = getattr(ctx.config.shell, "aux_approval_guidance", "")
    if raw_guidance:
        loaded = _load_guidance_text(ctx, raw_guidance)
        if loaded and loaded not in guidelines:
            guidelines.append(loaded)

    # 3. Session-scoped guidance
    session_guidance = getattr(ctx.tools, "aux_approval_guidance", [])
    if isinstance(session_guidance, str):
        session_guidance = [session_guidance]
    for item in session_guidance:
        item_text = str(item).strip()
        if item_text and item_text not in guidelines:
            guidelines.append(item_text)

    return guidelines


def build_aux_approval_prompt(ctx: "Context", command: str) -> str:
    """Build the aux-LLM evaluation prompt for shell command auto-approval."""
    guidelines = resolve_aux_approval_guidance(ctx)

    base = (
        "You are evaluating a shell command for execution.\n"
        f"Command: {command}\n"
        f"Working Directory: {ctx.config.workspace_path}\n"
        "Determine if this is a low-risk command that should be auto-approved, or if it requires user confirmation.\n"
        'Return a JSON object: {"auto_approve": bool, "reason": "<string>", "risk": "low" | "medium" | "high", "suggested_rule": "<string or null>"}\n'
        "If auto_approve is false, formulate a concise, natural-language exception rule in suggested_rule phrased as a "
        "constrained condition under which this command or class of operation is acceptable in this repo "
        '(e.g. "Auto-approve gh pr create and git push during PR workflow in this repo"). '
        "If auto_approve is true or no rule makes sense, set suggested_rule to null.\n"
    )

    if not guidelines:
        policy = (
            "Only auto-approve low risk read-only or harmless commands (like ls, git status, cat). "
            "Do not auto-approve anything that modifies state, installs software, makes network requests, etc."
        )
        return f"{base}{policy}"

    guidelines_formatted = "\n".join(f"- {g}" for g in guidelines)
    policy = (
        "Default policy: Only auto-approve low risk read-only or harmless commands (like ls, git status, cat). "
        "Do not auto-approve anything that modifies state, installs software, makes network requests, etc., "
        "unless explicitly permitted by the additional approval guidelines below.\n\n"
        "Additional Approval Guidelines:\n"
        f"{guidelines_formatted}\n\n"
        "You may auto-approve commands that match these guidelines, provided they remain within the stated bounds "
        "and do not pose unexpected risk or escape the workspace."
    )
    return f"{base}{policy}"


async def check_shell_approval(ctx: "Context", command: str, tool_name: str = "shell", message: str = "") -> dict:
    """Check whether a shell command is approved (shared by shell + background tools).

    Returns {"approved": True} if auto-approved, or the user's confirmation result.
    """
    # 1. Tier 1 fast path: Immediately block catastrophically dangerous commands
    tier1 = evaluate_command(
        command,
        workspace_path=ctx.config.workspace_path,
        is_autonomous=ctx.is_unattended,
    )
    if tier1.status == SecurityStatus.BLOCK:
        log.warning(f"[{tool_name}] blocked by security monitor: {command} (reason: {tier1.reason})")
        return {
            "approved": False,
            "reason": f"Blocked by security monitor: {tier1.reason}",
        }

    # 2. Explicit allow patterns take precedence and bypass Tier 2 LLM latency (#966)
    if _command_matches_pattern(command, ctx.tools.preapproved_shell_patterns):
        log.info(f"[{tool_name}] pre-approved by scoped pattern: {command}")
        return {"approved": True}

    patterns = _load_allow_patterns(ctx.config)
    if _command_matches_pattern(command, patterns):
        log.info(f"[{tool_name}] auto-approved by pattern: {command}")
        return {"approved": True}

    # 3. Security monitor evaluation (Tier 1 sensitive patterns + Tier 2 LLM classification)
    decision = await evaluate_command_llm(
        command,
        ctx=ctx,
        workspace_path=ctx.config.workspace_path,
        is_autonomous=ctx.is_unattended,
    )
    if decision.status == SecurityStatus.BLOCK:
        log.warning(f"[{tool_name}] blocked by security monitor: {command} (reason: {decision.reason})")
        return {
            "approved": False,
            "reason": f"Blocked by security monitor: {decision.reason}",
        }

    if decision.status == SecurityStatus.ASK:
        log.info(
            f"[{tool_name}] security monitor requires explicit confirmation: {command} (reason: {decision.reason})"
        )
        if ctx.is_unattended:
            log.warning(f"[{tool_name}] denied on unattended turn (security monitor ASK decision): {command}")
            return {
                "approved": False,
                "reason": f"Security monitor requires explicit confirmation: {decision.reason}",
            }
        suggested_pattern = _suggest_pattern(command)
        ask_message = f"Shell command (security review requested): `{command}`"
        if decision.reason:
            ask_message += f"\nReason: {decision.reason}"
        result = await request_confirmation(
            ctx,
            tool_name=tool_name,
            command=command,
            message=message or ask_message,
            suggested_pattern=suggested_pattern,
        )
        if result.get("add_pattern"):
            _save_allow_pattern(ctx.config, suggested_pattern)
        return result

    # 4. Blanket tool pre-approval (e.g. allowed-tools: shell without scoped patterns)
    if "shell" in ctx.tools.preapproved or tool_name in ctx.tools.preapproved:
        log.info(f"[{tool_name}] pre-approved by command: {command}")
        return {"approved": True}
    decline_reason = ""
    decline_risk = ""
    suggested_rule = ""
    if ctx.config.shell.aux_approval_enabled:
        if _command_matches_pattern(command, ctx.tools.llm_approved_shell_patterns):
            log.info(f"[{tool_name}] auto-approved by session aux-LLM memory: {command}")
            return {"approved": True}

        try:
            had_guidance = bool(resolve_aux_approval_guidance(ctx))
            prompt = build_aux_approval_prompt(ctx, command)
            messages = [{"role": "user", "content": prompt}]
            response = await ctx.aux_approval_llm()(messages)
            raw_text = response.get("content", "").strip()

            import re as re_mod

            if "```" in raw_text:
                raw_text = re_mod.sub(r"^```(?:json)?\n?", "", raw_text, flags=re_mod.MULTILINE)
                raw_text = re_mod.sub(r"```$", "", raw_text, flags=re_mod.MULTILINE).strip()

            import json as json_mod

            data = json_mod.loads(raw_text)

            if data.get("auto_approve") is True:
                log.info(
                    f"[{tool_name}] auto-approved by aux LLM (risk: {data.get('risk')}): {command} - {data.get('reason')}"
                )
                suggested_pattern = _suggest_aux_approval_pattern(command, has_guidance=had_guidance)
                ctx.tools.llm_approved_shell_patterns.append(suggested_pattern)

                msg_content = f"Command: {command}\nRisk: {data.get('risk')}\nReason: {data.get('reason')}"
                if not ctx.skip_archive:
                    try:
                        from decafclaw.archive import append_message

                        append_message(
                            ctx.config,
                            ctx.conv_id,
                            {"role": "shell_approval", "tool": "shell auto-approval: ALLOWED", "content": msg_content},
                        )
                    except Exception as e:
                        log.error(f"Archive write failed for shell_approval: {e}")
                await ctx.publish(
                    "shell_approval",
                    command=command,
                    risk=data.get("risk", ""),
                    reason=data.get("reason", ""),
                    approved=True,
                )

                return {"approved": True}
            else:
                log.info(
                    f"[{tool_name}] aux LLM declined auto-approval (risk: {data.get('risk')}): {command} - {data.get('reason')}"
                )

                decline_reason = str(data.get("reason") or "").strip()
                decline_risk = str(data.get("risk") or "").strip()
                suggested_rule = str(data.get("suggested_rule") or "").strip()

                msg_content = f"Command: {command}\nRisk: {data.get('risk')}\nReason: {data.get('reason')}"
                if not ctx.skip_archive:
                    try:
                        from decafclaw.archive import append_message

                        append_message(
                            ctx.config,
                            ctx.conv_id,
                            {"role": "shell_approval", "tool": "shell auto-approval: DECLINED", "content": msg_content},
                        )
                    except Exception as e:
                        log.error(f"Archive write failed for shell_approval: {e}")
                await ctx.publish(
                    "shell_approval",
                    command=command,
                    risk=data.get("risk", ""),
                    reason=data.get("reason", ""),
                    approved=False,
                )
        except Exception as e:
            log.warning(f"[{tool_name}] aux LLM approval failed, falling through: {e}")

    suggested_pattern = _suggest_pattern(command)
    if ctx.is_unattended:
        # Nobody can answer a prompt on this turn: it would block for the 60s
        # timeout and then be synthesized into this same denial. Deny now, and
        # say why rather than letting it look like a user decision.
        log.warning(f"[{tool_name}] denied on unattended turn (task_mode={ctx.task_mode!r}): {command}")
        return {"approved": False, "reason": "unattended turn: command matches no allow pattern"}

    prompt_message = f"Shell command: `{command}`"
    if decline_risk:
        prompt_message += f"\nRisk: {decline_risk}"
    if decline_reason:
        prompt_message += f"\nReason: {decline_reason}"
    result = await request_confirmation(
        ctx,
        tool_name=tool_name,
        command=command,
        message=message or prompt_message,
        suggested_pattern=suggested_pattern,
        decline_reason=decline_reason,
        suggested_rule=suggested_rule,
    )
    if result.get("approved") and result.get("add_pattern"):
        _save_allow_pattern(ctx.config, suggested_pattern)
    if result.get("approved") and result.get("add_rule") and suggested_rule:
        # If the user explicitly provided a rule field (including empty), use it;
        # otherwise default to the reviewer's suggested_rule.
        rule_val = result.get("rule")
        new_rule = str(rule_val).strip() if rule_val is not None else suggested_rule.strip()
        if new_rule:
            if new_rule not in ctx.tools.aux_approval_guidance:
                ctx.tools.aux_approval_guidance.append(new_rule)
                log.info(f"[{tool_name}] added per-conversation aux approval rule: '{new_rule}'")
    return result


async def tool_shell(ctx: "Context", command: str) -> ToolResult:
    """Run a shell command after user confirmation."""
    log.info(f"[tool:shell] requesting confirmation for: {command}")

    result = await check_shell_approval(ctx, command, tool_name="shell")
    if not result.get("approved"):
        reason = result.get("reason") or "denied by user"
        log.info(f"[tool:shell] command denied: {command} (reason: {reason})")
        return ToolResult(text=f"[error: command denied: {reason}]")

    budget = ctx.config.shell.foreground_wait_budget_sec
    if budget is not None and budget > 0:
        return await _execute_command_with_handoff(ctx, command)
    return await _execute_command(ctx, command)


# Foreground shell total runtime limit (seconds). #1136 makes this
# configurable via shell.max_runtime_sec; until it lands, keep the historical
# hardcoded 30s. The ``timeout`` parameter on _execute_command is a test seam.
_DEFAULT_TIMEOUT_SEC = 30.0

# How long to wait for output readers after the process exits. A grandchild
# can hold a pipe open past its parent's exit; we don't block the turn on that
# — proceed with whatever was captured (same bound as #1136's drain).
_DRAIN_TIMEOUT_SEC = 5.0


class _StreamCollector:
    """Bounded in-memory accumulator for one subprocess stream (#922).

    Decodes chunks with a streaming UTF-8 incremental decoder (multibyte
    characters split across chunk boundaries stay valid), keeps a rolling tail
    of at most ``max_bytes`` UTF-8 bytes, counts total lines/bytes, and spills
    the full output to an artifact file once a limit is exceeded (capped at
    ``disk_cap`` bytes). Memory stays O(max_bytes), never the full output.
    """

    def __init__(self, path: Path, max_bytes: int, disk_cap: int, max_lines: int):
        self._path = path
        self._max_bytes = max_bytes
        self._disk_cap = disk_cap
        self._max_lines = max_lines
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._tail = ""  # rolling tail (decoded), bounded to max_bytes
        self._newline_count = 0
        self._open_line = False  # decoded so far doesn't end with a newline
        self.total_bytes = 0  # raw bytes read from the pipe
        self._spill: io.TextIOWrapper | None = None
        self._spilled_bytes = 0
        self.spilled = False  # spill file opened (stays true after close)
        self._spill_failed = False
        self._disk_capped = False

    @property
    def tail(self) -> str:
        return self._tail

    @property
    def total_lines(self) -> int:
        """Line count, splitlines() semantics (a trailing open line counts)."""
        return self._newline_count + (1 if self._open_line else 0)

    @property
    def spilled_path(self) -> Path | None:
        """The artifact path, or None if the stream never spilled (or the
        spill file could not be opened)."""
        return self._path if self.spilled else None

    def feed(self, chunk: bytes) -> None:
        """Feed one raw pipe read (EOF is b"" — call finish() instead)."""
        self._feed_text(self._decoder.decode(chunk), len(chunk))

    def finish(self) -> None:
        """Flush the incremental decoder at EOF (a partial multibyte character
        at the end of the stream decodes to U+FFFD — valid, never a crash)."""
        self._feed_text(self._decoder.decode(b"", final=True), 0)

    def _feed_text(self, text: str, raw_bytes: int) -> None:
        self.total_bytes += raw_bytes
        if not text:
            return
        self._newline_count += text.count("\n")
        self._open_line = not text.endswith("\n")

        # Spill decision (once): everything decoded so far is in the tail,
        # because the tail has only been trimmed once a limit was exceeded.
        if (
            self._spill is None
            and not self._spill_failed
            and (self.total_bytes > self._max_bytes or self.total_lines > self._max_lines)
        ):
            self._open_spill()

        # Live spill writes (disk-capped — the file stops growing, the
        # preview keeps collecting).
        if self._spill is not None:
            if self._spilled_bytes >= self._disk_cap:
                if not self._disk_capped:
                    log.warning(
                        f"[tool:shell] output artifact {self._path} reached the disk cap "
                        f"({format_size(self._disk_cap)}); stopping writes, preview continues"
                    )
                    self._disk_capped = True
            else:
                try:
                    self._spill.write(text)
                    self._spilled_bytes += len(text.encode("utf-8"))
                except OSError as exc:
                    log.warning(f"[tool:shell] output artifact write failed ({self._path}): {exc}")
                    self.close()

        # Rolling tail (bounded to max_bytes, character-aligned).
        self._tail += text
        if len(self._tail.encode("utf-8")) > self._max_bytes:
            self._tail = tail_cut_to_bytes(self._tail, self._max_bytes)

    def _open_spill(self) -> None:
        """Open the artifact and write everything captured so far (the
        untrimmed tail); live chunks follow, so every decoded piece lands in
        the file exactly once."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._spill = open(self._path, "w", encoding="utf-8")
        except OSError as exc:
            log.warning(f"[tool:shell] could not open output artifact {self._path}: {exc}")
            self._spill_failed = True
            return
        try:
            self._spill.write(self._tail)
            self._spilled_bytes = len(self._tail.encode("utf-8"))
        except OSError as exc:
            log.warning(f"[tool:shell] output artifact write failed ({self._path}): {exc}")
            self.close()
        else:
            self.spilled = True

    def close(self) -> None:
        """Close the spill file (idempotent). Call on every exit path."""
        if self._spill is not None:
            try:
                self._spill.close()
            except OSError as exc:
                log.debug(f"[tool:shell] closing output artifact {self._path} failed: {exc}")
            self._spill = None


async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    """Terminate the command's process group (SIGTERM, grace period, SIGKILL).

    Reuses the background skill's _kill_process (function-level import —
    module-level would cycle: that module imports check_shell_approval from
    here). Falls back to a plain kill if the skill is unavailable.
    """
    try:
        from decafclaw.skills.background.tools import _kill_process
    except Exception as exc:  # noqa: BLE001 - bundled skill should always import
        log.warning(f"[tool:shell] background _kill_process unavailable ({exc}); using plain kill")
        if process.returncode is None:
            process.kill()
        await process.wait()
        return
    await _kill_process(process)


def _close_transport(process: asyncio.subprocess.Process) -> None:
    """Deterministically close the subprocess transport (the background skill's
    _close_transport pattern): Process.wait() reaps the child but never closes
    the transport; asyncio only does that when the Process is GC'd, which can
    land on a dead event loop and surface as PytestUnraisableExceptionWarning.
    """
    transport = getattr(process, "_transport", None)
    if transport is not None:
        try:
            transport.close()
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            log.debug(f"[tool:shell] closing subprocess transport failed: {exc}")


async def _execute_command(ctx: "Context", command: str, timeout: float = _DEFAULT_TIMEOUT_SEC) -> ToolResult:
    """Execute a shell command and return the output.

    Streams stdout/stderr with bounded in-memory previews (#922): each stream
    keeps a rolling tail of at most ``max_tool_output_bytes`` and spills the
    full output to the conversation's tool_outputs/ artifact as it streams, so
    memory stays bounded regardless of output size. On timeout or cancellation
    the whole process group is killed and readers/files are closed cleanly.
    """
    log.info(f"[tool:shell] executing command: {command}")
    # Expose the runtime workspace explicitly so skill scripts can place
    # state there rather than guessing from $0. The cwd is already the
    # workspace (see docs/skills.md), but a named env var is unambiguous and
    # survives manual/out-of-cwd invocation. Sits alongside DECAFCLAW_REPO /
    # CONTRIB, which the skill loader sets the same way.
    env = {**os.environ, "DECAFCLAW_WORKSPACE": str(ctx.config.workspace_path)}
    max_bytes = ctx.config.max_tool_output_bytes
    if max_bytes is None:  # collection bounding is a safety property, not a display preference
        max_bytes = 51200
    max_lines = ctx.config.max_tool_output_lines
    disk_cap = ctx.config.max_tool_output_disk_bytes

    # Artifact naming: the per-call id in production; a short uuid when called
    # directly (tests) without a forked tool-call context.
    call_id = ctx.tools.current_call_id or uuid.uuid4().hex[:8]
    tool_outputs_dir = conversation_dir(ctx.config, ctx.conv_id, create=True) / "tool_outputs"
    stdout_collector = _StreamCollector(tool_outputs_dir / f"{call_id}.stdout.txt", max_bytes, disk_cap, max_lines)
    stderr_collector = _StreamCollector(tool_outputs_dir / f"{call_id}.stderr.txt", max_bytes, disk_cap, max_lines)

    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(ctx.config.workspace_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # own process group — timeout/cancel kills the whole tree
        env=env,
    )

    async def _read(stream: asyncio.StreamReader | None, collector: _StreamCollector) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                collector.feed(chunk)
            collector.finish()
        except Exception as exc:  # noqa: BLE001 - a reader error must not kill the turn
            log.debug(f"[tool:shell] output reader ended with error: {exc}")

    # Two concurrent readers — sequential draining deadlocks on full pipes.
    stdout_task = asyncio.create_task(_read(process.stdout, stdout_collector))
    stderr_task = asyncio.create_task(_read(process.stderr, stderr_collector))

    timed_out = False
    try:
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            log.info(f"[tool:shell] command timed out after {timeout:.0f}s; killing process group")
            await _kill_process_group(process)
        except asyncio.CancelledError:
            # Turn cancelled mid-command: kill the process group so nothing is
            # left unmanaged, then propagate (execute_single_tool produces the
            # "[cancelled: shell]" result).
            await _kill_process_group(process)
            raise

        # Drain both readers (bounded — a grandchild can hold a pipe open past
        # exit; proceed with what was captured).
        for task in (stdout_task, stderr_task):
            try:
                await asyncio.wait_for(task, timeout=_DRAIN_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                log.debug("[tool:shell] output drain timed out; proceeding with captured buffers")
            except asyncio.CancelledError:
                raise  # finally closes everything; wait_for already cancelled the reader
    finally:
        for task in (stdout_task, stderr_task):
            if not task.done():
                task.cancel()
        for task in (stdout_task, stderr_task):
            try:
                await task
            except asyncio.CancelledError:
                pass  # expected for cancelled readers; the stream is released below
            except Exception as exc:  # noqa: BLE001 - reader errors are logged in _read
                log.debug(f"[tool:shell] output reader ended with error: {exc}")
        stdout_collector.close()
        stderr_collector.close()
        _close_transport(process)

    exit_code = process.returncode
    stdout_text = truncate_tail(stdout_collector.tail, max_lines=max_lines, max_bytes=max_bytes).content
    stderr_text = truncate_tail(stderr_collector.tail, max_lines=max_lines, max_bytes=max_bytes).content

    output = stdout_text
    if stderr_text:
        output += f"\n[stderr]\n{stderr_text}"
    if not timed_out and exit_code is not None and exit_code != 0:
        output += f"\n[exit code: {exit_code}]"

    for label, collector in (("stdout", stdout_collector), ("stderr", stderr_collector)):
        exceeded = collector.total_bytes > max_bytes or collector.total_lines > max_lines
        if not exceeded:
            continue
        path = collector.spilled_path
        if path is not None:
            log.info(
                f"[tool:shell] {label} truncated: {format_size(collector.total_bytes)} -> "
                f"{format_size(len(collector.tail.encode('utf-8')))} preview, full output at {path}"
            )
            output += truncation_note(str(path))
        else:  # a limit was exceeded but the spill file could not be opened
            output += f"\n\n[Output truncated. Full {label} could not be saved ({format_size(collector.total_bytes)}).]"

    if timed_out:
        prefix = f"[error: command timed out after {timeout:.0f} seconds]"
        return ToolResult(text=f"{prefix}\n\n{output}" if output else prefix)

    return ToolResult(text=output or "(no output)")


# How long to wait for the output reader after a foreground command exits. A
# grandchild process can hold the pipes open past the parent's exit; we don't
# block the turn on that — proceed with whatever was captured (#1136).
_FOREGROUND_DRAIN_TIMEOUT_SEC = 5.0


async def _execute_command_with_handoff(ctx: "Context", command: str) -> ToolResult:
    """Run a command through the shared background-job runner, waiting up to
    the configured foreground budget (#1136).

    - Exits within the budget -> ordinary foreground result (same text shape as
      ``_execute_command``); the job is dropped without a completion record.
    - Budget expires -> the SAME process continues as a managed background job;
      returns its job handle. The existing completion path (archive record +
      notification + wake) fires exactly once when it exits.
    - Turn cancelled before the handoff -> the process is killed so nothing is
      left unmanaged, and the CancelledError propagates.

    Approval already happened in ``tool_shell``; this never re-approves.
    """
    # Function-level import: decafclaw.skills.background.tools imports
    # check_shell_approval from this module, so a module-level import would cycle.
    try:
        from decafclaw.skills.background.tools import _get_job_manager
    except Exception as exc:  # noqa: BLE001 - fail open to the legacy path
        log.warning(f"[tool:shell] background runner unavailable ({exc}); using blocking execution")
        return await _execute_command(ctx, command)

    budget = ctx.config.shell.foreground_wait_budget_sec
    max_runtime = ctx.config.shell.max_runtime_sec
    env = {**os.environ, "DECAFCLAW_WORKSPACE": str(ctx.config.workspace_path)}

    manager = _get_job_manager(ctx)
    try:
        job = await manager.start(
            command,
            str(ctx.config.workspace_path),
            max_lifetime=max_runtime,
            config=ctx.config,
            conv_id=ctx.conv_id,
            event_bus=ctx.event_bus,
            completion_tail_lines=ctx.config.background.default_completion_tail_lines,
            manager=ctx.manager,
            env=env,
        )
    except Exception as e:  # noqa: BLE001 - spawn failure must not escape
        return ToolResult(text=f"[error: failed to start command: {e}]")

    # Claim the job as foreground BEFORE waiting. While this is True the
    # reader's finally skips _finalize_job, so a command that settles as a
    # foreground result never also fires the background completion sequence.
    job.foreground = True

    try:
        await asyncio.wait_for(job.process.wait(), timeout=budget)
    except asyncio.TimeoutError:
        # Budget expired: hand off. Clearing the claim synchronously (no await
        # between read and write) means the reader's later finally sees
        # foreground=False and runs _finalize_job exactly once.
        job.foreground = False
        log.info(
            f"[tool:shell] command exceeded the {budget:.0f}s foreground budget; "
            f"handed off to background job {job.job_id} (pid={job.pid})"
        )
        return ToolResult(
            text=(
                f"Command exceeded the {budget:.0f}s foreground budget and was moved to "
                f"the background — the same process is still running.\n"
                f"- **Job ID:** `{job.job_id}`\n"
                f"- **PID:** {job.pid}\n"
                f"- **Command:** `{command}`\n"
                "Check progress with `shell_background_status` and stop it with "
                "`shell_background_stop`. You will be woken when it completes."
            ),
            data={"job_id": job.job_id, "status": "running", "command": command, "pid": job.pid},
        )
    except asyncio.CancelledError:
        # Turn cancelled before the handoff: kill the process so nothing is left
        # unmanaged, then propagate. No archive record / notification / wake.
        await manager.abort(job.job_id)
        raise

    # Process exited within the budget: drain the reader so the output deques
    # are complete, then settle as an ordinary foreground result.
    if job.reader_task is not None and not job.reader_task.done():
        try:
            await asyncio.wait_for(job.reader_task, timeout=_FOREGROUND_DRAIN_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            # A grandchild can hold the output pipes open past process exit;
            # proceed with whatever was captured. wait_for already cancelled the
            # reader, whose finally skips finalize and closes the transport.
            log.debug(f"[tool:shell] output drain timed out for {job.job_id}; proceeding with captured buffers")
        except asyncio.CancelledError:
            # Turn cancelled while draining after the process exited. The command
            # did not hand off; abort drops it without a completion record.
            await manager.abort(job.job_id)
            raise

    # The reader may not have recorded the exit code (drain timeout); the
    # process is reaped, so returncode is authoritative.
    if job.exit_code is None:
        job.exit_code = job.process.returncode

    stdout_text = "\n".join(job.stdout_buffer)
    stderr_text = "\n".join(job.stderr_buffer)
    output = stdout_text
    if stderr_text:
        output += f"\n[stderr]\n{stderr_text}"
    if job.exit_code is not None and job.exit_code != 0:
        output += f"\n[exit code: {job.exit_code}]"

    # Drop the job without settling it: it was never a background job. abort()
    # is idempotent — the kill and reader-cancel are no-ops here, and it pops
    # the job so nothing leaks in the manager.
    await manager.abort(job.job_id)
    return ToolResult(text=output or "(no output)")


async def tool_shell_patterns(ctx: "Context", action: str = "list", pattern: str = "") -> str | ToolResult:
    """Manage shell command allow patterns."""
    log.info(f"[tool:shell_patterns] action={action} pattern={pattern}")

    if action == "list":
        patterns = _load_allow_patterns(ctx.config)
        if not patterns:
            return "No shell allow patterns configured."
        lines = ["**Shell allow patterns:**\n"]
        for p in patterns:
            lines.append(f"- `{p}`")
        return "\n".join(lines)

    elif action == "add" and pattern:
        # This requires confirmation — we're modifying admin config
        result = await request_confirmation(
            ctx,
            tool_name="shell_patterns",
            command=f"Add shell allow pattern: {pattern}",
            message=f"Add shell allow pattern: `{pattern}`",
            force=True,
        )

        if not result.get("approved"):
            return ToolResult(text="[error: denied]")

        _save_allow_pattern(ctx.config, pattern)
        return f"Added shell allow pattern: `{pattern}`"
    elif action == "remove" and pattern:
        patterns = _load_allow_patterns(ctx.config)
        if pattern not in patterns:
            return f"Pattern `{pattern}` not found."
        patterns.remove(pattern)
        path = _allow_patterns_path(ctx.config)
        path.write_text(json.dumps(patterns, indent=2) + "\n")
        return f"Removed shell allow pattern: `{pattern}`"

    return ToolResult(text="[error: invalid action. Use 'list', 'add', or 'remove'.]")


async def tool_shell_guidance(
    ctx: "Context",
    action: str = "list",
    preset: str = "",
    rule: str = "",
    mode: str = "",
) -> str | ToolResult:
    """Manage aux-LLM shell auto-approval prompt guidance and situational presets."""
    _warn_deprecated_guidance_file(ctx.config)
    log.info(f"[tool:shell_guidance] action={action} preset={preset} rule={rule} mode={mode}")

    all_presets = _get_all_presets(ctx.config)

    if action == "list":
        from ..modes import get_all_modes

        all_modes = get_all_modes(ctx.config)
        active_mode = getattr(ctx, "active_mode", "default") or "default"
        mode_lines = ["### Session Modes\n"]
        for m_name, m_obj in sorted(all_modes.items()):
            active_marker = " [ACTIVE]" if m_name == active_mode else ""
            presets_str = ", ".join(f"`{p}`" for p in m_obj.presets) if m_obj.presets else "none"
            tools_str = ", ".join(f"`{t}`" for t in m_obj.promoted_tools) if m_obj.promoted_tools else "none"
            mode_lines.append(
                f"- **`{m_name}`**{active_marker}: {m_obj.description} (presets: {presets_str}; promoted tools: {tools_str})"
            )

        session_active = set(getattr(ctx.tools, "active_aux_approval_presets", []))
        session_disabled = set(getattr(ctx.tools, "disabled_aux_approval_presets", []))

        config_active_raw = getattr(ctx.config.shell, "active_aux_approval_presets", [])
        if isinstance(config_active_raw, (list, tuple, set)):
            config_active = set(config_active_raw)
        else:
            config_active = set(p.strip() for p in str(config_active_raw).split(",") if p.strip())

        lines = ["### Shell Auto-Approval Presets\n"]
        for name, desc in sorted(all_presets.items()):
            sources = []
            if name in config_active:
                sources.append("config")
            if name in session_active:
                sources.append("session")

            if name in session_disabled:
                status = " [DISABLED in session]"
            elif sources:
                status = f" [ACTIVE via {', '.join(sources)}]"
            else:
                status = ""

            lines.append(f"- **`{name}`**{status}: {desc}")

        lines.append("\n### Active Guidance Rules\n")
        raw_config_guidance = getattr(ctx.config.shell, "aux_approval_guidance", "").strip()
        has_rules = False
        if raw_config_guidance:
            loaded_cfg = _load_guidance_text(ctx, raw_config_guidance)
            lines.append(f"- *(config)*: {loaded_cfg}")
            has_rules = True

        for r in getattr(ctx.tools, "aux_approval_guidance", []):
            lines.append(f"- *(session)*: {r}")
            has_rules = True

        if not has_rules:
            lines.append("*(No custom guidance rules configured)*")

        return "\n".join(mode_lines + ["\n"] + lines)

    if ctx.is_unattended:
        task_mode = getattr(ctx, "task_mode", "")
        log.warning(f"[tool:shell_guidance] denied on unattended turn (task_mode={task_mode!r})")
        return ToolResult(
            text="[error: denied on unattended turn: modifying approval rules requires user confirmation]"
        )

    scope_desc = "for this conversation"

    if action == "set_mode":
        if not mode:
            return ToolResult(text="[error: 'mode' is required for action 'set_mode']")
        from ..modes import get_all_modes

        all_modes = get_all_modes(ctx.config)
        if mode not in all_modes:
            available = ", ".join(sorted(all_modes.keys()))
            return ToolResult(text=f"[error: unknown mode '{mode}'. Available: {available}]")

        target_mode = all_modes[mode]
        presets_desc = (
            ", ".join(f"`{p}`" for p in target_mode.presets)
            if target_mode.presets
            else "none (default interactive confirmation)"
        )
        tools_desc = ", ".join(f"`{t}`" for t in target_mode.promoted_tools) if target_mode.promoted_tools else "none"

        confirm_msg = (
            f"Switch session mode to `{mode}` {scope_desc}?\n"
            f"- Auto-approval presets: {presets_desc}\n"
            f"- Promoted tools: {tools_desc}"
        )
        result = await request_confirmation(
            ctx,
            tool_name="shell_guidance",
            command=f"Switch session mode to '{mode}' ({scope_desc})",
            message=confirm_msg,
            force=True,
        )
        if not result.get("approved"):
            return ToolResult(text="[error: denied]")

        ctx.active_mode = mode
        if getattr(ctx, "_parent_ctx", None):
            ctx._parent_ctx.active_mode = mode
        ctx.tools.active_aux_approval_presets.clear()
        ctx.tools.active_aux_approval_presets.extend(target_mode.presets)
        ctx.tools.disabled_aux_approval_presets.clear()
        ctx.tools.llm_approved_shell_patterns.clear()

        if ctx.manager and ctx.conv_id:
            ctx.manager.set_flag(ctx.conv_id, "active_mode", mode)
            ctx.manager.set_flag(ctx.conv_id, "active_aux_approval_presets", list(target_mode.presets))
            ctx.manager.set_flag(ctx.conv_id, "disabled_aux_approval_presets", [])
            ctx.manager.set_flag(ctx.conv_id, "llm_approved_shell_patterns", [])

        if ctx.conv_id:
            from ..archive import append_message

            append_message(ctx.config, ctx.conv_id, {"role": "mode", "content": mode})

        from ..events import emit_for_ctx

        emit = emit_for_ctx(ctx)
        if emit:
            await emit(
                {
                    "type": "mode_changed",
                    "conv_id": ctx.conv_id,
                    "mode": mode,
                    "presets": list(target_mode.presets),
                    "promoted_tools": list(target_mode.promoted_tools),
                }
            )

        return f"Switched session mode to `{mode}` {scope_desc}."

    elif action == "enable_preset":
        if not preset:
            return ToolResult(text="[error: 'preset' is required for action 'enable_preset']")
        if preset not in all_presets:
            available = ", ".join(sorted(all_presets.keys()))
            return ToolResult(text=f"[error: unknown preset '{preset}'. Available: {available}]")

        confirm_msg = f"Enable shell auto-approval preset `{preset}` {scope_desc}?"
        result = await request_confirmation(
            ctx,
            tool_name="shell_guidance",
            command=f"Enable shell auto-approval preset '{preset}' ({scope_desc})",
            message=confirm_msg,
            force=True,
        )
        if not result.get("approved"):
            return ToolResult(text="[error: denied]")

        if preset in ctx.tools.disabled_aux_approval_presets:
            ctx.tools.disabled_aux_approval_presets.remove(preset)
        if preset not in ctx.tools.active_aux_approval_presets:
            ctx.tools.active_aux_approval_presets.append(preset)

        return f"Enabled shell auto-approval preset `{preset}` {scope_desc}."

    elif action == "disable_preset":
        if not preset:
            return ToolResult(text="[error: 'preset' is required for action 'disable_preset']")

        confirm_msg = f"Disable shell auto-approval preset `{preset}` {scope_desc}?"
        result = await request_confirmation(
            ctx,
            tool_name="shell_guidance",
            command=f"Disable shell auto-approval preset '{preset}' ({scope_desc})",
            message=confirm_msg,
            force=True,
        )
        if not result.get("approved"):
            return ToolResult(text="[error: denied]")

        if preset in ctx.tools.active_aux_approval_presets:
            ctx.tools.active_aux_approval_presets.remove(preset)
        if preset not in ctx.tools.disabled_aux_approval_presets:
            ctx.tools.disabled_aux_approval_presets.append(preset)

        # Invalidate aux-approval session cache when policy is narrowed
        ctx.tools.llm_approved_shell_patterns.clear()

        return f"Disabled shell auto-approval preset `{preset}` {scope_desc}."
    elif action == "add_rule":
        if not rule:
            return ToolResult(text="[error: 'rule' is required for action 'add_rule']")

        confirm_msg = f"Add shell auto-approval rule {scope_desc}:\n> {rule}"
        result = await request_confirmation(
            ctx,
            tool_name="shell_guidance",
            command=f"Add shell auto-approval rule: {rule} ({scope_desc})",
            message=confirm_msg,
            force=True,
        )
        if not result.get("approved"):
            return ToolResult(text="[error: denied]")

        if rule not in ctx.tools.aux_approval_guidance:
            ctx.tools.aux_approval_guidance.append(rule)

        return f"Added shell auto-approval rule {scope_desc}: '{rule}'"

    elif action == "remove_rule":
        if not rule:
            return ToolResult(text="[error: 'rule' is required for action 'remove_rule']")

        confirm_msg = f"Remove shell auto-approval rule {scope_desc}:\n> {rule}"
        result = await request_confirmation(
            ctx,
            tool_name="shell_guidance",
            command=f"Remove shell auto-approval rule: {rule} ({scope_desc})",
            message=confirm_msg,
            force=True,
        )
        if not result.get("approved"):
            return ToolResult(text="[error: denied]")

        if rule in ctx.tools.aux_approval_guidance:
            ctx.tools.aux_approval_guidance.remove(rule)

        # Invalidate aux-approval session cache when policy is narrowed
        ctx.tools.llm_approved_shell_patterns.clear()

        return f"Removed shell auto-approval rule {scope_desc}: '{rule}'"

    elif action == "save_preset":
        preset_clean = (preset or "").strip()
        if not preset_clean:
            return ToolResult(text="[error: 'preset' name is required for action 'save_preset']")

        if preset_clean in DEFAULT_AUX_APPROVAL_PRESETS:
            return ToolResult(
                text=f"[error: cannot overwrite built-in preset '{preset_clean}'. Please choose a custom preset name.]"
            )

        # Rules to save: explicit rule argument if supplied, otherwise all active conversation guidance
        if rule and rule.strip():
            rules_to_save = [rule.strip()]
        else:
            rules_to_save = [r.strip() for r in getattr(ctx.tools, "aux_approval_guidance", []) if r.strip()]

        if not rules_to_save:
            return ToolResult(
                text="[error: no guidance rules to save. Pass rule='...' or add rules to the conversation first.]"
            )

        try:
            saved_presets = _load_saved_presets(ctx.config)
        except ValueError as e:
            return ToolResult(
                text=f"[error: cannot save preset because {_saved_presets_path(ctx.config)} is unreadable or malformed: {e}]"
            )
        is_existing = preset_clean in saved_presets or preset_clean in getattr(
            ctx.config.shell, "aux_approval_presets", {}
        )
        action_verb = "Update" if is_existing else "Save"

        rules_formatted = "\n".join(f"> - {r}" for r in rules_to_save)
        confirm_msg = f"{action_verb} custom shell auto-approval preset `{preset_clean}` with rules:\n{rules_formatted}"
        result = await request_confirmation(
            ctx,
            tool_name="shell_guidance",
            command=f"{action_verb} shell auto-approval preset '{preset_clean}'",
            message=confirm_msg,
            force=True,
        )
        if not result.get("approved"):
            return ToolResult(text="[error: denied]")

        try:
            store = _get_preset_store(ctx.config)
            is_new, updated_text = await store.append_rules_from_tool(preset_clean, rules_to_save)
        except ValueError as e:
            return ToolResult(text=f"[error: failed to save preset: {e}]")
        status_msg = "Created" if is_new else "Updated"
        return f"{status_msg} custom shell auto-approval preset `{preset_clean}`:\n{updated_text}"

    return ToolResult(
        text="[error: invalid action. Use 'list', 'set_mode', 'enable_preset', 'disable_preset', 'add_rule', 'remove_rule', or 'save_preset'.]"
    )


SHELL_TOOLS = {
    "shell": tool_shell,
    "shell_patterns": tool_shell_patterns,
    "shell_guidance": tool_shell_guidance,
}

SHELL_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "low",
        "writes": True,
        "function": {
            "name": "shell_patterns",
            "description": (
                "Manage shell command allow patterns. Patterns auto-approve matching "
                "shell commands without confirmation. Use 'list' to see current patterns, "
                "'add' to add a new pattern (requires confirmation), 'remove' to remove one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "remove"],
                        "description": "Action to perform (default: list)",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (for add/remove). Example: 'python scripts/*.py *'",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "shell_guidance",
            "description": (
                "Manage aux-LLM shell auto-approval prompt guidance, situational presets, and session modes. "
                "Guidance rules, enabled presets, and session modes are scoped to the current conversation. "
                "Call at the start of software development, testing, or GitHub workflows to reduce approval friction on routine commands. "
                "Use 'list' to view available modes, presets, and active rules. "
                "Use 'set_mode' with mode='name' to switch conversation mode (e.g. 'default', 'dev', 'research', 'admin'). "
                "Use 'enable_preset' or 'disable_preset' with preset='name' to toggle situational presets (e.g. 'developer', 'github'). "
                "Use 'add_rule' or 'remove_rule' with rule='text' to add or remove custom auto-approval prompt guidelines for this conversation. "
                "Use 'save_preset' with preset='name' to bundle active conversation rules (or a specific rule) into a reusable custom preset on disk. "
                "Modifying approval rules or modes requires user confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list",
                            "set_mode",
                            "enable_preset",
                            "disable_preset",
                            "add_rule",
                            "remove_rule",
                            "save_preset",
                        ],
                        "description": "Action to perform (default: list)",
                    },
                    "mode": {
                        "type": "string",
                        "description": "Mode name for action 'set_mode' (e.g. 'default', 'dev', 'research', 'admin')",
                    },
                    "preset": {
                        "type": "string",
                        "description": "Preset name (for enable_preset / disable_preset / save_preset). E.g. 'developer', 'github', 'my_project'",
                    },
                    "rule": {
                        "type": "string",
                        "description": "Guidance rule text (for add_rule / remove_rule, or optional specific rule for save_preset). E.g. 'Auto-approve pytest and ruff in workspace'",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "timeout": None,
        "writes": True,
        "function": {
            "name": "shell",
            "description": (
                "Run a shell command. REQUIRES USER CONFIRMATION before execution "
                "unless the command matches an admin-configured allow pattern. "
                "The command runs in the workspace directory. Use for tasks that "
                "need system interaction: checking disk space, running scripts, "
                "installing packages, etc. The user will see the command and must "
                "approve it before it runs. If a foreground time budget is configured, "
                "a command that exceeds it is automatically moved to the background: "
                "you receive a job ID and can inspect or stop it with "
                "shell_background_status / shell_background_stop — you do not need to "
                "predict whether a command will run long."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
]
