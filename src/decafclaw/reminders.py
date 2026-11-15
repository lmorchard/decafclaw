"""Scoped reminders — admin-authored rules checked before a tool call runs (#1127).

A rule names a tool (or ``*``), a set of predicates over the call's finalized
arguments, and an action:

- ``reject`` stops the call before dispatch and returns correction guidance.
- ``remind`` lets the call run and queues one ephemeral message for the agent
  loop to inject (role ``user``, explicit non-authorship disclaimer, never
  archived — mirroring the loop-breaker nudge).

Rules live at ``data/{agent_id}/reminders.json``, the admin tier the agent's
file tools cannot write, so "trusted" is enforced by layout rather than by
convention. Every failure is fail-open: a malformed rule is skipped with a
warning and never breaks tool execution.

Nothing here grants capability. The gate can only reject or remind; shell
approval, skill permissions, child tool restrictions, and workflow allowlists
all still apply to every call that passes it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .conversation_paths import conversation_dir

if TYPE_CHECKING:
    from .config import Config
    from .context import Context

log = logging.getLogger(__name__)

REMINDERS_FILENAME = "reminders.json"
LEDGER_FILENAME = "reminders.jsonl"

_ACTIONS = frozenset({"reject", "remind"})
_OPERATORS = frozenset({"equals", "contains", "pattern", "exists"})

# Non-authorship disclaimer, mirroring the loop-breaker nudge (#680): the
# message rides at role "user" so the model weights it, and the text must say
# the user did not send it or the model apologizes for a correction nobody made.
_DISCLAIMER = (
    " Automated reminder from the agent runtime — the user did not send this, so do not respond to it as if they had."
)


@dataclass(frozen=True)
class ReminderRule:
    """One parsed rule. Invalid rules never reach this shape."""

    id: str
    tool: str
    match: tuple[dict[str, Any], ...]
    action: str
    message: str


@dataclass(frozen=True)
class ReminderHit:
    """A rule that matched one call, after budget accounting."""

    rule_id: str
    tool: str
    action: str
    message: str
    terse: bool


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

# path -> (mtime_ns, rules). Rules load once per agent start and refresh when
# the file changes, matching the spec's "once per start, on hot-reload" without
# a config-watcher ordering dependency.
_rules_cache: dict[Path, tuple[int, list[ReminderRule]]] = {}


def rules_path(config: "Config") -> Path:
    """Admin-tier rules file for this agent."""
    return config.agent_path / REMINDERS_FILENAME


def ledger_path(config: "Config", conv_id: str) -> Path | None:
    """Append-only match ledger for one conversation, or None without a conv."""
    if not conv_id:
        return None
    return conversation_dir(config, conv_id, create=True) / LEDGER_FILENAME


def load_rules(config: "Config") -> list[ReminderRule]:
    """Load and cache the rule file. Missing or unreadable means no rules."""
    path = rules_path(config)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return []
    cached = _rules_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    rules = _parse_rules(path)
    _rules_cache[path] = (mtime, rules)
    return rules


def clear_rules_cache() -> None:
    """Drop the parsed-rule cache (tests, and config hot-reload)."""
    _rules_cache.clear()


def _parse_rules(path: Path) -> list[ReminderRule]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("scoped reminders: cannot read %s (%s); no rules active", path, exc)
        return []
    items = raw.get("rules") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        log.warning("scoped reminders: %s has no 'rules' list; no rules active", path)
        return []
    rules: list[ReminderRule] = []
    for index, item in enumerate(items):
        rule = _parse_rule(item, index, path)
        if rule is not None:
            rules.append(rule)
    return rules


def _parse_rule(item: Any, index: int, path: Path) -> ReminderRule | None:
    def skip(reason: str) -> None:
        log.warning("scoped reminders: skipping rule %d in %s: %s", index, path, reason)

    if not isinstance(item, dict):
        skip("not an object")
        return None
    rule_id = item.get("id")
    tool = item.get("tool")
    action = item.get("action")
    message = item.get("message")
    match = item.get("match", [])
    if not isinstance(rule_id, str) or not rule_id.strip():
        skip("missing string 'id'")
        return None
    if not isinstance(tool, str) or not tool.strip():
        skip("missing string 'tool'")
        return None
    if action not in _ACTIONS:
        skip(f"'action' must be one of {sorted(_ACTIONS)}")
        return None
    if not isinstance(message, str) or not message.strip():
        skip("missing string 'message'")
        return None
    if not isinstance(match, list):
        skip("'match' must be a list")
        return None
    predicates: list[dict[str, Any]] = []
    for p_index, predicate in enumerate(match):
        problem = _predicate_problem(predicate)
        if problem:
            skip(f"match[{p_index}] {problem}")
            return None
        predicates.append(predicate)
    return ReminderRule(
        id=rule_id.strip(),
        tool=tool.strip(),
        match=tuple(predicates),
        action=action,
        message=message.strip(),
    )


def _predicate_problem(predicate: Any) -> str | None:
    """Return why a predicate is unusable, or None when it is valid."""
    if not isinstance(predicate, dict):
        return "is not an object"
    if not isinstance(predicate.get("path"), str) or not predicate["path"].strip():
        return "needs a string 'path'"
    operators = [op for op in _OPERATORS if op in predicate]
    if len(operators) != 1:
        return f"needs exactly one operator from {sorted(_OPERATORS)}"
    if operators[0] == "pattern":
        pattern = predicate["pattern"]
        if not isinstance(pattern, str) or not pattern:
            return "needs a non-empty string 'pattern'"
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"has an invalid regex ({exc})"
    return None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _resolve_path(arguments: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    node: Any = arguments
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False, None
    return True, node


def _predicate_matches(predicate: dict[str, Any], arguments: dict[str, Any]) -> bool:
    found, value = _resolve_path(arguments, predicate["path"])
    if "exists" in predicate:
        return found
    if not found:
        # A value predicate cannot hold for an absent path. Fail closed here
        # (no reminder) rather than treating "missing" as a match.
        return False
    if "equals" in predicate:
        return value == predicate["equals"]
    if "contains" in predicate:
        needle = predicate["contains"]
        if isinstance(value, str):
            return isinstance(needle, str) and needle in value
        if isinstance(value, list):
            return needle in value
        return False
    if isinstance(value, str):
        try:
            return re.search(predicate["pattern"], value) is not None
        except re.error as exc:  # validated at load; defensive
            log.warning("scoped reminders: bad pattern %r (%s)", predicate["pattern"], exc)
            return False
    return False


def _rule_matches(rule: ReminderRule, tool_name: str, arguments: dict[str, Any]) -> bool:
    if rule.tool != "*" and rule.tool != tool_name:
        return False
    # Empty ``match`` is a whole-tool rule: both actions are legal there.
    return all(_predicate_matches(predicate, arguments) for predicate in rule.match)


# ---------------------------------------------------------------------------
# Budgets and recording
# ---------------------------------------------------------------------------


def _ledger_count(config: "Config", conv_id: str, rule_id: str, limit: int) -> int:
    """Count this rule's recorded matches for the conversation, capped at limit."""
    path = ledger_path(config, conv_id)
    if path is None or not path.exists():
        return 0
    count = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if rule_id not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("rule_id") == rule_id:
                    count += 1
                    if count >= limit:
                        break
    except OSError as exc:
        log.warning("scoped reminders: cannot read ledger %s (%s)", path, exc)
        return 0
    return count


