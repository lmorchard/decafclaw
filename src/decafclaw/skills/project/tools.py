"""Project skill tools — structured workflow management.

The workflow is driven by project_next_task, which tells the agent
what to do at each phase. The agent just follows one instruction at
a time instead of remembering a multi-step sequence.

Control flow is mechanical:
- get_tools(ctx) returns only the tools valid for the current phase
- Phase-boundary tools return end_turn=True to stop the agent loop
"""

from datetime import datetime, timezone
from pathlib import Path

from decafclaw.media import EndTurnConfirm, ToolResult
from decafclaw.skills.project.plan_parser import (
    insert_steps,
    next_actionable,
    parse_plan,
    plan_progress,
    render_plan,
    update_step_status,
)
from decafclaw.skills.project.state import (
    ProjectInfo,
    ProjectState,
    create_project,
    list_projects,
    load_project,
    save_project,
    validate_transition,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str, **kwargs) -> str:
    """Load a prompt template from the prompts directory and format it."""
    path = _PROMPTS_DIR / f"{name}.md"
    text = path.read_text().strip()
    if kwargs:
        text = text.format(**kwargs)
    return text


def _get_current_project(ctx) -> str | None:
    return (ctx.skills.data or {}).get("current_project")


def _set_current_project(ctx, slug: str) -> None:
    if ctx.skills.data is None:
        ctx.skills.data = {}
    ctx.skills.data["current_project"] = slug


def _resolve_project(ctx, project: str = "") -> str:
    if project:
        return project
    current = _get_current_project(ctx)
    if current:
        return current
    raise ValueError(
        "no project specified and no current project set. "
        "Use project_create first."
    )


def _load_or_error(config, project: str) -> ProjectInfo | ToolResult:
    info = load_project(config, project)
    if info is None:
        return ToolResult(text=f"[error: project '{project}' not found]")
    return info


def _load_current(ctx) -> ProjectInfo | ToolResult:
    """Load the current project or return an error."""
    try:
        project = _resolve_project(ctx)
    except ValueError as e:
        return ToolResult(text=f"[error: {e}]")
    return _load_or_error(ctx.config, project)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def tool_project_create(ctx, description: str, slug: str = "") -> str | ToolResult:
    """Create a new structured project."""
    info = create_project(ctx.config, description, slug=slug)
    _set_current_project(ctx, info.slug)
    return (
        f"Project created: {info.slug}\n"
        f"Directory: {info.directory}\n"
        f"Status: {info.status.value}\n\n"
        f"Now call **project_next_task** to get your first instruction."
    )


async def tool_project_next_task(ctx) -> str | ToolResult:
    """Get the current instruction for the project phase.

    Returns what to do right now. Does NOT advance phases — call
    project_task_done when you've completed the current phase's work.
    Safe to call multiple times — always returns the same instruction
    until the phase changes.
    """
    result = _load_current(ctx)
    if isinstance(result, ToolResult):
        return result
    info = result

    if info.status == ProjectState.BRAINSTORMING:
        spec_content = info.spec_path.read_text().strip() if info.spec_path.exists() else ""
        if spec_content:
            return (
                "You have written the spec. Now present it to the user "
                "and call project_task_done to submit it for review."
            )
        return _load_prompt("brainstorming")

    elif info.status == ProjectState.SPEC_REVIEW:
        return (
            "The spec is awaiting user review. Read the user's message — "
            "if they approve, call project_task_done. If they have feedback, "
            "revise the spec with project_update_spec."
        )

    elif info.status == ProjectState.PLANNING:
        plan_content = info.plan_path.read_text().strip() if info.plan_path.exists() else ""
        if plan_content:
            _, steps, _ = parse_plan(plan_content)
            _, total = plan_progress(steps)
            if total > 0:
                return (
                    "You have written the plan. Now present it to the user "
                    "and call project_task_done to submit it for review."
                )
            return _load_prompt("plan_no_steps")
        return _load_prompt("planning")

    elif info.status == ProjectState.PLAN_REVIEW:
        return (
            "The plan is awaiting user review. Read the user's message — "
            "if they approve, call project_task_done. If they have feedback, "
            "revise the plan with project_update_plan."
        )

    elif info.status == ProjectState.EXECUTING:
        return _next_execution_step(info)

    elif info.status == ProjectState.DONE:
        return "Project is complete! No more tasks."

    return ToolResult(text=f"[error: unexpected state '{info.status.value}']")


