from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.config import Config
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _handle_load_history, _handle_set_mode, _subscribe_to_conv


@pytest.fixture
def ws_context(tmp_path):
    config = Config()
    config.agent.data_home = str(tmp_path)
    bus = EventBus()
    manager = ConversationManager(config, bus)

    sent = []

    state = {
        "config": config,
        "manager": manager,
        "conv_subscriptions": {},
    }
    index = {
        "conv-1": MagicMock(user_id="alice", conv_id="conv-1"),
    }
    return state, index, sent, manager, config


@pytest.mark.asyncio
async def test_load_history_includes_mode_info(ws_context):
    state, index, sent, manager, config = ws_context

    conv_state = manager._get_or_create("conv-1")
    conv_state.persisted.active_mode = "dev"
    conv_state.persisted.active_aux_approval_presets = ["developer", "github"]

    ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
    state["ws_send"] = ws_mock

    await _handle_load_history(
        ws_mock,
        index,
        "alice",
        {"conv_id": "conv-1"},
        state,
    )

    history_msg = next(m for m in sent if m.get("type") == WSMessageType.CONV_HISTORY)
    assert history_msg["active_mode"] == "dev"
    assert "available_modes" in history_msg
    mode_names = [m["name"] for m in history_msg["available_modes"]]
    assert "default" in mode_names
    assert "dev" in mode_names
    assert "research" in mode_names
    assert "admin" in mode_names


@pytest.mark.asyncio
async def test_load_history_detects_custom_mode(ws_context):
    state, index, sent, manager, config = ws_context

    conv_state = manager._get_or_create("conv-1")
    conv_state.persisted.active_mode = "dev"
    # Presets diverged from dev mode's ["developer", "github"]
    conv_state.persisted.active_aux_approval_presets = ["developer"]

    ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
    state["ws_send"] = ws_mock

    await _handle_load_history(
        ws_mock,
        index,
        "alice",
        {"conv_id": "conv-1"},
        state,
    )

    history_msg = next(m for m in sent if m.get("type") == WSMessageType.CONV_HISTORY)
    assert history_msg["active_mode"] == "custom"


@pytest.mark.asyncio
async def test_set_mode_validation(ws_context):
    state, index, sent, manager, config = ws_context

    # Unknown mode
    await _handle_set_mode(
        AsyncMock(side_effect=lambda m: sent.append(m)),
        index,
        "alice",
        {"conv_id": "conv-1", "mode": "unknown-mode"},
        state,
    )
    err = next(m for m in sent if m.get("type") == WSMessageType.ERROR)
    assert "Unknown mode: unknown-mode" in err["message"]


@pytest.mark.asyncio
async def test_set_mode_rejected_when_busy(ws_context):
    state, index, sent, manager, config = ws_context

    conv_state = manager._get_or_create("conv-1")
    conv_state.busy = True
    conv_state.persisted.active_mode = "default"

    await _handle_set_mode(
        AsyncMock(side_effect=lambda m: sent.append(m)),
        index,
        "alice",
        {"conv_id": "conv-1", "mode": "dev"},
        state,
    )
    # When busy, server rejects by echoing current mode via MODE_CHANGED without corrupting client busy state
    resp = next(m for m in sent if m.get("type") == WSMessageType.MODE_CHANGED)
    assert resp["mode"] == "default"
    assert conv_state.persisted.active_mode == "default"


@pytest.mark.asyncio
async def test_set_mode_success(ws_context):
    state, index, sent, manager, config = ws_context

    conv_state = manager._get_or_create("conv-1")
    conv_state.persisted.llm_approved_shell_patterns = ["some-pattern"]

    ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
    state["ws_send"] = ws_mock
    _subscribe_to_conv(state, "conv-1")

    await _handle_set_mode(
        ws_mock,
        index,
        "alice",
        {"conv_id": "conv-1", "mode": "dev"},
        state,
    )

    assert conv_state.persisted.active_mode == "dev"
    assert set(conv_state.persisted.active_aux_approval_presets) == {"developer", "github"}
    assert conv_state.persisted.llm_approved_shell_patterns == []

    mode_changed = next(m for m in sent if m.get("type") == WSMessageType.MODE_CHANGED)
    assert mode_changed["mode"] == "dev"
    assert "developer" in mode_changed["presets"]
    assert "workspace_diff" in mode_changed["promoted_tools"]
