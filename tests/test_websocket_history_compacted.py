"""Web history serves the compacted working view, not the raw archive (#1823)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.archive import append_message, read_archive, write_compacted_history
from decafclaw.compaction import SUMMARY_PREFIX
from decafclaw.config import Config
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _handle_load_history

CONV_ID = "conv-history"


@pytest.fixture
def ws_context(tmp_path):
    config = Config()
    config.agent.data_home = str(tmp_path)
    config.agent.workspace_path = str(tmp_path / "workspace")
    bus = EventBus()
    manager = ConversationManager(config, bus)
    sent = []
    state = {
        "config": config,
        "manager": manager,
        "event_bus": bus,
        "conv_subscriptions": {},
    }
    index = {CONV_ID: MagicMock(user_id="alice", conv_id=CONV_ID)}
    return state, index, sent, config


async def _load(state, index, sent, *, limit=50, before=""):
    ws_mock = AsyncMock(side_effect=lambda m: sent.append(m))
    state["ws_send"] = ws_mock
    payload = {"conv_id": CONV_ID, "limit": limit}
    if before:
        payload["before"] = before
    await _handle_load_history(ws_mock, index, "alice", payload, state)
    return [m for m in sent if m.get("type") == WSMessageType.CONV_HISTORY][-1]


def _seed_compacted(config):
    """Four archived turns, with a sidecar summarizing the first three."""
    for i in range(4):
        append_message(config, CONV_ID, {"role": "user", "content": f"old {i}"})
        append_message(config, CONV_ID, {"role": "assistant", "content": f"old reply {i}"})
    archived = read_archive(config, CONV_ID)
    write_compacted_history(
        config,
        CONV_ID,
        [
            {"role": "user", "content": f"{SUMMARY_PREFIX}Earlier turns summarized."},
            archived[-2],
            archived[-1],
        ],
    )
    return archived


@pytest.mark.asyncio
async def test_pages_the_compacted_view_and_hides_the_summary(ws_context):
    state, index, sent, config = ws_context
    _seed_compacted(config)

    frame = await _load(state, index, sent)

    contents = [m.get("content") for m in frame["messages"]]
    assert contents == ["old 3", "old reply 3"]
    assert all(not str(c).startswith(SUMMARY_PREFIX) for c in contents)


@pytest.mark.asyncio
async def test_includes_rows_appended_after_compaction(ws_context):
    state, index, sent, config = ws_context
    _seed_compacted(config)
    append_message(config, CONV_ID, {"role": "user", "content": "new question"})

    frame = await _load(state, index, sent)

    assert "new question" in [m.get("content") for m in frame["messages"]]


@pytest.mark.asyncio
async def test_pagination_does_not_resurrect_summarized_turns(ws_context):
    state, index, sent, config = ws_context
    archived = _seed_compacted(config)

    first = await _load(state, index, sent, limit=1)
    assert first["has_more"] is True
    assert [m.get("content") for m in first["messages"]] == ["old reply 3"]

    older = await _load(state, index, sent, limit=1, before=archived[-1]["timestamp"])
    assert older["has_more"] is False
    assert [m.get("content") for m in older["messages"]] == ["old 3"]
