"""Three Strikes procedural-toil audit (#1531).

The improvement scout records *negative* friction: tool errors, denied
permissions, and user corrections. It is blind to **invisible procedural
toil** — successful turns where the agent burns tokens rediscovering command
sequences or building defensive shell pipelines that a deterministic primitive
should replace.

This module is an on-demand, batched scanner (never an ambient per-turn
observer). It reads conversation archives, normalizes variable arguments
(paths, SHAs, URLs, ids, numbers), mines recurring contiguous tool sequences
of 2–5 calls across distinct conversations, prunes sub-sequences dominated by
a longer sequence with the same reach, and classifies each survivor as one of
three toil signatures:

* ``defensive_shell`` — defensive redirects/pipes (``> /tmp/x 2>&1``,
  ``| tail``, ``| grep``, ``|| echo``).
* ``read_after_write`` — a write tool immediately followed by a read of the
  same workspace path or vault page.
* ``discovery_ceremony`` — two or more directory traversals or dependency
  checks in one sequence.

Candidates that reach the strike threshold (three distinct conversations by
default) can be appended to ``workspace/improvement_inbox.jsonl`` as
``toil_strike_3`` records, reusing the #1482 inbox contract instead of
inventing a second store.

Usage::

    decafclaw toil audit [--days 7] [--min-strikes 3] [--json] [--export]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .conversation_paths import iter_conversation_archives
from .improvement_inbox import append_item, new_item, read_items

log = logging.getLogger(__name__)

DEFAULT_DAYS = 7
DEFAULT_MIN_STRIKES = 3
MIN_SEQUENCE_LEN = 2
MAX_SEQUENCE_LEN = 5
TOIL_TRIGGER = "toil_strike_3"

TOIL_KINDS = ("defensive_shell", "read_after_write", "discovery_ceremony")

_REMEDY_TEMPLATES = {
    "defensive_shell": (
        "A",
        "Missing wrapper",
        "Add an atomic tool or CLI command for this sequence so the agent does not "
        "assemble a defensive shell pipeline (bounded output, single exit status) "
        "each time.",
    ),
    "discovery_ceremony": (
        "B",
        "Incomplete primitive",
        "Complete the setup or discovery contract of the existing primitive so the "
        "agent does not repeat directory traversal or dependency checks before work.",
    ),
    "read_after_write": (
        "C",
        "Untrusted write",
        "Return verification metadata (path, version, size, or hash) from the write "
        "tool so the agent does not read the record back to confirm it.",
    ),
}

# --- argument normalization -------------------------------------------------

_URL_RE = re.compile(r"\b(?:https?|ftp)://[^\s'\"<>]+")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_SHA_RE = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
_TMP_PATH_RE = re.compile(r"(?:/tmp|/var/tmp|/private/tmp|/private/var/folders)[^\s'\"]*")
_PATH_TOKEN_RE = re.compile(r"(?<![\w])(?:~|\.{1,2})?/[A-Za-z0-9_.@%+~-]+(?:/[A-Za-z0-9_.@%+-]+)*")
_REL_PATH_RE = re.compile(r"(?<![\w./-])[A-Za-z0-9_.@%+-]+(?:/[A-Za-z0-9_.@%+-]+)+")
_ISSUE_REF_RE = re.compile(r"(?<![\w#])#\d+\b")
_NUMBER_RE = re.compile(r"(?<![>&<])\b\d+(?:\.\d+)?\b(?!>)")
_WHITESPACE_RE = re.compile(r"\s+")

# Free-form payload keys never contribute to a sequence signature. They vary
# on every call, so keeping them would prevent any cross-session recurrence.
_VOLATILE_ARG_KEYS = frozenset(
    {
        "body",
        "code",
        "content",
        "description",
        "instructions",
        "message",
        "new_string",
        "new_text",
        "old_string",
        "old_text",
        "prompt",
        "purpose",
        "replacement",
        "snippet",
        "summary",
        "text",
        "title",
        "value",
    }
)

_PATH_ARG_KEYS = ("path", "page", "file", "folder", "target", "source", "dest", "destination")
_TEXT_ARG_KEYS = frozenset({"query", "pattern", "glob", "q"})


def normalize_text(value: str) -> str:
    """Replace variable data in a string with stable placeholders."""
    text = _URL_RE.sub("<url>", value.strip())
    text = _UUID_RE.sub("<id>", text)
    text = _TMP_PATH_RE.sub("<tmp>", text)
    text = _PATH_TOKEN_RE.sub("<path>", text)
    text = _REL_PATH_RE.sub("<path>", text)
    text = _SHA_RE.sub("<sha>", text)
    text = _ISSUE_REF_RE.sub("<ref>", text)
    text = _NUMBER_RE.sub("<n>", text)
    return _WHITESPACE_RE.sub(" ", text)


def normalize_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool arguments, dropping volatile payload keys."""
    return {
        key: _normalize_value(value, key=key) for key, value in sorted(args.items()) if key not in _VOLATILE_ARG_KEYS
    }


