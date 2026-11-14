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
        "role": "model",
        "parts": [{"text": "Hello!"}],
    }


def test_tool_call_translation():
    """Assistant tool call → model functionCall."""
    messages = [
        {"role": "user", "content": "Search for cats"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "search",
                        "arguments": '{"query": "cats"}',
                    },
                }
            ],
        },
    ]
    body = _build_request_body(messages)
    model_msg = body["contents"][1]
    assert model_msg["role"] == "model"
    assert model_msg["parts"] == [
        {
            "functionCall": {"name": "search", "args": {"query": "cats"}},
        }
    ]


def test_tool_response_translation():
    """Tool response → user functionResponse."""
    messages = [
        {"role": "tool", "name": "search", "content": '{"results": ["cat1"]}'},
    ]
    body = _build_request_body(messages)
    user_msg = body["contents"][0]
    assert user_msg["role"] == "user"
    assert user_msg["parts"] == [
        {
            "functionResponse": {
                "name": "search",
                "response": {"results": ["cat1"]},
            },
        }
    ]


def test_parallel_tool_responses_merged():
    """Multiple consecutive tool responses merge into one user message."""
    messages = [
        {"role": "user", "content": "Do two things"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": '{"q":"a"}'}},
                {"id": "call_2", "type": "function", "function": {"name": "lookup", "arguments": '{"id":1}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result A"},
        {"role": "tool", "tool_call_id": "call_2", "content": "result B"},
    ]
    body = _build_request_body(messages)
    # The two tool responses should be merged into a single user message
    # with 2 functionResponse parts
    tool_response_msgs = [
        c for c in body["contents"] if c["role"] == "user" and any("functionResponse" in p for p in c["parts"])
    ]
    assert len(tool_response_msgs) == 1, f"Expected 1 merged user message, got {len(tool_response_msgs)}"
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
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "activate_skill", "arguments": '{"name": "tabstack"}'},
                }
            ],
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
    tools = [
        {
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
        }
    ]
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

    tools = [
        {
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
        }
    ]
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


def test_tool_schema_preserves_stripped_multiple_of_as_description():
    """A stripped ``multipleOf`` semantic constraint must survive as readable description text.

    The wire schema strips JSON-Schema keys Vertex/Gemini cannot process, but a
    numeric bound expressed as ``multipleOf`` carries meaning that must not be
    dropped silently from the model-facing declaration (#1147). The canonical
    schema keeps the bound; the wire schema renders it as description text on
    the node where the constraint was found.
    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "bounded_number",
                "description": "A number with a bound",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "number", "multipleOf": 5}},
                    "required": ["value"],
                },
            },
        }
    ]
    canonical = json.loads(json.dumps(tools[0]["function"]["parameters"]))
    body = _build_request_body([], tools=tools)
    decls = body["tools"][0]["functionDeclarations"]
    params = decls[0]["parameters"]

    # The unsupported key is stripped from the wire schema.
    assert "multipleOf" not in params["properties"]["value"]
    assert params["properties"]["value"]["type"] == "number"

    # The stripped semantic constraint is preserved as readable description text
    # on the property that carried it — not hoisted to the object level.
    assert "must be a multiple of 5" in (params["properties"]["value"].get("description") or "")
    assert "multiple of 5" not in (params.get("description") or "")

    # The canonical schema is not mutated by the cleaning pass.
    assert canonical["properties"]["value"]["multipleOf"] == 5


def test_tool_schema_multi_property_constraints_attach_to_their_properties():
    """Each constraint stays bound to the property that carried it (#1147).

    A multi-property schema with two different ``multipleOf`` bounds must not
    produce an ambiguous object-level description ("must be a multiple of 5;
    must be a multiple of 3") — the model could not tell which bound applies to
    which property. Each note attaches to its own node instead.
    """
    from decafclaw.llm.providers.vertex import _clean_schema_preserving

    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "number", "multipleOf": 5},
            "b": {"type": "number", "multipleOf": 3, "description": "the b value"},
        },
    }
    cleaned, diags = _clean_schema_preserving(schema)

    assert "must be a multiple of 5" in (cleaned["properties"]["a"].get("description") or "")
    assert "must be a multiple of 3" in (cleaned["properties"]["b"].get("description") or "")
    # Pre-existing description text is preserved, not replaced.
    assert cleaned["properties"]["b"]["description"].startswith("the b value")
    # Nothing was hoisted to the object level.
    assert "description" not in cleaned or "multiple of" not in (cleaned.get("description") or "")
    assert diags == []


def test_tool_schema_nested_constraint_preserved_per_node():
    """Constraints inside nested object schemas attach to the nested node."""
    from decafclaw.llm.providers.vertex import _clean_schema_preserving

    schema = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "object",
                "properties": {"step": {"type": "number", "multipleOf": 0.5}},
            },
        },
    }
    cleaned, diags = _clean_schema_preserving(schema)

    nested = cleaned["properties"]["filter"]
    assert "multipleOf" not in nested["properties"]["step"]
    assert "must be a multiple of 0.5" in (nested["properties"]["step"].get("description") or "")
    assert "multiple of" not in (nested.get("description") or "")
    assert diags == []


def test_tool_schema_property_names_resembling_keywords_survive():
    """Property *names* that look like schema keywords are not stripped (#1147).

    The denylist strips keys of a schema node, never the names inside its
    ``properties`` dict — a property literally called ``enum`` or ``type`` must
    survive the wire schema.
    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "keyword_named_props",
                "description": "Properties named like schema keywords",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "enum": {"type": "string"},
                        "type": {"type": "number", "multipleOf": 2},
                    },
                },
            },
        }
    ]
    body = _build_request_body([], tools=tools)
    params = body["tools"][0]["functionDeclarations"][0]["parameters"]

    assert "enum" in params["properties"]
    assert "type" in params["properties"]
    # The constraint on the property named 'type' still renders.
    assert "must be a multiple of 2" in (params["properties"]["type"].get("description") or "")