async def tool_project_task_done(ctx) -> str | ToolResult:
    """Signal that the current phase's work is complete and advance.

    Call this after you've done the work for the current phase:
    - After brainstorming: shows Approve/Needs Feedback buttons (EndTurnConfirm)
    - After spec approved: advances to planning
    - After planning: shows Approve/Needs Feedback buttons (EndTurnConfirm)
    - After plan approved: advances to executing
    - After executing (all steps done): marks project complete

    Review gates use EndTurnConfirm — the agent loop shows buttons and
    handles the response. Approval continues the loop; denial ends the turn.
    """
    result = _load_current(ctx)
    if isinstance(result, ToolResult):
        return result
    info = result

    if info.status in (ProjectState.BRAINSTORMING, ProjectState.SPEC_REVIEW):
        spec_content = info.spec_path.read_text().strip() if info.spec_path.exists() else ""
        if not spec_content:
            return ToolResult(
                text="[error: write the spec with project_update_spec before "
                "calling project_task_done]"
            )
        info.status = ProjectState.SPEC_REVIEW
        save_project(info)

        def _on_approve():
            info.status = ProjectState.PLANNING
            save_project(info)

        def _on_deny():
            info.status = ProjectState.BRAINSTORMING
            save_project(info)

        return ToolResult(
            text="Spec submitted for review.",
            end_turn=EndTurnConfirm(
                message=(
                    f"**Spec review for '{info.slug}'**\n\n"
                    f"Click **Approve** to proceed to planning, "
                    f"or **Needs Feedback** to request changes."
                ),
                approve_label="Approve",
                deny_label="Needs Feedback",
                on_approve=_on_approve,
                on_deny=_on_deny,
            ),
        )

    elif info.status in (ProjectState.PLANNING, ProjectState.PLAN_REVIEW):
        plan_content = info.plan_path.read_text().strip() if info.plan_path.exists() else ""
        if not plan_content:
            return ToolResult(
                text="[error: write the plan with project_update_plan before "
                "calling project_task_done]"
            )
        _, steps, _ = parse_plan(plan_content)
        _, total = plan_progress(steps)
        if total == 0:
            return ToolResult(text=_load_prompt("plan_no_steps"))
        info.status = ProjectState.PLAN_REVIEW
        save_project(info)

        def _on_approve():
            info.status = ProjectState.EXECUTING
            save_project(info)

        def _on_deny():
            info.status = ProjectState.PLANNING
            save_project(info)

        return ToolResult(
            text="Plan submitted for review.",
            end_turn=EndTurnConfirm(
                message=(
                    f"**Plan review for '{info.slug}'**\n\n"
                    f"Click **Approve** to proceed to execution, "
                    f"or **Needs Feedback** to request changes."
                ),
                approve_label="Approve",
                deny_label="Needs Feedback",
                on_approve=_on_approve,
                on_deny=_on_deny,
            ),
        )

    elif info.status == ProjectState.EXECUTING:
        _, steps, _ = parse_plan(info.plan_path.read_text())
        if next_actionable(steps) is not None:
            return ToolResult(
                text="[error: there are still incomplete steps. Finish them "
                "before calling project_task_done.]"
            )
        info.status = ProjectState.DONE
        save_project(info)
        return ToolResult(text="Project complete!", end_turn=True)

    elif info.status == ProjectState.DONE:
        return "Project is already complete."

    return ToolResult(text=f"[error: unexpected state '{info.status.value}']")


def _next_execution_step(info: ProjectInfo) -> str:
    """Build the next-step instruction for execution phase."""
    content = info.plan_path.read_text() if info.plan_path.exists() else ""
    if not content.strip():
        return "No plan found. Use project_update_plan to write one."

    _, steps, _ = parse_plan(content)
    done, total = plan_progress(steps)
    nxt = next_actionable(steps)

    if nxt is None:
        return _load_prompt("executing_complete", total=total)

    if nxt.status == "in_progress":
        return _load_prompt(
            "executing_continue",
            done=done, total=total,
            number=nxt.number, description=nxt.description,
            directory=info.directory,
        )
    else:
        return _load_prompt(
            "executing_next",
            done=done, total=total,
            number=nxt.number, description=nxt.description,
            directory=info.directory,
        )