def _normalize_value(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            nested_key: _normalize_value(nested_value, key=nested_key)
            for nested_key, nested_value in sorted(value.items())
            if nested_key not in _VOLATILE_ARG_KEYS
        }
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return "<n>"
    if isinstance(value, str):
        if key in _PATH_ARG_KEYS and value.strip():
            return "<path>"
        if key in _TEXT_ARG_KEYS and value.strip():
            return "<text>"
        return normalize_text(value)
    return value


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": normalize_text(raw)}
        if isinstance(parsed, dict):
            return parsed
        return {"_value": parsed}
    return {}


def _signature(name: str, args: dict[str, Any]) -> str:
    if name == "shell" and isinstance(args.get("command"), str):
        stable: dict[str, Any] = {"command": normalize_text(args["command"])}
        cwd = args.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            stable["cwd"] = "<path>"
    else:
        stable = normalize_arguments(args)
    if not stable:
        return name
    rendered = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{name} {rendered}"


def _path_key(args: dict[str, Any]) -> str | None:
    for key in _PATH_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().strip("/").replace("\\", "/").lower()
    return None


# --- toil signatures --------------------------------------------------------

_DEFENSIVE_PATTERNS = (
    re.compile(r"2>&1"),
    re.compile(r"(?:^|\s)>>?\s*\S*/tmp"),
    re.compile(r"\|\s*(?:tail|head|grep|rg)\b"),
    re.compile(r"\|\|\s*(?:echo|true|cat)\b"),
    re.compile(r">\s*/dev/null"),
    re.compile(r"2>\s*/dev/null"),
    re.compile(r"\becho\s+\$\?"),
)

_DISCOVERY_SHELL_RE = re.compile(
    r"^\s*(?:"
    r"ls|find|tree|pwd|which|whereis|du|df|rg|grep|cat|head|tail|env|printenv|"
    r"git\s+(?:status|log|branch|remote|rev-parse)|"
    r"uv\s+sync|uv\s+run\s+sync|pip3?\s+install|npm\s+(?:install|ci)|pnpm\s+install|"
    r"make\s+install|python3?\s+--version|node\s+--version|uv\s+--version|command\s+-v"
    r")\b"
)

_WRITE_TOOLS = frozenset(
    {
        "vault_delete",
        "vault_journal_append",
        "vault_edit",
        "vault_append",
        "vault_replace_lines",
        "vault_rename",
        "vault_update_frontmatter",
        "vault_write",
        "workspace_append",
        "workspace_copy",
        "workspace_delete",
        "workspace_edit",
        "workspace_insert",
        "workspace_mkdir",
        "workspace_move",
        "workspace_replace_lines",
        "workspace_write",
    }
)

_READ_TOOLS = frozenset(
    {
        "vault_backlinks",
        "vault_list",
        "vault_read",
        "vault_recent",
        "vault_search",
        "vault_tags",
        "workspace_diff",
        "workspace_glob",
        "workspace_list",
        "workspace_outline",
        "workspace_preview_markdown",
        "workspace_read",
        "workspace_search",
    }
)

_DISCOVERY_TOOLS = frozenset(
    {
        "vault_backlinks",
        "vault_list",
        "vault_recent",
        "vault_search",
        "vault_tags",
        "workspace_glob",
        "workspace_list",
        "workspace_outline",
        "workspace_search",
    }
)


def is_defensive_shell(command: str) -> bool:
    """True when a raw shell command carries a defensive toil pattern."""
    return any(pattern.search(command) for pattern in _DEFENSIVE_PATTERNS)


def is_discovery_shell(command: str) -> bool:
    """True when a shell command is traversal or a dependency check."""
    for part in re.split(r"&&|\|\||[;|]", command):
        if _DISCOVERY_SHELL_RE.match(part):
            return True
    return False


