"""Confirmation types, serialization, and handler registry."""

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


@dataclass
class ConfirmationRequest:
    """A confirmation request that can be persisted in conversation history."""
    action_type: ConfirmationAction
    action_data: dict = field(default_factory=dict)
    message: str = ""
    approve_label: str = "Approve"
    deny_label: str = "Deny"
    tool_call_id: str = ""
    timeout: float = 300.0
    confirmation_id: str = field(default_factory=lambda: uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_archive_message(self) -> dict:
        """Serialize to a dict suitable for JSONL archive."""
        return {
            "role": "confirmation_request",
            "confirmation_id": self.confirmation_id,
            "action_type": self.action_type.value,
            "action_data": self.action_data,
            "message": self.message,
            "approve_label": self.approve_label,
            "deny_label": self.deny_label,
            "tool_call_id": self.tool_call_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_archive_message(cls, msg: dict) -> "ConfirmationRequest":
        """Deserialize from an archive message dict."""
        return cls(
            action_type=ConfirmationAction(msg["action_type"]),
            action_data=msg.get("action_data", {}),
            message=msg.get("message", ""),
            approve_label=msg.get("approve_label", "Approve"),
            deny_label=msg.get("deny_label", "Deny"),
            tool_call_id=msg.get("tool_call_id", ""),
            confirmation_id=msg["confirmation_id"],
            timestamp=msg.get("timestamp", ""),
        )


@dataclass
class ConfirmationResponse:
    """A confirmation response that can be persisted in conversation history."""
    confirmation_id: str
    approved: bool
    always: bool = False
    add_pattern: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_archive_message(self) -> dict:
        """Serialize to a dict suitable for JSONL archive."""
        msg: dict[str, Any] = {
            "role": "confirmation_response",
            "confirmation_id": self.confirmation_id,
            "approved": self.approved,
            "timestamp": self.timestamp,
        }
        if self.always:
            msg["always"] = True
        if self.add_pattern:
            msg["add_pattern"] = True
        return msg

    @classmethod
    def from_archive_message(cls, msg: dict) -> "ConfirmationResponse":
        """Deserialize from an archive message dict."""
        return cls(
            confirmation_id=msg["confirmation_id"],
            approved=msg.get("approved", False),
            always=msg.get("always", False),
            add_pattern=msg.get("add_pattern", False),
            timestamp=msg.get("timestamp", ""),
        )


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