async def tool_project_status(ctx) -> str | ToolResult:
    """Check the current state of a project."""
    result = _load_current(ctx)
    if isinstance(result, ToolResult):
        return result
    info = result

    lines = [
        f"# Project: {info.slug}",
        f"**Description:** {info.description}",
        f"**Status:** {info.status.value}",
        f"**Directory:** {info.directory}",
        f"**Updated:** {info.updated_at}",
    ]

    if info.status == ProjectState.EXECUTING and info.plan_path.exists():
        content = info.plan_path.read_text()
        if content.strip():
            _, steps, _ = parse_plan(content)
            done, total = plan_progress(steps)
            lines.append(f"**Progress:** {done}/{total} steps completed")
            nxt = next_actionable(steps)
            if nxt:
                lines.append(f"**Next step:** {nxt.number}. {nxt.description}")

    return "\n".join(lines)


async def tool_project_list(ctx) -> str | ToolResult:
    """List all projects with their status."""
    projects = list_projects(ctx.config)
    if not projects:
        return "No projects found."

    lines = ["| Slug | Status | Description | Updated |", "| --- | --- | --- | --- |"]
    for p in projects:
        lines.append(f"| {p.slug} | {p.status.value} | {p.description} | {p.updated_at} |")
    return "\n".join(lines)


async def tool_project_switch(ctx, project: str) -> str | ToolResult:
    """Switch the current project context."""
    result = _load_or_error(ctx.config, project)
    if isinstance(result, ToolResult):
        return result
    info = result
    _set_current_project(ctx, info.slug)
    return f"Switched to project '{info.slug}' ({info.status.value}). Call project_next_task."


async def tool_project_update_spec(ctx, spec_text: str) -> str | ToolResult:
    """Write or update the project spec."""

    result = _load_current(ctx)
    if isinstance(result, ToolResult):
        return result
    info = result

    if info.status not in (ProjectState.BRAINSTORMING, ProjectState.SPEC_REVIEW):
        return ToolResult(
            text=f"[error: can only update spec during brainstorming or spec_review, "
            f"not {info.status.value}]"
        )

    info.spec_path.write_text(spec_text)
    info.status = ProjectState.SPEC_REVIEW
    save_project(info)

    def _on_approve():
        info.status = ProjectState.PLANNING
        save_project(info)

    def _on_deny():
        info.status = ProjectState.BRAINSTORMING
        save_project(info)

    return ToolResult(
        text=f"Spec updated ({len(spec_text)} chars). "
        f"Present the spec to the user.\n\n---\n{spec_text}\n---",
        end_turn=EndTurnConfirm(
            message=(
                f"**Spec review for '{info.slug}'**\n\n"
                f"Click **Approve** to proceed to planning, "
                f"or **Needs Feedback** to request changes."
            ),
            approve_label="Approve",
            deny_label="Needs Feedback",
            on_approve=_on_approve,
            on_deny=_on_deny,
        ),
    )


async def tool_project_update_plan(ctx, plan_text: str) -> str | ToolResult:
    """Write or update the project plan."""

    result = _load_current(ctx)
    if isinstance(result, ToolResult):
        return result
    info = result

    if info.status not in (ProjectState.PLANNING, ProjectState.PLAN_REVIEW):
        return ToolResult(
            text=f"[error: can only update plan during planning or plan_review, "
            f"not {info.status.value}]"
        )

    overview, steps, tail = parse_plan(plan_text)
    _, total = plan_progress(steps)

    if plan_text.strip() and total == 0:
        return ToolResult(
            text=(
                "[error: no steps parsed. Use checkbox format:\n"
                "  - [ ] 1. First step\n"
                "  - [ ] 2. Second step]"
            )
        )

    rendered = render_plan(overview, steps, tail)
    info.plan_path.write_text(rendered)
    info.status = ProjectState.PLAN_REVIEW
    save_project(info)

    def _on_approve():
        info.status = ProjectState.EXECUTING
        save_project(info)

    def _on_deny():
        info.status = ProjectState.PLANNING
        save_project(info)

    return ToolResult(
        text=f"Plan updated ({total} steps). "
        f"Present the plan to the user.\n\n---\n{rendered}\n---",
        end_turn=EndTurnConfirm(
            message=(
                f"**Plan review for '{info.slug}'**\n\n"
                f"Click **Approve** to proceed to execution, "
                f"or **Needs Feedback** to request changes."
            ),
            approve_label="Approve",
            deny_label="Needs Feedback",
            on_approve=_on_approve,
            on_deny=_on_deny,
        ),
    )