@dataclass(frozen=True)
class ToolCall:
    """One archived tool invocation with its stable and literal forms."""

    name: str
    signature: str
    path_key: str | None = None
    raw_command: str | None = None
    discovery: bool = False


def extract_tool_calls(messages: Iterable[dict[str, Any]]) -> list[ToolCall]:
    """Extract ordered tool calls from archive messages."""
    calls: list[ToolCall] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for raw_call in message.get("tool_calls") or []:
            if not isinstance(raw_call, dict):
                continue
            function_value = raw_call.get("function")
            function: dict[str, Any] = function_value if isinstance(function_value, dict) else {}
            name = function.get("name") or raw_call.get("name")
            if not isinstance(name, str) or not name:
                continue
            args = _parse_arguments(function.get("arguments", raw_call.get("arguments")))
            raw_command = args.get("command") if name == "shell" else None
            if not isinstance(raw_command, str):
                raw_command = None
            calls.append(
                ToolCall(
                    name=name,
                    signature=_signature(name, args),
                    path_key=_path_key(args),
                    raw_command=raw_command,
                    discovery=name in _DISCOVERY_TOOLS or bool(raw_command and is_discovery_shell(raw_command)),
                )
            )
    return calls


# --- mining and classification ---------------------------------------------


@dataclass(frozen=True)
class ToilCandidate:
    """A recurring normalized tool sequence that exhibits a toil signature."""

    sequence: tuple[str, ...]
    conversation_ids: tuple[str, ...]
    kind: str
    subsystem: str
    severity: str
    sample: tuple[ToolCall, ...]

    @property
    def strikes(self) -> int:
        return len(self.conversation_ids)

    @property
    def template_id(self) -> str:
        return _REMEDY_TEMPLATES[self.kind][0]

    @property
    def title(self) -> str:
        summary = " -> ".join(_short(signature) for signature in self.sequence)
        return _truncate(f"Procedural toil: {summary}", 180)

    @property
    def observed_friction(self) -> str:
        lines = [
            f"Repeated sequence ({self.kind}) in {self.strikes} distinct conversations:",
        ]
        lines.extend(f"  {index}. {signature}" for index, signature in enumerate(self.sequence, start=1))
        lines.append(f"Conversations: {', '.join(self.conversation_ids)}")
        return "\n".join(lines)

    @property
    def suggested_improvement(self) -> str:
        template_id, name, text = _REMEDY_TEMPLATES[self.kind]
        return f"Template {template_id} ({name}): {text}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "template": self.template_id,
            "strikes": self.strikes,
            "conversations": list(self.conversation_ids),
            "subsystem": self.subsystem,
            "severity": self.severity,
            "sequence": list(self.sequence),
            "title": self.title,
            "observed_friction": self.observed_friction,
            "suggested_improvement": self.suggested_improvement,
        }


def _short(signature: str, limit: int = 100) -> str:
    return _truncate(signature, limit)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _subsystem(name: str) -> str:
    for prefix in ("workspace", "vault", "tabstack", "mcp"):
        if name == prefix or name.startswith(f"{prefix}_"):
            return prefix
    return "shell" if name == "shell" else "unknown"


def _sequence_subsystem(calls: Sequence[ToolCall]) -> str:
    for call in calls:
        subsystem = _subsystem(call.name)
        if subsystem != "unknown":
            return subsystem
    return "unknown"


def _severity(strikes: int) -> str:
    return "high" if strikes >= 5 else "medium"


def classify_sequence(calls: Sequence[ToolCall]) -> str | None:
    """Classify one observed sequence, or return None when it is not toil."""
    if any(call.raw_command and is_defensive_shell(call.raw_command) for call in calls):
        return "defensive_shell"
    for first, second in zip(calls, calls[1:]):
        if (
            first.name in _WRITE_TOOLS
            and second.name in _READ_TOOLS
            and first.path_key is not None
            and first.path_key == second.path_key
        ):
            return "read_after_write"
    if sum(1 for call in calls if call.discovery) >= 2:
        return "discovery_ceremony"
    return None


@dataclass
class _Bucket:
    sequence: tuple[str, ...]
    kinds: dict[str, str]
    samples: dict[str, tuple[ToolCall, ...]]


