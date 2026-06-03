"""Workflow definition loader.

Parses a skill directory containing:
  - SKILL.md   (frontmatter: name, description, kind: workflow, ...)
  - workflow.yaml  (initial-step + steps list)
  - prompts/*.md   (optional, referenced via prompt-from: in steps)

Returns a frozen WorkflowDef ready for the engine to execute.

Load-time validation:
  - every ``to:`` reference resolves to a step id or ""
  - ``initial-step`` exists
  - unreachable steps emit warnings
  - all step kinds are implemented as of Phase 5
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from .types import EdgeRef, RouteChoice, StepDef, StepKind, WorkflowDef

log = logging.getLogger(__name__)

# All step kinds are implemented as of Phase 5; _KIND_PHASE is now empty.
_KIND_PHASE: dict = {}
_IMPLEMENTED_KINDS = {
    StepKind.LLM_CALL,
    StepKind.TOOL_CALL,
    StepKind.SUBAGENT,
    StepKind.ROUTE,
    StepKind.PYTHON,
    StepKind.USER_INPUT,
}


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract YAML frontmatter from a markdown file.

    Returns an empty dict if no frontmatter block is found.
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        log.warning("[loader] failed to parse frontmatter: %s", exc)
        return {}


def _parse_next_edges(next_field: Any) -> tuple[EdgeRef, ...]:
    """Parse the ``next:`` field into a tuple of EdgeRef objects.

    Supported forms:
      next: step_id              — single unconditional edge
      next: [{if: expr, to: id}, {to: id}]  — conditional list
    """
    if next_field is None:
        return ()
    if isinstance(next_field, str):
        return (EdgeRef(to=next_field),)
    if isinstance(next_field, list):
        edges = []
        for entry in next_field:
            if isinstance(entry, dict):
                to = entry.get("to", "")
                if_expr = entry.get("if", "")
                edges.append(EdgeRef(to=str(to), if_expr=str(if_expr)))
            elif isinstance(entry, str):
                edges.append(EdgeRef(to=entry))
        return tuple(edges)
    raise ValueError(f"Invalid 'next' field: {next_field!r}")


def _parse_choices(choices_field: Any, step_id: str = "") -> tuple[RouteChoice, ...]:
    """Parse the ``choices:`` list into a tuple of RouteChoice objects.

    Raises ValueError if any two choices share the same ``id`` value —
    duplicate ids would produce an invalid JSON Schema enum and silently
    shadow the second binding at runtime.
    """
    if not choices_field:
        return ()
    result = []
    seen_ids: set[str] = set()
    for entry in choices_field:
        if isinstance(entry, dict):
            choice_id = str(entry.get("id", ""))
            if choice_id in seen_ids:
                raise ValueError(
                    f"step {step_id!r} has duplicate choice ids: {choice_id!r}"
                )
            seen_ids.add(choice_id)
            result.append(RouteChoice(
                id=choice_id,
                to=str(entry.get("to", "")),
                when=str(entry.get("when", "")),
                label=str(entry.get("label", "")),
            ))
    return tuple(result)


def load_workflow(skill_dir: Path) -> WorkflowDef:
    """Parse a skill directory into a frozen WorkflowDef.

    Raises ValueError for structural validation errors (bad refs,
    missing initial-step). Raises NotImplementedError for step kinds
    not yet implemented in this phase. Warns on unreachable steps.
    """
    skill_dir = Path(skill_dir)

    # 1. Parse SKILL.md frontmatter
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file():
        fm = _parse_frontmatter(skill_md.read_text())
    else:
        fm = {}

    name = str(fm.get("name", skill_dir.name))
    wf_description = str(fm.get("description", ""))

    # 2. Parse workflow.yaml
    wf_yaml_path = skill_dir / "workflow.yaml"
    if not wf_yaml_path.is_file():
        raise ValueError(f"No workflow.yaml found in {skill_dir}")

    wf_data = yaml.safe_load(wf_yaml_path.read_text()) or {}
    initial_step = str(wf_data.get("initial-step", ""))
    raw_steps = wf_data.get("steps", []) or []

    # 3. Parse each step
    steps: list[StepDef] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            raise ValueError(f"Step must be a dict, got: {raw!r}")

        step_id = str(raw.get("id", ""))
        kind_str = str(raw.get("kind", ""))
        try:
            kind = StepKind(kind_str)
        except ValueError:
            raise ValueError(
                f"Unknown step kind {kind_str!r} for step {step_id!r}"
            ) from None

        # Phase gate — raise for not-yet-implemented kinds
        if kind not in _IMPLEMENTED_KINDS:
            phase = _KIND_PHASE.get(kind, "?")
            raise NotImplementedError(
                f"Step kind {kind.value!r} (step {step_id!r}) is not yet "
                f"implemented. It ships in Phase {phase}."
            )

        # Build kind-specific config
        config: dict[str, Any] = {}
        if kind == StepKind.LLM_CALL:
            # prompt or prompt-from
            if "prompt-from" in raw:
                prompt_file = skill_dir / "prompts" / raw["prompt-from"]
                if not prompt_file.is_file():
                    raise ValueError(
                        f"prompt-from {raw['prompt-from']!r} not found at {prompt_file}"
                    )
                config["prompt"] = prompt_file.read_text()
            elif "prompt" in raw:
                config["prompt"] = str(raw["prompt"])
            else:
                config["prompt"] = ""
            config["schema"] = raw.get("schema") or {"type": "object"}
            if "system" in raw:
                config["system"] = str(raw["system"])

        elif kind == StepKind.TOOL_CALL:
            if "tool" not in raw:
                raise ValueError(
                    f"tool_call step {step_id!r} is missing required 'tool' field"
                )
            config["tool"] = str(raw["tool"])
            config["args"] = dict(raw["args"]) if raw.get("args") else {}

        elif kind == StepKind.SUBAGENT:
            # prompt or prompt-from
            if "prompt-from" in raw:
                prompt_file = skill_dir / "prompts" / raw["prompt-from"]
                if not prompt_file.is_file():
                    raise ValueError(
                        f"prompt-from {raw['prompt-from']!r} not found at {prompt_file}"
                    )
                config["prompt"] = prompt_file.read_text()
            elif "prompt" in raw:
                config["prompt"] = str(raw["prompt"])
            else:
                config["prompt"] = ""
            # Skill to load as the child's system prompt body (optional)
            if "skill" in raw:
                config["skill"] = str(raw["skill"])
            # Tools whitelist for the child (list of names or globs)
            config["tools"] = list(raw["tools"]) if raw.get("tools") else []
            # Declared output filenames the child must produce
            config["outputs"] = list(raw["outputs"]) if raw.get("outputs") else []
            # Context-profile overrides for the child context composer
            config["context-profile"] = dict(raw["context-profile"]) \
                if raw.get("context-profile") else {}

        elif kind == StepKind.ROUTE:
            # prompt or prompt-from (required — the LLM needs context to choose)
            if "prompt-from" in raw:
                prompt_file = skill_dir / "prompts" / raw["prompt-from"]
                if not prompt_file.is_file():
                    raise ValueError(
                        f"prompt-from {raw['prompt-from']!r} not found at {prompt_file}"
                    )
                config["prompt"] = prompt_file.read_text()
            elif "prompt" in raw:
                config["prompt"] = str(raw["prompt"])
            else:
                config["prompt"] = ""
            if "system" in raw:
                config["system"] = str(raw["system"])
            # Validate choices at load time — empty enum causes a runtime schema error
            raw_choices = raw.get("choices") or []
            if len(raw_choices) < 1:
                raise ValueError(
                    f"route step {step_id!r} requires at least one choice"
                )
            # choices are parsed into step.choices below via _parse_choices;
            # no extra config needed here

        elif kind == StepKind.PYTHON:
            if "fn" not in raw:
                raise ValueError(
                    f"python step {step_id!r} is missing required 'fn' field"
                )
            config["fn"] = str(raw["fn"])

        elif kind == StepKind.USER_INPUT:
            # prompt or prompt-from (required — the user needs context)
            if "prompt-from" in raw:
                prompt_file = skill_dir / "prompts" / raw["prompt-from"]
                if not prompt_file.is_file():
                    raise ValueError(
                        f"prompt-from {raw['prompt-from']!r} not found at {prompt_file}"
                    )
                config["prompt"] = prompt_file.read_text()
            elif "prompt" in raw:
                config["prompt"] = str(raw["prompt"])
            else:
                config["prompt"] = ""
            # Optional: input mode ("text") — if absent and choices present, choice mode
            if "input" in raw:
                input_mode = str(raw["input"])
                if input_mode not in ("text",):
                    raise ValueError(
                        f"user_input step {step_id!r}: unknown input mode "
                        f"{input_mode!r} — supported: 'text'"
                    )
                config["input"] = input_mode
            # Validate: must have either input:text or choices
            raw_choices = raw.get("choices") or []
            if not config.get("input") and not raw_choices:
                raise ValueError(
                    f"user_input step {step_id!r}: must have `input: text` "
                    f"or a `choices:` list"
                )

        next_edges = _parse_next_edges(raw.get("next"))
        choices = _parse_choices(raw.get("choices"), step_id=step_id)
        description = str(raw.get("description", ""))

        steps.append(StepDef(
            id=step_id,
            kind=kind,
            config=config,
            next_edges=next_edges,
            choices=choices,
            description=description,
        ))

    steps_tuple = tuple(steps)
    steps_by_id = {s.id: s for s in steps_tuple}

    # 4. Validate: initial-step must exist
    if not initial_step:
        initial_step = steps[0].id if steps else ""
    if initial_step not in steps_by_id:
        raise ValueError(
            f"initial-step {initial_step!r} does not match any step id "
            f"(available: {list(steps_by_id)})"
        )

    # 5. Validate: every to: ref must resolve or be ""
    all_ids = set(steps_by_id)
    for step in steps_tuple:
        for edge in step.next_edges:
            if edge.to and edge.to not in all_ids:
                raise ValueError(
                    f"Step {step.id!r} has an edge to {edge.to!r} which "
                    f"does not match any step id (available: {sorted(all_ids)})"
                )
        for choice in step.choices:
            if choice.to and choice.to not in all_ids:
                raise ValueError(
                    f"Step {step.id!r} has a choice to {choice.to!r} which "
                    f"does not match any step id (available: {sorted(all_ids)})"
                )

    # 6. Warn on unreachable steps (BFS from initial_step)
    reachable: set[str] = set()
    queue = [initial_step]
    while queue:
        sid = queue.pop()
        if sid in reachable:
            continue
        reachable.add(sid)
        step = steps_by_id[sid]
        for edge in step.next_edges:
            if edge.to and edge.to not in reachable:
                queue.append(edge.to)
        for choice in step.choices:
            if choice.to and choice.to not in reachable:
                queue.append(choice.to)

    for sid in all_ids:
        if sid not in reachable:
            log.warning(
                "[workflow:%s] step %r is unreachable from initial-step %r",
                name, sid, initial_step,
            )

    return WorkflowDef(
        name=name,
        description=wf_description,
        initial_step=initial_step,
        steps=steps_tuple,
        skill_dir=skill_dir,
    )
