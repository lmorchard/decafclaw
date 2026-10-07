"""Shell tool with confirmation — requires user approval before execution."""

import fnmatch
import json
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from decafclaw.security_monitor import SecurityStatus, evaluate_command, evaluate_command_llm

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
            return data
        if isinstance(data, dict):
            return data.get("patterns", [])
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
#   "\n"  newline as a statement separator
# Note this covers command *chaining* only. Redirection (`>`, `<`) is not
# blocked: it cannot introduce a second command, and rejecting it would break
# too many legitimate invocations.
_SHELL_CHAIN_TOKENS = (";", "&", "|", "`", "$(", "\n")
_SHELL_INTERPRETERS = {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh"}
_SHELL_WRAPPERS = {"env", "sudo", "nohup"}
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

    Additionally, if the command invokes an interpreter or eval (e.g. sh -c,
    bash -c, eval), script arguments undergo a second parsing pass by the shell.
    Those script arguments are checked recursively so quoted chaining operators
    cannot bypass confirmation (#966).
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
            if ch == "$" and i + 1 < n and command[i + 1] == "(":
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
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            return True

        i += 1

    if state != "normal":
        return True

    # Check for nested evaluation in subshells or eval
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return True

    if not argv:
        return False

    nested_script = _extract_subshell_script(argv)
    if nested_script is not None and _has_shell_metacharacters(nested_script):
        return True

    return False


def _parse_effective_command(argv: list[str]) -> tuple[str, list[str]] | None:
    """Extract effective command and its argument list, skipping wrappers and env vars.

    Handles wrappers (env, sudo, nohup), env options (including env -S), and
    NAME=value variable assignments preceding commands (#966 review).
    """
    if not argv:
        return None

    tokens = list(argv)
    idx = 0

    while idx < len(tokens):
        token = tokens[idx]

        # Skip shell environment variable assignments (e.g. FOO=bar)
        if _VAR_ASSIGN_RE.match(token):
            idx += 1
            continue

        base = Path(token).name
        if base in _SHELL_WRAPPERS:
            idx += 1
            if base == "env":
                while idx < len(tokens):
                    arg = tokens[idx]
                    if arg in ("-u", "--unset", "-C", "--chdir") and idx + 1 < len(tokens):
                        idx += 2
                    elif arg.startswith("-S") or arg.startswith("--split-string"):
                        if arg in ("-S", "--split-string"):
                            if idx + 1 < len(tokens):
                                split_str = tokens[idx + 1]
                                idx += 2
                                try:
                                    sub_tokens = shlex.split(split_str, posix=True)
                                    tokens = tokens[:idx] + sub_tokens + tokens[idx:]
                                except ValueError:
                                    pass
                        else:
                            split_val = arg[2:] if arg.startswith("-S") else arg.split("=", 1)[1]
                            idx += 1
                            try:
                                sub_tokens = shlex.split(split_val, posix=True)
                                tokens = tokens[:idx] + sub_tokens + tokens[idx:]
                            except ValueError:
                                pass
                    elif arg.startswith("-"):
                        idx += 1
                    elif _VAR_ASSIGN_RE.match(arg):
                        idx += 1
                    else:
                        break
            elif base == "sudo":
                while idx < len(tokens):
                    arg = tokens[idx]
                    if arg in ("-u", "-g", "-p", "-h", "-c", "-C") and idx + 1 < len(tokens):
                        idx += 2
                    elif arg.startswith("-"):
                        idx += 1
                    else:
                        break
            elif base == "nohup":
                while idx < len(tokens) and tokens[idx].startswith("-"):
                    idx += 1
            continue

        return base, tokens[idx + 1 :]

    return None


def _extract_subshell_script(argv: list[str]) -> str | None:
    """Extract nested script argument if command invokes eval or a shell interpreter with -c.

    Normalizes execution wrappers (env, sudo, nohup) and recognizes bundled shell flags
    like -lc or -ec (#966 review). Returns the script string if found, or None.
    """
    parsed = _parse_effective_command(argv)
    if parsed is None:
        return None
    cmd_name, args = parsed

    if cmd_name == "eval":
        return " ".join(args) if args else None

    if cmd_name in _SHELL_INTERPRETERS:
        for idx, arg in enumerate(args):
            if arg.startswith("-") and not arg.startswith("--"):
                if "c" in arg:
                    if idx + 1 < len(args):
                        return args[idx + 1]
                    return None
            elif arg == "--":
                break
            elif not arg.startswith("-"):
                break

    return None


def _is_glob_pattern(pattern: str) -> bool:
    """Check if a pattern contains fnmatch wildcards."""
    return any(ch in pattern for ch in _GLOB_CHARS)


def _is_ineligible_wildcard_pattern(pattern: str) -> bool:
    """Check if a wildcard pattern is too dangerous to allow class matching (#966)."""
    if not _is_glob_pattern(pattern):
        return False
    try:
        parts = shlex.split(pattern, posix=True)
    except ValueError:
        parts = pattern.strip().split()
    if not parts:
        return False

    parsed = _parse_effective_command(parts)
    if parsed is None:
        return False
    cmd_name, args = parsed

    if cmd_name == "eval":
        return True
    if cmd_name in _SHELL_INTERPRETERS:
        for opt in args:
            if opt.startswith("-") and not opt.startswith("--") and "c" in opt:
                return True
    return False


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
    Preserves raw token substrings so quotes are not stripped in the suggested pattern (#966 review).
    """
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        argv = command.strip().split()

    # Never wildcard interpreter/eval commands (#966 review)
    if _extract_subshell_script(argv) is not None:
        return command

    raw_tokens = _split_raw_tokens(command, max_tokens=3)
    if not raw_tokens:
        return command

    exe = raw_tokens[0]

    # If second part looks like a file path or subcommand, keep it
    if len(raw_tokens) >= 2:
        second = raw_tokens[1]
        # Keep the second part if it looks like a path or known subcommand
        if "/" in second or "." in second:
            if len(raw_tokens) > 2:
                return f"{exe} {second} *"
            return f"{exe} {second}"
        if len(raw_tokens) <= 2:
            return command
        return f"{exe} {second} *"

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


DEFAULT_AUX_APPROVAL_PRESETS: dict[str, str] = {
    "developer": (
        "Auto-approve standard software development commands within active workspace repositories, "
        "including running test suites (e.g. pytest, npm test, vitest), linters and formatters (e.g. ruff, black, eslint, prettier), "
        "build commands, and non-destructive local git operations (e.g. git status, git diff, git log, git add, git commit, git branch, "
        "git checkout, git switch). "
        "Do not auto-approve destructive operations (e.g. git reset --hard, deleting uncommitted work), "
        "or commands operating outside the workspace."
    ),
    "github": (
        "Auto-approve GitHub CLI (gh) commands for issue/PR inspection and safe workflow management "
        "(e.g. gh issue list/view, gh pr list/view/diff/checkout/create, gh run list/view). "
        "Do not auto-approve destructive operations (such as repository deletion or deleting releases/tags)."
    ),
}


def _get_all_presets(config) -> dict[str, str]:
    """Merge built-in presets with user-defined presets from config."""
    raw_custom = getattr(config.shell, "aux_approval_presets", {})
    if isinstance(raw_custom, str):
        try:
            custom = json.loads(raw_custom)
            if not isinstance(custom, dict):
                custom = {}
        except (json.JSONDecodeError, ValueError):
            custom = {}
    elif isinstance(raw_custom, dict):
        custom = raw_custom
    else:
        custom = {}
    return {**DEFAULT_AUX_APPROVAL_PRESETS, **custom}


def _persistent_guidance_path(config) -> Path:
    """Path to persistent shell approval guidance and preset overrides."""
    return config.agent_path / "shell_approval_guidance.json"


def _load_persistent_guidance(config) -> dict:
    """Load persistent guidance from disk. Returns {'active_presets': [], 'disabled_presets': [], 'rules': []}."""
    path = _persistent_guidance_path(config)
    if not path.exists():
        return {"active_presets": [], "disabled_presets": [], "rules": []}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {"active_presets": [], "disabled_presets": [], "rules": []}
        return {
            "active_presets": [str(p) for p in data.get("active_presets", [])],
            "disabled_presets": [str(p) for p in data.get("disabled_presets", [])],
            "rules": [str(r) for r in data.get("rules", [])],
        }
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read shell approval guidance: {e}")
        return {"active_presets": [], "disabled_presets": [], "rules": []}


def _save_persistent_preset(config, preset: str) -> None:
    path = _persistent_guidance_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_persistent_guidance(config)
    changed = False
    if preset in data["disabled_presets"]:
        data["disabled_presets"].remove(preset)
        changed = True
    if preset not in data["active_presets"]:
        data["active_presets"].append(preset)
        changed = True
    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n")
        log.info(f"Saved persistent shell approval preset: {preset}")


def _remove_persistent_preset(config, preset: str) -> None:
    path = _persistent_guidance_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_persistent_guidance(config)
    changed = False
    if preset in data["active_presets"]:
        data["active_presets"].remove(preset)
        changed = True
    if preset not in data["disabled_presets"]:
        data["disabled_presets"].append(preset)
        changed = True
    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n")
        log.info(f"Persistently disabled shell approval preset: {preset}")


def _save_persistent_rule(config, rule: str) -> None:
    path = _persistent_guidance_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_persistent_guidance(config)
    if rule not in data["rules"]:
        data["rules"].append(rule)
        path.write_text(json.dumps(data, indent=2) + "\n")
        log.info(f"Saved persistent shell approval rule: {rule}")


def _remove_persistent_rule(config, rule: str) -> None:
    path = _persistent_guidance_path(config)
    if not path.exists():
        return
    data = _load_persistent_guidance(config)
    if rule in data["rules"]:
        data["rules"].remove(rule)
        path.write_text(json.dumps(data, indent=2) + "\n")
        log.info(f"Removed persistent shell approval rule: {rule}")


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
    guidelines: list[str] = []

    # 1. Resolve presets (config + disk + session - disabled)
    active_preset_names: list[str] = []
    config_active = getattr(ctx.config.shell, "active_aux_approval_presets", [])
    if isinstance(config_active, str):
        active_preset_names.extend(p.strip() for p in config_active.split(",") if p.strip())
    elif isinstance(config_active, (list, tuple, set)):
        active_preset_names.extend(str(p).strip() for p in config_active if str(p).strip())

    persisted = _load_persistent_guidance(ctx.config)
    active_preset_names.extend(persisted.get("active_presets", []))

    session_active = getattr(ctx.tools, "active_aux_approval_presets", [])
    if isinstance(session_active, str):
        active_preset_names.extend(p.strip() for p in session_active.split(",") if p.strip())
    elif isinstance(session_active, (list, tuple, set)):
        active_preset_names.extend(str(p).strip() for p in session_active if str(p).strip())

    disabled_presets = set(getattr(ctx.tools, "disabled_aux_approval_presets", [])) | set(
        persisted.get("disabled_presets", [])
    )

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

    # 3. Persistent rules from disk
    for r in persisted.get("rules", []):
        r_text = str(r).strip()
        if r_text and r_text not in guidelines:
            guidelines.append(r_text)

    # 4. Session-scoped guidance
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
        'Return a JSON object: {"auto_approve": bool, "reason": "<string>", "risk": "low" | "medium" | "high"}\n'
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
        result = await request_confirmation(
            ctx,
            tool_name=tool_name,
            command=command,
            message=message or f"Shell command (security review requested): `{command}`",
            suggested_pattern=suggested_pattern,
        )
        if result.get("add_pattern"):
            _save_allow_pattern(ctx.config, suggested_pattern)
        return result

    # 4. Blanket tool pre-approval (e.g. allowed-tools: shell without scoped patterns)
    if "shell" in ctx.tools.preapproved or tool_name in ctx.tools.preapproved:
        log.info(f"[{tool_name}] pre-approved by command: {command}")
        return {"approved": True}
    if ctx.config.shell.aux_approval_enabled:
        if _command_matches_pattern(command, ctx.tools.llm_approved_shell_patterns):
            log.info(f"[{tool_name}] auto-approved by session aux-LLM memory: {command}")
            return {"approved": True}

        try:
            had_guidance = bool(resolve_aux_approval_guidance(ctx))
            prompt = build_aux_approval_prompt(ctx, command)
            messages = [{"role": "user", "content": prompt}]
            response = await ctx.aux_llm()(messages)
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

    result = await request_confirmation(
        ctx,
        tool_name=tool_name,
        command=command,
        message=message or f"Shell command: `{command}`",
        suggested_pattern=suggested_pattern,
    )
    if result.get("add_pattern"):
        _save_allow_pattern(ctx.config, suggested_pattern)
    return result


async def tool_shell(ctx: "Context", command: str) -> ToolResult:
    """Run a shell command after user confirmation."""
    log.info(f"[tool:shell] requesting confirmation for: {command}")

    result = await check_shell_approval(ctx, command, tool_name="shell")
    if not result.get("approved"):
        reason = result.get("reason") or "denied by user"
        log.info(f"[tool:shell] command denied: {command} (reason: {reason})")
        return ToolResult(text=f"[error: command denied: {reason}]")

    return _execute_command(ctx, command)


def _execute_command(ctx: "Context", command: str) -> ToolResult:
    """Execute a shell command and return the output."""
    log.info(f"[tool:shell] executing command: {command}")
    # Expose the runtime workspace explicitly so skill scripts can place
    # state there rather than guessing from $0. The cwd is already the
    # workspace (see docs/skills.md), but a named env var is unambiguous and
    # survives manual/out-of-cwd invocation. Sits alongside DECAFCLAW_REPO /
    # CONTRIB, which the skill loader sets the same way.
    env = {**os.environ, "DECAFCLAW_WORKSPACE": str(ctx.config.workspace_path)}
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(ctx.config.workspace_path),
            env=env,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return ToolResult(text=output or "(no output)")
    except subprocess.TimeoutExpired:
        return ToolResult(text="[error: command timed out after 30 seconds]")


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
    persistent: bool = False,
) -> str | ToolResult:
    """Manage aux-LLM shell auto-approval prompt guidance and situational presets."""
    log.info(f"[tool:shell_guidance] action={action} preset={preset} rule={rule} persistent={persistent}")

    all_presets = _get_all_presets(ctx.config)

    if action == "list":
        persisted = _load_persistent_guidance(ctx.config)
        session_active = set(getattr(ctx.tools, "active_aux_approval_presets", []))
        session_disabled = set(getattr(ctx.tools, "disabled_aux_approval_presets", []))

        config_active_raw = getattr(ctx.config.shell, "active_aux_approval_presets", [])
        if isinstance(config_active_raw, (list, tuple, set)):
            config_active = set(config_active_raw)
        else:
            config_active = set(p.strip() for p in str(config_active_raw).split(",") if p.strip())

        persistent_active = set(persisted.get("active_presets", []))
        persistent_disabled = set(persisted.get("disabled_presets", []))

        lines = ["### Shell Auto-Approval Presets\n"]
        for name, desc in sorted(all_presets.items()):
            sources = []
            if name in config_active:
                sources.append("config")
            if name in persistent_active:
                sources.append("persistent")
            if name in session_active:
                sources.append("session")

            if name in session_disabled:
                status = " [DISABLED in session]"
            elif name in persistent_disabled:
                status = " [DISABLED persistently]"
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

        for r in persisted.get("rules", []):
            lines.append(f"- *(persistent)*: {r}")
            has_rules = True

        for r in getattr(ctx.tools, "aux_approval_guidance", []):
            lines.append(f"- *(session)*: {r}")
            has_rules = True

        if not has_rules:
            lines.append("*(No custom guidance rules configured)*")

        return "\n".join(lines)

    if ctx.is_unattended:
        task_mode = getattr(ctx, "task_mode", "")
        log.warning(f"[tool:shell_guidance] denied on unattended turn (task_mode={task_mode!r})")
        return ToolResult(
            text="[error: denied on unattended turn: modifying approval rules requires user confirmation]"
        )

    scope_desc = "persistently (across all conversations)" if persistent else "for this conversation"

    if action == "enable_preset":
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

        if persistent:
            _save_persistent_preset(ctx.config, preset)

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

        if persistent:
            _remove_persistent_preset(ctx.config, preset)

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

        if persistent:
            _save_persistent_rule(ctx.config, rule)

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

        if persistent:
            _remove_persistent_rule(ctx.config, rule)

        if rule in ctx.tools.aux_approval_guidance:
            ctx.tools.aux_approval_guidance.remove(rule)

        # Invalidate aux-approval session cache when policy is narrowed
        ctx.tools.llm_approved_shell_patterns.clear()

        return f"Removed shell auto-approval rule {scope_desc}: '{rule}'"
    return ToolResult(
        text="[error: invalid action. Use 'list', 'enable_preset', 'disable_preset', 'add_rule', or 'remove_rule'.]"
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
        "function": {
            "name": "shell_guidance",
            "description": (
                "Manage aux-LLM shell auto-approval prompt guidance and situational presets. "
                "Use 'list' to view available presets and active rules. "
                "Use 'enable_preset' or 'disable_preset' with preset='name' to toggle situational presets (e.g. 'developer', 'github'). "
                "Use 'add_rule' or 'remove_rule' with rule='text' to add or remove custom auto-approval prompt guidelines. "
                "Set persistent=true to save changes permanently across all conversations (defaults to false, session-only). "
                "Modifying approval rules requires user confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "enable_preset", "disable_preset", "add_rule", "remove_rule"],
                        "description": "Action to perform (default: list)",
                    },
                    "preset": {
                        "type": "string",
                        "description": "Preset name (for enable_preset / disable_preset). E.g. 'developer', 'github'",
                    },
                    "rule": {
                        "type": "string",
                        "description": "Guidance rule text (for add_rule / remove_rule). E.g. 'Auto-approve pytest and ruff in workspace'",
                    },
                    "persistent": {
                        "type": "boolean",
                        "description": "Whether the change should persist across all future conversations (default: false, session-only)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "shell",
            "description": (
                "Run a shell command. REQUIRES USER CONFIRMATION before execution "
                "unless the command matches an admin-configured allow pattern. "
                "The command runs in the workspace directory. Use for tasks that "
                "need system interaction: checking disk space, running scripts, "
                "installing packages, etc. The user will see the command and must "
                "approve it before it runs."
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
