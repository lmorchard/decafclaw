"""Pre-execution security monitor for tool calls.

Evaluates shell commands before execution to classify them as ALLOW, BLOCK, or ASK.
Includes Tier 1 pattern matching and Tier 2 LLM classification for ambiguous commands.
"""

import json
import logging
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


class SecurityStatus(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ASK = "ASK"


@dataclass(frozen=True)
class SecurityDecision:
    status: SecurityStatus
    reason: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.status == SecurityStatus.BLOCK

    @property
    def is_allowed(self) -> bool:
        return self.status == SecurityStatus.ALLOW

    @property
    def requires_confirmation(self) -> bool:
        return self.status == SecurityStatus.ASK


# Known-dangerous command patterns (regexes) -> BLOCK
DANGEROUS_PATTERNS = [
    (
        re.compile(r"\brm\s+.*-(?:[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)\b"),
        "Dangerous recursive force deletion (rm -rf)",
    ),
    (
        re.compile(r"\brm\s+.*-(?:[a-zA-Z]*r|--recursive)\b"),
        "Dangerous recursive deletion (rm -r)",
    ),
    (
        re.compile(r"\bgit\s+push\s+.*(?:--force|-f)\b"),
        "Dangerous forced git push (git push --force)",
    ),
    (
        re.compile(r"\bcurl\s+.*(?:-X\s*POST|--request\s+POST|-XPOST)\b", re.IGNORECASE),
        "External HTTP POST request via curl",
    ),
    (
        re.compile(r"\bwget\s+.*--post-(?:data|file)\b", re.IGNORECASE),
        "External HTTP POST request via wget",
    ),
    (
        re.compile(r"\bchmod\b"),
        "System permission modification (chmod)",
    ),
    (
        re.compile(r"\bchown\b"),
        "System ownership modification (chown)",
    ),
    (
        re.compile(r"\b(?:kill|pkill|killall)\b"),
        "Process termination command (kill)",
    ),
]

# Sensitive command patterns -> ASK (explicit user confirmation required)
SENSITIVE_PATTERNS = [
    (
        re.compile(
            r"\b(?:npm|pnpm|yarn|pip|pip3|cargo|brew)\s+(?:install|add|update|upgrade|remove|uninstall)\b",
            re.IGNORECASE,
        ),
        "Package installation or dependency modification",
    ),
    (
        re.compile(r"\bgit\s+push\b", re.IGNORECASE),
        "Git push operation",
    ),
    (
        re.compile(r"\b(?:curl|wget)\b", re.IGNORECASE),
        "External network request via curl/wget",
    ),
]

# Tokens that indicate shell chaining or complex subshell execution
CHAIN_TOKENS = (";", "&", "|", "`", "$(", "\n")

# Ambiguous command constructs that warrant Tier 2 LLM classification
AMBIGUOUS_PATTERNS = [
    re.compile(r"\|"),  # Piping
    re.compile(r"\b(?:eval|base64|sh\s+-c|bash\s+-c|python\s+-c|perl\s+-e)\b", re.IGNORECASE),  # Encoded/eval execution
    re.compile(r"#"),  # Inline comments in shell command
    re.compile(r"`|\$\("),  # Subshell substitution
]


# Standard system directories containing binaries or special devices that are allowed at index 0 or as devices
SYSTEM_EXEC_DIRS = {
    Path("/bin"),
    Path("/usr/bin"),
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
    Path("/usr/libexec"),
    Path("/sbin"),
    Path("/usr/sbin"),
    Path("/System"),
}

ALLOWED_DEVICES = {
    Path("/dev/null"),
    Path("/dev/stdout"),
    Path("/dev/stderr"),
    Path("/dev/zero"),
}


def is_ambiguous_command(command: str) -> bool:
    """Check if a shell command is complex/ambiguous and warrants Tier 2 LLM evaluation."""
    return any(p.search(command) for p in AMBIGUOUS_PATTERNS)


def evaluate_command(
    command: str,
    workspace_path: str | Path | None = None,
    is_autonomous: bool = False,
    is_child_agent: bool = False,
) -> SecurityDecision:
    """Evaluate a shell command using Tier 1 pattern matching and return a SecurityDecision."""
    if not command or not command.strip():
        return SecurityDecision(SecurityStatus.ALLOW, "Empty command")

    # Tier 1: Check known-dangerous command patterns
    for pattern, reason in DANGEROUS_PATTERNS:
        if pattern.search(command):
            log.warning(f"[security_monitor] BLOCKED dangerous pattern ({reason}): {command}")
            return SecurityDecision(SecurityStatus.BLOCK, f"Command contains blocked pattern: {reason}")

    # Tier 1: Check out-of-workspace file operations
    resolved_workspace = Path(workspace_path or Path.cwd()).resolve()

    try:
        tokens = shlex.split(command, posix=True)
    except Exception:
        # Fallback to simple whitespace split if shlex fails on unclosed quotes
        tokens = command.split()

    for idx, token in enumerate(tokens):
        # Ignore options/flags
        if token.startswith("-") and not token.startswith("-/") and not token.startswith("-~"):
            continue

        # Check if token represents a path
        if token.startswith("/") or token.startswith("~") or ".." in token:
            try:
                if token.startswith("~"):
                    token_path = Path(token).expanduser().resolve()
                elif token.startswith("/"):
                    token_path = Path(token).resolve()
                    # If absolute path does not exist on system (e.g. /skills/...), check if virtual relative path in workspace
                    if not token_path.exists() and not token_path.parent.exists():
                        rel_in_ws = (resolved_workspace / token.lstrip("/")).resolve()
                        rel_in_parent = (resolved_workspace.parent / token.lstrip("/")).resolve()
                        if rel_in_ws.is_relative_to(resolved_workspace) or rel_in_parent.is_relative_to(resolved_workspace.parent):
                            continue
                else:
                    token_path = (resolved_workspace / token).resolve()

                # Allowed devices
                if token_path in ALLOWED_DEVICES:
                    continue

                # If token is the executable command (idx 0), allow system binaries
                if idx == 0 and any(token_path.is_relative_to(sys_dir) for sys_dir in SYSTEM_EXEC_DIRS):
                    continue

                # Check if path is within workspace
                if not token_path.is_relative_to(resolved_workspace):
                    log.warning(
                        f"[security_monitor] BLOCKED out-of-workspace path: {token} "
                        f"(resolved: {token_path}, workspace: {resolved_workspace})"
                    )
                    return SecurityDecision(
                        SecurityStatus.BLOCK,
                        f"Attempted operation outside workspace path: {token}",
                    )
            except Exception as exc:
                log.debug(f"[security_monitor] Error evaluating path token {token!r}: {exc}")

    # Tier 1: Check sensitive command patterns requiring ASK status
    for pattern, reason in SENSITIVE_PATTERNS:
        if pattern.search(command):
            log.info(f"[security_monitor] ASK sensitive pattern ({reason}): {command}")
            return SecurityDecision(
                SecurityStatus.ASK,
                f"Sensitive command requires explicit confirmation: {reason}",
            )

    # Tier 1: Check context-dependent rules for autonomous or child agent execution
    if is_autonomous or is_child_agent:
        context_type = "Autonomous" if is_autonomous else "Child agent"
        if any(tok in command for tok in CHAIN_TOKENS):
            log.info(f"[security_monitor] ASK chained command during {context_type.lower()} execution: {command}")
            return SecurityDecision(
                SecurityStatus.ASK,
                f"{context_type} execution of chained shell command requires explicit confirmation",
            )

    return SecurityDecision(SecurityStatus.ALLOW, "Command passed security monitor checks")


async def evaluate_command_llm(
    command: str,
    ctx: "Context | None" = None,
    workspace_path: str | Path | None = None,
    is_autonomous: bool = False,
    is_child_agent: bool = False,
    force_llm: bool = False,
) -> SecurityDecision:
    """Evaluate a command using Tier 1 pattern matching and Tier 2 LLM classification."""
    tier1_decision = evaluate_command(
        command,
        workspace_path=workspace_path,
        is_autonomous=is_autonomous,
        is_child_agent=is_child_agent,
    )

    if tier1_decision.status in (SecurityStatus.BLOCK, SecurityStatus.ASK):
        return tier1_decision

    if ctx is None:
        return tier1_decision

    should_run_tier2 = force_llm or is_ambiguous_command(command)
    if not should_run_tier2:
        return tier1_decision

    try:
        from decafclaw.llm import call_llm

        context_label = "Autonomous" if is_autonomous else ("Child agent" if is_child_agent else "Interactive")
        prompt = (
            "You are a pre-execution security monitor classifier for shell commands.\n"
            "Analyze the following shell command for potential security risks, dangerous intent, "
            "obfuscation, unintended destructive actions, or data exfiltration.\n\n"
            f"Workspace directory: {workspace_path or Path.cwd()}\n"
            f"Execution context: {context_label}\n\n"
            f"Command to analyze:\n```\n{command}\n```\n\n"
            "Classify the command into one of the following decisions:\n"
            "- ALLOW: Safe command with clear, non-destructive intent within the workspace.\n"
            "- BLOCK: Dangerous command, data exfiltration, destructive action, or unauthorized system modification.\n"
            "- ASK: Ambiguous command, sensitive side effects, or complex intent requiring explicit human confirmation.\n\n"
            "Respond ONLY with a valid JSON object in the format:\n"
            '{"status": "ALLOW" | "BLOCK" | "ASK", "reason": "<brief explanation>"}'
        )

        messages = [{"role": "user", "content": prompt}]
        response = await call_llm(ctx.config, messages)
        raw_text = response.get("content", "").strip()

        if "```" in raw_text:
            raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text, flags=re.MULTILINE)
            raw_text = re.sub(r"```$", "", raw_text, flags=re.MULTILINE).strip()

        data = json.loads(raw_text)
        status_str = str(data.get("status", "")).upper()
        reason = str(data.get("reason", "LLM classifier evaluation"))

        if status_str in SecurityStatus.__members__:
            log.info(f"[security_monitor] Tier 2 LLM decision for {command!r}: {status_str} ({reason})")
            return SecurityDecision(SecurityStatus[status_str], f"LLM classifier: {reason}")

    except Exception as exc:
        log.warning(f"[security_monitor] Tier 2 LLM classification failed: {exc}")
        return SecurityDecision(
            SecurityStatus.ASK,
            "LLM classifier failed on ambiguous command; manual confirmation required",
        )

    return tier1_decision