def mine_sequences(
    conversations: dict[str, list[ToolCall]],
    *,
    min_strikes: int = DEFAULT_MIN_STRIKES,
    min_len: int = MIN_SEQUENCE_LEN,
    max_len: int = MAX_SEQUENCE_LEN,
) -> list[ToilCandidate]:
    """Mine recurring toil sequences, counting distinct conversations per key."""
    buckets: dict[tuple[str, ...], _Bucket] = {}
    for conv_id, calls in conversations.items():
        for start in range(len(calls)):
            for length in range(min_len, max_len + 1):
                end = start + length
                if end > len(calls):
                    break
                window = tuple(calls[start:end])
                kind = classify_sequence(window)
                if kind is None:
                    continue
                sequence = tuple(call.signature for call in window)
                bucket = buckets.get(sequence)
                if bucket is None:
                    bucket = _Bucket(sequence, {}, {})
                    buckets[sequence] = bucket
                if conv_id not in bucket.kinds:
                    bucket.kinds[conv_id] = kind
                    bucket.samples[conv_id] = window

    candidates: list[ToilCandidate] = []
    for bucket in buckets.values():
        kind = next(name for name in TOIL_KINDS if name in bucket.kinds.values())
        conversation_ids = tuple(sorted(conv_id for conv_id, found in bucket.kinds.items() if found == kind))
        if len(conversation_ids) < min_strikes:
            continue
        sample = bucket.samples[conversation_ids[0]]
        candidates.append(
            ToilCandidate(
                sequence=bucket.sequence,
                conversation_ids=conversation_ids,
                kind=kind,
                subsystem=_sequence_subsystem(sample),
                severity=_severity(len(conversation_ids)),
                sample=sample,
            )
        )
    return prune_dominated(candidates)


