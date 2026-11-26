"""Host-keyed SSH approval rules (#1936).

Admin rules live at ``data/{agent_id}/ssh_rules.jsonl`` (admin-protected, outside
the workspace). Each line is:

    {"tool": "ssh_exec", "host": "scavenger-*", "action": "allow"}

``tool`` may be ``ssh_exec``, ``ssh_copy``, or ``*``. ``host`` is an fnmatch
pattern over the SSH alias. ``action`` is ``allow``, ``ask``, or ``deny``.

Safety: a malformed or unreadable rulebook fails open to the normal behavior
(known host runs; unlisted host asks). Precedence is deny > allow > ask; a
malformed line is skipped, never treated as allow.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

RULES_FILENAME = "ssh_rules.jsonl"
ACTIONS = ("allow", "ask", "deny")
TOOLS = ("ssh_exec", "ssh_copy", "*")


@dataclass(frozen=True)
class SshRule:
    tool: str
    host: str
    action: str


def rules_path(config) -> Path:
    return Path(config.agent_path) / RULES_FILENAME


def load_rules(config) -> list[SshRule]:
    """Load valid rules; skip malformed lines and unreadable files."""
    path = rules_path(config)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rules: list[SshRule] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            tool = str(data.get("tool") or "").strip()
            host = str(data.get("host") or "").strip()
            action = str(data.get("action") or "").strip().lower()
        except (json.JSONDecodeError, AttributeError):
            log.warning("Skipping malformed SSH rule: %r", line)
            continue
        if not tool or not host or action not in ACTIONS:
            log.warning("Skipping invalid SSH rule: %r", line)
            continue
        rules.append(SshRule(tool=tool, host=host, action=action))
    return rules


def matching_rules(config, tool: str, host: str) -> list[SshRule]:
    return [rule for rule in load_rules(config) if rule.tool in (tool, "*") and fnmatch.fnmatchcase(host, rule.host)]


def decide(config, tool: str, host: str) -> str | None:
    """Return the effective action for (tool, host), or None for default behavior."""
    actions = {rule.action for rule in matching_rules(config, tool, host)}
    for action in ("deny", "allow", "ask"):
        if action in actions:
            return action
    return None


def validate_rule(tool: str, host: str, action: str, *, allow_empty_action: bool = False) -> SshRule:
    """Validate one rule and return it, raising ValueError on bad input."""
    tool = str(tool or "").strip()
    host = str(host or "").strip()
    action = str(action or "").strip().lower()
    if tool not in TOOLS:
        raise ValueError(f"tool must be one of {', '.join(TOOLS)}")
    if not host:
        raise ValueError("host is required")
    if any(ch.isspace() for ch in host):
        raise ValueError("host must not contain whitespace")
    if not allow_empty_action and action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    return SshRule(tool=tool, host=host, action=action)


def format_rule(rule: SshRule) -> str:
    """Serialize one rule as a stable JSON line."""
    return json.dumps({"tool": rule.tool, "host": rule.host, "action": rule.action})
