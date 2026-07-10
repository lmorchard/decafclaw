"""Unit tests for the eval-mode ConversationManager subclass (#536).

Covers the deterministic plumbing:
- Every new conversation the manager tracks gets an auto-confirm
  subscriber installed on first ``_get_or_create``.
- The subscriber resolves ``confirmation_request`` emits with the
  verdict passed to the subclass constructor.
- The resolver is defensive — errors from
  ``respond_to_confirmation`` log and don't propagate out of the
  emit gather.

``ctx.manager = manager`` wiring in ``run_test`` and the full LLM
end-to-end are exercised by ``evals/delegate.yaml`` under
``make eval`` — not this file.
"""

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.config import Config
from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest
from decafclaw.eval.runner import _EvalConversationManager
from decafclaw.events import EventBus


@pytest.mark.asyncio
async def test_new_conversation_gets_auto_confirm_subscriber():
    """``_get_or_create`` installs a subscriber on brand-new conv_ids."""
    manager = _EvalConversationManager(Config(), EventBus(), auto_confirm=True)
    state = manager._get_or_create("child-1")
    assert len(state.subscribers) == 1

    # A second access to the same conv_id doesn't stack subscribers.
    manager._get_or_create("child-1")
    assert len(state.subscribers) == 1


@pytest.mark.asyncio
async def test_auto_approve_resolves_pending_confirmation():
    """A ``confirmation_request`` on a tracked conv_id gets auto-approved."""
    manager = _EvalConversationManager(Config(), EventBus(), auto_confirm=True)
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        message="run 'echo hi'?",
        timeout=5.0,
    )
    # Fire and await the manager's request_confirmation. The auto-confirm
    # subscriber will resolve it via respond_to_confirmation before the
    # timeout.
    response = await manager.request_confirmation("child-abc", request)
    assert response.approved is True


@pytest.mark.asyncio
async def test_auto_deny_when_auto_confirm_false():
    """setup.auto_confirm=false → auto-deny propagates through the manager."""
    manager = _EvalConversationManager(Config(), EventBus(), auto_confirm=False)
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        message="run 'rm -rf /'?",
        timeout=5.0,
    )
    response = await manager.request_confirmation("child-xyz", request)
    assert response.approved is False


@pytest.mark.asyncio
async def test_nested_child_conv_ids_also_get_subscribed():
    """A deep delegate chain (grandchild etc.) gets auto-confirm on each
    level — every ``_get_or_create`` call installs a subscriber."""
    manager = _EvalConversationManager(Config(), EventBus(), auto_confirm=True)
    for conv_id in ("child-1", "child-2", "grandchild-1"):
        state = manager._get_or_create(conv_id)
        assert len(state.subscribers) == 1, (
            f"expected auto-confirm subscriber on {conv_id}, "
            f"got {len(state.subscribers)}"
        )


@pytest.mark.asyncio
async def test_resolver_ignores_non_confirmation_events():
    """The subscriber only fires for ``confirmation_request`` — other
    event types (tool_status, stream chunks, etc.) pass through untouched."""
    manager = _EvalConversationManager(Config(), EventBus(), auto_confirm=True)
    state = manager._get_or_create("child-noise")
    # Grab the resolver and hand-drive it with a non-confirmation event.
    resolver = next(iter(state.subscribers.values()))
    # Should not raise, should not touch pending_confirmation state.
    await resolver({"type": "tool_status", "message": "working"})
    assert state.pending_confirmation is None


@pytest.mark.asyncio
async def test_resolver_error_is_logged_not_raised(caplog):
    """The resolver catches exceptions from ``respond_to_confirmation`` and
    logs them — a raise would propagate out of the manager's emit gather
    (which uses ``return_exceptions=True``) into stderr noise and mask
    the real test failure."""
    manager = _EvalConversationManager(Config(), EventBus(), auto_confirm=True)
    state = manager._get_or_create("child-boom")
    resolver = next(iter(state.subscribers.values()))

    boom = AsyncMock(side_effect=RuntimeError("simulated failure"))
    with patch.object(manager, "respond_to_confirmation", boom):
        # Should not raise. The resolver's try/except catches and logs.
        await resolver({
            "type": "confirmation_request",
            "confirmation_id": "abc123",
        })

    boom.assert_awaited_once()
    assert any(
        "Eval auto-confirm resolver failed" in rec.message
        for rec in caplog.records
    ), f"expected resolver failure log; got {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_resolver_skips_events_missing_confirmation_id():
    """A ``confirmation_request`` emit without a confirmation_id is
    ignored — no exception, no call to ``respond_to_confirmation``."""
    manager = _EvalConversationManager(Config(), EventBus(), auto_confirm=True)
    state = manager._get_or_create("child-no-cid")
    resolver = next(iter(state.subscribers.values()))

    spy = AsyncMock()
    with patch.object(manager, "respond_to_confirmation", spy):
        await resolver({"type": "confirmation_request"})
        await resolver({"type": "confirmation_request", "confirmation_id": ""})
    spy.assert_not_called()
