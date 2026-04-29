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


def test_tool_schema_strips_all_unsupported_keywords():
    """Every keyword in ``_VERTEX_UNSUPPORTED_KEYS`` must be stripped from
    tool parameter schemas before the request goes out — otherwise Vertex
    returns HTTP 400 ``Unknown name "X"`` and the agent turn dies.

    This test was written in response to a real failure: a Playwright
    MCP tool's input schema used ``propertyNames``. Coverage spans every
    member of ``_VERTEX_UNSUPPORTED_KEYS`` so a regression that re-adds
    one to the kept set (or that drops one from the strip list) gets
    caught locally instead of in production.
    """
    from decafclaw.llm.providers.vertex import _VERTEX_UNSUPPORTED_KEYS

    tools = [{
        "type": "function",
        "function": {
            "name": "exhaustive_unsupported_keys",
            "description": "Exercises every key in _VERTEX_UNSUPPORTED_KEYS",
            "parameters": {
                "type": "object",
                # Schema-metadata family.
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "https://example.com/schemas/foo",
                "$defs": {"Foo": {"type": "string"}},
                "definitions": {"Bar": {"type": "string"}},
                "$ref": "#/$defs/Foo",
                "properties": {
                    "kw": {
                        "type": "object",
                        "propertyNames": {"pattern": "^[a-z]+$"},
                        "patternProperties": {"^x_": {"type": "string"}},
                    },
                    "n": {"type": "number", "multipleOf": 5},
                    "branches": {
                        "type": "object",
                        "if": {"required": ["a"]},
                        "then": {"properties": {"b": {"type": "string"}}},
                        "else": {"properties": {"c": {"type": "string"}}},
                    },
                    "deps": {
                        "type": "object",
                        "dependentRequired": {"a": ["b"]},
                        "dependentSchemas": {"a": {"required": ["b"]}},
                        "dependencies": {"x": ["y"]},
                    },
                },
                "required": ["kw"],
            },
        },
    }]
    body = _build_request_body([], tools=tools)
    decls = body["tools"][0]["functionDeclarations"]
    params = decls[0]["parameters"]

    # Top-level metadata family — none should survive at the root.
    for key in ("$schema", "$id", "$defs", "definitions", "$ref"):
        assert key not in params, f"top-level {key!r} should be stripped"

    # Stripped from nested object schemas.
    kw = params["properties"]["kw"]
    assert "propertyNames" not in kw
    assert "patternProperties" not in kw

    n = params["properties"]["n"]
    assert "multipleOf" not in n
    assert n["type"] == "number"  # legitimate keys preserved

    branches = params["properties"]["branches"]
    for key in ("if", "then", "else"):
        assert key not in branches, f"branch keyword {key!r} should be stripped"

    deps = params["properties"]["deps"]
    for key in ("dependentRequired", "dependentSchemas", "dependencies"):
        assert key not in deps, f"dep keyword {key!r} should be stripped"

    # Sanity: legitimate constraints survive the scrub.
    assert params["properties"]["kw"]["type"] == "object"
    assert params["required"] == ["kw"]

    # Defense-in-depth: if a new key is added to the denylist later, this
    # test should remind the maintainer to add coverage for it. We assert
    # the test schema mentions every current denylist key somewhere.
    schema_str = json.dumps(tools[0]["function"]["parameters"])
    for key in _VERTEX_UNSUPPORTED_KEYS:
        assert key in schema_str, (
            f"_VERTEX_UNSUPPORTED_KEYS contains {key!r} but the test "
            f"schema does not exercise it — extend the schema and add an "
            f"assertion so the strip is verified end-to-end."
        )


def test_tool_schema_scrubs_unsupported_keys_inside_combinator_branches():
    """``oneOf`` / ``anyOf`` / ``allOf`` branches contain full subschemas;
    unsupported keywords inside them must also be scrubbed, since Vertex
    walks the tree and rejects on the first match. Specific guarantee for
    the combinator-recursion path added alongside the strip list.
    """
    tools = [{
        "type": "function",
        "function": {
            "name": "combinator_branches",
            "description": "Tool whose schema buries unsupported keys in combinator branches",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "oneOf": [
                            # Branch A: propertyNames inside oneOf
                            {
                                "type": "object",
                                "propertyNames": {"pattern": "^a_"},
                            },
                            # Branch B: anyOf nested inside oneOf, with
                            # patternProperties at one more level down.
                            {
                                "anyOf": [
                                    {"type": "string"},
                                    {
                                        "type": "object",
                                        "patternProperties": {"^b_": {"type": "string"}},
                                    },
                                ],
                            },
                            # Branch C: allOf carrying $defs (metadata key)
                            {
                                "allOf": [
                                    {"$defs": {"Z": {"type": "string"}}, "type": "object"},
                                ],
                            },
                        ],
                    },
                },
            },
        },
    }]
    body = _build_request_body([], tools=tools)
    one_of = body["tools"][0]["functionDeclarations"][0]["parameters"]["properties"]["value"]["oneOf"]

    # Branch A — propertyNames stripped, but type preserved.
    assert "propertyNames" not in one_of[0]
    assert one_of[0]["type"] == "object"

    # Branch B — patternProperties stripped two levels deep.
    nested_object = one_of[1]["anyOf"][1]
    assert "patternProperties" not in nested_object
    assert nested_object["type"] == "object"

    # Branch C — $defs stripped from inside the allOf entry.
    assert "$defs" not in one_of[2]["allOf"][0]
    assert one_of[2]["allOf"][0]["type"] == "object"


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


# -- Multimodal content (image attachments) ------------------------------------


def test_user_message_with_image_attachment():
    """Multimodal user content (from _resolve_attachments) → Vertex inlineData."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "What's in this image?"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
            },
        ],
    }]
    body = _build_request_body(messages)
    parts = body["contents"][0]["parts"]
    assert len(parts) == 2
    assert parts[0] == {"text": "What's in this image?"}
    assert parts[1] == {
        "inlineData": {"mimeType": "image/png", "data": "iVBORw0KGgo="},
    }


def test_user_message_with_image_only():
    """Multimodal content with no text, just an image."""
    messages = [{
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ="},
            },
        ],
    }]
    body = _build_request_body(messages)
    parts = body["contents"][0]["parts"]
    assert len(parts) == 1
    assert parts[0] == {
        "inlineData": {"mimeType": "image/jpeg", "data": "/9j/4AAQ="},
    }


def test_user_message_with_multiple_images():
    """Multiple images in one message."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Compare these:"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAAA"},
            },
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,BBBB"},
            },
        ],
    }]
    body = _build_request_body(messages)
    parts = body["contents"][0]["parts"]
    assert len(parts) == 3
    assert parts[0] == {"text": "Compare these:"}
    assert parts[1]["inlineData"]["data"] == "AAAA"
    assert parts[2]["inlineData"]["data"] == "BBBB"
