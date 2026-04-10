"""Tests for Vertex/Gemini message and tool format translation."""

import json

import pytest

from decafclaw.llm.providers.vertex import (
    _build_request_body,
    _parse_response,
    _parse_usage,
)

# -- Request body translation --------------------------------------------------


def test_basic_user_message():
    messages = [{"role": "user", "content": "Hello"}]
    body = _build_request_body(messages)
    assert body == {
        "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
    }


def test_system_message_becomes_instruction():
    messages = [
        {"role": "system", "content": "You are a bot."},
        {"role": "user", "content": "Hi"},
    ]
    body = _build_request_body(messages)
    assert body["systemInstruction"] == {
        "parts": [{"text": "You are a bot."}],
    }
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Hi"}]},
    ]


def test_assistant_message_becomes_model():
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]
    body = _build_request_body(messages)
    assert body["contents"][1] == {
        "role": "model", "parts": [{"text": "Hello!"}],
    }


def test_tool_call_translation():
    """Assistant tool call → model functionCall."""
    messages = [
        {"role": "user", "content": "Search for cats"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "search",
                    "arguments": '{"query": "cats"}',
                },
            }],
        },
    ]
    body = _build_request_body(messages)
    model_msg = body["contents"][1]
    assert model_msg["role"] == "model"
    assert model_msg["parts"] == [{
        "functionCall": {"name": "search", "args": {"query": "cats"}},
    }]


def test_tool_response_translation():
    """Tool response → user functionResponse."""
    messages = [
        {"role": "tool", "name": "search", "content": '{"results": ["cat1"]}'},
    ]
    body = _build_request_body(messages)
    user_msg = body["contents"][0]
    assert user_msg["role"] == "user"
    assert user_msg["parts"] == [{
        "functionResponse": {
            "name": "search",
            "response": {"results": ["cat1"]},
        },
    }]


def test_parallel_tool_responses_merged():
    """Multiple consecutive tool responses merge into one user message."""
    messages = [
        {"role": "user", "content": "Do two things"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "search", "arguments": '{"q":"a"}'}},
                {"id": "call_2", "type": "function",
                 "function": {"name": "lookup", "arguments": '{"id":1}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result A"},
        {"role": "tool", "tool_call_id": "call_2", "content": "result B"},
    ]
    body = _build_request_body(messages)
    # The two tool responses should be merged into a single user message
    # with 2 functionResponse parts
    tool_response_msgs = [
        c for c in body["contents"]
        if c["role"] == "user" and any("functionResponse" in p for p in c["parts"])
    ]
    assert len(tool_response_msgs) == 1, (
        f"Expected 1 merged user message, got {len(tool_response_msgs)}"
    )
    parts = tool_response_msgs[0]["parts"]
    assert len(parts) == 2
    assert parts[0]["functionResponse"]["name"] == "search"
    assert parts[1]["functionResponse"]["name"] == "lookup"


def test_tool_response_plain_string():
    """Non-JSON tool response gets wrapped in {result: ...}."""
    messages = [
        {"role": "tool", "name": "echo", "content": "hello world"},
    ]
    body = _build_request_body(messages)
    fr = body["contents"][0]["parts"][0]["functionResponse"]
    assert fr["response"] == {"result": "hello world"}


def test_tool_response_name_from_tool_call_id():
    """Tool response without name resolves name from preceding tool_call_id."""
    messages = [
        {"role": "user", "content": "Do it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {"name": "activate_skill", "arguments": '{"name": "tabstack"}'},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "call_abc",
            "content": "Skill activated.",
        },
    ]
    body = _build_request_body(messages)
    # The tool response should have the function name resolved from tool_call_id
    fr = body["contents"][2]["parts"][0]["functionResponse"]
    assert fr["name"] == "activate_skill"


def test_tool_definitions_translation():
    """OpenAI tool format → Gemini functionDeclarations."""
    tools = [{
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search things",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }]
    body = _build_request_body([], tools=tools)
    decls = body["tools"][0]["functionDeclarations"]
    assert len(decls) == 1
    assert decls[0]["name"] == "search"
    assert decls[0]["description"] == "Search things"
    assert decls[0]["parameters"]["type"] == "object"


def test_multiple_system_messages_concatenated():
    messages = [
        {"role": "system", "content": "Rule 1."},
        {"role": "system", "content": "Rule 2."},
        {"role": "user", "content": "Hi"},
    ]
    body = _build_request_body(messages)
    parts = body["systemInstruction"]["parts"]
    assert len(parts) == 2
    assert parts[0]["text"] == "Rule 1."
    assert parts[1]["text"] == "Rule 2."


def test_mixed_text_and_tool_calls():
    """Assistant with both text and tool calls."""
    messages = [{
        "role": "assistant",
        "content": "Let me search that.",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": "{}"},
        }],
    }]
    body = _build_request_body(messages)
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "Let me search that."}
    assert parts[1] == {"functionCall": {"name": "search", "args": {}}}


# -- Response parsing ----------------------------------------------------------


def test_parse_text_response():
    data = {
        "candidates": [{
            "content": {"role": "model", "parts": [{"text": "Hello!"}]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "totalTokenCount": 15,
        },
    }
    result = _parse_response(data)
    assert result["content"] == "Hello!"
    assert result["tool_calls"] is None
    assert result["role"] == "assistant"
    assert result["usage"]["prompt_tokens"] == 10
    assert result["usage"]["completion_tokens"] == 5


def test_parse_function_call_response():
    data = {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [{
                    "functionCall": {
                        "name": "search",
                        "args": {"query": "cats"},
                    },
                }],
            },
            "finishReason": "STOP",
        }],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 10,
            "totalTokenCount": 30,
        },
    }
    result = _parse_response(data)
    assert result["content"] is None
    assert len(result["tool_calls"]) == 1
    tc = result["tool_calls"][0]
    assert tc["function"]["name"] == "search"
    assert json.loads(tc["function"]["arguments"]) == {"query": "cats"}
    assert tc["id"].startswith("call_")


def test_parse_multiple_function_calls():
    data = {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [
                    {"functionCall": {"name": "search", "args": {"q": "a"}}},
                    {"functionCall": {"name": "lookup", "args": {"id": 1}}},
                ],
            },
        }],
    }
    result = _parse_response(data)
    assert len(result["tool_calls"]) == 2
    assert result["tool_calls"][0]["function"]["name"] == "search"
    assert result["tool_calls"][1]["function"]["name"] == "lookup"


def test_parse_empty_response():
    data = {"candidates": []}
    result = _parse_response(data)
    assert result["content"] is None
    assert result["tool_calls"] is None


def test_parse_usage():
    data = {
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 50,
            "totalTokenCount": 150,
        },
    }
    usage = _parse_usage(data)
    assert usage == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    }


def test_parse_usage_missing():
    assert _parse_usage({}) is None