async def tool_project_update_step(
    ctx, step: str, status: str, note: str = ""
) -> str | ToolResult:
    """Update a plan step's status."""

    result = _load_current(ctx)
    if isinstance(result, ToolResult):
        return result
    info = result

    if info.status != ProjectState.EXECUTING:
        return ToolResult(text="[error: can only update steps during executing]")

    if status not in ("pending", "in_progress", "done", "skipped"):
        return ToolResult(text="[error: status must be pending, in_progress, done, or skipped]")

    content = info.plan_path.read_text()
    overview, steps, tail = parse_plan(content)
    if not update_step_status(steps, step, status, note):
        return ToolResult(text=f"[error: step '{step}' not found]")

    info.plan_path.write_text(render_plan(overview, steps, tail))
    save_project(info)

    done, total = plan_progress(steps)
    msg = f"Step {step} → **{status}** ({done}/{total})"
    if note:
        msg += f"\n{note}"
    return msg


async def tool_project_add_steps(
    ctx, after_step: str, steps: list[str]
) -> str | ToolResult:
    """Insert new steps after a given step."""

    result = _load_current(ctx)
    if isinstance(result, ToolResult):
        return result
    info = result

    if info.status not in (ProjectState.EXECUTING, ProjectState.PLANNING, ProjectState.PLAN_REVIEW):
        return ToolResult(text="[error: can only add steps during planning, plan_review, or executing]")

    content = info.plan_path.read_text()
    overview, plan_steps, tail = parse_plan(content)
    if not insert_steps(plan_steps, after_step, steps):
        return ToolResult(text=f"[error: step '{after_step}' not found]")

    info.plan_path.write_text(render_plan(overview, plan_steps, tail))
    save_project(info)

    _, total = plan_progress(plan_steps)
    return f"Added {len(steps)} step(s) after step {after_step}. Plan now has {total} steps."


async def tool_project_advance(ctx, target_status: str = "") -> str | ToolResult:
    """Go back to an earlier phase (e.g. replanning). Use project_task_done to go forward."""

    result = _load_current(ctx)
    if isinstance(result, ToolResult):
        return result
    info = result

    if not target_status:
        return ToolResult(
            text="[error: specify target_status for backward transitions "
            "(e.g. 'planning', 'brainstorming'). Use project_task_done to advance forward.]"
        )

    # Backward transition
    try:
        target = ProjectState(target_status)
    except ValueError:
        valid = ", ".join(s.value for s in ProjectState)
        return ToolResult(text=f"[error: invalid state '{target_status}'. Valid: {valid}]")

    if not validate_transition(info.status, target):
        from decafclaw.skills.project.state import TRANSITIONS
        valid = ", ".join(s.value for s in TRANSITIONS.get(info.status, set()))
        return ToolResult(
            text=f"[error: cannot go from '{info.status.value}' to '{target.value}'. "
            f"Valid: {valid}]"
        )

    info.status = target
    save_project(info)
    return f"Project reverted to {target.value}. Call project_next_task."


async def tool_project_note(ctx, note_text: str) -> str | ToolResult:
    """Append a timestamped note to the project."""

    result = _load_current(ctx)
    if isinstance(result, ToolResult):
        return result
    info = result

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n## {timestamp}\n\n{note_text}\n"
    with open(info.notes_path, "a") as f:
        f.write(entry)
    save_project(info)
    return f"Note added to project '{info.slug}'."


# ---------------------------------------------------------------------------
# Tool registry (static — full set, used as fallback and for pre-load cache)
# ---------------------------------------------------------------------------

