"""Harness glue: run a workflow turn, and resume it after user input.

run_workflow_turn is invoked by ConversationManager._start_turn for
TurnKind.WORKFLOW. WorkflowUserInputHandler is registered on the
ConfirmationRegistry; it fires via the confirmation *recovery* path
(no awaiting waiter) because a workflow suspend ends the turn.
"""
import logging

from decafclaw.confirmations import (
    ConfirmationAction,
    ConfirmationRequest,
    ConfirmationResponse,
)
from decafclaw.media import ToolResult

from .engine import run_workflow
from .journal import Journal, load_journal, save_journal
from .registry import get_workflow

log = logging.getLogger(__name__)


def _render_artifact(result) -> str:
    if isinstance(result, dict) and "title" in result and "body" in result:
        return f"# {result['title']}\n\n{result['body']}"
    return str(result)


async def run_workflow_turn(ctx, manager, *,
                            workflow_name: str, resume: bool) -> ToolResult:
    spec = get_workflow(workflow_name)
    if spec is None:
        return ToolResult(text=f"[error: unknown workflow {workflow_name!r}]")

    journal = load_journal(ctx.config, ctx.conv_id)
    if journal is None:
        if resume:
            return ToolResult(text="[error: no workflow journal to resume]")
        journal = Journal(workflow_name=workflow_name)

    await ctx.publish("tool_status", tool="workflow",
                      message=f"[workflow: {workflow_name}] running")
    outcome = await run_workflow(ctx, spec.fn, journal, model=spec.model)

    if outcome.status == "done":
        await ctx.publish("tool_status", tool="workflow",
                          message=f"[workflow: {workflow_name}] complete")
        artifact_text = _render_artifact(outcome.result)
        # Persist the rendered artifact so a conversation reload still
        # shows the workflow's final output (the WORKFLOW turn path bypasses
        # the agent loop's normal assistant-message archive write).
        from decafclaw.archive import append_message  # noqa: PLC0415
        append_message(ctx.config, ctx.conv_id,
                       {"role": "assistant", "content": artifact_text})
        return ToolResult(text=artifact_text)

    if outcome.status == "suspended":
        assert outcome.suspend is not None  # guaranteed by engine when status="suspended"
        s = outcome.suspend
        request = ConfirmationRequest(
            action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
            message=s.prompt,
            action_data={
                "workflow_name": workflow_name,
                "seq": s.seq,
                "args_fingerprint": s.args_fingerprint,
                "prompt": s.prompt,
                "choices": s.choices,
            },
            timeout=None,
        )
        await manager.post_confirmation(ctx.conv_id, request)
        return ToolResult(text=f"_{s.prompt}_")

    return ToolResult(text=f"[error: workflow failed: {outcome.error}]")


class WorkflowUserInputHandler:
    """Resolves a WORKFLOW_USER_INPUT confirmation via the recovery path."""

    def __init__(self, manager):
        self.manager = manager

    async def on_approve(self, ctx, request: ConfirmationRequest,
                         response: ConfirmationResponse) -> dict:
        ad = request.action_data
        answer = (response.data or {}).get("value", "")
        journal = load_journal(ctx.config, ctx.conv_id)
        if journal is None:
            log.error("workflow resume: no journal for conv %s", ctx.conv_id)
            return {"continue_loop": False}
        journal.append(ad["seq"], "user_input", ad["args_fingerprint"], answer)
        journal.status = "running"
        save_journal(ctx.config, ctx.conv_id, journal)

        # Lazy import to avoid a circular import (conversation_manager
        # will import this module once TurnKind.WORKFLOW dispatch is wired).
        from decafclaw.conversation_manager import TurnKind  # noqa: PLC0415
        await self.manager.enqueue_turn(
            ctx.conv_id, kind=TurnKind.WORKFLOW, prompt="",
            metadata={"workflow_name": ad["workflow_name"], "resume": True})
        return {"continue_loop": False}

    async def on_deny(self, ctx, request: ConfirmationRequest,
                      response: ConfirmationResponse) -> dict:
        ad = request.action_data
        journal = load_journal(ctx.config, ctx.conv_id)
        if journal is not None:
            journal.status = "error"
            save_journal(ctx.config, ctx.conv_id, journal)
        from decafclaw.archive import append_message  # noqa: PLC0415
        append_message(ctx.config, ctx.conv_id, {
            "role": "assistant",
            "source": "workflow_cancelled",
            "content": f"Workflow {ad.get('workflow_name', '?')!r} cancelled.",
        })
        return {"continue_loop": False}
