"""Golden-corpus replay for direction-aware shell approval (#1661).

Replay historical conversation archives through the current *deterministic*
approval pipeline in dry-run: no command is executed, no model is called, and
nothing is written except an optional denial report the caller asks for.

What is real:

- the Tier 1 / sensitive-pattern security monitor (``evaluate_command``);
- the persisted shell allow patterns and their wildcard guard;
- the read-only classifier (``is_read_only_command``), called for real;
- activity derivation (``derive_activity``) over a per-command history slice.

What cannot be replayed without a model or a human, and is reported as
``judge`` rather than guessed: the Tier 2 security classifier, the aux
approval judge, and the human prompt. A ``judge`` outcome may become a prompt,
so the report gives both the definite prompt count and a prompt range.

The acceptance question for #1641 is not "fewer prompts" but "fewer prompts
while the real human denials are still caught". ``--check-denials`` fails when
any archived human denial would now be silently allowed.

Usage::

    decafclaw approvals replay --root ~/.decafclaw/decafclaw/workspace
    decafclaw approvals replay --all --check-denials
    decafclaw approvals replay --denials-out /tmp/denials.json
"""

import json
import logging
import random
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from decafclaw.activity import Activity, derive_activity
from decafclaw.security_monitor import SecurityStatus, evaluate_command, is_ambiguous_command
from decafclaw.shell_readonly import is_read_only_command
from decafclaw.tools.shell_tools import _command_matches_pattern, _load_allow_patterns

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

_SHELL_TOOL_NAMES = frozenset({"shell", "shell_background_start"})
DEFAULT_SAMPLE = 200


@dataclass(frozen=True)
class ReplaySettings:
    """The current pipeline's settings, applied to historical commands."""

    workspace: Path
    agent_path: Path
    activity_window: int = 50
    read_only_enabled: bool = True
    aux_approval_enabled: bool = False
    allow_patterns: tuple[str, ...] = ()
    opaque_arg_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayCommand:
    """One archived shell call plus the history visible when it ran."""

    conv_id: str
    tool_call_id: str
    command: str
    cwd: str | None
    history: Sequence[dict]
    unattended: bool = False


@dataclass(frozen=True)
class ReplayResult:
    """What the current pipeline would do with one archived command."""

    conv_id: str
    tool_call_id: str
    command: str
    cwd: str | None
    activity: str
    activity_established: bool | None
    tier: str
    # allow | block | prompt | judge. `judge` means a model call (or a human,
    # when the aux judge is disabled) would decide; it is not a silent allow.
    decision: str
    reason: str = ""
    human_approved: bool | None = None
    historical_judge: str | None = None

    @property
    def denied_by_human(self) -> bool:
        return self.human_approved is False


@dataclass
class ReplayReport:
    archives_total: int = 0
    archives_replayed: int = 0
    commands: int = 0
    results: list[ReplayResult] = field(default_factory=list)
    baseline_prompts: int = 0
    baseline_approved: int = 0
    baseline_denied: int = 0
    unmatched_denials: int = 0
    conversations_with_shell: int = 0

    @property
    def per_tier(self) -> dict[str, int]:
        return dict(sorted(Counter(result.tier for result in self.results).items()))

    @property
    def per_decision(self) -> dict[str, int]:
        return dict(sorted(Counter(result.decision for result in self.results).items()))

    @property
    def denial_results(self) -> list[ReplayResult]:
        return [result for result in self.results if result.denied_by_human]

    @property
    def silently_allowed_denials(self) -> list[ReplayResult]:
        return [result for result in self.denial_results if result.decision == "allow"]

    @property
    def judged_denials(self) -> list[ReplayResult]:
        return [result for result in self.denial_results if result.decision == "judge"]

    def summary(self) -> dict[str, Any]:
        decisions = self.per_decision
        definite_prompts = decisions.get("prompt", 0)
        may_prompt = decisions.get("judge", 0)
        return {
            "archives_total": self.archives_total,
            "archives_replayed": self.archives_replayed,
            "conversations_with_shell": self.conversations_with_shell,
            "commands": self.commands,
            "baseline_prompts": self.baseline_prompts,
            "baseline_approved": self.baseline_approved,
            "baseline_denied": self.baseline_denied,
            "replay_allow": decisions.get("allow", 0),
            "replay_block": decisions.get("block", 0),
            "replay_prompts": definite_prompts,
            "replay_judge": may_prompt,
            "replay_prompt_range": [definite_prompts, definite_prompts + may_prompt],
            "by_tier": self.per_tier,
            "by_decision": decisions,
            "denials": len(self.denial_results),
            "denials_silently_allowed": len(self.silently_allowed_denials),
            "denials_judged": len(self.judged_denials),
            "denials_prompted": sum(1 for result in self.denial_results if result.decision == "prompt"),
            "unmatched_denials": self.unmatched_denials,
        }