TOOLS = {
    "project_create": tool_project_create,
    "project_next_task": tool_project_next_task,
    "project_task_done": tool_project_task_done,
    "project_status": tool_project_status,
    "project_list": tool_project_list,
    "project_switch": tool_project_switch,
    "project_update_spec": tool_project_update_spec,
    "project_update_plan": tool_project_update_plan,
    "project_update_step": tool_project_update_step,
    "project_add_steps": tool_project_add_steps,
    "project_advance": tool_project_advance,
    "project_note": tool_project_note,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "project_create",
            "description": "Create a new project. Call project_next_task after this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What to accomplish"},
                    "slug": {"type": "string", "description": "Short name (optional)"},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_next_task",
            "description": "Get the current instruction for the project. Does NOT advance phases.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_task_done",
            "description": "Signal that the current phase is complete. Triggers review or advances to next phase.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_status",
            "description": "Show current project state and progress.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_list",
            "description": "List all projects with their status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_switch",
            "description": "Switch to a different project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_update_spec",
            "description": "Write the project specification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec_text": {"type": "string", "description": "Full spec as markdown"},
                },
                "required": ["spec_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_update_plan",
            "description": "Write the project plan with checkbox steps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_text": {"type": "string", "description": "Full plan as markdown"},
                },
                "required": ["plan_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_update_step",
            "description": "Mark a step as in_progress, done, or skipped.",
            "parameters": {
                "type": "object",
                "properties": {
                    "step": {"type": "string", "description": "Step number (e.g. '1', '1.2')"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "done", "skipped"]},
                    "note": {"type": "string", "description": "What was done or why skipped"},
                },
                "required": ["step", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_add_steps",
            "description": "Insert new steps after an existing step.",
            "parameters": {
                "type": "object",
                "properties": {
                    "after_step": {"type": "string", "description": "Step number to insert after"},
                    "steps": {"type": "array", "items": {"type": "string"}, "description": "Step descriptions"},
                },
                "required": ["after_step", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_advance",
            "description": "Go back to an earlier phase (e.g. 'planning', 'brainstorming'). Use project_task_done to go forward.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_status": {"type": "string", "description": "Target state (e.g. 'planning', 'brainstorming')"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_note",
            "description": "Append a timestamped note to the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_text": {"type": "string", "description": "Note content as markdown"},
                },
                "required": ["note_text"],
            },
        },
    },
]

# Build lookup dicts for get_tools to select subsets
_TOOLS_BY_NAME = TOOLS
_DEFS_BY_NAME = {
    td["function"]["name"]: td for td in TOOL_DEFINITIONS
}

# Phase-to-tool-names mapping
_PHASE_TOOLS: dict[ProjectState | None, list[str]] = {
    None: ["project_create", "project_list", "project_switch"],
    ProjectState.BRAINSTORMING: [
        "project_next_task", "project_update_spec", "project_task_done",
        "project_note", "project_status",
    ],
    ProjectState.SPEC_REVIEW: [
        "project_task_done", "project_update_spec", "project_status",
    ],
    ProjectState.PLANNING: [
        "project_next_task", "project_update_plan", "project_task_done",
        "project_note", "project_status",
    ],
    ProjectState.PLAN_REVIEW: [
        "project_task_done", "project_update_plan", "project_status",
    ],
    ProjectState.EXECUTING: [
        "project_next_task", "project_task_done", "project_update_step",
        "project_add_steps", "project_advance", "project_note", "project_status",
    ],
    ProjectState.DONE: [
        "project_status", "project_list", "project_switch", "project_note",
    ],
}


def _tools_for_phase(phase: ProjectState | None) -> tuple[dict, list]:
    """Return (tools_dict, tool_definitions) for a given phase."""
    names = _PHASE_TOOLS.get(phase, list(TOOLS.keys()))
    tools = {n: _TOOLS_BY_NAME[n] for n in names if n in _TOOLS_BY_NAME}
    defs = [_DEFS_BY_NAME[n] for n in names if n in _DEFS_BY_NAME]
    return tools, defs


def get_tools(ctx) -> tuple[dict, list]:
    """Dynamic tool provider — returns phase-appropriate tools each turn."""
    slug = _get_current_project(ctx)
    if not slug:
        return _tools_for_phase(None)

    info = load_project(ctx.config, slug)
    if info is None:
        return _tools_for_phase(None)

    return _tools_for_phase(info.status)
