"""Confirmation types, serialization, and handler registry."""

import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

log = logging.getLogger(__name__)


class ConfirmationAction(str, Enum):
    """Known confirmation action types. Each maps to a registered handler."""
    RUN_SHELL_COMMAND = "run_shell_command"
    ACTIVATE_SKILL = "activate_skill"
    CONTINUE_TURN = "continue_turn"
    ADVANCE_PROJECT_PHASE = "advance_project_phase"
    WIDGET_RESPONSE = "widget_response"


@dataclass
class ConfirmationRequest:
    """A confirmation request that can be persisted in conversation history.

    ``timeout=None`` disables the await deadline — used by widget requests
    where the user responds when ready.
    """
    action_type: ConfirmationAction
    action_data: dict = field(default_factory=dict)
    message: str = ""
    approve_label: str = "Approve"
    deny_label: str = "Deny"
    tool_call_id: str = ""
    timeout: float | None = 300.0
    confirmation_id: str = field(default_factory=lambda: uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_archive_message(self) -> dict:
        """Serialize to a dict suitable for JSONL archive."""
        msg = dataclasses.asdict(self)
        msg["role"] = "confirmation_request"
        # Convert enum to str so JSON serialization stays explicit, regardless
        # of which encoder downstream uses.
        msg["action_type"] = self.action_type.value
        return msg

    @classmethod
    def from_archive_message(cls, msg: dict) -> "ConfirmationRequest":
        """Deserialize from an archive message dict."""
        # Filter to known fields. Drops the "role" tag and provides forward
        # compat — keys written by a future agent version that this build
        # doesn't recognize are ignored. Missing keys fall back to dataclass
        # defaults, so older archives written before a field was added
        # remain readable (backward compat).
        field_names = {f.name for f in dataclasses.fields(cls)}
        kwargs = {k: v for k, v in msg.items() if k in field_names}
        kwargs["action_type"] = ConfirmationAction(kwargs["action_type"])
        return cls(**kwargs)


@dataclass
class ConfirmationResponse:
    """A confirmation response that can be persisted in conversation history.

    ``data`` carries free-form response payload for actions that need more
    than approve/deny — e.g., widget submissions send their selected
    values here.
    """
    confirmation_id: str
    approved: bool
    always: bool = False
    add_pattern: bool = False
    data: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_archive_message(self) -> dict:
        """Serialize to a dict suitable for JSONL archive."""
        msg = dataclasses.asdict(self)
        msg["role"] = "confirmation_response"
        # Drop falsy optional flags / containers to keep archive output lean;
        # from_archive tolerates their absence via the dataclass defaults.
        if not msg["always"]:
            msg.pop("always")
        if not msg["add_pattern"]:
            msg.pop("add_pattern")
        if not msg["data"]:
            msg.pop("data")
        return msg

    @classmethod
    def from_archive_message(cls, msg: dict) -> "ConfirmationResponse":
        """Deserialize from an archive message dict."""
        field_names = {f.name for f in dataclasses.fields(cls)}
        kwargs = {k: v for k, v in msg.items() if k in field_names}
        return cls(**kwargs)


class ConfirmationHandler(Protocol):
    """Protocol for confirmation action handlers.

    Handlers return a dict that the agent loop uses to determine next steps.
    Keys may include:
    - inject_message: str — message to inject into history for the LLM
    - continue_loop: bool — whether the agent loop should continue iterating
    - result: Any — action-specific result data (e.g., shell command output)
    """
    async def on_approve(self, ctx: Any, request: ConfirmationRequest,
                         response: ConfirmationResponse) -> dict: ...
    async def on_deny(self, ctx: Any, request: ConfirmationRequest,
                      response: ConfirmationResponse) -> dict: ...


class ConfirmationRegistry:
    """Registry of confirmation action handlers.

    Each ConfirmationAction maps to a handler that knows how to execute
    the action on approval and what to do on denial. This makes
    confirmations recoverable after server restart — the action type
    and data are enough to reconstruct the handler's behavior.
    """

    def __init__(self):
        self._handlers: dict[ConfirmationAction, ConfirmationHandler] = {}

    def register(self, action_type: ConfirmationAction,
                 handler: ConfirmationHandler) -> None:
        """Register a handler for an action type."""
        self._handlers[action_type] = handler

    def get_handler(self, action_type: ConfirmationAction
                    ) -> ConfirmationHandler | None:
        """Look up the handler for an action type."""
        return self._handlers.get(action_type)

    async def dispatch(self, ctx: Any, request: ConfirmationRequest,
                       response: ConfirmationResponse) -> dict:
        """Dispatch a confirmation response to the appropriate handler.

        Returns the handler's result dict, or a default dict if no handler
        is registered.
        """
        handler = self._handlers.get(request.action_type)
        if handler is None:
            log.warning("No handler for confirmation action %s",
                        request.action_type)
            return {"continue_loop": response.approved}

        if response.approved:
            return await handler.on_approve(ctx, request, response)
        else:
            return await handler.on_deny(ctx, request, response)