class _HistorySlice(Sequence[dict]):
    """A cheap prefix view of an archive, so a full run does not copy history."""

    def __init__(self, messages: list[dict], end: int):
        self._messages = messages
        self._end = end

    def __len__(self) -> int:
        return self._end

    def __getitem__(self, index):
        if isinstance(index, slice):
            return self._messages[: self._end][index]
        resolved = index + self._end if index < 0 else index
        if resolved < 0 or resolved >= self._end:
            raise IndexError(index)
        return self._messages[resolved]


def _load_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        log.warning("replay: cannot read %s: %s", path, exc)
        return []
    messages: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            messages.append(message)
    return messages


def _normalized_root(root: Path) -> Path:
    root = root.expanduser().resolve()
    if (root / "archive.jsonl").is_file():
        return root.parent
    # A workspace dir already holds conversations/ or sessions/. Only descend
    # into workspace/ when the caller passed the agent data dir itself.
    if (root / "conversations").is_dir() or (root / "sessions").is_dir():
        return root
    if (root / "workspace").is_dir():
        return (root / "workspace").resolve()
    return root


def iter_archives(root: Path) -> list[Path]:
    """Archives under an agent data dir, a workspace dir, or one conversation."""
    root = root.expanduser().resolve()
    if (root / "archive.jsonl").is_file():
        return [root / "archive.jsonl"]
    root = _normalized_root(root)
    found = sorted(root.glob("*/*/archive.jsonl"))
    if not found:
        found = sorted(root.rglob("archive.jsonl"))
    return found


def _sample(paths: list[Path], limit: int, all_: bool, seed: int) -> list[Path]:
    if all_ or limit <= 0 or limit >= len(paths):
        return paths
    return sorted(random.Random(seed).sample(paths, limit))


def _parse_arguments(raw: Any) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _looks_unattended(messages: list[dict], conv_id: str) -> bool:
    if conv_id.startswith(("schedule-", "heartbeat-", "background-wake-")):
        return True
    for message in messages[:3]:
        if message.get("role") == "user":
            content = str(message.get("content") or "")
            if "You are running a scheduled task" in content or "heartbeat" in content.lower():
                return True
    return False


def iter_commands(conv_id: str, messages: list[dict]) -> Iterator[ReplayCommand]:
    unattended = _looks_unattended(messages, conv_id)
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict) or function.get("name") not in _SHELL_TOOL_NAMES:
                continue
            args = _parse_arguments(function.get("arguments"))
            if args is None:
                continue
            command = str(args.get("command") or "")
            if not command.strip():
                continue
            cwd = args.get("cwd")
            yield ReplayCommand(
                conv_id=conv_id,
                tool_call_id=str(tool_call.get("id") or ""),
                command=command,
                cwd=str(cwd) if cwd is not None else None,
                history=_HistorySlice(messages, index + 1),
                unattended=unattended,
            )


