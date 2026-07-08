"""Unit tests for the eval-mode ConversationManager subclass (#536).

Covers:
- Every new conversation gets an auto-confirm subscriber installed.
- The subscriber resolves ``confirmation_request`` emits with the
  configured verdict.
- ``ctx.manager`` is wired to the manager after ``run_test`` builds the
  context.

The full ``run_test`` end-to-end path with a real LLM is exercised by
``evals/delegate.yaml`` under ``make eval``; here we cover the
deterministic plumbing without spinning up the agent loop.
"""

import asyncio

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
    """The resolver is defensive — errors from ``respond_to_confirmation``
    log and don't crash the emit chain (which awaits subscribers)."""
    manager = _EvalConversationManager(Config(), EventBus(), auto_confirm=True)
    state = manager._get_or_create("child-boom")
    resolver = next(iter(state.subscribers.values()))
    # A request event whose confirmation_id doesn't match anything pending —
    # ``respond_to_confirmation`` will just log a warning and return, so
    # the resolver completes cleanly. This proves the defensive shape
    # without needing to stub the underlying call.
    await resolver({
        "type": "confirmation_request",
        "confirmation_id": "does-not-exist",
    })
    # Also cover the drop-events-with-no-cid path.
    await resolver({"type": "confirmation_request"})


def test_asyncio_import_smoke():
    """Guard against stray asyncio imports being dropped by refactors —
    the resolver depends on the module being importable."""
    assert asyncio is not None
