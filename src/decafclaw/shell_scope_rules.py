"""Global, scope-keyed shell approval rulebook (#1641 phase 4).

Two files with different trust levels:

- ``shell_scope_rules.jsonl`` — **policy**. Durable rules keyed by
  ``(scope, command shape)``. The runtime reads it; it never writes it. All
  writes go through the ``admin_*`` tool family, which is rooted at
  ``agent_path``, requires interactive confirmation with a diff preview, and is
  refused on unattended turns and child agents. That is the self-granting hole
  this design exists to avoid: a file that steers the shell reviewer must not be
  writable by the agent it reviews.
- ``shell_scope_ledger.jsonl`` — **evidence**. Append-only human decisions,
  recorded by the runtime. It steers nothing by itself. The promotion pass
  reads it and proposes rules; a human still confirms the ``admin_write``.

Both live under ``config.agent_path`` (``data/{agent_id}/``), next to
``shell_allow_patterns.json`` and ``approval_state/`` — never under the
agent-writable workspace or the vault.

Rule key: the activity scope (``activity.derive_domains``: repo/project label)
composed with the command shape (``shell_tools._suggest_pattern``: executable +
subcommand/script, args wildcarded). That is the existing vocabulary the
per-command suggestions already use; this module does not invent a second one.
"""

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .activity import UNKNOWN_ACTIVITY, Activity

log = logging.getLogger(__name__)

SCOPE_RULES_FILENAME = "shell_scope_rules.jsonl"
SCOPE_LEDGER_FILENAME = "shell_scope_ledger.jsonl"

# Review surface: past this many rules, condensation is recommended (#1267).
CONDENSE_RECOMMENDED_AFTER = 30

_warned_condense_paths: set[str] = set()


def scope_rules_path(config) -> Path:
    return config.agent_path / SCOPE_RULES_FILENAME


def scope_ledger_path(config) -> Path:
    return config.agent_path / SCOPE_LEDGER_FILENAME


@dataclass(frozen=True)
class ScopeRule:
    """One rule in the global rulebook."""

    scope: str
    pattern: str
    rule: str
    approvals: int = 0
    denials: int = 0
    updated: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.scope, self.pattern)


@dataclass
class ScopeRuleFile:
    """Parsed rulebook plus the malformed lines it skipped."""

    rules: list[ScopeRule] = field(default_factory=list)
    skipped: int = 0


def rule_shape(command: str) -> str:
    """The command-shape key, using the existing suggestion vocabulary."""
    # Local import: shell_tools imports this module's read/record helpers.
    from .tools.shell_tools import _suggest_pattern

    return _suggest_pattern(command).strip()


def scope_for_activity(activity: Activity | None) -> str:
    """The promotion scope: the command's primary domain, never ``unknown``."""
    if activity is None or activity.domain == UNKNOWN_ACTIVITY:
        return ""
    return activity.domain


def record_human_decision(
    config,
    *,
    conv_id: str,
    command: str,
    cwd: str | None,
    decision: str,
    activity: Activity | None,
) -> bool:
    """Append one human verdict to the evidence ledger.

    Only explicit human decisions are evidence for promotion. Judge/read-only
    allowances are telemetry, not consent. Fail-open: a ledger write failure
    must never change the command's outcome.
    """
    try:
        scope = scope_for_activity(activity)
        shape = rule_shape(command)
        if not scope or not shape:
            return False
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "conv_id": conv_id,
            "scope": scope,
            "pattern": shape,
            "command": " ".join(str(command or "").split())[:500],
            "cwd": " ".join(str(cwd or "").split())[:500],
            "decision": decision,
            "approved": decision == "allow",
        }
        path = scope_ledger_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001 - evidence must never break a decision
        log.warning("shell scope ledger: could not record decision: %s", exc)
        return False


