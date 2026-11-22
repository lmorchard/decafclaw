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
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from decafclaw.activity import Activity, derive_activity
from decafclaw.conversation_paths import conversation_dir
from decafclaw.loop_breaker import fingerprint
from decafclaw.output_truncation import format_size, tail_cut_to_bytes, truncate_tail, truncation_note
from decafclaw.security_monitor import (
    SecurityStatus,
    evaluate_command,
    evaluate_command_llm,
    venv_executable_path,
)
from decafclaw.shell_judge import AUX_RISK_CATEGORIES, render_aux_approval_prompt
from decafclaw.shell_readonly import is_read_only_command
from decafclaw.subprocess_limits import describe_kill, resource_cap_prefix
from decafclaw.tool_outputs import CAPPED, COMPLETE, format_handle, record_artifact

from ..media import ToolResult
from .confirmation import request_confirmation

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def shell_cap_prefix(config, label: str = "shell") -> list[str]:
    """Return the cgroup-limit prefix for a spawned shell command.

    Reads ``config.shell``'s resource-limit settings and delegates to
    :func:`decafclaw.subprocess_limits.resource_cap_prefix`. Returns ``[]``
    when caps are disabled or the host cannot apply them, in which case the
    command runs exactly as it did before.

    An explicit ``resource_limits_enabled = false`` skips the helper entirely
    so an intentional disable logs no "cap unavailable" warning.
    """
    shell_cfg = getattr(config, "shell", None)
    if shell_cfg is None or not getattr(shell_cfg, "resource_limits_enabled", False):
        return []
    return resource_cap_prefix(
        memory_max=getattr(shell_cfg, "memory_max", "") or "",
        memory_high=getattr(shell_cfg, "memory_high", "") or "",
        memory_swap_max=getattr(shell_cfg, "memory_swap_max", "") or "",
        cpu_weight=getattr(shell_cfg, "cpu_weight", None),
        tasks_max=getattr(shell_cfg, "tasks_max", None),
        label=label,
    )


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


def resolve_scope_rule_guidance(
    ctx: "Context",
    *,
    command: str = "",
    activity: Activity | None = None,
) -> list[str]:
    """Collect the global scope-keyed rules that apply to this command (#1641 phase 4).

    Approval knowledge now lives only in ``shell_scope_rules.jsonl``. A rule
    for another repo never leaks into the prompt. Callers that omit
    ``command``/``activity`` (tests, listing) get no command-specific guidance.
    """
    guidelines: list[str] = []
    if command and activity is not None:
        from ..shell_scope_rules import matching_rules

        for item in matching_rules(ctx.config, command, activity):
            if item and item not in guidelines:
                guidelines.append(item)
    return guidelines


def _single_line(text: str, limit: int = 500) -> str:
    """Collapse untrusted text to one bounded line (#1559 review).

    The stated purpose is agent-supplied. Injecting it with its newlines intact
    would let it add instruction-shaped lines to the judge prompt or forge lines
    on a confirmation card, so every consumer renders the collapsed form.
    """
    return " ".join(str(text or "").split())[:limit]


# The aux judge may decline only for a concrete, named risk (#1641 Phase 2).
# Style preferences ("use workspace_search instead") are advisory, never a
# decline reason. The category set lives with the prompt in shell_judge.


def _activity_fields(activity: Activity | None) -> dict:
    return {
        "activity": activity.domain if activity else "",
        "activity_established": activity.established if activity else None,
    }


async def _publish_approval_event(ctx: "Context", event_type: str, **fields) -> None:
    """Publish a telemetry event; measurement must never change a decision.

    The EventBus never propagates subscriber exceptions, but a test double or a
    context without a real bus can still raise here. Approval behavior is more
    important than the counter, so this is fail-open.
    """
    conv_id = getattr(ctx, "conv_id", "")
    if not isinstance(conv_id, str):
        conv_id = ""
    try:
        await ctx.publish(event_type, conv_id=conv_id, **fields)
    except Exception as exc:
        log.debug("approval telemetry publish failed (%s): %s", event_type, exc)


async def _record_approval(
    ctx: "Context",
    *,
    command: str,
    cwd: str | None = None,
    decision: str,
    tier: str,
    reason: str = "",
    activity: Activity | None = None,
) -> None:
    """Publish one approval decision for the audit log (#1641 Phase 0)."""
    await _publish_approval_event(
        ctx,
        "approval_decision",
        command=_single_line(command),
        cwd=_single_line(cwd or ""),
        decision=decision,
        tier=tier,
        reason=_single_line(reason),
        **_activity_fields(activity),
    )
    if tier == "human":
        # Evidence for the global scope rulebook (#1641 phase 4). Only explicit
        # human verdicts count: judge/read-only allowances are telemetry, not
        # consent. This writes the ledger, never the rulebook.
        from ..shell_scope_rules import record_human_decision

        record_human_decision(
            ctx.config,
            conv_id=getattr(ctx, "conv_id", "") or "",
            command=command,
            cwd=cwd,
            decision=decision,
            activity=activity,
        )


async def _request_confirmation_timed(
    ctx: "Context",
    *,
    tier: str,
    command: str,
    cwd: str | None,
    activity: Activity | None,
    **kwargs,
) -> dict:
    """Ask the human, and record the prompt latency and verdict (#1641 Phase 0)."""
    started = time.monotonic()
    result = await request_confirmation(ctx, command=command, **kwargs)
    latency_ms = int((time.monotonic() - started) * 1000)
    approved = bool(result.get("approved"))
    await _publish_approval_event(
        ctx,
        "approval_human",
        command=_single_line(command),
        approved=approved,
        latency_ms=latency_ms,
        tier=tier,
        **_activity_fields(activity),
    )
    await _record_approval(
        ctx,
        command=command,
        cwd=cwd,
        decision="allow" if approved else "deny",
        tier="human",
        activity=activity,
    )
    return result


