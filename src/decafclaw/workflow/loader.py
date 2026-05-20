"""Parse a workflow skill directory (SKILL.md + phases/*.md) into a
WorkflowDef. Strict validation at load time — invalid workflows raise
LoaderError. The skill loader catches LoaderError, logs a warning, and
skips the workflow.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from .types import (
    EdgeDef,
    GateDef,
    PhaseDef,
    PhaseKind,
    WorkflowDef,
)

log = logging.getLogger(__name__)

_PHASE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_SUPPORTED_GATE_TYPES = {"review"}


class LoaderError(ValueError):
    """A workflow definition failed validation at load time."""


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body. Raises if missing."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        raise LoaderError("missing YAML frontmatter")
    body_start = stripped.find("\n", 3)
    end = stripped.find("\n---", body_start)
    if end == -1:
        raise LoaderError("unterminated YAML frontmatter")
    fm_str = stripped[3:end].strip()
    body = stripped[end + 4:].lstrip()
    try:
        meta = yaml.safe_load(fm_str) or {}
    except yaml.YAMLError as exc:
        raise LoaderError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise LoaderError("frontmatter must be a mapping")
    return meta, body


def _parse_gate(raw: dict, phase_id: str, edge_target: str) -> GateDef:
    gate_type = raw.get("type", "review")
    if gate_type not in _SUPPORTED_GATE_TYPES:
        supported = ", ".join(sorted(_SUPPORTED_GATE_TYPES))
        raise LoaderError(
            f"phase '{phase_id}': edge to '{edge_target}': "
            f"unsupported gate type '{gate_type}' (supported: {supported})")
    return GateDef(
        type=gate_type,
        message=raw.get("message", ""),
        approve_label=raw.get("approve-label", "Approve"),
        deny_label=raw.get("deny-label", "Deny"),
        on_deny=raw.get("on-deny", ""),
    )


def _parse_edges(raw: list, phase_id: str) -> list[EdgeDef]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise LoaderError(
            f"phase '{phase_id}': next-phases must be a list")
    edges: list[EdgeDef] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise LoaderError(
                f"phase '{phase_id}': next-phases[{i}] must be a mapping")
        target = entry.get("id")
        if not target:
            raise LoaderError(
                f"phase '{phase_id}': next-phases[{i}] missing 'id'")
        gate_raw = entry.get("gate")
        gate = _parse_gate(gate_raw, phase_id, target) if gate_raw else None
        edges.append(EdgeDef(
            id=target,
            when=entry.get("when", "") or "",
            gate=gate,
        ))
    return edges


def _parse_phase(path: Path) -> PhaseDef:
    phase_id = path.stem
    if not _PHASE_ID_RE.match(phase_id):
        raise LoaderError(
            f"phase '{phase_id}': id must match [a-z][a-z0-9_-]*")
    text = path.read_text()
    meta, body = _split_frontmatter(text)

    kind_raw = meta.get("kind", "inline")
    try:
        kind = PhaseKind(kind_raw)
    except ValueError:
        raise LoaderError(
            f"phase '{phase_id}': unknown kind '{kind_raw}'") from None

    tools_raw = meta.get("tools") or []
    if not isinstance(tools_raw, list):
        raise LoaderError(
            f"phase '{phase_id}': tools must be a list")
    tools = [str(t) for t in tools_raw]

    outputs_raw = meta.get("outputs") or []
    if not isinstance(outputs_raw, list):
        raise LoaderError(
            f"phase '{phase_id}': outputs must be a list")
    for i, entry in enumerate(outputs_raw):
        if not isinstance(entry, str) or not entry.strip():
            raise LoaderError(
                f"phase '{phase_id}': outputs[{i}] must be a "
                "non-empty string")
    outputs = tuple(outputs_raw)

    edges = _parse_edges(meta.get("next-phases") or [], phase_id)
    context_profile = meta.get("context-profile") or {}
    if not isinstance(context_profile, dict):
        raise LoaderError(
            f"phase '{phase_id}': context-profile must be a mapping")
    subagent_skill = meta.get("subagent-skill")

    return PhaseDef(
        id=phase_id,
        kind=kind,
        prompt=body.strip(),
        tools=tools,
        next_phases=edges,
        gate=None,
        outputs=outputs,
        subagent_skill=subagent_skill,
        context_profile=context_profile,
    )


def load_workflow(skill_dir: Path) -> WorkflowDef:
    """Load a workflow from a skill directory.

    Raises LoaderError if anything is invalid. The skill loader calls
    this and catches LoaderError to log + skip bad workflows.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise LoaderError(f"missing SKILL.md in {skill_dir}")
    meta, _body = _split_frontmatter(skill_md.read_text())

    name = meta.get("name")
    description = meta.get("description", "")
    if not name:
        raise LoaderError("SKILL.md missing 'name'")
    if meta.get("kind") != "workflow":
        raise LoaderError("SKILL.md kind must be 'workflow'")

    wf_block = meta.get("workflow") or {}
    if not isinstance(wf_block, dict):
        raise LoaderError("workflow: block must be a mapping")
    initial = wf_block.get("initial-phase")
    if not initial:
        raise LoaderError("workflow.initial-phase is required")

    phases_dir = skill_dir / "phases"
    if not phases_dir.is_dir():
        raise LoaderError(
            f"missing phases/ directory in {skill_dir}")

    phases: dict[str, PhaseDef] = {}
    for phase_file in sorted(phases_dir.glob("*.md")):
        phase = _parse_phase(phase_file)
        if phase.id in phases:
            raise LoaderError(
                f"duplicate phase id '{phase.id}'")
        phases[phase.id] = phase

    if not phases:
        raise LoaderError("no phase files found in phases/")
    if initial not in phases:
        raise LoaderError(
            f"workflow.initial-phase '{initial}' is not defined")

    _validate_phases(phases)

    return WorkflowDef(
        name=name,
        description=description,
        initial_phase=initial,
        phases=phases,
        user_invocable=bool(meta.get("user-invocable", False)),
        argument_hint=meta.get("argument-hint", ""),
    )


def _validate_phases(phases: dict[str, PhaseDef]) -> None:
    for phase in phases.values():
        # Edge targets resolve
        for edge in phase.next_phases:
            if edge.id not in phases:
                raise LoaderError(
                    f"phase '{phase.id}': edge target '{edge.id}' is "
                    "not defined")
            if edge.gate and edge.gate.on_deny \
                    and edge.gate.on_deny not in phases:
                raise LoaderError(
                    f"phase '{phase.id}': gate on-deny '{edge.gate.on_deny}'"
                    f" is not defined")
        # Multi-edge must have when: on every edge
        if len(phase.next_phases) > 1:
            for edge in phase.next_phases:
                if not edge.when.strip():
                    raise LoaderError(
                        f"phase '{phase.id}': multi-edge phases require "
                        f"'when:' on every edge (missing on '{edge.id}')")
        # Subagent constraints
        if phase.kind == PhaseKind.SUBAGENT:
            if phase.subagent_skill is None and not phase.outputs:
                raise LoaderError(
                    f"phase '{phase.id}': subagent phases require "
                    "'outputs:' (or a subagent-skill: that owns its own "
                    "output contract)")
            if len(phase.next_phases) > 1:
                raise LoaderError(
                    f"phase '{phase.id}': subagent phases must have "
                    "exactly one next-phases edge (no agent choice)")
            for edge in phase.next_phases:
                if edge.gate is not None:
                    raise LoaderError(
                        f"phase '{phase.id}': subagent phases cannot "
                        "have gated edges (gates are user-facing)")
