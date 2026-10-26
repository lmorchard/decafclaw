"""Tests for ephemeral explanation-only side questions (issue #1141)."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.archive import append_message, read_archive
from decafclaw.conversation_manager import (
    SIDE_QUESTION_SYSTEM_PROMPT,
    ConversationManager,
)
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token
from decafclaw.web.conversations import ConversationIndex


@pytest.fixture
def manager(config):
    bus = EventBus()
    return ConversationManager(config, bus)


@pytest.fixture
def http_config(config, monkeypatch, tmp_path):
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.host = "127.0.0.1"
    config.http.port = 18881
    config.http.base_url = ""
    monkeypatch.chdir(tmp_path)
    config.agent.data_home = "data"
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def app(http_config, manager):
    return create_app(http_config, manager.event_bus, manager=manager)


@pytest.fixture
async def client(app, http_config):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = create_token(http_config, "testuser")
        resp = await c.post("/api/auth/login", json={"token": token})
        c.cookies = resp.cookies
        yield c


@pytest.mark.asyncio
async def test_side_question_idle_conversation(manager, config):
    conv_id = "test-conv-idle"
    append_message(config, conv_id, {"role": "user", "content": "Initial user request"})
    append_message(config, conv_id, {"role": "assistant", "content": "Assistant completed response"})

    recorded_calls = []

    async def fake_streaming(cfg, messages, tools=None, on_chunk=None, cancel_event=None, model_name=None):
        recorded_calls.append(
            {
                "messages": messages,
                "tools": tools,
                "cancel_event": cancel_event,
                "model_name": model_name,
            }
        )
        if on_chunk:
            await on_chunk("text", "This is an explanation.")
        return {"role": "assistant", "content": "This is an explanation."}

    with patch("decafclaw.conversation_manager.call_llm_streaming", side_effect=fake_streaming):
        result = await manager.run_side_question(conv_id, "What does this mean?")

    assert result["content"] == "This is an explanation."
    assert len(recorded_calls) == 1
    call = recorded_calls[0]
    assert call["tools"] is None

    messages = call["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == SIDE_QUESTION_SYSTEM_PROMPT
    assert messages[1] == {"role": "user", "content": "Initial user request"}
    assert messages[2] == {"role": "assistant", "content": "Assistant completed response"}
    assert messages[3] == {"role": "user", "content": "What does this mean?"}

    # Verify archive was NOT modified (ephemeral)
    archive = read_archive(config, conv_id)
    assert len(archive) == 2
    assert manager.get_activity_status(conv_id) == "idle"


@pytest.mark.asyncio
async def test_side_question_during_active_busy_turn(manager, config):
    conv_id = "test-conv-busy"
    state = manager._get_or_create(conv_id)
    state.busy = True
    state.agent_task = asyncio.create_task(asyncio.sleep(10))
    state.cancel_event = asyncio.Event()
    state.history = [{"role": "user", "content": "Long task running"}]

    recorded_calls = []

    async def fake_streaming(cfg, messages, tools=None, on_chunk=None, cancel_event=None, model_name=None):
        recorded_calls.append({"messages": messages, "tools": tools})
        return {"role": "assistant", "content": "The agent is executing step 1."}

    with patch("decafclaw.conversation_manager.call_llm_streaming", side_effect=fake_streaming):
        result = await manager.run_side_question(conv_id, "What are you doing?")

    assert result["content"] == "The agent is executing step 1."
    assert len(recorded_calls) == 1
    # Verify main task remains busy and running
    assert state.busy is True
    assert not state.agent_task.done()
    assert not state.cancel_event.is_set()

    # Clean up background task
    state.agent_task.cancel()
    try:
        await state.agent_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_side_question_includes_partial_assistant_streaming_chunks(manager, config):
    conv_id = "test-conv-partial"
    state = manager._get_or_create(conv_id)
    state.busy = True
    state.history = [{"role": "user", "content": "Run analysis"}]
    state.partial_assistant_chunks = ["I am reading ", "the data file now."]

    captured_messages = []

    async def fake_streaming(cfg, messages, **kwargs):
        captured_messages.extend(messages)
        return {"role": "assistant", "content": "It is reading the data file."}

    with patch("decafclaw.conversation_manager.call_llm_streaming", side_effect=fake_streaming):
        await manager.run_side_question(conv_id, "What file?")

    # Verify partial assistant content is included in snapshot
    assistant_msgs = [m for m in captured_messages if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["content"] == "I am reading the data file now."


@pytest.mark.asyncio
async def test_side_question_strips_tools_and_reformats_tool_calls(manager, config):
    conv_id = "test-conv-tools"
    state = manager._get_or_create(conv_id)
    state.history = [
        {"role": "user", "content": "Deploy app"},
        {
            "role": "assistant",
            "content": "Deploying now.",
            "tool_calls": [
                {
                    "function": {
                        "name": "run_shell_command",
                        "arguments": '{"command": "make deploy"}',
                    }
                }
            ],
        },
        {
            "role": "tool",
            "name": "run_shell_command",
            "tool_call_id": "call-1",
            "content": "Deployed version 1.2.3",
        },
        {"role": "vault_retrieval", "content": "Retrieved deployment notes"},
    ]

    captured_messages = []
    captured_tools = "not-none"

    async def fake_streaming(cfg, messages, tools=None, **kwargs):
        nonlocal captured_tools
        captured_messages.extend(messages)
        captured_tools = tools
        return {"role": "assistant", "content": "make deploy runs the deployment Makefile target."}

    with patch("decafclaw.conversation_manager.call_llm_streaming", side_effect=fake_streaming):
        await manager.run_side_question(conv_id, "What is make deploy?")

    assert captured_tools is None
    # No tool_calls or role="tool" in messages
    for msg in captured_messages:
        assert "tool_calls" not in msg
        assert msg["role"] in ("system", "user", "assistant")

    # Assistant message has the tool call formatted as readable text
    assistant_msgs = [m for m in captured_messages if m["role"] == "assistant"]
    assert "Deploying now." in assistant_msgs[0]["content"]
    assert "[Tool call: run_shell_command(" in assistant_msgs[0]["content"]

    # Tool message was converted to user message
    user_msgs = [m for m in captured_messages if m["role"] == "user"]
    assert any("[Tool result: run_shell_command]" in m["content"] for m in user_msgs)
    assert any("Deployed version 1.2.3" in m["content"] for m in user_msgs)


@pytest.mark.asyncio
async def test_side_question_side_history_followup(manager, config):
    conv_id = "test-conv-followup"
    state = manager._get_or_create(conv_id)
    state.history = [{"role": "user", "content": "Main prompt"}]

    captured_messages = []

    async def fake_streaming(cfg, messages, **kwargs):
        captured_messages.extend(messages)
        return {"role": "assistant", "content": "Second answer"}

    side_history = [
        {"role": "user", "content": "Side Q1"},
        {"role": "assistant", "content": "Side A1"},
    ]

    with patch("decafclaw.conversation_manager.call_llm_streaming", side_effect=fake_streaming):
        await manager.run_side_question(
            conv_id,
            "Side Q2 follow-up",
            side_history=side_history,
        )

    # Check side history and question order
    assert captured_messages[-3] == {"role": "user", "content": "Side Q1"}
    assert captured_messages[-2] == {"role": "assistant", "content": "Side A1"}
    assert captured_messages[-1] == {"role": "user", "content": "Side Q2 follow-up"}


@pytest.mark.asyncio
async def test_independent_cancellation(manager, config):
    conv_id = "test-conv-cancel"
    state = manager._get_or_create(conv_id)
    state.busy = True
    main_cancel = asyncio.Event()
    state.cancel_event = main_cancel
    state.agent_task = asyncio.create_task(asyncio.sleep(10))

    side_cancel = asyncio.Event()

    async def fake_streaming(cfg, messages, cancel_event=None, **kwargs):
        assert cancel_event is side_cancel
        # Simulate cancellation during streaming
        side_cancel.set()
        return {"role": "assistant", "content": "Partial"}

    with patch("decafclaw.conversation_manager.call_llm_streaming", side_effect=fake_streaming):
        await manager.run_side_question(conv_id, "Question", cancel_event=side_cancel)

    # Side question cancellation does NOT cancel main task
    assert not main_cancel.is_set()
    assert state.busy is True
    assert not state.agent_task.done()

    # Cancelling main task does NOT touch side question
    another_side_cancel = asyncio.Event()
    await manager.cancel_turn(conv_id)
    assert main_cancel.is_set()
    assert not another_side_cancel.is_set()

    # Clean up
    state.agent_task.cancel()
    try:
        await state.agent_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_side_question_http_endpoint_sse(client, http_config, manager):
    index = ConversationIndex(http_config)
    conv = index.create("testuser", title="Test Conversation")
    conv_id = conv.conv_id

    async def fake_streaming(cfg, messages, on_chunk=None, **kwargs):
        if on_chunk:
            await on_chunk("text", "Chunk 1 ")
            await on_chunk("text", "Chunk 2")
        return {"role": "assistant", "content": "Chunk 1 Chunk 2"}

    with patch("decafclaw.conversation_manager.call_llm_streaming", side_effect=fake_streaming):
        response = await client.post(
            f"/api/conversations/{conv_id}/side-question",
            json={"question": "Explain this test"},
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    # Parse SSE text
    lines = response.text.split("\n")
    events = []
    current_event = None
    for line in lines:
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current_event:
            data = json.loads(line.split(":", 1)[1].strip())
            events.append((current_event, data))
            current_event = None

    chunk_events = [e for e in events if e[0] == "chunk"]
    done_events = [e for e in events if e[0] == "done"]

    assert len(chunk_events) >= 1
    assert len(done_events) == 1
    assert done_events[0][1]["text"] == "Chunk 1 Chunk 2"


@pytest.mark.asyncio
async def test_side_question_http_endpoint_auth_and_validation(client, http_config):
    # Missing conversation
    resp = await client.post("/api/conversations/nonexistent/side-question", json={"question": "hello"})
    assert resp.status_code == 404

    # Existing conversation belonging to user
    index = ConversationIndex(http_config)
    conv = index.create("testuser", title="Test")

    # Missing question
    resp = await client.post(f"/api/conversations/{conv.conv_id}/side-question", json={})
    assert resp.status_code == 400

    # Unknown model
    resp = await client.post(
        f"/api/conversations/{conv.conv_id}/side-question",
        json={"question": "hello", "model": "nonexistent-model-xyz"},
    )
    assert resp.status_code == 400
