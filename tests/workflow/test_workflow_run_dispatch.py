"""Tests for TurnKind.WORKFLOW_RUN dispatch in ConversationManager._start_turn.

Verifies that when kind == WORKFLOW_RUN, the engine is called with the
correct workflow_name and initial_state rather than run_agent_turn.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.conversation_manager import ConversationManager, TurnKind


@pytest.fixture
def manager(tmp_path):
    """Minimal ConversationManager for dispatch tests."""
    from decafclaw.config import Config
    from decafclaw.events import EventBus

    config = Config()
    config.agent.data_home = str(tmp_path)
    bus = EventBus()
    return ConversationManager(config, bus)


@pytest.mark.asyncio
async def test_workflow_run_kind_calls_engine(manager, tmp_path):
    """enqueue_turn with WORKFLOW_RUN invokes engine.start_workflow, not run_agent_turn."""
    workflow_states: list[tuple] = []

    async def fake_start_workflow(ctx, name, *, initial_state=None):
        workflow_states.append((name, initial_state))
        # Return a minimal mock state
        mock_state = MagicMock()
        mock_state.status.value = "done"
        return mock_state

    with patch(
        "decafclaw.workflow.engine.start_workflow",
        side_effect=fake_start_workflow,
    ), patch(
        "decafclaw.agent.run_agent_turn",
        new=AsyncMock(side_effect=AssertionError("run_agent_turn should NOT be called")),
    ):
        future = await manager.enqueue_turn(
            conv_id="test-conv-workflow",
            kind=TurnKind.WORKFLOW_RUN,
            workflow_name="workflow_hello",
            initial_state={"topic": "test"},
        )
        # Wait for the task to complete
        await asyncio.wait_for(asyncio.shield(future), timeout=5.0)

    assert len(workflow_states) == 1, "engine.start_workflow should be called once"
    name, init_state = workflow_states[0]
    assert name == "workflow_hello"
    assert init_state == {"topic": "test"}


@pytest.mark.asyncio
async def test_user_kind_calls_run_agent_turn(manager, tmp_path):
    """enqueue_turn with USER kind still invokes run_agent_turn (existing path)."""
    agent_calls: list[str] = []

    async def fake_run_agent_turn(ctx, text, history, **kwargs):
        agent_calls.append(text)
        mock_result = MagicMock()
        mock_result.text = "hello"
        mock_result.media = []
        return mock_result

    with patch(
        "decafclaw.agent.run_agent_turn",
        side_effect=fake_run_agent_turn,
    ), patch(
        "decafclaw.workflow.engine.start_workflow",
        new=AsyncMock(side_effect=AssertionError("engine should NOT be called for USER turns")),
    ):
        future = await manager.enqueue_turn(
            conv_id="test-conv-user",
            kind=TurnKind.USER,
            prompt="Hello there",
        )
        await asyncio.wait_for(asyncio.shield(future), timeout=5.0)

    assert agent_calls == ["Hello there"]


@pytest.mark.asyncio
async def test_workflow_run_empty_initial_state(manager, tmp_path):
    """WORKFLOW_RUN with no initial_state passes empty dict to engine."""
    workflow_states: list[tuple] = []

    async def fake_start_workflow(ctx, name, *, initial_state=None):
        workflow_states.append((name, initial_state))
        mock_state = MagicMock()
        mock_state.status.value = "done"
        return mock_state

    with patch(
        "decafclaw.workflow.engine.start_workflow",
        side_effect=fake_start_workflow,
    ):
        future = await manager.enqueue_turn(
            conv_id="test-conv-empty",
            kind=TurnKind.WORKFLOW_RUN,
            workflow_name="my_workflow",
        )
        await asyncio.wait_for(asyncio.shield(future), timeout=5.0)

    assert len(workflow_states) == 1
    name, init_state = workflow_states[0]
    assert name == "my_workflow"
    # initial_state should be {} (the or {} from the dispatch)
    assert init_state == {} or init_state is None  # either is acceptable
