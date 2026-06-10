"""WebSocket workflow-command intercept tests.

Regression coverage for #573 verification finding #2: the workflow-command
intercept in `_handle_send` enqueues a TurnKind.WORKFLOW turn but must also
archive the user's invocation as a `role: "user"` message, otherwise a
conversation reload shows zero messages for the entire workflow run.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_workflow_command_archives_user_invocation(config):
    """Sending `/interview` should write a role=user archive row with the
    typed text BEFORE enqueueing the WORKFLOW turn, so a conversation reload
    has something to render."""
    import decafclaw.workflow.workflows  # noqa: F401 — register interview
    from decafclaw.archive import read_archive
    from decafclaw.events import EventBus
    from decafclaw.web import websocket

    conv_id = "conv-wf-archive"

    enqueued: list[dict] = []

    class FakeManager:
        # `subscribe` is invoked by _subscribe_to_conv; return a sentinel id.
        def subscribe(self, conv_id, callback):
            return "sub-id"

        async def enqueue_turn(self, conv_id, **kw):
            enqueued.append({"conv_id": conv_id, **kw})

    async def ws_send(_msg):
        pass

    state = {
        "config": config,
        "event_bus": EventBus(),
        "manager": FakeManager(),
        "ws_send": ws_send,
    }
    index = MagicMock()
    conv = MagicMock()
    conv.user_id = "testuser"
    index.get.return_value = conv

    await websocket._handle_send(
        ws_send, index, "testuser",
        {"conv_id": conv_id, "text": "/interview"},
        state,
    )

    assert enqueued, "workflow intercept should enqueue a WORKFLOW turn"
    msgs = read_archive(config, conv_id)
    user_msgs = [m for m in msgs if m.get("role") == "user"]
    assert user_msgs, (
        "expected a role=user archive row for the /interview invocation"
    )
    assert user_msgs[0].get("content") == "/interview"
