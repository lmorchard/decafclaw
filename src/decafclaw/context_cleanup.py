"""Tool-result clearing — a lightweight pre-compaction tier.

Walks the agent's in-memory history once per agent loop iteration and
replaces the body of old, large tool messages with a short stub so
the model doesn't keep paying attention budget on raw tool output it
has already synthesized. The original body remains durably written to
the conversation's JSONL archive — this is purely an in-memory edit.

A second, more aggressive pass stubs tool *errors* that a later success
of the same tool has already resolved (#540), so a recovered failure no
longer earns attention budget either. Same in-memory-only invariant: the
archive keeps the original.

See ``docs/context-composer.md`` and #298 for design rationale.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .context_composer import ComposerMode

log = logging.getLogger(__name__)

_STUB_PREFIX = "[tool output cleared:"
_SUPERSEDED_PREFIX = "[tool error superseded"
_SUPERSEDED_STUB = "[tool error superseded by later success]"
_SUPERSEDED_READ_STUB = "[tool output superseded by a later successful read]"

# Tools whose successful result is a file read. A later successful full read
# of the same path supersedes an older read of it (#1146) — the older copy can
# conflict with current content once the file changed.
_FILE_READ_TOOLS = frozenset({"workspace_read", "admin_read"})

# Non-interactive turn kinds skip resolved-error pruning, mirroring the mode
# gate memory retrieval uses (see ComposerMode and
# context_composer.py:``skip_modes``). The size-based clear below still runs
# in every mode — only this pass is mode-gated.
_PRUNE_SKIP_MODES = frozenset({ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT})


@dataclass
class ClearStats:
    """Counts of what got cleared. Per-call deltas; the agent loop
    accumulates these onto ``ctx.composer.cleanup_cleared_count`` and
    ``ctx.composer.cleanup_cleared_bytes``."""

    cleared_count: int = 0
    cleared_bytes: int = 0

    def merge(self, other: ClearStats) -> None:
        self.cleared_count += other.cleared_count
        self.cleared_bytes += other.cleared_bytes


def _is_already_cleared(content) -> bool:
    """Return True if this content already starts with the stub
    prefix (idempotent re-runs)."""
    return isinstance(content, str) and content.startswith(_STUB_PREFIX)


def _is_error_result(content) -> bool:
    """Return True if this tool result encodes a failure.

    Tools report errors as ``ToolResult(text="[error: ...]")``, which lands
    in the in-memory history as a tool-role message whose content starts
    with ``[error`` (see ``tool_execution.py`` and
    ``agent.py:_extract_call_signatures``). Leading whitespace is tolerated.
    """
    return isinstance(content, str) and content.lstrip().startswith("[error")


def _user_turn_boundary_indices(history: list[dict]) -> list[int]:
    """Indices in ``history`` where ``role == "user"`` — these mark
    the start of each user turn."""
    return [i for i, m in enumerate(history) if m.get("role") == "user"]


def _build_tool_name_index(history: list[dict]) -> dict[str, str]:
    """Build a one-pass ``{tool_call_id: tool_name}`` lookup.

    Walks assistant messages once and collects every named tool call.
    Pre-computing this at the start of a clearing pass avoids the
    O(n²) scan that a per-message lookup would do on long histories.

    Tool names that can't be resolved (e.g. the originating assistant
    message was compacted away into a summary) simply won't appear in
    the returned map. Callers should treat a missing entry as "name
    unknown — preserve_tools can't be honored, eligibility falls back
    to old-and-large-is-sufficient."
    """
    by_call_id: dict[str, str] = {}
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            if not isinstance(tc, dict):
                continue
            call_id = tc.get("id")
            if not call_id:
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if name:
                by_call_id[call_id] = name
    return by_call_id


def _build_tool_args_index(history: list[dict]) -> dict[str, object]:
    """Build a one-pass ``{tool_call_id: arguments}`` lookup used only for
    same-args matching (``prune_resolved_same_args_only``).

    JSON argument strings are parsed so key order and whitespace don't
    affect equality (``{"a":1,"b":2}`` == ``{"b":2,"a":1}``). Unparseable
    arguments fall back to their raw string, degrading the comparison to
    exact-string equality rather than guessing.
    """
    by_call_id: dict[str, object] = {}
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            if not isinstance(tc, dict):
                continue
            call_id = tc.get("id")
            if not call_id:
                continue
            fn = tc.get("function") or {}
            raw = fn.get("arguments", tc.get("arguments", ""))
            if isinstance(raw, str):
                try:
                    by_call_id[call_id] = json.loads(raw)
                except (TypeError, ValueError):
                    by_call_id[call_id] = raw
            else:
                by_call_id[call_id] = raw
    return by_call_id


def _prune_resolved_errors(history: list[dict], cfg, tool_names_by_call_id, stats) -> None:
    """Stub tool *errors* that a later success of the same tool resolved.

    More aggressive than the size-based clear: an error is eligible
    regardless of size or age once a later same-tool result is not itself an
    error. When ``cfg.prune_resolved_same_args_only`` is set, the later
    success must also carry the same arguments as the failed call (the
    conservative matching mode). Mutates ``history`` in place and folds the
    reclaimed bytes into ``stats``.
    """
    n = len(history)
    tool_args_by_call_id = _build_tool_args_index(history) if cfg.prune_resolved_same_args_only else None
    stub_bytes = len(_SUPERSEDED_STUB.encode("utf-8"))

    for i, msg in enumerate(history):
        if msg.get("role") != "tool":
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str) or not _is_error_result(content):
            continue
        if content.startswith(_SUPERSEDED_PREFIX):
            # Idempotent re-run guard — already pruned this pass or a prior one.
            continue
        call_id = msg.get("tool_call_id", "")
        name = tool_names_by_call_id.get(call_id)
        if not name:
            # Originating assistant message compacted away — can't match a tool.
            continue
        resolved = False
        for j in range(i + 1, n):
            later = history[j]
            if later.get("role") != "tool":
                continue
            later_content = later.get("content") or ""
            if not isinstance(later_content, str) or _is_error_result(later_content):
                continue
            if tool_names_by_call_id.get(later.get("tool_call_id", "")) != name:
                continue
            if tool_args_by_call_id is not None and tool_args_by_call_id.get(call_id) != tool_args_by_call_id.get(
                later.get("tool_call_id", "")
            ):
                continue
            resolved = True
            break
        if not resolved:
            continue
        original_bytes = len(content.encode("utf-8"))
        if original_bytes <= stub_bytes:
            # A recovered error this small isn't worth the edit — stubbing
            # it would make the in-memory view *larger*. Mirrors the
            # size/age clear's pathological-case skip below.
            continue
        msg["content"] = _SUPERSEDED_STUB
        stats.cleared_count += 1
        stats.cleared_bytes += original_bytes - stub_bytes
        log.debug("Pruned resolved error at history[%d] (tool=%s, %d bytes)", i, name, original_bytes)


def _is_full_read_content(content) -> bool:
    """True when ``content`` is a complete (untruncated, un-ranged,
    un-sectioned) file-read body, not a range/section/truncation envelope or
    an error stub. Only full reads may supersede an older read (#1146)."""
    if not isinstance(content, str):
        return False
    text = content.strip()
    if not text or text.startswith("[error") or text.startswith(_STUB_PREFIX) or text.startswith(_SUPERSEDED_STUB):
        return False
    if text.startswith("Lines ") or text.startswith("Section "):
        # Range / section reads are partial coverage by construction.
        return False
    if "showing first" in text:
        # Truncated full read — not a full-file replacement.
        return False
    return True


def _read_path_from_args(args_by_call_id: dict, call_id: str) -> str | None:
    """Best-effort canonical path from a file-read tool's arguments."""
    args = args_by_call_id.get(call_id)
    if not isinstance(args, dict):
        return None
    path = args.get("path")
    if isinstance(path, str) and path:
        return path
    return None


def _prune_superseded_reads(
    history: list[dict],
    cfg,
    tool_names_by_call_id: dict[str, str],
    tool_args_by_call_id: dict,
    stats,
) -> None:
    """Stub successful full-file reads superseded by a later successful full
    read of the same path (#1146).

    Conservative supersession: only a later same-tool result that is itself a
    complete read (no truncation / range / section envelope) and names the same
    path supersedes. Mutates ``history`` in place and folds the reclaimed bytes
    into ``stats``. Runs before the size/age cutoff region.
    """
    n = len(history)
    for i, msg in enumerate(history):
        if msg.get("role") != "tool":
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str) or _is_error_result(content):
            continue
        if (
            content.startswith(_STUB_PREFIX)
            or content.startswith(_SUPERSEDED_STUB)
            or content.startswith(_SUPERSEDED_READ_STUB)
        ):
            # Idempotent re-run guard — already pruned this pass or a prior one.
            continue
        call_id = msg.get("tool_call_id", "")
        name = tool_names_by_call_id.get(call_id)
        if name not in _FILE_READ_TOOLS:
            continue
        path = _read_path_from_args(tool_args_by_call_id, call_id)
        if not path:
            continue
        superseded = False
        for j in range(i + 1, n):
            later = history[j]
            if later.get("role") != "tool":
                continue
            later_content = later.get("content") or ""
            if not isinstance(later_content, str) or _is_error_result(later_content):
                continue
            if (
                later_content.startswith(_STUB_PREFIX)
                or later_content.startswith(_SUPERSEDED_STUB)
                or later_content.startswith(_SUPERSEDED_READ_STUB)
            ):
                continue
            if tool_names_by_call_id.get(later.get("tool_call_id", "")) != name:
                continue
            later_path = _read_path_from_args(tool_args_by_call_id, later.get("tool_call_id", ""))
            if later_path != path:
                continue
            if not _is_full_read_content(later_content):
                continue
            superseded = True
            break
        if not superseded:
            continue
        original_bytes = len(content.encode("utf-8"))
        superseded_stub_bytes = len(_SUPERSEDED_READ_STUB.encode("utf-8"))
        if original_bytes <= superseded_stub_bytes:
            # Stub would not be smaller than the original — nothing to reclaim.
            continue
        msg["content"] = _SUPERSEDED_READ_STUB
        stats.cleared_count += 1
        stats.cleared_bytes += original_bytes - superseded_stub_bytes
        log.debug("Pruned superseded read at history[%d] (tool=%s, path=%s, %d bytes)", i, name, path, original_bytes)