def _human_prompts(messages: list[dict]) -> tuple[list[dict], int]:
    """Human decisions on shell confirmations, keyed where the archive allows.

    Pairing uses ``confirmation_request.tool_call_id`` and
    ``confirmation_response.confirmation_id`` — the archive's own identifiers —
    not message order. Older archives lack ``tool_call_id``; those are paired
    by exact command text as a fallback, and a denial with neither identifier
    nor command is reported as unmatched rather than silently dropped.

    Returns ``(prompts, request_count)`` where each prompt has
    ``tool_call_id``, ``command``, and ``approved`` (``None`` = unanswered).
    """
    requests: dict[str, dict] = {}
    answered: set[str] = set()
    for message in messages:
        role = message.get("role")
        confirmation_id = str(message.get("confirmation_id") or "")
        if role == "confirmation_request":
            if message.get("action_type") != "run_shell_command" and not (message.get("action_data") or {}).get(
                "command"
            ):
                continue
            requests[confirmation_id] = message
        elif role == "confirmation_response":
            answered.add(confirmation_id)
    prompts: list[dict] = []
    for confirmation_id, request in requests.items():
        action = request.get("action_data") or {}
        prompts.append(
            {
                "confirmation_id": confirmation_id,
                "tool_call_id": str(request.get("tool_call_id") or ""),
                "command": str(action.get("command") or ""),
                "approved": None,
            }
        )
    by_id = {prompt["confirmation_id"]: prompt for prompt in prompts}
    for message in messages:
        if message.get("role") != "confirmation_response":
            continue
        approved = message.get("approved")
        if not isinstance(approved, bool):
            continue
        prompt = by_id.get(str(message.get("confirmation_id") or ""))
        if prompt is not None:
            prompt["approved"] = approved
    return prompts, len(requests)


def _judge_verdicts(messages: list[dict]) -> list[tuple[str, str]]:
    """Historical aux-judge verdicts, when the archive has them.

    Records predating Phase 0 (#1641) have none. ``content`` may be a JSON
    object/string carrying ``command``; when it does not, the verdict cannot be
    paired and is skipped rather than guessed.
    """
    verdicts: list[tuple[str, str]] = []
    for message in messages:
        if message.get("role") != "shell_approval":
            continue
        tool = str(message.get("tool") or "").upper()
        verdict = "ALLOWED" if "ALLOWED" in tool else "DECLINED" if "DECLINED" in tool else ""
        if not verdict:
            continue
        content = message.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                content = None
        command = ""
        if isinstance(content, dict):
            command = str(content.get("command") or "")
        if command:
            verdicts.append((command, verdict))
    return verdicts


def _shell_cwd(cwd: str | None, workspace: Path) -> tuple[Path | None, str]:
    if not cwd:
        return workspace, ""
    candidate = Path(cwd)
    target = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
    if not target.is_relative_to(workspace):
        return None, f"cwd is outside the workspace: {cwd}"
    return target, ""


@dataclass(frozen=True)
class _ReplayShell:
    activity_window: int


@dataclass(frozen=True)
class _ReplayConfig:
    workspace_path: Path
    agent_path: Path
    shell: _ReplayShell


@dataclass(frozen=True)
class _ReplayTools:
    current_call_id: str


@dataclass(frozen=True)
class _ReplayContext:
    config: _ReplayConfig
    history: Sequence[dict]
    tools: _ReplayTools


def _activity_for(command: ReplayCommand, settings: ReplaySettings) -> Activity:
    ctx = _ReplayContext(
        config=_ReplayConfig(
            workspace_path=settings.workspace,
            agent_path=settings.agent_path,
            shell=_ReplayShell(activity_window=settings.activity_window),
        ),
        history=command.history,
        tools=_ReplayTools(current_call_id=command.tool_call_id),
    )
    return derive_activity(cast("Context", ctx), command.command, command.cwd)


def _replay_one(command: ReplayCommand, settings: ReplaySettings) -> ReplayResult:
    tier1 = evaluate_command(
        command.command,
        workspace_path=settings.workspace,
        is_autonomous=False,
        opaque_arg_commands=list(settings.opaque_arg_commands),
    )

    def result(tier: str, decision: str, reason: str = "", activity: Activity | None = None) -> ReplayResult:
        return ReplayResult(
            conv_id=command.conv_id,
            tool_call_id=command.tool_call_id,
            command=command.command,
            cwd=command.cwd,
            activity=activity.domain if activity else "",
            activity_established=activity.established if activity else None,
            tier=tier,
            decision=decision,
            reason=reason,
        )

    if tier1.status == SecurityStatus.BLOCK:
        return result("tier1_block", "block", tier1.reason)
    if _command_matches_pattern(command.command, list(settings.allow_patterns)):
        return result("persisted_pattern", "allow")
    cwd, cwd_reason = _shell_cwd(command.cwd, settings.workspace)
    if cwd is None:
        return result("cwd_outside", "block", cwd_reason)
    if settings.read_only_enabled and is_read_only_command(command.command, cwd=cwd, workspace=settings.workspace):
        return result("read_only", "allow")
    if tier1.status == SecurityStatus.ASK:
        return result("security_monitor_ask", "prompt", tier1.reason)
    if is_ambiguous_command(command.command):
        return result("security_monitor_tier2", "judge", "ambiguous command needs the Tier 2 classifier")

    activity = _activity_for(command, settings)
    if not activity.established:
        decision = "judge" if command.unattended else "prompt"
        return result("activity_new", decision, activity.reason(), activity)
    if settings.aux_approval_enabled:
        return result("aux_judge", "judge", activity.reason(), activity)
    return result("human_prompt", "prompt", activity.reason(), activity)