def test_tool_schema_resolves_local_ref():
    """A local ``$ref`` is resolved so the referenced shape reaches the wire (#1147).

    Stripping both ``$ref`` and ``$defs`` would silently broaden a referenced
    parameter into an unconstrained object; resolution prevents that.
    """
    from decafclaw.llm.providers.vertex import _clean_schema_preserving

    schema = {
        "type": "object",
        "$defs": {"Name": {"type": "string", "minLength": 1}},
        "properties": {
            "name": {"$ref": "#/$defs/Name"},
        },
    }
    cleaned, diags = _clean_schema_preserving(schema)

    assert "$ref" not in cleaned["properties"]["name"]
    assert cleaned["properties"]["name"]["type"] == "string"
    assert cleaned["properties"]["name"]["minLength"] == 1
    # The $defs block itself is metadata Vertex does not process.
    assert "$defs" not in cleaned
    assert diags == []


def test_tool_schema_unresolvable_ref_diagnosed_and_annotated():
    """An unresolvable ``$ref`` is reported and annotated, never silent (#1147)."""
    from decafclaw.llm.providers.vertex import _clean_schema_preserving

    schema = {
        "type": "object",
        "properties": {
            "target": {"$ref": "https://example.com/schemas/target.json"},
        },
    }
    cleaned, diags = _clean_schema_preserving(schema)

    assert "$ref" not in cleaned["properties"]["target"]
    # The model is told the parameter lost its shape.
    assert "unconstrained" in (cleaned["properties"]["target"].get("description") or "")
    assert any("unresolvable $ref" in d for d in diags)


def test_tool_schema_circular_ref_diagnosed_without_hanging():
    """A circular ``$ref`` chain is diagnosed, not followed forever (#1147)."""
    from decafclaw.llm.providers.vertex import _clean_schema_preserving

    schema = {
        "type": "object",
        "$defs": {
            "A": {"$ref": "#/$defs/B"},
            "B": {"type": "object", "$ref": "#/$defs/A"},
        },
        "properties": {"node": {"$ref": "#/$defs/A"}},
    }
    cleaned, diags = _clean_schema_preserving(schema)

    assert any("circular $ref" in d for d in diags)
    # The node is annotated rather than silently unconstrained.
    assert "unconstrained" in (cleaned["properties"]["node"].get("description") or "")


def test_tool_schema_structural_constructs_diagnosed():
    """Stripped structural constructs are reported in diagnostics (#1147)."""
    from decafclaw.llm.providers.vertex import _clean_schema_preserving

    schema = {
        "type": "object",
        "properties": {
            "kw": {"type": "object", "patternProperties": {"^x_": {"type": "string"}}},
            "branches": {
                "type": "object",
                "if": {"required": ["a"]},
                "then": {"properties": {"b": {"type": "string"}}},
            },
        },
    }
    cleaned, diags = _clean_schema_preserving(schema)

    assert "patternProperties" not in cleaned["properties"]["kw"]
    assert "if" not in cleaned["properties"]["branches"]
    joined = "\n".join(diags)
    assert "patternProperties" in joined
    assert "'if'" in joined and "'then'" in joined


