"""Mixed-timestamp archives: newest turns must not vanish (#1829).

Pre-#1829 archives hold two timestamp shapes in the same file:

* naive local time — ``2026-11-25T00:23:51.207302`` (``archive.append_message``)
* UTC with an offset — ``2026-11-25T08:22:53.077847+00:00`` (background
  events, confirmation requests)

Lexicographic comparison orders the naive form *before* the UTC form by the
host's UTC offset, so a naive row written after a UTC boundary compared as
older and was dropped. These tests pin the ordering behaviour, not the
wall-clock value.
"""

import json

import pytest

from decafclaw.archive import (
    append_message,
    now_iso,
    parse_timestamp,
    restore_history,
)
from decafclaw.compaction import SUMMARY_PREFIX
from decafclaw.config import Config
from decafclaw.config_types import ModelConfig
from decafclaw.context_sidecars import write_context_sidecar
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _handle_load_history

CONV_ID = "conv-mixed-ts"


# -- parse_timestamp ---------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-25T08:22:53.077847+00:00",
        "2026-11-25T08:22:53Z",
        "2026-11-25T08:22:53",
    ],
)
def test_parse_timestamp_accepts_every_written_shape(value):
    """All three on-disk shapes parse to the same aware UTC instant."""
    parsed = parse_timestamp(value)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert (parsed.year, parsed.hour, parsed.minute) == (2026, 8, 22)


@pytest.mark.parametrize("value", [None, "", "not-a-timestamp"])
def test_parse_timestamp_returns_none_when_unusable(value):
    assert parse_timestamp(value) is None


def test_parse_timestamp_orders_mixed_shapes_consistently():
    """The bug in one assertion: naive-local must not sort before UTC."""
    local_naive = "2026-11-25T00:23:51.207302"
    utc_later = "2026-11-25T00:30:00Z"
    assert parse_timestamp(local_naive) < parse_timestamp(utc_later)


def test_now_iso_is_utc_aware():
    parsed = parse_timestamp(now_iso())
    assert parsed is not None
    assert parsed.utcoffset().total_seconds() == 0


# -- restore_history ---------------------------------------------------------


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


@pytest.fixture
def config(tmp_path):
    cfg = Config()
    cfg.agent.data_home = str(tmp_path)
    cfg.agent.workspace_path = str(tmp_path / "workspace")
    return cfg


def _conv_dir(config):
    return config.workspace_path / "conversations"


def test_restore_history_keeps_naive_rows_after_utc_boundary(config):
    """A naive-local row after a UTC sidecar boundary is still restored."""
    conv = _conv_dir(config) / CONV_ID
    sidecar = [
        {"role": "user", "content": f"{SUMMARY_PREFIX}summary", "timestamp": "2026-11-25T00:10:00"},
        {"role": "assistant", "content": "recent", "timestamp": "2026-11-25T00:20:00"},
    ]
    archive = [
        *sidecar,
        # Written after the boundary, but naive-local: string-compares as older.
        {"role": "user", "content": "typed after compaction", "timestamp": "2026-11-25T00:23:51.207302"},
    ]
    _write_jsonl(conv / "compacted.jsonl", sidecar)
    _write_jsonl(conv / "archive.jsonl", archive)

    restored = restore_history(config, CONV_ID)

    assert restored is not None
    assert restored[-1]["content"] == "typed after compaction"


def test_restore_history_still_excludes_metadata_roles(config):
    """The #1829 rewrite must not resurrect compaction/model/mode rows."""
    conv = _conv_dir(config) / CONV_ID
    sidecar = [{"role": "user", "content": f"{SUMMARY_PREFIX}s", "timestamp": "2026-11-25T00:10:00"}]
    archive = [
        *sidecar,
        {"role": "model", "content": "x/y", "timestamp": "2026-11-25T00:15:00"},
        {"role": "compaction", "content": "compacted", "timestamp": "2026-11-25T00:16:00"},
        {"role": "user", "content": "kept", "timestamp": "2026-11-25T00:20:00"},
    ]
    _write_jsonl(conv / "compacted.jsonl", sidecar)
    _write_jsonl(conv / "archive.jsonl", archive)

    restored = restore_history(config, CONV_ID)

    assert [r["role"] for r in restored] == ["user", "user"]
    assert restored[-1]["content"] == "kept"


# -- web history -------------------------------------------------------------


@pytest.fixture
def ws_context(config):
    bus = EventBus()
    manager = ConversationManager(config, bus)
    state = {"config": config, "manager": manager, "event_bus": bus, "conv_subscriptions": {}}
    index = {CONV_ID: _index_entry(CONV_ID)}
    return state, index


def _index_entry(conv_id):
    from unittest.mock import MagicMock

    return MagicMock(user_id="alice", conv_id=conv_id)


