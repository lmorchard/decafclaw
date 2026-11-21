"""Conversation activity derived from the shell command stream (#1641).

Direction decides *whether* shell approval asks a human; the aux judge decides
only whether a command in the conversation's established direction is
acceptable. This module derives that direction without a model call, a user
action, or a mode switch: it reads the shell commands already in the
conversation history and labels them with a coarse activity domain.

Sources, in priority order:

1. ``cwd`` — resolved by ``_resolve_shell_cwd`` before approval; the primary
   signal, because most commands carry no domain token but their directory does.
2. Domain tokens in the command string — repo names and known project paths,
   matched only after the agent workspace prefix is removed (the workspace path
   itself contains ``decafclaw``, which would otherwise tag every command).
3. ``unknown`` — no attributable domain. Treated as its own activity: the first
   unattributed command in a conversation asks, later ones are in-direction.

Decay is a sliding window of the most recent shell calls (``shell.
activity_window``). A domain is "established" when every domain the current
command touches was seen in that window. Recomputing from ``ctx.history`` on
each call means a compacted conversation automatically loses stale direction —
there is no in-memory activity state to reset after compaction. Restarting the
server loses nothing that the history does not also lose.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from decafclaw.context import Context

UNKNOWN_ACTIVITY = "unknown"

# Tools that count as conversation activity. `shell_background_start` runs the
# same approval pipeline, so it belongs to the same stream.
_SHELL_TOOL_NAMES = frozenset({"shell", "shell_background_start"})

# A worktree is one repo, not one repo per branch (#1641 §4.2). Dropping the
# `worktrees/<branch>` segment keeps a rule or activity label from re-learning
# the same repo on every branch.
_WORKTREE_SEGMENT_RE = re.compile(r"(?:^|/)(?:\.claude/)?worktrees/[^/]+/?")

# Ordered: the first match names the primary activity. Paths are matched
# case-insensitively; the decafclaw pattern requires a path segment so a bare
# prose mention does not tag a command.
_DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("homelab", re.compile(r"lmorchard-homelab", re.IGNORECASE)),
    ("blog", re.compile(r"blog\.lmorchard\.com|blog-ssg|content/posts", re.IGNORECASE)),
    ("vault", re.compile(r"obsidian|(?:^|/)vault(?:/|$)|vault_", re.IGNORECASE)),
    ("television", re.compile(r"television|\btv\b", re.IGNORECASE)),
    ("me2md", re.compile(r"me-to-markdown", re.IGNORECASE)),
    ("handwriting", re.compile(r"handwriting", re.IGNORECASE)),
    ("weeknotes", re.compile(r"weeknotes", re.IGNORECASE)),
    ("decafclaw", re.compile(r"(?:^|/)decafclaw(?:/|$)")),
)


@dataclass(frozen=True)
class Activity:
    """The conversation direction a command falls in.

    ``domain`` is the label shown to a human and the judge. ``established`` is
    false when any domain the command touches is new to the recent command
    window. ``prior_domains`` are the other domains seen in that window, so a
    prompt can say what the conversation *was* doing.
    """

    domain: str
    established: bool
    prior_domains: tuple[str, ...] = ()

    def reason(self) -> str:
        """One-line explanation for a human confirmation card."""
        if self.established:
            return f"`{self.domain}` is an established activity in this conversation"
        if self.domain == UNKNOWN_ACTIVITY:
            if self.prior_domains:
                prior = ", ".join(f"`{name}`" for name in self.prior_domains)
                return f"first shell command outside the conversation's recent activity ({prior})"
            return "first shell command in this conversation"
        if self.prior_domains:
            prior = ", ".join(f"`{name}`" for name in self.prior_domains)
            return f"first `{self.domain}` command in this conversation; recent activity: {prior}"
        return f"first `{self.domain}` command in this conversation"


def _normalize_location(text: str) -> str:
    """Collapse a path or command fragment into a matchable location string."""
    normalized = text.replace("\\", "/")
    normalized = _WORKTREE_SEGMENT_RE.sub("/", normalized)
    normalized = re.sub(r"/+", "/", normalized)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _path_location(value: str | None, workspace: Path) -> str:
    """Workspace-relative location for a cwd argument."""
    if not value:
        return ""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        relative = candidate.resolve().relative_to(workspace.resolve())
    except (OSError, RuntimeError, ValueError):
        # Outside the workspace: no activity attribution from the path. The
        # command will prompt anyway because it leaves the sandbox.
        return ""
    return _normalize_location(str(relative))


def _command_location(command: str, workspace: Path) -> str:
    """Command text with the agent workspace prefix removed."""
    text = command or ""
    workspace_text = str(workspace.resolve())
    text = text.replace(workspace_text + "/", "").replace(workspace_text, "")
    return _normalize_location(text)


def _match_domains(location: str) -> list[str]:
    return [name for name, pattern in _DOMAIN_PATTERNS if pattern.search(location)]


def derive_domains(command: str, cwd: str | None, workspace: Path) -> tuple[str, ...]:
    """Domains the command touches, cwd first and command text second.

    cwd names the primary activity — the work the command is actually being run
    against — and command tokens add any other project the command reaches for,
    so mentioning another repo counts as drift instead of hiding inside the
    cwd's label.
    """
    domains: list[str] = []
    location = _path_location(cwd, workspace)
    if location:
        domains.extend(_match_domains(location))
    for name in _match_domains(_command_location(command, workspace)):
        if name not in domains:
            domains.append(name)
    return tuple(domains)


def _tool_arguments(tool_call: dict[str, Any]) -> dict[str, Any] | None:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None
    raw = function.get("arguments")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def prior_domains(
    history: list | None,
    workspace: Path,
    *,
    window: int,
    current_command: str,
    current_cwd: str | None,
    current_call_id: str = "",
) -> set[str]:
    """Domains from the most recent ``window`` shell calls before this one.

    The current call is already appended to the assistant message that carries
    it, so it must not count as its own precedent. Its tool-call id is the
    precise exclusion; the command/cwd pair is the fallback for history that
    predates ids (tests and restored transcripts).
    """
    seen: set[str] = set()
    count = 0
    skipped_current = False
    normalized_cwd = (current_cwd or "").rstrip("/")
    for message in reversed(history or []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for tool_call in reversed(message.get("tool_calls") or []):
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict) or function.get("name") not in _SHELL_TOOL_NAMES:
                continue
            args = _tool_arguments(tool_call)
            if args is None:
                continue
            command = str(args.get("command") or "")
            cwd = args.get("cwd")
            cwd_text = str(cwd) if cwd is not None else ""
            if current_call_id:
                if tool_call.get("id") == current_call_id:
                    continue
            elif not skipped_current and command == current_command and cwd_text.rstrip("/") == normalized_cwd:
                skipped_current = True
                continue
            domains = derive_domains(command, cwd_text, workspace)
            seen.update(domains or (UNKNOWN_ACTIVITY,))
            count += 1
            if window > 0 and count >= window:
                return seen
    return seen


def derive_activity(ctx: "Context", command: str, cwd: str | None) -> Activity:
    """Derive the current command's activity from this conversation's history."""
    workspace = ctx.config.workspace_path
    domains = derive_domains(command, cwd, workspace)
    current = set(domains) or {UNKNOWN_ACTIVITY}
    domain = domains[0] if domains else UNKNOWN_ACTIVITY
    window = ctx.config.shell.activity_window
    known = prior_domains(
        getattr(ctx, "history", None),
        workspace,
        window=window,
        current_command=command,
        current_cwd=cwd,
        current_call_id=ctx.tools.current_call_id,
    )
    established = current.issubset(known)
    prior = tuple(sorted(known - current - {UNKNOWN_ACTIVITY}))
    return Activity(domain=domain, established=established, prior_domains=prior)
