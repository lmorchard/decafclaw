"""Workflow tools — thin LLM-facing surface for the step-primitive engine.

Tools here are the only workflow-related tools exposed to the agent. They
are kept deliberately thin: they delegate to the engine and conv_state
helpers rather than containing logic.

No phase_advance. No refresh_workflow_tools. No _build_phase_allowed_set.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..confirmations import ConfirmationAction, ConfirmationRequest
from ..media import ToolResult, WidgetRequest
from ..workflow import engine
from ..workflow.conv_state import (
    archive_workflow_state,
    artifacts_dir,
    load_workflow_state,
    save_workflow_state,
)
from ..workflow.types import RunStatus, WorkflowState

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _transform_response(response_data: dict, mode: str) -> dict:
    """Transform widget response data into the workflow engine's format.

    Returns ``{"value": ...}`` for text mode, ``{"choice": ...}`` for choice
    mode. Dead code kept for Phase 4 cleanup (Phase 3 deletes this fully).
    """
    if mode == "text":
        return {"value": response_data.get("value", "")}
    # choice mode: multiple_choice widget submits {"selected": id_or_list}.
    selected = response_data.get("selected", "")
    if isinstance(selected, list):
        selected = selected[0] if selected else ""
    return {"choice": selected}


def _summary_for_response(user_input: dict, mode: str) -> str:
    """Build the synthetic-message summary for a user input."""
    if mode == "text":
        return f"User provided text input: {user_input['value']!r}"
    return f"User selected choice: {user_input['choice']!r}"


def _summary_for_completion(state: WorkflowState) -> str:
    """Describe the final state after the workflow exits user_input pauses."""
    return (
        f"Workflow {state.workflow!r} reached status "
        f"{state.status.value!r} after user input cycle."
    )


def _build_confirmation_for_pause(state: WorkflowState) -> ConfirmationRequest:
    """Build a ConfirmationRequest for a new PAUSED_USER_INPUT step.

    Mirrors what ``_build_paused_tool_result`` puts into the WidgetRequest,
    but produces a ``ConfirmationRequest`` directly so the callback loop can
    await ``ctx.request_confirmation`` without going back through the agent
    loop's tool-execution machinery.

    A fresh ``tool_call_id`` is generated so the request is distinct from
    the one that triggered the original pause.
    """
    pending = state.pending
    prompt = pending.get("prompt", "The workflow needs your input.")
    mode = pending.get("mode", "")

    if mode == "text":
        widget_payload = {
            "widget_type": "text_input",
            "target": "inline",
            "data": {
                "prompt": prompt,
                "fields": [
                    {
                        "key": "value",
                        "label": "Your answer",
                        "multiline": True,
                        "required": True,
                    }
                ],
            },
        }
    elif mode == "choice":
        choices = pending.get("choices", [])
        options = [
            {"value": c["id"], "label": c["label"]}
            for c in choices
        ]
        widget_payload = {
            "widget_type": "multiple_choice",
            "target": "inline",
            "data": {
                "prompt": prompt,
                "options": options,
            },
        }
    else:
        # Unknown mode — build a minimal payload so the caller can still
        # surface something to the user rather than silently stalling.
        widget_payload = {
            "widget_type": "text_input",
            "target": "inline",
            "data": {"prompt": prompt, "fields": []},
        }

    return ConfirmationRequest(
        action_type=ConfirmationAction.WIDGET_RESPONSE,
        action_data=widget_payload,
        tool_call_id=secrets.token_hex(8),
        timeout=None,  # widget responses have no deadline
    )


def _make_on_response(ctx: Any, state: WorkflowState, mode: str) -> Any:
    """Build the on_response callback for a user_input widget pause.

    The callback loops over the user_input cycle: each user response is fed
    to ``engine.resume_user_input``; if the workflow lands at another
    user_input pause, the callback awaits ``ctx.request_confirmation`` for
    that pause directly and continues. Returns a summary string only when
    the workflow exits user_input (DONE, ERROR, paused at non-user-input).

    This keeps the entire interview cycle within one agent turn from the
    agent loop's perspective. The LLM never has to decide to call
    ``workflow_start`` to continue the cycle — code drives the loop.
    Aligned with the design thesis (code drives the process; LLM is a
    constrained worker on focused problems).
    """
    async def on_response(response_data: dict) -> str:
        user_input = _transform_response(response_data, mode)
        current_mode = mode

        log.info(
            "[workflow] user_input response received for conv %s: %s",
            ctx.conv_id, user_input,
        )

        while True:
            # Resume the workflow inline.
            # NOTE: resume_user_input removed in Phase 1; this callback is
            # dead code kept for Phase 4 cleanup when workflow_tools.py
            # is fully rewritten. Phase 3 deletes _make_on_response entirely.
            try:
                await engine.resume_user_input(ctx, state, user_input)  # type: ignore[attr-defined]
                log.info(
                    "[workflow] resume_user_input completed for conv %s, "
                    "new status: %s", ctx.conv_id, state.status.value,
                )
            except Exception:
                log.exception(
                    "[workflow] resume_user_input failed for conv %s; "
                    "returning partial summary (fail-open)", ctx.conv_id,
                )
                return _summary_for_response(user_input, current_mode)

            if state.status != RunStatus.PAUSED_USER_INPUT:
                # DONE, ERROR, or paused at a non-user-input step — done.
                return _summary_for_completion(state)

            # Workflow landed at another user_input pause. Build a new
            # ConfirmationRequest for this pause and await the user's
            # response without returning to the agent loop.
            current_mode = state.pending.get("mode", "")
            next_request = _build_confirmation_for_pause(state)
            log.info(
                "[workflow] new user_input pause for conv %s (mode=%r), "
                "awaiting confirmation directly",
                ctx.conv_id, current_mode,
            )
            next_response = await ctx.request_confirmation(next_request)
            if not next_response.approved:
                # Cancelled or denied.
                log.info(
                    "[workflow] user_input confirmation cancelled for conv %s",
                    ctx.conv_id,
                )
                return f"Workflow {state.workflow!r} cancelled during user input."

            user_input = _transform_response(next_response.data, current_mode)

    return on_response


def _build_paused_tool_result(ctx: Any, state: WorkflowState) -> ToolResult:
    """Build the appropriate ToolResult for a PAUSED_USER_INPUT workflow.

    Maps the pending mode to the right input widget:
    - ``"text"``   → ``text_input`` widget (free-form answer)
    - ``"choice"`` → ``multiple_choice`` widget (one option from a list)

    Returns a plain status ToolResult for unexpected modes.
    """
    pending = state.pending
    prompt = pending.get("prompt", "The workflow needs your input.")
    mode = pending.get("mode", "")

    if mode == "text":
        widget_data: dict = {
            "prompt": prompt,
            "fields": [
                {
                    "key": "value",
                    "label": "Your answer",
                    "multiline": True,
                    "required": True,
                }
            ],
        }
        return ToolResult(
            text=prompt,
            end_turn=True,
            widget=WidgetRequest(
                widget_type="text_input",
                data=widget_data,
                on_response=_make_on_response(ctx, state, "text"),
            ),
        )

    if mode == "choice":
        choices = pending.get("choices", [])
        options = [
            {"value": c["id"], "label": c["label"]}
            for c in choices
        ]
        widget_data = {
            "prompt": prompt,
            "options": options,
        }
        return ToolResult(
            text=prompt,
            end_turn=True,
            widget=WidgetRequest(
                widget_type="multiple_choice",
                data=widget_data,
                on_response=_make_on_response(ctx, state, "choice"),
            ),
        )

    # Unknown mode — fall back to a plain status message.
    log.warning(
        "[workflow] unknown user_input mode %r for workflow %r, conv %s",
        mode, state.workflow, ctx.conv_id,
    )
    return ToolResult(
        text=(
            f"Workflow **{state.workflow}** is paused waiting for "
            f"input (mode: {mode!r}). Status: {state.status.value}."
        ),
        data={"status": state.status.value, "pending": pending},
    )


async def tool_workflow_start(ctx, name: str) -> ToolResult:
    """Start a workflow by name. Runs until completion or suspension.

    Idempotent for the paused-user-input + same-name case: if the same
    workflow is already paused waiting for user input, re-renders the
    current pause widget so the agent's natural "start the workflow again"
    recovery instinct correctly surfaces the next question to the user.

    All other already-active cases (different name, running, error, or
    non-user-input suspension) return an error as before.
    """
    existing = load_workflow_state(ctx)
    if existing is not None:
        if (
            existing.workflow == name
            and existing.status == RunStatus.PAUSED_USER_INPUT
            and existing.pending.get("mode") in ("text", "choice")
        ):
            # Idempotent re-render: agent called workflow_start again after
            # the inline-resume advanced to a new pause. Return the new
            # pause's widget so it surfaces to the user.
            return _build_paused_tool_result(ctx, existing)
        return ToolResult(
            text=(
                f"[error: a workflow is already active in this conversation "
                f"(workflow={existing.workflow!r}, status={existing.status.value!r}); "
                f"call workflow_abort or wait for it to finish before starting another]"
            ),
        )

    try:
        state = await engine.start_workflow(ctx, name)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")
    except Exception as exc:
        log.error("[workflow_start] unexpected error: %s", exc)
        return ToolResult(text=f"[error: workflow {name!r} failed: {exc}]")

    if state.status == RunStatus.DONE:
        return ToolResult(
            text=f"Workflow **{name}** completed successfully.",
            data={"status": state.status.value, "state": state.state},
        )
    if state.status == RunStatus.ERROR:
        return ToolResult(
            text=f"Workflow **{name}** encountered an error.",
            data={"status": state.status.value},
        )
    if state.status == RunStatus.PAUSED_USER_INPUT:
        return _build_paused_tool_result(ctx, state)
    # PAUSED_SUBAGENT (and any future suspension kinds)
    return ToolResult(
        text=f"Workflow **{name}** is paused (status: {state.status.value}).",
        data={"status": state.status.value, "pending": state.pending},
    )


async def tool_workflow_status(ctx) -> ToolResult:
    """Return the current status of the active workflow in this conversation."""
    state = load_workflow_state(ctx)
    if state is None:
        return ToolResult(text="No active workflow in this conversation.")
    return ToolResult(
        text=f"Workflow **{state.workflow}** — status: {state.status.value}",
        data={
            "workflow": state.workflow,
            "run_id": state.run_id,
            "status": state.status.value,
            "current_step": state.current_step,
            "state_keys": list(state.state.keys()),
        },
    )


async def tool_workflow_abort(ctx, reason: str = "user requested abort") -> ToolResult:
    """Abort the active workflow in this conversation.

    Sets status to ERROR and appends an ``"aborted": True`` transition entry
    so downstream consumers can distinguish a deliberate user abort from an
    execution failure (which sets ERROR without that flag).  Check
    ``transitions[-1].get("aborted")`` to tell them apart.
    """
    state = load_workflow_state(ctx)
    if state is None:
        return ToolResult(text="No active workflow in this conversation.")
    state.transitions.append({
        "step": state.current_step,
        "ts": _now_iso(),
        "aborted": True,
        "reason": reason,
    })
    state.status = RunStatus.ERROR
    save_workflow_state(ctx, state)
    archive_workflow_state(ctx)
    return ToolResult(text=f"Workflow **{state.workflow}** aborted.")


async def tool_workflow_artifact_read(ctx, path: str) -> ToolResult:
    """Read a workflow artifact file. Path is relative to the artifacts/ root."""
    artifacts = artifacts_dir(ctx)
    full_path = (artifacts / path).resolve()
    # Security: ensure path stays within artifacts dir
    try:
        full_path.relative_to(artifacts.resolve())
    except ValueError:
        return ToolResult(text=f"[error: path {path!r} escapes artifacts directory]")
    if not full_path.is_file():
        return ToolResult(text=f"[error: artifact {path!r} not found]")
    try:
        content = full_path.read_text()
    except OSError as exc:
        return ToolResult(text=f"[error: could not read {path!r}: {exc}]")
    return ToolResult(text=content)


async def tool_workflow_artifact_write(ctx, path: str, content: str) -> ToolResult:
    """Write content to a workflow artifact file. Path is relative to artifacts/ root."""
    artifacts = artifacts_dir(ctx)
    full_path = (artifacts / path).resolve()
    # Security: ensure path stays within artifacts dir
    try:
        full_path.relative_to(artifacts.resolve())
    except ValueError:
        return ToolResult(text=f"[error: path {path!r} escapes artifacts directory]")
    full_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        full_path.write_text(content)
    except OSError as exc:
        return ToolResult(text=f"[error: could not write {path!r}: {exc}]")
    return ToolResult(text=f"Written {len(content)} bytes to artifact {path!r}.")


WORKFLOW_TOOLS = {
    # workflow_start is intentionally NOT registered here — workflows initiate
    # only via /command dispatch, scheduled tasks (kind: workflow), or subagent
    # steps. The function definition remains for reference; Phase 4 deletes it.
    "workflow_status": tool_workflow_status,
    "workflow_abort": tool_workflow_abort,
    "workflow_artifact_read": tool_workflow_artifact_read,
    "workflow_artifact_write": tool_workflow_artifact_write,
}

WORKFLOW_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_status",
            "description": (
                "Return the current status of the active workflow in this "
                "conversation. Use to check if a workflow is running, "
                "paused, or complete."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_abort",
            "description": (
                "Abort the currently active workflow. Archives the state "
                "so a new workflow can start."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_artifact_read",
            "description": (
                "Read a file written by a workflow step. Path is relative "
                "to the workflow's artifacts/ directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the artifacts/ directory.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_artifact_write",
            "description": (
                "Write content to a workflow artifact file. Use from "
                "within a subagent step to produce outputs declared in "
                "the step's outputs: list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within artifacts/.",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]