def _seed_count(ctx: "Context", rule_id: str) -> int:
    """Matches so far for this rule, read from the ledger on first use.

    The ledger is the durable source; the in-memory count takes over once
    seeded so concurrent calls debit one budget without re-reading the file.
    """
    state = ctx.reminder_state
    if rule_id not in state.seeded_rules:
        state.seeded_rules.add(rule_id)
        limit = max(1, int(ctx.config.reminders.max_matches_per_rule)) + 1
        state.match_counts[rule_id] = _ledger_count(ctx.config, ctx.conv_id, rule_id, limit)
    return state.match_counts.get(rule_id, 0)


def _append_ledger(ctx: "Context", entry: dict[str, Any]) -> None:
    path = ledger_path(ctx.config, ctx.conv_id)
    if path is None:
        return
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:
        # Observability only — never fail a tool call over the ledger.
        log.warning("scoped reminders: cannot append ledger %s (%s)", path, exc)


async def _record(ctx: "Context", rule: ReminderRule, tool_name: str, terse: bool) -> None:
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "rule_id": rule.id,
        "tool": tool_name,
        "action": rule.action,
        "terse": terse,
        "conv_id": ctx.conv_id,
    }
    _append_ledger(ctx, entry)
    try:
        await ctx.publish(
            "scoped_reminder",
            rule_id=rule.id,
            tool=tool_name,
            action=rule.action,
            terse=terse,
        )
    except Exception:  # pragma: no cover - event bus is optional in tests
        log.debug("scoped reminders: failed to publish match event", exc_info=True)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


async def check(ctx: "Context", tool_name: str, arguments: dict[str, Any]) -> ReminderHit | None:
    """Evaluate the rules for one call, debit budgets, and record a match.

    Returns the hit the caller must act on: ``reject`` must not dispatch,
    ``remind`` dispatches and the agent loop injects the message. ``None`` means
    no rule matched, or the rule is out of budget.
    """
    config = ctx.config.reminders
    if not config.enabled:
        return None
    rules = load_rules(ctx.config)
    if not rules:
        return None

    matched = [rule for rule in rules if _rule_matches(rule, tool_name, arguments)]
    if not matched:
        return None
    # A reject outranks a remind in the same batch: the safety action wins.
    reject = next((rule for rule in matched if rule.action == "reject"), None)
    rule = reject or matched[0]

    state = ctx.reminder_state
    count = _seed_count(ctx, rule.id)
    if rule.action == "remind":
        # Advisory reminders expire: repeated copies are context tax.
        if count >= config.max_matches_per_rule:
            return None
        if state.injections_this_turn >= config.max_per_turn:
            return None

    state.match_counts[rule.id] = count + 1
    if rule.action == "remind":
        state.injections_this_turn += 1
    terse = rule.action == "reject" and count + 1 > config.max_matches_per_rule
    await _record(ctx, rule, tool_name, terse)
    return ReminderHit(
        rule_id=rule.id,
        tool=tool_name,
        action=rule.action,
        message=rule.message,
        terse=terse,
    )


def reject_text(hit: ReminderHit) -> str:
    """Model-facing text for a rejected call. Terse once the message budget is spent."""
    if hit.terse:
        return f"[error: scoped reminder '{hit.rule_id}' still blocks this call]"
    return f"[error: {hit.message}]"


def reminder_message(hit: ReminderHit) -> dict[str, str]:
    """The ephemeral user-role message the agent loop injects for a remind hit.

    Separate from the tool result on purpose: a reminder inside trusted tool
    output would be indistinguishable from untrusted tool text.
    """
    return {
        "role": "user",
        "content": f"[scoped-reminder] {hit.message}{_DISCLAIMER} (rule: {hit.rule_id})",
    }
