"""Durable per-conversation shell approval state (#1160).

The shell-approval settings a user builds up in a conversation — "Approve +
remember why" rules, preset toggles, and the session mode — live on
``PersistedTurnState`` in memory. This module saves them to a small JSON
file so they survive a server restart.

The file lives under the admin data dir, ``data/{agent_id}/approval_state/
{conv_id}.json`` (see ``conversation_paths.approval_state_path``), not in the
agent-writable workspace: approval guidance steers the shell reviewer, so an
agent that could write this file could grant itself shell access.

Reviewer approval caches (``llm_approved_shell_patterns``) are deliberately
not persisted — after a restart each pattern is earned again with one
reviewer call.

Fail-open throughout: a missing, unreadable, or malformed file yields
``None`` (callers keep defaults) and a failed write logs a warning.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .conversation_paths import approval_state_path

log = logging.getLogger(__name__)


@dataclass
class ApprovalState:
    """The persisted subset of approval state for one conversation.

    Field names match the corresponding ``PersistedTurnState`` fields, so
    transfer between the two walks ``fields(ApprovalState)`` instead of a
    hand-maintained list. Add a field here (with a matching
    ``PersistedTurnState`` field) to make more state durable.
    """

    aux_approval_guidance: list[str] = field(default_factory=list)
    active_aux_approval_presets: list[str] = field(default_factory=list)
    disabled_aux_approval_presets: list[str] = field(default_factory=list)
    active_mode: str = "default"


APPROVAL_STATE_FIELDS: frozenset[str] = frozenset(f.name for f in fields(ApprovalState))


def _copy_value(value: Any) -> Any:
    return list(value) if isinstance(value, list) else value


def approval_state_from(persisted: Any) -> ApprovalState:
    """Snapshot the approval subset of a ``PersistedTurnState``."""
    return ApprovalState(**{f.name: _copy_value(getattr(persisted, f.name)) for f in fields(ApprovalState)})


def apply_approval_state(persisted: Any, approval: ApprovalState) -> None:
    """Write an ``ApprovalState`` onto a ``PersistedTurnState``."""
    for f in fields(ApprovalState):
        setattr(persisted, f.name, _copy_value(getattr(approval, f.name)))


def _coerce(raw: dict) -> ApprovalState:
    """Build an ApprovalState from parsed JSON.

    Unknown keys are ignored; missing keys and values of the wrong type
    take the dataclass default; non-string list items are dropped.
    """
    defaults = ApprovalState()
    values: dict[str, Any] = {}
    for f in fields(ApprovalState):
        if f.name not in raw:
            continue
        value = raw[f.name]
        default = getattr(defaults, f.name)
        if isinstance(default, list):
            if isinstance(value, list):
                values[f.name] = [item for item in value if isinstance(item, str)]
            else:
                log.warning("approval_state: ignoring non-list %s", f.name)
        elif isinstance(value, type(default)) and value:
            values[f.name] = value
        else:
            log.warning("approval_state: ignoring invalid %s", f.name)
    return ApprovalState(**values)


def load_approval_state(config, conv_id: str) -> ApprovalState | None:
    """Load the conversation's approval state, or ``None`` if there is none.

    A missing file returns ``None`` quietly; an unreadable or malformed file
    logs a warning and returns ``None``. Never raises.
    """
    try:
        path = approval_state_path(config, conv_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load approval state for conv %s: %s", conv_id, exc)
        return None
    if not isinstance(raw, dict):
        log.warning("Ignoring malformed approval state for conv %s (not an object)", conv_id)
        return None
    return _coerce(raw)


def save_approval_state(config, conv_id: str, approval: ApprovalState) -> bool:
    """Write the conversation's approval state atomically.

    Returns True on success. Failures log a warning and return False —
    never raise into the turn.
    """
    try:
        path = approval_state_path(config, conv_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(approval), indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as exc:
        log.warning("Failed to save approval state for conv %s: %s", conv_id, exc)
        return False