def _is_contiguous_subsequence(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    size = len(short)
    if size >= len(long):
        return False
    return any(long[index : index + size] == short for index in range(len(long) - size + 1))


def prune_dominated(candidates: Iterable[ToilCandidate]) -> list[ToilCandidate]:
    """Drop sequences contained in a longer, at-least-as-frequent one of the same kind."""
    ordered = sorted(candidates, key=lambda c: (-len(c.sequence), -c.strikes, c.sequence))
    kept: list[ToilCandidate] = []
    for candidate in ordered:
        dominated = any(
            keeper.kind == candidate.kind
            and keeper.strikes >= candidate.strikes
            and _is_contiguous_subsequence(candidate.sequence, keeper.sequence)
            for keeper in kept
        )
        if not dominated:
            kept.append(candidate)
    return sorted(kept, key=lambda c: (-c.strikes, -len(c.sequence), c.sequence))


# --- archive scan -----------------------------------------------------------


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _read_archive(path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("Skipping corrupt archive line %d in %s", lineno, path)
                    continue
                if isinstance(record, dict):
                    messages.append(record)
    except OSError as exc:
        log.warning("Could not read conversation archive %s: %s", path, exc)
    return messages


def _last_activity(messages: Sequence[dict[str, Any]], path: Path) -> datetime | None:
    for message in reversed(messages):
        parsed = _parse_timestamp(message.get("timestamp"))
        if parsed is not None:
            return parsed
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return None


@dataclass
class ToilReport:
    """Result of one archive scan."""

    scanned: int
    candidates: list[ToilCandidate]
    min_strikes: int
    days: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "min_strikes": self.min_strikes,
            "scanned_conversations": self.scanned,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def scan_conversations(
    config: Any,
    *,
    days: int | None = DEFAULT_DAYS,
    min_strikes: int = DEFAULT_MIN_STRIKES,
    now: datetime | None = None,
) -> tuple[int, dict[str, list[ToolCall]]]:
    """Load tool-call sequences from archives inside the lookback window."""
    cutoff = None
    if days:
        reference = now or datetime.now().astimezone()
        cutoff = reference - timedelta(days=days)
    conversations: dict[str, list[ToolCall]] = {}
    scanned = 0
    for conv_id, path in iter_conversation_archives(config):
        messages = _read_archive(path)
        latest = _last_activity(messages, path)
        if cutoff is not None and latest is not None and latest < cutoff:
            continue
        scanned += 1
        calls = extract_tool_calls(messages)
        if calls:
            conversations[conv_id] = calls
    return scanned, conversations


def audit_toil(
    config: Any,
    *,
    days: int | None = DEFAULT_DAYS,
    min_strikes: int = DEFAULT_MIN_STRIKES,
    now: datetime | None = None,
) -> ToilReport:
    """Scan archives and return pruned toil candidates at the strike threshold."""
    scanned, conversations = scan_conversations(config, days=days, min_strikes=min_strikes, now=now)
    candidates = mine_sequences(conversations, min_strikes=max(1, min_strikes))
    return ToilReport(scanned=scanned, candidates=candidates, min_strikes=min_strikes, days=days)


# --- inbox export -----------------------------------------------------------


def export_candidates(
    config: Any, candidates: Iterable[ToilCandidate]
) -> tuple[list[dict[str, Any]], list[ToilCandidate]]:
    """Append new strike candidates to the improvement inbox, skipping seen ones."""
    existing = read_items(config)
    seen_sequences = {tuple(item["toil_sequence"]) for item in existing if isinstance(item.get("toil_sequence"), list)}
    seen_titles = {item.get("title") for item in existing if item.get("trigger") == TOIL_TRIGGER}

    exported: list[dict[str, Any]] = []
    skipped: list[ToilCandidate] = []
    for candidate in candidates:
        if candidate.sequence in seen_sequences or candidate.title in seen_titles:
            skipped.append(candidate)
            continue
        item = new_item(
            conversation_id=", ".join(candidate.conversation_ids),
            trigger=TOIL_TRIGGER,
            title=candidate.title,
            observed_friction=candidate.observed_friction,
            suggested_improvement=candidate.suggested_improvement,
            subsystem=candidate.subsystem,
            severity=candidate.severity,
        )
        item["toil_sequence"] = list(candidate.sequence)
        item["strike_count"] = candidate.strikes
        append_item(config, item)
        exported.append(item)
        seen_sequences.add(candidate.sequence)
        seen_titles.add(candidate.title)
    return exported, skipped


# --- CLI --------------------------------------------------------------------


def _conversation_list(ids: Sequence[str], limit: int = 10) -> str:
    if len(ids) <= limit:
        return ", ".join(ids)
    return ", ".join(ids[:limit]) + f", … ({len(ids)} total)"


def format_report(report: ToilReport, *, exported: Sequence[dict[str, Any]] = (), skipped: int = 0) -> str:
    window = f"last {report.days} day(s)" if report.days else "all time"
    lines = [
        f"Toil audit ({window}): scanned {report.scanned} conversation(s); "
        f"{len(report.candidates)} candidate(s) at >= {report.min_strikes} strike(s)."
    ]
    if not report.candidates:
        lines.append("No procedural toil reached the strike threshold.")
    for index, candidate in enumerate(report.candidates, start=1):
        lines.append("")
        lines.append(
            f"{index}. [{candidate.severity}] {candidate.kind} — {candidate.strikes} strike(s), "
            f"subsystem {candidate.subsystem}, template {candidate.template_id}"
        )
        lines.append(f"   {candidate.title}")
        lines.extend(f"   - {signature}" for signature in candidate.sequence)
        lines.append(f"   Conversations: {_conversation_list(candidate.conversation_ids)}")
    if exported:
        ids = ", ".join(str(item.get("id", "")) for item in exported)
        lines.append("")
        lines.append(f"Buffered {len(exported)} item(s) as {TOIL_TRIGGER}: {ids}")
    if skipped:
        lines.append(f"Skipped {skipped} candidate(s) already in the improvement inbox.")
    return "\n".join(lines)


def _cmd_audit(args: argparse.Namespace) -> int:
    from .config import load_config

    config = load_config()
    days = None if args.days <= 0 else args.days
    min_strikes = max(1, args.min_strikes)
    report = audit_toil(config, days=days, min_strikes=min_strikes)
    exported: list[dict[str, Any]] = []
    skipped = 0
    if args.export:
        exported, skipped_candidates = export_candidates(config, report.candidates)
        skipped = len(skipped_candidates)

    if args.json:
        payload = report.to_dict()
        if args.export:
            payload["exported"] = [str(item.get("id", "")) for item in exported]
            payload["skipped_existing"] = skipped
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(report, exported=exported, skipped=skipped))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="decafclaw toil", description="Procedural-toil auditing.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Mine conversation archives for repeated toil sequences.")
    audit.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Lookback window in days (default {DEFAULT_DAYS}); 0 scans every archive.",
    )
    audit.add_argument(
        "--min-strikes",
        type=int,
        default=DEFAULT_MIN_STRIKES,
        help=f"Distinct-conversation threshold (default {DEFAULT_MIN_STRIKES}).",
    )
    audit.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    audit.add_argument(
        "--export",
        action="store_true",
        help=f"Append new candidates to the improvement inbox as {TOIL_TRIGGER} items.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "audit":
        return _cmd_audit(args)
    return 2


if __name__ == "__main__":
    main()