def clear_old_tool_results(history: list[dict], config, mode: ComposerMode = ComposerMode.INTERACTIVE) -> ClearStats:
    """Mutate ``history`` in place in three passes.

    Pass 1 — resolved-error pruning (#540): a tool error that a later
    success of the same tool has resolved is stubbed, more aggressively than
    the size/age clear (any size, any age). Skipped when
    ``cleanup.prune_resolved_errors`` is off or in a non-interactive
    ``mode`` (heartbeat / scheduled / child).

    Pass 2 — size/age clear. For each tool message that's old, large, and
    not allowlisted, replace ``content`` with a short stub. Eligibility (all
    must hold):
      1. ``role == "tool"``
      2. ``content`` is not already a stub (idempotent re-run guard)
      3. originating tool name is not in ``cleanup.preserve_tools``
          (None — name-not-resolvable — is treated as eligible)
      4. message is older than ``cleanup.min_turn_age`` user-turn
          boundaries
      5. ``len(content.encode("utf-8")) >= cleanup.min_size_bytes``
      6. the stub itself would actually be smaller than the original
          (skips the pathological case where ``min_size_bytes`` is set
          below the stub length)

    Pass 3 — superseded file reads (#1146): an older successful full-file read
    that a later successful full read of the same path superseded is stubbed
    with a reference to the newer observation. Skipped when
    ``cleanup.prune_superseded_reads`` is off or in a non-interactive
    ``mode`` (heartbeat / scheduled / child).

    Returns a ``ClearStats`` with the count + bytes of *this call's*
    deletions (all passes, not cumulative).
    """
    stats = ClearStats()
    cfg = config.cleanup
    if not cfg.enabled:
        return stats

    tool_names_by_call_id = _build_tool_name_index(history)

    # Pass 1 — resolved-error pruning. Runs before the size/age guards below
    # because it's independent of them: a resolved error is worth stubbing
    # even when it's small (under ``min_size_bytes``) or recent (inside the
    # protected window). Skipped in non-interactive modes, like memory
    # retrieval; the size/age clear is unaffected by ``mode``.
    if cfg.prune_resolved_errors and mode not in _PRUNE_SKIP_MODES:
        _prune_resolved_errors(history, cfg, tool_names_by_call_id, stats)

    # Defensive guard: a misconfigured ``min_turn_age`` <= 0 would index
    # ``boundaries[-0] == boundaries[0]`` and silently clear everything
    # past the first user message (or raise on a negative value), so
    # treat it as a safe no-op. Same for non-positive ``min_size_bytes``
    # — it can't legitimately mean "clear everything."
    if cfg.min_turn_age <= 0 or cfg.min_size_bytes <= 0:
        return stats

    boundaries = _user_turn_boundary_indices(history)
    if len(boundaries) < cfg.min_turn_age:
        # Not enough user turns yet — nothing is "old".
        return stats

    # The cutoff is the index of the user message that begins the
    # protected window. Any tool message at index >= cutoff is in a
    # protected turn; messages strictly before cutoff are eligible.
    cutoff = boundaries[-cfg.min_turn_age]
    preserve = set(cfg.preserve_tools)

    for i, msg in enumerate(history):
        if i >= cutoff:
            break
        if msg.get("role") != "tool":
            continue
        content = msg.get("content") or ""
        if _is_already_cleared(content):
            continue
        if not isinstance(content, str):
            # Provider-specific shapes (e.g. parts arrays) — skip
            # rather than guess at a safe edit.
            continue
        content_bytes = len(content.encode("utf-8"))
        if content_bytes < cfg.min_size_bytes:
            continue
        tool_name = tool_names_by_call_id.get(msg.get("tool_call_id", ""))
        if tool_name is not None and tool_name in preserve:
            continue
        stub = f"{_STUB_PREFIX} {content_bytes} bytes]"
        reclaimed = content_bytes - len(stub.encode("utf-8"))
        if reclaimed <= 0:
            # Stub is no smaller than the original — nothing to reclaim.
            continue
        msg["content"] = stub
        stats.cleared_count += 1
        stats.cleared_bytes += reclaimed
        log.debug(
            "Cleared tool message at history[%d] (tool=%s, %d bytes)",
            i,
            tool_name or "?",
            content_bytes,
        )

    # Pass 3 — superseded file reads (#1146): an older successful full-file
    # read that a later successful full read of the same path superseded gets
    # stubbed with a reference to the newer observation. Independent of the
    # size/age gate — stale-by-supersession reads are worth retiring even when
    # small. Same mode gate as the error pass.
    if cfg.prune_superseded_reads and mode not in _PRUNE_SKIP_MODES:
        _prune_superseded_reads(history, cfg, tool_names_by_call_id, _build_tool_args_index(history), stats)

    return stats