def _record_tool_gap(
    ctx: "Context",
    *,
    command: str,
    purpose: str,
    proposed_tool: str,
    rationale: str,
    trigger: str,
) -> None:
    """Append a capability-gap item to the improvement inbox (#1559, #1482).

    Fail-open: the review pipeline has already made its decision, and an inbox
    write failure must not change the command's outcome.
    """
    try:
        from decafclaw.improvement_inbox import append_item, new_item

        title = (
            f"Opportunity for native tool: {proposed_tool}"
            if proposed_tool
            else "Opportunity for native tool: reviewer flagged a missing capability"
        )
        friction = f"Agent executed shell command '{command}'"
        purpose_line = _single_line(purpose)
        if purpose_line:
            friction += f" because no native tool covers: {purpose_line}"
        else:
            friction += " because no native tool covers that operation."
        append_item(
            ctx.config,
            new_item(
                conversation_id=ctx.conv_id,
                trigger=trigger,
                title=title,
                observed_friction=friction,
                suggested_improvement=rationale or "Add a constrained native tool for this operation.",
                subsystem="tools",
                severity="low",
            ),
        )
        log.info(f"[shell] recorded tool gap for {proposed_tool or command!r} in the improvement inbox")
    except Exception as exc:
        log.warning(f"[shell] failed to record tool gap in improvement inbox: {exc}")


def _finalize_denial(ctx: "Context", result: dict, *, command: str, purpose: str, fallback_feedback: str = "") -> dict:
    """Attach feedback to a denied confirmation and log reviewer-flagged tool gaps.

    Shared by the security-monitor ASK branch and the ordinary confirmation
    path: the shell card appears in both, so a reviewer's feedback and
    missing-tool flag must be honored wherever it appears.
    """
    if result.get("approved"):
        return result
    human_feedback = _single_line(str(result.get("feedback") or ""), limit=2000)
    feedback = human_feedback or fallback_feedback
    if feedback:
        result["feedback"] = feedback
    data = result.get("data")
    if isinstance(data, dict) and data.get("flag_missing_tool"):
        _record_tool_gap(
            ctx,
            command=command,
            purpose=purpose,
            proposed_tool=str(data.get("proposed_tool") or "").strip(),
            rationale="Reviewer denied the command and flagged a missing native tool.",
            trigger="shell_tool_review",
        )
    return result


def _apply_approval_additions(
    ctx: "Context",
    result: dict,
    *,
    suggested_pattern: str,
) -> None:
    """Persist the allowlist pattern when the human chose it (#1641)."""
    if not result.get("approved"):
        return
    if result.get("add_pattern"):
        _save_allow_pattern(ctx.config, suggested_pattern)


# A judge-declined command gets one same-turn redirect before a human is
# interrupted (#1626). The budget is one redirect per (command, purpose)
# fingerprint per turn, with a small total cap as a backstop against a model
# spraying unrelated commands instead of correcting itself.
_MAX_SHELL_REDIRECTS_PER_TURN = 3

# Direct virtualenv binaries are outside the workspace and machine-specific;
# `uv run` executes the same environment through the trusted system binary.
_VENV_REDIRECT_FEEDBACK = (
    "Do not invoke virtualenv binaries directly via absolute paths. "
    "Use 'uv run' (e.g. 'uv run pytest ...' or 'uv run python ...') instead."
)

# Deflecting `shell` back to `shell` is meaningless, and `tool_search` is
# named separately as the way to reach deferred tools.
_NATIVE_INDEX_EXCLUDED = frozenset({"shell", "shell_background_start", "tool_search"})

# Category map for the generated native-tool index (#1626). Unknown tools are
# still listed under "Other": omission, not untidiness, is the failure this
# index exists to prevent. Adding a tool to the registry does not require
# touching this map; it only decides where a known name is grouped.
_NATIVE_TOOL_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "File reading/inspection",
        (
            "workspace_read",
            "workspace_search",
            "workspace_glob",
            "workspace_list",
            "workspace_outline",
            "workspace_diff",
            "workspace_preview_markdown",
            "tool_output_read",
            "list_attachments",
            "get_attachment",
        ),
    ),
    (
        "File editing/mutation",
        (
            "workspace_edit",
            "workspace_write",
            "workspace_append",
            "workspace_insert",
            "workspace_replace_lines",
            "workspace_move",
            "workspace_copy",
            "workspace_mkdir",
            "workspace_delete",
        ),
    ),
    ("Network / HTTP", ("web_fetch", "web_search", "http_request")),
    (
        "Persistent knowledge",
        (
            "vault_read",
            "vault_write",
            "vault_search",
            "vault_list",
            "vault_recent",
            "vault_tags",
            "vault_journal_append",
        ),
    ),
    ("Notes / conversation", ("notes_append", "notes_read", "conversation_search", "conversation_compact")),
    (
        "Administration",
        (
            "admin_read",
            "admin_list",
            "admin_edit",
            "admin_write",
            "admin_replace_lines",
            "admin_delete",
            "admin_restart",
            "update_system_prompt",
        ),
    ),
    (
        "Background jobs",
        ("shell_background_status", "shell_background_stop", "shell_background_list", "delegate_task"),
    ),
    ("System clock", ("current_time",)),
)
_NATIVE_TOOL_CATEGORY_BY_NAME = {name: category for category, names in _NATIVE_TOOL_CATEGORIES for name in names}
_NATIVE_TOOL_CATEGORY_ORDER = tuple(category for category, _ in _NATIVE_TOOL_CATEGORIES)

# Cheap read-command heuristic: when the declined command looks like
# inspection, the inspection tools lead the index so the judge's suggestion is
# the obvious one.
_READ_COMMANDS = frozenset(
    {
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "ack",
        "find",
        "fd",
        "cat",
        "head",
        "tail",
        "ls",
        "tree",
        "wc",
        "less",
        "more",
        "stat",
        "file",
        "sort",
        "uniq",
        "cut",
        "awk",
        "sed",
    }
)


def _command_head(command: str) -> str:
    """First word of a command, with any path prefix removed."""
    stripped = (command or "").strip()
    if not stripped:
        return ""
    return stripped.split(maxsplit=1)[0].rsplit("/", 1)[-1]


def _reachable_tool_names(ctx: "Context") -> tuple[list[str], list[str]]:
    """Names of active and deferred tools the agent can reach this turn.

    ``collect_all_tool_defs`` is the authoritative gatherer (core + activated
    skills + bundled-skill natives + MCP + extra). Deferred members come from
    the turn's budget classification, and the same allowed/disallowed filters
    ``build_tool_list`` applies are applied here — the judge must never be
    told to use a tool this turn cannot reach.
    """
    # Function-level import: decafclaw.tools (which this module is part of)
    # imports shell_tools at package load, so a module-level import cycles.
    from decafclaw.tool_definitions import collect_all_tool_defs

    deferred = {td.get("function", {}).get("name") for td in ctx.tools.deferred_pool}
    allowed = ctx.tools.allowed
    disallowed = ctx.tools.disallowed or set()
    active: list[str] = []
    deferred_names: list[str] = []
    seen: set[str] = set()
    for td in collect_all_tool_defs(ctx):
        name = td.get("function", {}).get("name", "")
        if not name or name in seen or name in _NATIVE_INDEX_EXCLUDED:
            continue
        seen.add(name)
        if allowed is not None and name not in allowed:
            continue
        if name in disallowed:
            continue
        (deferred_names if name in deferred else active).append(name)
    return sorted(active), sorted(deferred_names)