def _read_jsonl(path: Path) -> tuple[list[dict], int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    records: list[dict] = []
    skipped = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            skipped += 1
    return records, skipped


def load_scope_rules(config) -> ScopeRuleFile:
    """Parse the rulebook. Fail-open: missing is empty, malformed is skipped."""
    path = scope_rules_path(config)
    records, skipped = _read_jsonl(path)
    rules: list[ScopeRule] = []
    for record in records:
        scope = str(record.get("scope") or "").strip()
        pattern = str(record.get("pattern") or "").strip()
        text = str(record.get("rule") or "").strip()
        if not scope or not pattern or not text:
            skipped += 1
            continue
        rules.append(
            ScopeRule(
                scope=scope,
                pattern=pattern,
                rule=text,
                approvals=_coerce_int(record.get("approvals")),
                denials=_coerce_int(record.get("denials")),
                updated=str(record.get("updated") or ""),
            )
        )
    if skipped:
        log.warning("shell scope rules: skipped %d malformed line(s) in %s", skipped, path)
    if len(rules) > CONDENSE_RECOMMENDED_AFTER and str(path) not in _warned_condense_paths:
        # Automatic trigger for the review surface. The LLM condensation itself
        # stays human/agent-initiated: an unbounded model call inside the
        # approval path would add latency and cost to every command.
        _warned_condense_paths.add(str(path))
        log.warning(
            "shell scope rules: %d rules exceeds %d; run shell_guidance(action='condense_scope_rules')",
            len(rules),
            CONDENSE_RECOMMENDED_AFTER,
        )
    return ScopeRuleFile(rules=rules, skipped=skipped)


def _coerce_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def render_scope_rules(rules: list[ScopeRule]) -> str:
    """Serialize the rulebook as JSONL, one rule per line, stable order."""
    lines = [
        json.dumps(asdict(rule), sort_keys=True) for rule in sorted(rules, key=lambda item: (item.scope, item.pattern))
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def rules_enabled(config) -> bool:
    return getattr(config.shell, "scope_rules_enabled", True) is not False


def rule_thresholds(config) -> tuple[int, int]:
    minimum = getattr(config.shell, "scope_rule_min_approvals", 2)
    maximum = getattr(config.shell, "scope_rule_max_denials", 0)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        minimum = 2
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        maximum = 0
    return minimum, maximum


def matching_rules(config, command: str, activity: Activity | None) -> list[str]:
    """Rule texts that apply to this command's scope and shape.

    A rule applies only when its scope is one the command actually touches and
    its command shape matches. Rules for other repos never leak into the judge
    prompt.
    """
    if not rules_enabled(config) or not command or activity is None:
        return []
    scopes = set(activity.domains) if activity.domains else {activity.domain}
    scopes.discard(UNKNOWN_ACTIVITY)
    if not scopes:
        return []
    # Local import: shell_tools imports this module's read/record helpers.
    from .tools.shell_tools import _command_matches_pattern

    matched: list[str] = []
    for rule in load_scope_rules(config).rules:
        if rule.scope not in scopes:
            continue
        if not _command_matches_pattern(command, [rule.pattern]):
            continue
        if rule.rule not in matched:
            matched.append(rule.rule)
    return matched


def ledger_counts(config) -> dict[tuple[str, str], tuple[int, int]]:
    """``(scope, pattern) -> (approvals, denials)`` from human decisions."""
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    records, _ = _read_jsonl(scope_ledger_path(config))
    for record in records:
        scope = str(record.get("scope") or "").strip()
        pattern = str(record.get("pattern") or "").strip()
        if not scope or not pattern or scope == UNKNOWN_ACTIVITY:
            continue
        approved = record.get("approved")
        if approved is True:
            counts[(scope, pattern)][0] += 1
        elif approved is False:
            counts[(scope, pattern)][1] += 1
    return {key: (values[0], values[1]) for key, values in counts.items()}


def promotion_candidates(config) -> list[ScopeRule]:
    """Shapes that earned a rule: N approvals, no more than M denials."""
    minimum, maximum = rule_thresholds(config)
    existing = {rule.key for rule in load_scope_rules(config).rules}
    candidates: list[ScopeRule] = []
    now = datetime.now(timezone.utc).isoformat()
    for (scope, pattern), (approvals, denials) in sorted(ledger_counts(config).items()):
        if (scope, pattern) in existing:
            continue
        if approvals < minimum or denials > maximum:
            continue
        candidates.append(
            ScopeRule(
                scope=scope,
                pattern=pattern,
                rule=(
                    f"In scope `{scope}`, `{pattern}` commands are typically approved: "
                    f"{approvals} human approvals, {denials} denials. Still decline a "
                    "concrete risk category."
                ),
                approvals=approvals,
                denials=denials,
                updated=now,
            )
        )
    return candidates


def promotion_proposal(config) -> tuple[list[ScopeRule], str]:
    """Candidates plus the full rulebook content an ``admin_write`` would apply."""
    existing = list(load_scope_rules(config).rules)
    candidates = promotion_candidates(config)
    return candidates, render_scope_rules(existing + candidates)


def format_rules_summary(config) -> str:
    """Review surface for ``shell_guidance list``."""
    parsed = load_scope_rules(config)
    rules = parsed.rules
    if not rules_enabled(config):
        return "### Global Scope Rules\n\n*(disabled by `shell.scope_rules_enabled`)*"
    lines = [f"### Global Scope Rules ({len(rules)})\n"]
    if not rules:
        lines.append("*(none — approve commands in a scope to accumulate evidence)*")
    for rule in sorted(rules, key=lambda item: (item.scope, item.pattern)):
        lines.append(f"- `{rule.scope}` · `{rule.pattern}`: {rule.rule}")
    if parsed.skipped:
        lines.append(f"\n*({parsed.skipped} malformed line(s) skipped)*")
    if len(rules) > CONDENSE_RECOMMENDED_AFTER:
        lines.append(
            f"\nCondensation recommended: {len(rules)} rules exceeds "
            f'{CONDENSE_RECOMMENDED_AFTER}. Use `shell_guidance(action="condense_scope_rules")`.'
        )
    candidates = promotion_candidates(config)
    if candidates:
        lines.append(
            f"\n{len(candidates)} promotion candidate(s) from the decision ledger. "
            'Use `shell_guidance(action="promote_scope_rules")` to draft them.'
        )
    return "\n".join(lines)


def build_scope_condense_prompt(rules: list[ScopeRule]) -> str:
    """Prompt asking the condense model to merge scope-keyed rules without losing keys."""
    numbered = "\n".join(
        f"{index}. scope={rule.scope!r} pattern={rule.pattern!r} rule={rule.rule!r}"
        for index, rule in enumerate(rules, 1)
    )
    return (
        "You are condensing shell auto-approval rules. Each rule below is keyed by a "
        "scope (a repo/project activity) and a command shape, and its text is guidance "
        "for an LLM that decides whether to auto-approve a shell command. Rewrite them "
        "into fewer rules that preserve the same intent and coverage: merge rules with "
        "the same scope and compatible shapes, generalize specific commands into their "
        "category where safe, and drop rules fully subsumed by a more general one. "
        "Every output rule MUST have a non-empty scope, pattern, and rule. Do not invent "
        "new permissions. Return ONLY JSON of the form "
        '{"rules": [{"scope": "...", "pattern": "...", "rule": "..."}]} with no prose '
        f"outside the JSON.\n\nCurrent rules:\n{numbered}"
    )
