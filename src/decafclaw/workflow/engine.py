"""run_workflow — runs an orchestrator once, classifying the outcome.

The engine owns control flow only in the trivial sense that it invokes the
orchestrator and interprets how it returned: completed, suspended for user
input, or errored. The orchestrator itself is plain async Python.
"""
import dataclasses
import logging
from typing import Any, Awaitable, Callable

from .errors import WorkflowNonDeterministic, WorkflowSuspended
from .handle import WorkflowHandle
from .journal import Journal, save_journal

log = logging.getLogger(__name__)


@dataclasses.dataclass
class WorkflowOutcome:
    status: str  # "done" | "suspended" | "error"
    result: Any = None
    suspend: WorkflowSuspended | None = None
    error: str = ""


async def run_workflow(
    ctx,
    workflow_fn: Callable[[WorkflowHandle], Awaitable[Any]],
    journal: Journal,
    *,
    llm_caller=None,
    model: str = "vertex-gemini-flash",
) -> WorkflowOutcome:
    handle = WorkflowHandle(ctx, journal, llm_caller=llm_caller, model=model)

    def _persist(status: str) -> None:
        journal.status = status
        save_journal(ctx.config, ctx.conv_id, journal)

    try:
        result = await workflow_fn(handle)
    except WorkflowSuspended as s:
        _persist("suspended")
        return WorkflowOutcome(status="suspended", suspend=s)
    except WorkflowNonDeterministic as e:
        log.error("workflow non-deterministic: %s", e)
        _persist("error")
        return WorkflowOutcome(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001 — terminal classification
        log.exception("workflow orchestrator raised")
        _persist("error")
        return WorkflowOutcome(status="error", error=str(e))

    _persist("done")
    return WorkflowOutcome(status="done", result=result)