def _group_names_by_category(names: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for name in names:
        category = _NATIVE_TOOL_CATEGORY_BY_NAME.get(name, "Other")
        groups.setdefault(category, []).append(name)
    return groups


def _format_tool_groups(names: list[str], *, read_first: bool) -> list[str]:
    """Render one line per category; every reachable name survives.

    Names are never truncated. The index is bounded by the registry — the
    agent is already shown every one of these tools as a schema — and an
    elided tool is exactly the dead-end redirect this index exists to prevent.
    """
    groups = _group_names_by_category(names)
    order = list(_NATIVE_TOOL_CATEGORY_ORDER)
    if read_first:
        order.remove("File reading/inspection")
        order.insert(0, "File reading/inspection")
    order.append("Other")
    lines: list[str] = []
    for category in order:
        members = groups.pop(category, [])
        if not members:
            continue
        # Registry names are not free text, but a workspace skill controls its
        # own tool names; collapse line breaks so the judge prompt cannot gain
        # instruction-shaped lines (#1626).
        clean = [_single_line(member, limit=200) for member in members]
        lines.append(f"{category}: {', '.join(clean)}")
    return lines


def build_native_tool_index(ctx: "Context", command: str = "") -> str:
    """Render the aux judge's tool index from the live registry (#1626).

    Active tools are grouped by category; deferred tools are listed separately
    so the judge can tell the agent whether a suggestion needs ``tool_search``
    first. Unknown-but-present tools land in "Other" instead of vanishing.
    """
    active, deferred = _reachable_tool_names(ctx)
    read_first = _command_head(command) in _READ_COMMANDS
    lines = _format_tool_groups(active, read_first=read_first)
    if deferred:
        lines.append("Deferred (not loaded yet; call tool_search to fetch):")
        lines.extend(_format_tool_groups(deferred, read_first=read_first))
    return "\n".join(lines)


def _redirect_text(feedback: str) -> str:
    """Tool result for a judge redirect (#1626).

    Keeps the ``[error`` prefix: ``agent.py`` classifies tool results that way
    and feeds them to the loop breaker's error accounting. A redirect is a
    failed call the agent should correct, not a success.
    """
    return (
        "[error: command not run — no approval requested yet. "
        f"Feedback: {_single_line(feedback, limit=2000)} "
        "This is the only redirect for this command; if you repeat it, a human will be asked.]"
    )


def _claim_shell_redirect(ctx: "Context", *, command: str, cwd: str | None, purpose: str) -> bool:
    """Claim this (command, purpose) fingerprint's one redirect for the turn.

    The claim is recorded before returning with no await in between, so two
    concurrent shell calls cannot both claim the same fingerprint. The budget
    object lives on ``ctx.tools`` and is shared by ``fork_for_tool_call``
    children; a new turn builds a new Context, so it starts empty.
    """
    fp = fingerprint(
        "shell",
        {"command": command, "cwd": cwd or "", "purpose": _single_line(purpose)},
    )
    claimed = ctx.tools.shell_redirect_fingerprints
    if fp in claimed or len(claimed) >= _MAX_SHELL_REDIRECTS_PER_TURN:
        return False
    claimed.add(fp)
    return True


def build_aux_approval_prompt(
    ctx: "Context",
    command: str,
    cwd: str | None = None,
    purpose: str = "",
    activity: Activity | None = None,
) -> str:
    """Build the aux-LLM evaluation prompt for shell command auto-approval.

    Direction has already decided *whether* to ask (#1641): this prompt only
    sees commands inside the conversation's established activity, and the judge
    decides whether such a command carries a concrete risk. Style preferences —
    notably "a native tool would fit better" — are advisory feedback on an
    allowed command, never a decline reason.

    The template is external markdown (bundled + optional agent override); this
    wrapper only supplies the live values.
    """
    return render_aux_approval_prompt(
        ctx.config,
        command=command,
        working_dir=cwd or str(ctx.config.workspace_path),
        stated_purpose=_single_line(purpose) or "(none provided)",
        activity=activity,
        native_tool_index=build_native_tool_index(ctx, command),
        guidelines=resolve_scope_rule_guidance(ctx, command=command, activity=activity),
    )


async def check_shell_approval(
    ctx: "Context",
    command: str,
    tool_name: str = "shell",
    message: str = "",
    cwd: str | None = None,
    purpose: str = "",
) -> dict:
    """Check whether a shell command is approved (shared by shell + background tools).

    ``cwd`` is the directory the command will run in, when known. It does not
    change the workspace boundary the security monitor enforces; it is shown to
    the auto-approval judge and the human so the same command text in a
    different directory is evaluated in context.

    ``purpose`` is the agent's stated intent (#1559). It is untrusted metadata:
    Tier 1 blocks ignore it, the Tier 2 classifier and the aux auto-approval
    judge use it to check command/purpose congruence, and the confirmation card
    shows it to the human. A denial may carry ``feedback`` — the aux judge's
    safer-alternative advice or the human reviewer's — which the tool result
    surfaces back to the agent.

    Optional return ``{"approved": False, "redirected": True, "feedback": ...}``
    means the aux judge declined with actionable advice and nobody was
    prompted: the caller must return the redirect text, not execute the
    command. Everything else is ``{"approved": True}`` or the user's
    confirmation result.
    """
    # Show the working directory only when it differs from the workspace root:
    # the same command text means something different in a subproject. It rides
    # in the message for transports that show it, and in action_data so the web
    # and Mattermost confirm cards can render it next to the command.
    cwd_note = ""
    cwd_action: dict = {}
    if cwd:
        try:
            differs = Path(cwd).resolve() != ctx.config.workspace_path.resolve()
        except OSError:
            differs = True
        if differs:
            cwd_note = f"\nWorking directory: `{cwd}`"
            cwd_action = {"action_data": {"cwd": cwd}}

    # Direction is derived once for every decision below so audit events carry
    # it and the judge sees the same activity the read-only/ask tiers did.
    activity = derive_activity(ctx, command, cwd)
    if getattr(ctx.config.shell, "direction_shadow_mode", False):
        # Shadow mode (#1661): observe what direction would do, change nothing.
        # `defer` is the unattended case (no human can answer); `pass` means the
        # command is in an established activity and would proceed to the judge.
        if activity.established:
            would_decide = "pass"
        elif ctx.is_unattended:
            would_decide = "defer"
        else:
            would_decide = "prompt"
        await _publish_approval_event(
            ctx,
            "direction_shadow",
            command=_single_line(command),
            cwd=_single_line(cwd or ""),
            would_decide=would_decide,
            tier="activity_new" if not activity.established else "aux_judge",
            **_activity_fields(activity),
        )
    try:
        shell_cwd = _resolve_shell_cwd(ctx, cwd) if cwd else ctx.config.workspace_path.resolve()
    except ValueError:
        shell_cwd = ctx.config.workspace_path.resolve()
    # Kill switch for the silent read-only tier. Defaults to enabled when the
    # key is absent or unreadable, but an explicit False always wins — an
    # operator turning this off must never be overridden by a config typo.
    read_only_enabled = getattr(ctx.config.shell, "read_only_enabled", True) is not False

    # 1. Tier 1 fast path: Immediately block catastrophically dangerous commands
    tier1 = evaluate_command(
        command,
        workspace_path=ctx.config.workspace_path,
        is_autonomous=ctx.is_unattended,
    )
    if tier1.status == SecurityStatus.BLOCK:
        log.warning(f"[{tool_name}] blocked by security monitor: {command} (reason: {tier1.reason})")
        await _record_approval(
            ctx,
            command=command,
            cwd=cwd,
            decision="block",
            tier="tier1_block",
            reason=tier1.reason,
            activity=activity,
        )
        return {
            "approved": False,
            "reason": f"Blocked by security monitor: {tier1.reason}",
        }

    # 2. Explicit allow patterns take precedence and bypass Tier 2 LLM latency (#966)
    if _command_matches_pattern(command, ctx.tools.preapproved_shell_patterns):
        log.info(f"[{tool_name}] pre-approved by scoped pattern: {command}")
        await _record_approval(
            ctx, command=command, cwd=cwd, decision="allow", tier="scoped_pattern", activity=activity
        )
        return {"approved": True}

    patterns = _load_allow_patterns(ctx.config)
    if _command_matches_pattern(command, patterns):
        log.info(f"[{tool_name}] auto-approved by pattern: {command}")
        await _record_approval(
            ctx, command=command, cwd=cwd, decision="allow", tier="persisted_pattern", activity=activity
        )
        return {"approved": True}

    # 3. Deterministic read-only tier (#1641 Phase 1): a provably read-only
    # command whose paths stay inside the workspace never reaches a model.
    #
    # This deliberately runs BEFORE the security monitor. The monitor's Tier 2
    # classifier is soft and stateless: `is_ambiguous_command` treats any `#` as
    # ambiguous, so `grep '#1641' src/` would spend a model call and could come
    # back ASK — costing a human prompt in exactly the category this tier
    # exists to silence. Tier 1's hard BLOCK above still wins, and allow
    # patterns are unchanged; only the soft classifier is bypassed.
    #
    # Gated by `shell.read_only_enabled` (kill switch): this is the only tier
    # that executes with no human and no model, so a suspect classifier must be
    # neutralizable by config rather than by reverting a deploy.
    if read_only_enabled and is_read_only_command(command, cwd=shell_cwd, workspace=ctx.config.workspace_path):
        log.info(f"[{tool_name}] auto-approved as read-only: {command}")
        await _record_approval(ctx, command=command, cwd=cwd, decision="allow", tier="read_only", activity=activity)
        return {"approved": True}

    # 4. Security monitor evaluation (Tier 1 sensitive patterns + Tier 2 LLM classification)
    decision = await evaluate_command_llm(
        command,
        ctx=ctx,
        workspace_path=ctx.config.workspace_path,
        is_autonomous=ctx.is_unattended,
        purpose=purpose,
    )
    if decision.status == SecurityStatus.BLOCK:
        log.warning(f"[{tool_name}] blocked by security monitor: {command} (reason: {decision.reason})")
        await _record_approval(
            ctx,
            command=command,
            cwd=cwd,
            decision="block",
            tier="security_monitor",
            reason=decision.reason,
            activity=activity,
        )
        return {
            "approved": False,
            "reason": f"Blocked by security monitor: {decision.reason}",
        }

    if decision.status == SecurityStatus.ASK:
        log.info(
            f"[{tool_name}] security monitor requires explicit confirmation: {command} (reason: {decision.reason})"
        )
        venv_path = venv_executable_path(command, ctx.config.workspace_path)
        if venv_path and _claim_shell_redirect(ctx, command=command, cwd=cwd, purpose=purpose):
            log.info(f"[{tool_name}] redirecting virtualenv invocation to uv run: {command}")
            await _record_approval(
                ctx,
                command=command,
                cwd=cwd,
                decision="redirect",
                tier="venv_redirect",
                reason=f"virtualenv binary: {venv_path}",
                activity=activity,
            )
            return {
                "approved": False,
                "redirected": True,
                "feedback": _VENV_REDIRECT_FEEDBACK,
            }
        if ctx.is_unattended:
            log.warning(f"[{tool_name}] denied on unattended turn (security monitor ASK decision): {command}")
            await _record_approval(
                ctx,
                command=command,
                cwd=cwd,
                decision="deny",
                tier="security_monitor_unattended",
                reason=decision.reason,
                activity=activity,
            )
            denial: dict = {
                "approved": False,
                "reason": f"Security monitor requires explicit confirmation: {decision.reason}",
            }
            if venv_path:
                denial["feedback"] = _VENV_REDIRECT_FEEDBACK
            return denial
        suggested_pattern = _suggest_pattern(command)
        ask_message = f"Shell command (security review requested): `{command}`{cwd_note}"
        if decision.reason:
            ask_message += f"\nReason: {decision.reason}"
        if venv_path:
            ask_message += f"\nSuggestion: {_VENV_REDIRECT_FEEDBACK}"
        await _record_approval(
            ctx,
            command=command,
            cwd=cwd,
            decision="prompt",
            tier="security_monitor_ask",
            reason=decision.reason,
            activity=activity,
        )
        action_data = dict(cwd_action.get("action_data", {}))
        action_data.update(
            {
                "tier": "security_monitor_ask",
                "decline_reason": decision.reason,
                "reason": decision.reason,
            }
        )
        result = await _request_confirmation_timed(
            ctx,
            tier="security_monitor_ask",
            command=command,
            cwd=cwd,
            activity=activity,
            tool_name=tool_name,
            message=message or ask_message,
            suggested_pattern=suggested_pattern,
            decline_reason=decision.reason,
            reason=decision.reason,
            purpose=purpose,
            feedback=_VENV_REDIRECT_FEEDBACK if venv_path else "",
            action_data=action_data,
        )
        if result.get("approved") and result.get("add_pattern"):
            _save_allow_pattern(ctx.config, suggested_pattern)
        return _finalize_denial(
            ctx,
            result,
            command=command,
            purpose=purpose,
            fallback_feedback=_VENV_REDIRECT_FEEDBACK if venv_path else "",
        )

    # 5. Blanket tool pre-approval (e.g. allowed-tools: shell without scoped patterns)
    if "shell" in ctx.tools.preapproved or tool_name in ctx.tools.preapproved:
        log.info(f"[{tool_name}] pre-approved by command: {command}")
        await _record_approval(
            ctx, command=command, cwd=cwd, decision="allow", tier="blanket_preapproval", activity=activity
        )
        return {"approved": True}

    # 6. Direction (#1641 Phase 3): a command that starts a new activity asks
    # once, with the direction stated. Drift is priced at one prompt, never
    # blocked; once the operator answers, the activity is established.
    #
    # Unattended turns have nobody to ask, but they must NOT hard-deny here:
    # scheduled work legitimately starts fresh activity every run (a new
    # newsletter, a new ingest target), and this tier's whole premise is that
    # the command is plausible-but-unfamiliar, not risky. A deny would be
    # strictly worse than the judge decline it replaced — that at least fed a
    # redirect back to the agent. So on unattended turns direction abstains and
    # the command proceeds to the judge, which keeps the last word on risk.
    if not activity.established and not ctx.is_unattended:
        reason = activity.reason()
        suggested_pattern = _suggest_pattern(command)
        await _record_approval(
            ctx,
            command=command,
            cwd=cwd,
            decision="prompt",
            tier="activity_new",
            reason=reason,
            activity=activity,
        )
        prompt_message = f"Shell command: `{command}`{cwd_note}\nActivity: {reason}"
        if _single_line(purpose):
            prompt_message += f"\nPurpose: {_single_line(purpose)}"
        action_data = dict(cwd_action.get("action_data", {}))
        action_data.update(
            {
                "activity": activity.domain,
                "activity_established": False,
                "tier": "activity_new",
                "decline_reason": reason,
                "reason": reason,
            }
        )
        result = await _request_confirmation_timed(
            ctx,
            tier="activity_new",
            command=command,
            cwd=cwd,
            activity=activity,
            tool_name=tool_name,
            message=message or prompt_message,
            suggested_pattern=suggested_pattern,
            decline_reason=reason,
            purpose=purpose,
            action_data=action_data,
        )
        _apply_approval_additions(
            ctx,
            result,
            suggested_pattern=suggested_pattern,
        )
        return _finalize_denial(ctx, result, command=command, purpose=purpose)
    if not activity.established:
        # Unattended: record that direction abstained so the audit trail shows
        # the judge, not the operator, carried the decision.
        log.info(f"[{tool_name}] new activity on unattended turn, deferring to judge: {command}")
        await _record_approval(
            ctx,
            command=command,
            cwd=cwd,
            decision="defer",
            tier="activity_new_unattended",
            reason=activity.reason(),
            activity=activity,
        )

    decline_reason = ""
    decline_risk = ""
    decline_feedback = ""
    if ctx.config.shell.aux_approval_enabled:
        if _command_matches_pattern(command, ctx.tools.llm_approved_shell_patterns):
            log.info(f"[{tool_name}] auto-approved by session aux-LLM memory: {command}")
            await _record_approval(
                ctx, command=command, cwd=cwd, decision="allow", tier="aux_memory", activity=activity
            )
            return {"approved": True}

        try:
            had_guidance = bool(resolve_scope_rule_guidance(ctx, command=command, activity=activity))
            prompt = build_aux_approval_prompt(ctx, command, cwd=cwd, purpose=purpose, activity=activity)
            messages = [{"role": "user", "content": prompt}]
            response = await ctx.aux_approval_llm()(messages)
            raw_text = response.get("content", "").strip()

            import re as re_mod

            if "```" in raw_text:
                raw_text = re_mod.sub(r"^```(?:json)?\n?", "", raw_text, flags=re_mod.MULTILINE)
                raw_text = re_mod.sub(r"```$", "", raw_text, flags=re_mod.MULTILINE).strip()

            import json as json_mod

            data = json_mod.loads(raw_text)

            # A decline needs a named, concrete risk (#1641 Phase 2). A judge
            # that merely prefers a native tool returns auto_approve false with
            # no risk category; that is advisory, and the command runs.
            raw_category = str(data.get("risk_category") or "").strip().lower()
            risk_category = raw_category if raw_category in AUX_RISK_CATEGORIES else ""
            advisory = str(data.get("feedback") or "").strip()

            gap = data.get("tool_gap")
            if isinstance(gap, dict) and str(gap.get("proposed_tool") or "").strip():
                _record_tool_gap(
                    ctx,
                    command=command,
                    purpose=purpose,
                    proposed_tool=str(gap.get("proposed_tool")).strip(),
                    rationale=str(gap.get("rationale") or "").strip(),
                    trigger="shell_tool_review",
                )

            if not risk_category:
                log.info(
                    f"[{tool_name}] auto-approved by aux LLM (risk: {data.get('risk')}): {command} - {data.get('reason')}"
                )
                suggested_pattern = _suggest_aux_approval_pattern(command, has_guidance=had_guidance)
                ctx.tools.llm_approved_shell_patterns.append(suggested_pattern)

                msg_content = f"Command: {command}\nRisk: {data.get('risk')}\nReason: {data.get('reason')}"
                if _single_line(purpose):
                    msg_content += f"\nPurpose: {_single_line(purpose)}"
                if advisory:
                    msg_content += f"\nAdvisory: {advisory}"
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
                await _record_approval(
                    ctx, command=command, cwd=cwd, decision="allow", tier="aux_allow", activity=activity
                )

                allowed: dict = {"approved": True}
                if advisory:
                    allowed["advisory"] = advisory
                return allowed

            log.info(
                f"[{tool_name}] aux LLM declined auto-approval ({risk_category}): {command} - {data.get('reason')}"
            )

            decline_reason = str(data.get("reason") or "").strip()
            decline_risk = str(data.get("risk") or "").strip()
            decline_feedback = advisory
            await _record_approval(
                ctx,
                command=command,
                cwd=cwd,
                decision="decline",
                tier="aux_decline",
                reason=decline_reason or risk_category,
                activity=activity,
            )

            msg_content = f"Command: {command}\nRisk: {data.get('risk')}\nReason: {data.get('reason')}"
            if _single_line(purpose):
                msg_content += f"\nPurpose: {_single_line(purpose)}"
            if risk_category:
                msg_content += f"\nRisk category: {risk_category}"
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
            if not decline_reason:
                decline_reason = f"Auto-approval judge error ({e}); confirmation requested"

    # Self-correction before interruption (#1626): a judge decline carrying
    # actionable advice returns a redirect instead of prompting a human. One
    # redirect per (command, purpose) fingerprint per turn — a repeat, or a
    # fourth unrelated redirect, falls through and the human decides.
    if decline_feedback and _claim_shell_redirect(ctx, command=command, cwd=cwd, purpose=purpose):
        log.info(f"[{tool_name}] redirecting agent after aux-judge decline: {command}")
        await _record_approval(
            ctx, command=command, cwd=cwd, decision="redirect", tier="aux_redirect", activity=activity
        )
        return {
            "approved": False,
            "redirected": True,
            "feedback": _single_line(decline_feedback, limit=2000),
        }

    suggested_pattern = _suggest_pattern(command)
    if ctx.is_unattended:
        # Nobody can answer a prompt on this turn: it would block for the 60s
        # timeout and then be synthesized into this same denial. Deny now, and
        # say why rather than letting it look like a user decision.
        log.warning(f"[{tool_name}] denied on unattended turn (task_mode={ctx.task_mode.value}): {command}")
        await _record_approval(ctx, command=command, cwd=cwd, decision="deny", tier="unattended", activity=activity)
        denial: dict = {"approved": False, "reason": "unattended turn: command matches no allow pattern"}
        if decline_feedback:
            denial["feedback"] = decline_feedback
        return denial

    prompt_message = f"Shell command: `{command}`{cwd_note}"
    if _single_line(purpose):
        prompt_message += f"\nPurpose: {_single_line(purpose)}"
    if decline_risk:
        prompt_message += f"\nRisk: {decline_risk}"
    if decline_reason:
        prompt_message += f"\nReason: {decline_reason}"
    await _record_approval(
        ctx, command=command, cwd=cwd, decision="prompt", tier="aux_decline_prompt", activity=activity
    )
    action_data = dict(cwd_action.get("action_data", {}))
    action_data.update(
        {
            "activity": activity.domain,
            "activity_established": activity.established,
            "tier": "aux_decline_prompt",
            "decline_reason": decline_reason,
            "reason": decline_reason,
        }
    )
    result = await _request_confirmation_timed(
        ctx,
        tier="aux_decline_prompt",
        command=command,
        cwd=cwd,
        activity=activity,
        tool_name=tool_name,
        message=message or prompt_message,
        suggested_pattern=suggested_pattern,
        decline_reason=decline_reason,
        purpose=purpose,
        feedback=decline_feedback,
        action_data=action_data,
    )
    _apply_approval_additions(
        ctx,
        result,
        suggested_pattern=suggested_pattern,
    )
    if result.get("approved"):
        return result
    # A human's denial feedback wins; otherwise pass along the judge's
    # safer-alternative advice so the agent can self-correct.
    return _finalize_denial(ctx, result, command=command, purpose=purpose, fallback_feedback=decline_feedback)


def _resolve_shell_cwd(ctx: "Context", cwd: str | None) -> Path:
    """Resolve an optional shell working directory inside the workspace.

    ``cwd`` is interpreted relative to the workspace root; an absolute path is
    accepted only when it stays inside the workspace. The workspace itself is
    the default. Raises ``ValueError`` with a user-facing message when the path
    is unusable, before any approval or execution happens.
    """
    workspace = ctx.config.workspace_path.resolve()
    if not cwd:
        return workspace
    candidate = Path(cwd)
    target = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
    if not target.is_relative_to(workspace):
        raise ValueError(f"cwd is outside the workspace: {cwd}")
    if not target.exists():
        raise ValueError(f"cwd does not exist: {cwd}")
    if not target.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    return target


_CD_CHAIN_PATTERN = re.compile(
    r"""^\s*cd(?:\s+(?:-[A-Za-z]+|--))*\s*(?P<dir>'[^']*'|"[^"]*"|[^\s;&|\n]+)?\s*(?:&&|;|\|\||\n)\s*(?P<cmd>.*)$""",
    re.DOTALL,
)
_CD_SOLO_PATTERN = re.compile(
    r"""^\s*cd(?:\s+(?:-[A-Za-z]+|--))*\s*(?P<dir>'[^']*'|"[^"]*"|[^\s;&|\n]+)?\s*$""",
    re.DOTALL,
)


def _check_cd_rejection(command: str) -> str | None:
    """Reject commands starting with 'cd' and advise using 'cwd' instead (#1467).

    LLMs have a strong pre-training bias toward ``cd <dir> && <command>`` chaining.
    In decafclaw, shell commands execute in subshells and the ``cwd`` parameter
    should be used instead.
    """
    if m := _CD_CHAIN_PATTERN.match(command):
        target_dir = (m.group("dir") or "").strip("'\"")
        rest_cmd = (m.group("cmd") or "").strip()
        if target_dir and rest_cmd:
            return f"Shell commands must not use 'cd ... &&'. Set cwd='{target_dir}' and command='{rest_cmd}' instead."
        if target_dir:
            return (
                f"Shell commands cannot change directory with 'cd' because each "
                f"invocation runs in a new subshell. Set cwd='{target_dir}' on the "
                f"command you want to run instead."
            )
        return "Shell commands must not use 'cd ... &&'. Use the 'cwd' parameter instead."
    if m := _CD_SOLO_PATTERN.match(command):
        target_dir = (m.group("dir") or "").strip("'\"")
        if target_dir:
            return (
                f"Shell commands cannot change directory with 'cd' because each "
                f"invocation runs in a new subshell. Set cwd='{target_dir}' on the "
                f"command you want to run instead."
            )
        return (
            "Shell commands cannot change directory with 'cd' because each "
            "invocation runs in a new subshell. Use the 'cwd' parameter to set a "
            "working directory."
        )
    return None


async def tool_shell(ctx: "Context", command: str, cwd: str | None = None, purpose: str = "") -> ToolResult:
    """Run a shell command after user confirmation, optionally inside a workspace subdirectory."""
    if cd_err := _check_cd_rejection(command):
        log.info(f"[tool:shell] rejected command {command}: {cd_err}")
        return ToolResult(text=f"[error: {cd_err}]")

    try:
        workdir = _resolve_shell_cwd(ctx, cwd)
    except ValueError as exc:
        log.info(f"[tool:shell] rejected command {command}: {exc}")
        return ToolResult(text=f"[error: {exc}]")

    log.info(f"[tool:shell] requesting confirmation for: {command} (cwd: {workdir})")

    result = await check_shell_approval(ctx, command, tool_name="shell", cwd=str(workdir), purpose=purpose)
    if result.get("redirected"):
        log.info(f"[tool:shell] redirecting instead of prompting: {command}")
        return ToolResult(text=_redirect_text(str(result.get("feedback") or "")))
    if not result.get("approved"):
        reason = result.get("reason") or "denied by user"
        feedback = str(result.get("feedback") or "").strip()
        log.info(f"[tool:shell] command denied: {command} (reason: {reason})")
        text = f"[error: command denied: {reason}]"
        if feedback:
            text = f"[error: command denied: {reason}. Feedback: {feedback}]"
        return ToolResult(text=text)

    budget = ctx.config.shell.foreground_wait_budget_sec
    if budget is not None and budget > 0:
        output = await _execute_command_with_handoff(ctx, command, cwd=str(workdir))
    else:
        output = await _execute_command(ctx, command, cwd=str(workdir))
    advisory = _single_line(result.get("advisory") or "")
    if advisory:
        # A native-tool suggestion is advice on an allowed command (#1641
        # Phase 2), never a denial. Surface it after the output so the agent
        # can prefer the tool next time without losing this result.
        output.text = f"{output.text}\n\n[advisory: {advisory}]"
    return output


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

    @property
    def disk_capped(self) -> bool:
        """True once the artifact stopped growing at the disk cap (#922), so a
        reader knows the file holds only a prefix of the stream."""
        return self._disk_capped

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


async def _execute_command(
    ctx: "Context", command: str, timeout: float = _DEFAULT_TIMEOUT_SEC, cwd: str | None = None
) -> ToolResult:
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

    # Run the command under a transient cgroup scope when caps are available:
    # the kernel then kills this command instead of the daemon when it outgrows
    # its memory or pid budget. An empty prefix means "no caps available" and
    # the command runs exactly as before.
    #
    # Argument escaping: in systemd < 256 (e.g. Debian 12 / Ubuntu 24.04), the
    # service manager expands environment variables and specifiers on every
    # argument in ExecStart, unescaping "$$" to "$" and expanding "$VAR". Passing
    # an arbitrary shell command directly on systemd-run's argv would corrupt
    # script text (e.g. `echo $$` becoming `echo $`).
    #
    # We bounce through a one-line Python launcher that reads the command from
    # the process environment (DECAFCLAW_SHELL_CMD) and execl()s /bin/sh in place.
    # Systemd-run's argv stays fixed and free of "$", while execl keeps the same
    # PID, process group, cgroup, and file descriptors.
    cap_prefix = shell_cap_prefix(ctx.config, label="shell")
    if cap_prefix:
        env["DECAFCLAW_SHELL_CMD"] = command
        spawn_args = [
            *cap_prefix,
            sys.executable,
            "-c",
            "import os; os.execl('/bin/sh', 'sh', '-c', os.environ['DECAFCLAW_SHELL_CMD'])",
        ]
    else:
        spawn_args = ["/bin/sh", "-c", command]

    process = await asyncio.create_subprocess_exec(
        *spawn_args,
        cwd=cwd or str(ctx.config.workspace_path),
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
            handle = format_handle(ctx.conv_id, path.name) if ctx.conv_id else None
            if handle is not None:
                try:
                    stored_bytes = path.stat().st_size
                except OSError:
                    stored_bytes = 0
                record_artifact(
                    ctx.config,
                    ctx.conv_id,
                    path.name,
                    state=CAPPED if collector.disk_capped else COMPLETE,
                    total_bytes=collector.total_bytes,
                    stored_bytes=stored_bytes,
                    tool="shell",
                    tool_call_id=call_id,
                )
            output += truncation_note(str(path), handle=handle)
        else:  # a limit was exceeded but the spill file could not be opened
            output += f"\n\n[Output truncated. Full {label} could not be saved ({format_size(collector.total_bytes)}).]"

    if timed_out:
        prefix = f"[error: command timed out after {timeout:.0f} seconds]"
        return ToolResult(text=f"{prefix}\n\n{output}" if output else prefix)

    # A cap that fires should say so: a bare "-9" reads as a mystery crash.
    # Outside a capped scope the kernel can still pick this command (or something
    # in its tree) as an OOM victim when the whole host is short, so this note is
    # useful either way.
    if cap_prefix:
        kill_note = describe_kill(exit_code, label="command")
        if kill_note:
            return ToolResult(text=f"[error: {kill_note}]\n\n{output}" if output else f"[error: {kill_note}]")

    return ToolResult(text=output or "(no output)")


# How long to wait for the output reader after a foreground command exits. A
# grandchild process can hold the pipes open past the parent's exit; we don't
# block the turn on that — proceed with whatever was captured (#1136).
_FOREGROUND_DRAIN_TIMEOUT_SEC = 5.0


async def _execute_command_with_handoff(ctx: "Context", command: str, cwd: str | None = None) -> ToolResult:
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
        from decafclaw.skills.background.tools import JobStatus, _get_job_manager
    except Exception as exc:  # noqa: BLE001 - fail open to the legacy path
        log.warning(f"[tool:shell] background runner unavailable ({exc}); using blocking execution")
        return await _execute_command(ctx, command, cwd=cwd)

    budget = ctx.config.shell.foreground_wait_budget_sec
    max_runtime = ctx.config.shell.max_runtime_sec
    env = {**os.environ, "DECAFCLAW_WORKSPACE": str(ctx.config.workspace_path)}

    manager = _get_job_manager(ctx)
    try:
        job = await manager.start(
            command,
            cwd or str(ctx.config.workspace_path),
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
            data={"job_id": job.job_id, "status": JobStatus.RUNNING, "command": command, "pid": job.pid},
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
) -> str | ToolResult:
    """Inspect and draft the global shell scope rulebook."""
    log.info(f"[tool:shell_guidance] action={action}")

    if action == "list":
        from ..shell_scope_rules import format_rules_summary

        return format_rules_summary(ctx.config)

    if ctx.is_unattended:
        log.warning(f"[tool:shell_guidance] denied on unattended turn (task_mode={ctx.task_mode.value})")
        return ToolResult(
            text="[error: denied on unattended turn: modifying approval rules requires user confirmation]"
        )

    if action == "promote_scope_rules":
        # Read-only proposal. The rulebook is policy and is written only by the
        # admin_* tools after an interactive human confirmation (#1641 phase 4).
        from ..shell_scope_rules import promotion_proposal, scope_rules_path

        candidates, content = promotion_proposal(ctx.config)
        if not candidates:
            return "No global scope-rule promotion candidates at the current thresholds."
        path = scope_rules_path(ctx.config)
        preview = "\n".join(
            f"- `{rule.scope}` · `{rule.pattern}` ({rule.approvals} approvals, {rule.denials} denials)"
            for rule in candidates
        )
        return ToolResult(
            text=(
                f"{len(candidates)} promotion candidate(s) from the decision ledger.\n{preview}\n\n"
                f"Nothing was written. To apply, call admin_write(path={path.name!r}, content=<the "
                '"content" field>) — that requires human confirmation and a diff preview.'
            ),
            data={
                "path": str(path),
                "content": content,
                "candidates": [
                    {"scope": rule.scope, "pattern": rule.pattern, "approvals": rule.approvals, "denials": rule.denials}
                    for rule in candidates
                ],
            },
        )

    elif action == "condense_scope_rules":
        # Draft-only proposal: the model's output lands in a rulebook the human
        # applies via admin_write.
        from ..model_catalog import effective_active_model
        from ..shell_scope_rules import (
            ScopeRule,
            build_scope_condense_prompt,
            load_scope_rules,
            render_scope_rules,
            scope_rules_path,
        )

        existing = load_scope_rules(ctx.config).rules
        if len(existing) < 2:
            return "Need at least two global scope rules to condense."

        from ..llm import call_llm

        model_name = effective_active_model(ctx.config, getattr(ctx, "active_model", "") or "")
        try:
            response = await call_llm(
                ctx.config,
                [{"role": "user", "content": build_scope_condense_prompt(existing)}],
                model_name=model_name,
            )
        except Exception as exc:
            log.warning(f"[tool:shell_guidance] scope-rule condensation failed: {exc}")
            return ToolResult(text=f"[error: condensation failed: {exc}]")

        raw_text = (response.get("content") or "").strip()
        if "```" in raw_text:
            raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text, flags=re.MULTILINE)
            raw_text = re.sub(r"```$", "", raw_text, flags=re.MULTILINE).strip()
        try:
            data = json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            return ToolResult(text="[error: condensation returned unparseable output; nothing written]")
        proposed_raw = data.get("rules") if isinstance(data, dict) else None
        if not isinstance(proposed_raw, list):
            return ToolResult(text="[error: condensation response missing a 'rules' list; nothing written]")
        proposed: list[ScopeRule] = []
        for item in proposed_raw:
            if not isinstance(item, dict):
                continue
            scope = str(item.get("scope") or "").strip()
            pattern = str(item.get("pattern") or "").strip()
            text = str(item.get("rule") or "").strip()
            if scope and pattern and text:
                proposed.append(ScopeRule(scope=scope, pattern=pattern, rule=text))
        if not proposed:
            return ToolResult(text="[error: condensation returned no usable rules; nothing written]")

        path = scope_rules_path(ctx.config)
        content = render_scope_rules(proposed)
        return ToolResult(
            text=(
                f"Proposed {len(proposed)} rule(s) from {len(existing)} (draft only; nothing written). "
                f'To apply, call admin_write(path={path.name!r}, content=<the "content" field>) — '
                "that requires human confirmation and a diff preview."
            ),
            data={"path": str(path), "content": content, "before": len(existing), "after": len(proposed)},
        )

    return ToolResult(text=("[error: invalid action. Use 'list', 'promote_scope_rules', or 'condense_scope_rules'.]"))


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
        "summary": "Manage shell command allow patterns.",
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
        "summary": "Inspect and refine the global shell scope rulebook.",
        "function": {
            "name": "shell_guidance",
            "description": (
                "Inspect and refine the global shell scope rulebook. The rulebook is keyed by "
                "(activity scope, command shape) from the decision ledger of human approvals and "
                "denials, and is the sole mechanism for accumulating cross-conversation approval "
                "knowledge. Use 'list' to view current rules. "
                "Use 'promote_scope_rules' to draft rules from repeated human approvals in the "
                "evidence ledger; use 'condense_scope_rules' to merge the global rulebook into "
                "fewer rules. Both are draft-only: apply the returned content with admin_write, "
                "which requires human confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list",
                            "promote_scope_rules",
                            "condense_scope_rules",
                        ],
                        "description": "Action to perform (default: list)",
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
                "The command runs in the workspace directory, or in the `cwd` "
                "subdirectory when given. Use `cwd` instead of `cd <path> && ...` "
                "so the command stays a single atomic invocation. Use for tasks that "
                "need system interaction: checking disk space, running scripts, "
                "installing packages, etc. The user will see the command and must "
                "approve it before it runs. If a foreground time budget is configured, "
                "a command that exceeds it is automatically moved to the background: "
                "you receive a job ID and can inspect or stop it with "
                "shell_background_status / shell_background_stop — you do not need to "
                "predict whether a command will run long. Always fill `purpose` with what "
                "you are trying to accomplish; the security and auto-approval evaluators "
                "use it, and the user sees it on the confirmation prompt. If a command is "
                "denied with feedback, read the feedback carefully, revise your approach "
                "to address the concern, and execute the safer alternative."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Optional working directory, relative to the workspace root "
                            "(an absolute path inside the workspace also works). Must be "
                            "an existing directory inside the workspace. Defaults to the "
                            "workspace root."
                        ),
                    },
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                    "purpose": {
                        "type": "string",
                        "description": (
                            "Brief explanation of what you intend to accomplish with this "
                            "command and why it is needed. Used by the security and "
                            "auto-approval evaluators, and shown to the user on confirmation."
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
]
