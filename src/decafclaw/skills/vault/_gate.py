"""Vault write-gate machinery — decides whether a vault mutation needs user confirmation.

Extracted from ``tools.py`` (#1367): the gate is a self-contained decision layer
over path predicates, unrelated to page-name resolution or search. It answers
"may this write/delete/rename proceed, and if not, how do we ask?"

The three-tier decision (``_check_user_write_allowed``):
    1. Path is under ``agent/``                          -> ALLOW (the agent's own folder)
    2. Path is in the ``vault.user_writable_paths`` list -> ALLOW (config allowlist)
    3. Path is under a per-conversation folder grant     -> ALLOW (``_grants``)
    4. No interactive confirmation available             -> NON_INTERACTIVE_ERROR
    5. Otherwise                                         -> NEEDS_CONFIRMATION

``_run_gate_or_confirm`` turns a set of outcomes into either an error
``ToolResult`` or ``None`` (proceed), prompting the user once when any path
needs confirmation.

The two path predicates (``_is_in_agent_dir``, ``_is_in_user_writable_paths``)
are the gate's allow/deny checks; the agent-dir predicate lives in ``_paths``
because path safety needs it too. ``_is_in_user_writable_paths`` reads the
vault directory straight off ``config`` so this module has no dependency on
``tools.py`` (which imports it) — keeping the import graph one-way.
"""

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from decafclaw.media import ToolResult
from decafclaw.skills.vault._grants import is_path_in_grants, normalize_folder
from decafclaw.skills.vault._paths import _is_in_agent_dir

if TYPE_CHECKING:
    from decafclaw.context import Context


class GateOutcome(Enum):
    """Outcome of the user-page write gate. See `_check_user_write_allowed`."""

    ALLOW = "allow"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NON_INTERACTIVE_ERROR = "non_interactive_error"


def _is_in_user_writable_paths(config, path: Path) -> bool:
    """Check if a vault-relative path is under any configured allowlist entry.

    Per-call: silent on invalid entries. The allowlist is validated once at
    config-load time (see `load_config` in `decafclaw.config`), which surfaces
    misconfigured entries without spamming logs on every write attempt.
    """
    paths = config.vault.user_writable_paths or []
    if not paths:
        return False
    try:
        rel = str(path.resolve().relative_to(config.vault_root.resolve()))
    except (ValueError, OSError):
        return False
    rel = rel.replace("\\", "/")
    for entry in paths:
        norm = normalize_folder(entry)
        if norm and rel.startswith(norm):
            return True
    return False


def _check_user_write_allowed(ctx: "Context", path: Path) -> GateOutcome:
    """Decide how to handle a vault write/delete/rename for the given path.

    Returns:
        GateOutcome.ALLOW — write directly (path is in agent/, allowlist, or grants)
        GateOutcome.NEEDS_CONFIRMATION — caller must call request_confirmation()
        GateOutcome.NON_INTERACTIVE_ERROR — no UI available; caller returns error
    """
    if _is_in_agent_dir(ctx.config, path):
        return GateOutcome.ALLOW
    if _is_in_user_writable_paths(ctx.config, path):
        return GateOutcome.ALLOW
    if is_path_in_grants(ctx.config, ctx.conv_id, path):
        return GateOutcome.ALLOW
    if ctx.request_confirmation is None:
        return GateOutcome.NON_INTERACTIVE_ERROR
    return GateOutcome.NEEDS_CONFIRMATION


async def _run_gate_or_confirm(
    ctx: "Context",
    outcomes: list[GateOutcome],
    *,
    tool_name: str,
    command: str,
    preview: str,
    non_interactive_error: str,
    denied_error: str,
) -> ToolResult | None:
    """Run the user-write gate flow shared by vault_write/delete/rename.

    Pass one or more `GateOutcome` values (write/delete take one path; rename
    takes both old and new). Returns:
        - ToolResult with the appropriate error message if any outcome is
          NON_INTERACTIVE_ERROR or if the user denies confirmation.
        - None if the operation should proceed (all outcomes are ALLOW, or the
          user approved confirmation).
    """
    # Resolve `request_confirmation` from the tools module rather than importing
    # it here: tests patch `decafclaw.skills.vault.tools.request_confirmation`
    # and must keep intercepting this call. A module-level `import ...tools`
    # would be circular (tools imports this module), so the import is deferred
    # to call time, when both modules are fully loaded.
    from decafclaw.skills.vault import tools

    if any(o == GateOutcome.NON_INTERACTIVE_ERROR for o in outcomes):
        return ToolResult(text=non_interactive_error)
    if any(o == GateOutcome.NEEDS_CONFIRMATION for o in outcomes):
        approval = await tools.request_confirmation(
            ctx,
            tool_name=tool_name,
            command=command,
            message=preview,
        )
        if not approval.get("approved"):
            return ToolResult(text=denied_error)
    return None