async def _load_history(state, index, *, limit=50, before=""):
    sent = []

    async def ws_send(msg):
        sent.append(msg)

    # _handle_load_history reuses the websocket's own send callable for
    # follow-up frames, so it must be on state before the call.
    state["ws_send"] = ws_send
    payload = {"conv_id": CONV_ID, "limit": limit}
    if before:
        payload["before"] = before
    await _handle_load_history(ws_send, index, "alice", payload, state)
    return [m for m in sent if m.get("type") == WSMessageType.CONV_HISTORY][-1]


async def test_web_history_includes_turns_typed_after_compaction(ws_context, config):
    """The reported bug: post-compaction turns disappear on reload."""
    state, index = ws_context
    conv = _conv_dir(config) / CONV_ID
    sidecar = [
        {"role": "user", "content": f"{SUMMARY_PREFIX}summary", "timestamp": "2026-11-25T00:10:00"},
        {"role": "assistant", "content": "recent", "timestamp": "2026-11-25T00:20:00"},
    ]
    archive = [
        *sidecar,
        {"role": "user", "content": "my new turn", "timestamp": "2026-11-25T00:23:51.207302"},
        {"role": "assistant", "content": "my new answer", "timestamp": "2026-11-25T00:24:03.355323"},
    ]
    _write_jsonl(conv / "compacted.jsonl", sidecar)
    _write_jsonl(conv / "archive.jsonl", archive)

    history = await _load_history(state, index)

    contents = [m.get("content") for m in history["messages"]]
    assert "my new turn" in contents
    assert "my new answer" in contents


async def test_web_history_budget_ignores_background_event_noise(ws_context, config):
    """Job cards must not crowd real turns out of a small page (#1829)."""
    state, index = ws_context
    conv = _conv_dir(config) / CONV_ID
    rows = []
    for i in range(3):
        rows.append({"role": "user", "content": f"turn {i}", "timestamp": f"2026-11-25T00:0{i}:00+00:00"})
        for j in range(5):
            rows.append(
                {
                    "role": "background_event",
                    "content": f"job {j}",
                    "timestamp": f"2026-11-25T00:0{i}:3{j}:00+00:00",
                }
            )
    _write_jsonl(conv / "archive.jsonl", rows)

    # limit=2 counts conversation rows only; background cards ride along.
    history = await _load_history(state, index, limit=2)

    contents = [m.get("content") for m in history["messages"]]
    assert "turn 2" in contents
    assert "turn 1" in contents
    assert "turn 0" not in contents
    assert history["has_more"] is True


async def test_web_history_before_cursor_parses_mixed_shapes(ws_context, config):
    """A cursor from a naive row still pages correctly against UTC rows."""
    state, index = ws_context
    conv = _conv_dir(config) / CONV_ID
    rows = [
        {"role": "user", "content": "old", "timestamp": "2026-11-25T00:01:00+00:00"},
        {"role": "user", "content": "middle", "timestamp": "2026-11-25T00:02:00"},
        {"role": "user", "content": "newest", "timestamp": "2026-11-25T00:03:00Z"},
    ]
    _write_jsonl(conv / "archive.jsonl", rows)

    history = await _load_history(state, index, before="2026-11-25T00:02:00")

    contents = [m.get("content") for m in history["messages"]]
    assert contents == ["old"]


# -- context meter basis on reload (#1275) ------------------------------------


async def test_conv_history_uses_model_window_and_last_usage(ws_context, config):
    """Reload uses the model's window and the last turn's recorded usage."""
    state, index = ws_context
    config.model_configs["big"] = ModelConfig(provider="scripted", model="x", context_window_size=200_000)
    config.default_model = "big"
    conv = _conv_dir(config) / CONV_ID
    _write_jsonl(
        conv / "archive.jsonl",
        [{"role": "user", "content": "hello", "timestamp": "2026-11-25T00:00:00"}],
    )
    write_context_sidecar(config, CONV_ID, {"context_usage": 12345})

    history = await _load_history(state, index)

    assert history["context_limit"] == 200_000
    assert history["context_usage"] == 12345


async def test_conv_history_falls_back_to_compaction_cap(ws_context, config):
    """An unknown window keeps the compaction cap; no sidecar means no usage."""
    state, index = ws_context
    config.compaction.max_tokens = 10000
    config.llm.context_window_size = 0
    conv = _conv_dir(config) / CONV_ID
    _write_jsonl(
        conv / "archive.jsonl",
        [{"role": "user", "content": "hello", "timestamp": "2026-11-25T00:00:00"}],
    )

    history = await _load_history(state, index)

    assert history["context_limit"] == 10000
    assert "context_usage" not in history
    assert "estimated_tokens" in history