def test_tool_schema_optional_null_and_maps_pass_through():
    """Optional/null unions and maps are legitimate OpenAPI — they survive (#1147)."""
    from decafclaw.llm.providers.vertex import _clean_schema_preserving

    schema = {
        "type": "object",
        "properties": {
            "label": {"type": ["string", "null"]},
            "tags": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }
    cleaned, diags = _clean_schema_preserving(schema)

    assert cleaned["properties"]["label"]["type"] == ["string", "null"]
    assert cleaned["properties"]["tags"]["additionalProperties"] == {"type": "string"}
    assert diags == []


def test_tool_schema_dependency_rendered_and_diagnosed():
    """Required-form dependencies render as prose; schema-form ones are diagnosed."""
    from decafclaw.llm.providers.vertex import _clean_schema_preserving

    schema = {
        "type": "object",
        "properties": {
            "req": {"type": "object", "dependentRequired": {"url": ["method"]}},
            "sch": {
                "type": "object",
                "dependencies": {"url": ["method"], "headers": {"type": "object"}},
            },
        },
    }
    cleaned, diags = _clean_schema_preserving(schema)

    # Required-form: rendered on the node, key stripped.
    assert "dependentRequired" not in cleaned["properties"]["req"]
    assert "if 'url' is present, method is required" in (cleaned["properties"]["req"].get("description") or "")
    # Schema-form entry: diagnosed, not rendered.
    assert "dependencies" not in cleaned["properties"]["sch"]
    assert any("schema-form dependency" in d for d in diags)


def test_vertex_adaptation_does_not_mutate_canonical_schema():
    """The canonical registry schema survives provider adaptation unchanged (#1147).

    Adaptation must derive a wire copy, never mutate the registry contract or
    other providers' inputs — even with refs, constraints, and structural keys.
    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "everything",
                "description": "Exercises refs, constraints, and structural keys",
                "parameters": {
                    "type": "object",
                    "$defs": {"Name": {"type": "string"}},
                    "properties": {
                        "name": {"$ref": "#/$defs/Name"},
                        "n": {"type": "number", "multipleOf": 5},
                        "kw": {
                            "type": "object",
                            "propertyNames": {"pattern": "^[a-z]+$"},
                            "patternProperties": {"^x_": {"type": "string"}},
                        },
                    },
                },
            },
        }
    ]
    canonical = json.loads(json.dumps(tools[0]["function"]["parameters"]))
    _build_request_body([], tools=tools)
    assert tools[0]["function"]["parameters"] == canonical


def test_tool_schema_scrubs_unsupported_keys_inside_combinator_branches():
    """``oneOf`` / ``anyOf`` / ``allOf`` branches contain full subschemas;
    unsupported keywords inside them must also be scrubbed, since Vertex
    walks the tree and rejects on the first match. Specific guarantee for
    the combinator-recursion path added alongside the strip list.
    """
    tools = [
        {
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
        }
    ]
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
    messages = [
        {
            "role": "assistant",
            "content": "Let me search that.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        }
    ]
    body = _build_request_body(messages)
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "Let me search that."}
    assert parts[1] == {"functionCall": {"name": "search", "args": {}}}


# -- Response parsing ----------------------------------------------------------


def test_parse_text_response():
    data = {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": "Hello!"}]},
                "finishReason": "STOP",
            }
        ],
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
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "search",
                                "args": {"query": "cats"},
                            },
                        }
                    ],
                },
                "finishReason": "STOP",
            }
        ],
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
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"functionCall": {"name": "search", "args": {"q": "a"}}},
                        {"functionCall": {"name": "lookup", "args": {"id": 1}}},
                    ],
                },
            }
        ],
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
        "cached_tokens": 0,
    }


def test_parse_usage_missing():
    assert _parse_usage({}) is None


# -- Multimodal content (image attachments) ------------------------------------


def test_user_message_with_image_attachment():
    """Multimodal user content (from resolve_attachments) → Vertex inlineData."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
                },
            ],
        }
    ]
    body = _build_request_body(messages)
    parts = body["contents"][0]["parts"]
    assert len(parts) == 2
    assert parts[0] == {"text": "What's in this image?"}
    assert parts[1] == {
        "inlineData": {"mimeType": "image/png", "data": "iVBORw0KGgo="},
    }


def test_user_message_with_image_only():
    """Multimodal content with no text, just an image."""
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ="},
                },
            ],
        }
    ]
    body = _build_request_body(messages)
    parts = body["contents"][0]["parts"]
    assert len(parts) == 1
    assert parts[0] == {
        "inlineData": {"mimeType": "image/jpeg", "data": "/9j/4AAQ="},
    }


def test_user_message_with_multiple_images():
    """Multiple images in one message."""
    messages = [
        {
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
        }
    ]
    body = _build_request_body(messages)
    parts = body["contents"][0]["parts"]
    assert len(parts) == 3
    assert parts[0] == {"text": "Compare these:"}
    assert parts[1]["inlineData"]["data"] == "AAAA"
    assert parts[2]["inlineData"]["data"] == "BBBB"


def test_parse_response_preserves_thought_signature():
    data = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "vault_search",
                                "args": {"query": "test"},
                            },
                            "thoughtSignature": "sig_abc123",
                        }
                    ],
                },
            }
        ],
    }
    result = _parse_response(data)
    tc = result["tool_calls"][0]
    assert tc["thought_signature"] == "sig_abc123"


def test_build_request_body_preserves_thought_signature():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "vault_search",
                        "arguments": '{"query": "test"}',
                    },
                    "thought_signature": "sig_abc123",
                }
            ],
        },
    ]
    body = _build_request_body(messages)
    model_msg = body["contents"][0]
    assert model_msg["role"] == "model"
    part = model_msg["parts"][0]
    assert part["functionCall"] == {"name": "vault_search", "args": {"query": "test"}}
    assert part["thoughtSignature"] == "sig_abc123"
    assert "thoughtSignature" not in part["functionCall"]
    assert "thought_signature" not in part["functionCall"]