def _pair_history(
    results: list[ReplayResult],
    messages: list[dict],
    settings: ReplaySettings,
    conv_id: str,
) -> tuple[int, int, int, int, list[ReplayResult]]:
    """Attach human verdicts and historical judge verdicts.

    Returns ``(prompts, approved, denied, unmatched, extra_results)``. A denied
    prompt that cannot be tied to an archived tool call but carries its command
    text is replayed standalone and appended to ``extra_results`` so the denial
    gate still sees it.
    """
    prompts, prompt_count = _human_prompts(messages)
    used: set[int] = set()
    extra: list[ReplayResult] = []
    unmatched = 0
    approved = denied = 0
    unattended = _looks_unattended(messages, conv_id)
    for prompt in prompts:
        verdict = prompt["approved"]
        if verdict is True:
            approved += 1
        elif verdict is False:
            denied += 1
        if verdict is None:
            continue
        target = None
        call_id = prompt["tool_call_id"]
        if call_id:
            target = next(
                (index for index, result in enumerate(results) if result.tool_call_id == call_id and index not in used),
                None,
            )
        if target is None and prompt["command"]:
            target = next(
                (
                    index
                    for index, result in enumerate(results)
                    if index not in used and result.command.strip() == prompt["command"].strip()
                ),
                None,
            )
        if target is not None:
            used.add(target)
            results[target] = replace(results[target], human_approved=verdict)
            continue
        if verdict is False and prompt["command"]:
            synthetic = ReplayCommand(
                conv_id=conv_id,
                tool_call_id="",
                command=prompt["command"],
                cwd=None,
                history=(),
                unattended=unattended,
            )
            extra.append(replace(_replay_one(synthetic, settings), human_approved=False))
        elif verdict is False:
            unmatched += 1

    judge = _judge_verdicts(messages)
    if judge:
        remaining = list(judge)
        for index, result in enumerate(results):
            for offset, (command, verdict) in enumerate(remaining):
                if command.strip() == result.command.strip():
                    results[index] = replace(result, historical_judge=verdict)
                    remaining.pop(offset)
                    break
    return prompt_count, approved, denied, unmatched, extra


def replay_archives(
    root: Path,
    settings: ReplaySettings,
    *,
    limit: int = DEFAULT_SAMPLE,
    all_: bool = False,
    seed: int = 0,
) -> ReplayReport:
    """Replay sampled archives under ``root`` through the current pipeline."""
    archives = iter_archives(root)
    report = ReplayReport(archives_total=len(archives))
    sampled = _sample(archives, limit, all_, seed)
    report.archives_replayed = len(sampled)
    for archive in sampled:
        messages = _load_jsonl(archive)
        if not messages:
            continue
        conv_id = archive.parent.name
        commands = list(iter_commands(conv_id, messages))
        if commands:
            report.conversations_with_shell += 1
        results = [_replay_one(command, settings) for command in commands]
        prompts, approved, denied, unmatched, extra = _pair_history(results, messages, settings, conv_id)
        report.baseline_prompts += prompts
        report.baseline_approved += approved
        report.baseline_denied += denied
        report.unmatched_denials += unmatched
        report.commands += len(results) + len(extra)
        report.results.extend(results)
        report.results.extend(extra)
    return report


def settings_from_config(config, root: Path, workspace_override: Path | None = None) -> ReplaySettings:
    """Build replay settings from the live agent config for an archive root.

    ``workspace_override`` is for a corpus recorded under an older workspace
    path (a relocated data dir): containment and activity attribution must use
    the path the commands actually ran in, or every absolute path looks like an
    escape and the replay reports prompts that never happened.
    """
    workspace = workspace_override.expanduser().resolve() if workspace_override else _normalized_root(root)
    agent_path = Path(getattr(config, "agent_path", workspace.parent))
    return ReplaySettings(
        workspace=workspace,
        agent_path=agent_path,
        activity_window=int(getattr(config.shell, "activity_window", 50)),
        read_only_enabled=getattr(config.shell, "read_only_enabled", True) is not False,
        aux_approval_enabled=bool(getattr(config.shell, "aux_approval_enabled", False)),
        allow_patterns=tuple(_load_allow_patterns(SimpleNamespace(agent_path=agent_path))),
        opaque_arg_commands=tuple(getattr(config.shell, "opaque_arg_commands", ()) or ()),
    )


def check_denials(report: ReplayReport) -> list[ReplayResult]:
    """The acceptance gate: every archived human denial must not be silently allowed."""
    return report.silently_allowed_denials


def format_text(report: ReplayReport, root: Path) -> str:
    summary = report.summary()
    low, high = summary["replay_prompt_range"]
    lines = [
        "Approval replay (dry run — nothing executed, no model calls)",
        f"archives: {summary['archives_replayed']} of {summary['archives_total']} under {root}",
        f"conversations with shell commands: {summary['conversations_with_shell']}",
        f"shell commands: {summary['commands']}",
        "",
        f"baseline human prompts: {summary['baseline_prompts']} "
        f"(approved {summary['baseline_approved']}, denied {summary['baseline_denied']})",
        f"replay silent allows:   {summary['replay_allow']}",
        f"replay blocks:          {summary['replay_block']}",
        f"replay definite prompts:{summary['replay_prompts']}",
        f"replay judge calls:     {summary['replay_judge']} (may become prompts)",
        f"replay prompt range:    {low}..{high}",
        "",
        "by tier:",
    ]
    for tier, count in summary["by_tier"].items():
        lines.append(f"  {tier:<28} {count}")
    if not summary["by_tier"]:
        lines.append("  (no commands)")
    lines.extend(
        [
            "",
            f"human denials: {summary['denials']} "
            f"(prompted {summary['denials_prompted']}, judged {summary['denials_judged']}, "
            f"silently allowed {summary['denials_silently_allowed']}, "
            f"unmatched {summary['unmatched_denials']})",
        ]
    )
    if summary["denials_silently_allowed"]:
        lines.append("REGRESSIONS — denials the current pipeline would allow silently:")
        for result in report.silently_allowed_denials:
            lines.append(f"  [{result.conv_id}] {result.command}")
    return "\n".join(lines)


def denials_payload(report: ReplayReport) -> list[dict]:
    return [
        {
            "conv_id": result.conv_id,
            "command": result.command,
            "cwd": result.cwd,
            "activity": result.activity,
            "activity_established": result.activity_established,
            "tier": result.tier,
            "decision": result.decision,
            "would_notify_human": result.decision in ("prompt", "judge"),
        }
        for result in report.denial_results
    ]


def run_cli(args) -> int:
    from .config import load_config

    config = load_config()
    root = Path(args.root).expanduser() if args.root else config.workspace_path
    settings = settings_from_config(config, root, Path(args.workspace) if args.workspace else None)
    if args.activity_window is not None:
        settings = replace(settings, activity_window=args.activity_window)
    if args.no_read_only:
        settings = replace(settings, read_only_enabled=False)
    if args.aux_enabled == "on":
        settings = replace(settings, aux_approval_enabled=True)
    elif args.aux_enabled == "off":
        settings = replace(settings, aux_approval_enabled=False)

    report = replay_archives(root, settings, limit=args.limit, all_=args.all, seed=args.seed)

    if args.json:
        print(json.dumps({"root": str(_normalized_root(root)), "summary": report.summary()}, indent=2))
    else:
        print(format_text(report, _normalized_root(root)))

    if args.show_denials:
        print("\ndenials:")
        for entry in denials_payload(report):
            print(f"  {entry['decision']:<6} {entry['tier']:<22} {entry['conv_id']}  {entry['command']}")

    if args.denials_out:
        Path(args.denials_out).expanduser().write_text(json.dumps(denials_payload(report), indent=2) + "\n")
        print(f"\nwrote denial report: {args.denials_out}")

    regressions = check_denials(report)
    if args.check_denials and regressions:
        print(f"\nFAIL: {len(regressions)} archived human denial(s) would now be silently allowed")
        return 1
    return 0
