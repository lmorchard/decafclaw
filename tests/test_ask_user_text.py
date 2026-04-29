"""Tests for the ask_user_text core tool."""

import json

import pytest

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.tools.core import (
    _default_text_input_callback,
    _normalize_text_input_fields,
    tool_ask_user_text,
)

# ------------- field normalization -------------

def test_normalize_bare_strings_title_cases_label():
    out = _normalize_text_input_fields(["name", "email_address"])
    assert out == [
        {"key": "name", "label": "Name"},
        {"key": "email_address", "label": "Email Address"},
    ]


def test_normalize_dicts_with_optionals():
    out = _normalize_text_input_fields([
        {"key": "bio", "label": "Bio", "multiline": True,
         "max_length": 500, "required": False, "placeholder": "tell me",
         "default": "x"},
    ])
    assert out == [{
        "key": "bio", "label": "Bio", "multiline": True,
        "max_length": 500, "required": False,
        "placeholder": "tell me", "default": "x",
    }]


def test_normalize_dict_missing_key_or_label_is_rejected():
    assert _normalize_text_input_fields([{"label": "no key"}]) is None
    assert _normalize_text_input_fields([{"key": "v"}]) is None


def test_normalize_duplicate_keys_rejected():
    assert _normalize_text_input_fields([
        {"key": "v", "label": "A"}, {"key": "v", "label": "B"},
    ]) is None


def test_normalize_duplicate_keys_after_str_coercion_rejected():
    """Catch the bug where dedup happened on the raw value before
    coercing to str: int 1 vs str "1" must collide once normalized."""
    assert _normalize_text_input_fields([
        {"key": 1, "label": "A"}, {"key": "1", "label": "B"},
    ]) is None


def test_normalize_strips_whitespace_from_dict_key():
    out = _normalize_text_input_fields([
        {"key": "  v  ", "label": "V"},
    ])
    assert out == [{"key": "v", "label": "V"}]


def test_normalize_bad_max_length_dropped():
    out = _normalize_text_input_fields([
        {"key": "v", "label": "V", "max_length": 0},
    ])
    assert out == [{"key": "v", "label": "V"}]


def test_normalize_None_returns_None():
    assert _normalize_text_input_fields(None) is None


def test_normalize_bad_entry_returns_None():
    assert _normalize_text_input_fields([123]) is None


# ------------- default on_response -------------

def test_default_callback_single_returns_bare_value():
    cb = _default_text_input_callback(["value"])
    assert cb({"value": "Hello"}) == "User responded: Hello"


def test_default_callback_single_strips_whitespace():
    cb = _default_text_input_callback(["value"])
    assert cb({"value": "  Hi  "}) == "User responded: Hi"


def test_default_callback_single_empty_says_no_response():
    cb = _default_text_input_callback(["value"])
    assert cb({"value": ""}) == "User did not respond."
    assert cb({}) == "User did not respond."


def test_default_callback_multi_returns_json():
    cb = _default_text_input_callback(["name", "email"])
    out = cb({"name": "Les", "email": "x@y"})
    assert out.startswith("User responded: ")
    assert json.loads(out[len("User responded: "):]) == {
        "name": "Les", "email": "x@y"}


def test_default_callback_multi_preserves_field_order():
    cb = _default_text_input_callback(["b", "a"])
    out = cb({"a": "first", "b": "second"})
    body = out[len("User responded: "):]
    assert body.index('"b"') < body.index('"a"')


def test_default_callback_multi_all_empty_says_no_response():
    cb = _default_text_input_callback(["a", "b"])
    assert cb({"a": "", "b": "  "}) == "User did not respond."


# ------------- tool integration -------------

@pytest.mark.asyncio
async def test_tool_happy_single_field_default():
    ctx = object()
    result = await tool_ask_user_text(ctx, prompt="Your name?")
    assert isinstance(result, ToolResult)
    assert result.end_turn is True
    assert isinstance(result.widget, WidgetRequest)
    assert result.widget.widget_type == "text_input"
    fields = result.widget.data["fields"]
    assert len(fields) == 1
    assert fields[0] == {"key": "value", "label": "Your name?"}
    assert "awaiting user response" in result.text


@pytest.mark.asyncio
async def test_tool_multi_field():
    ctx = object()
    result = await tool_ask_user_text(
        ctx, prompt="Contact info?",
        fields=[
            {"key": "name", "label": "Name"},
            {"key": "email", "label": "Email", "required": False},
        ],
        submit_label="Send",
    )
    assert result.widget.data["submit_label"] == "Send"
    assert len(result.widget.data["fields"]) == 2
    inject = result.widget.on_response({"name": "Les", "email": "x@y"})
    assert json.loads(inject[len("User responded: "):]) == {
        "name": "Les", "email": "x@y"}


@pytest.mark.asyncio
async def test_tool_blank_prompt_returns_error():
    ctx = object()
    result = await tool_ask_user_text(ctx, prompt="   ")
    assert result.widget is None
    assert "error" in result.text.lower()


@pytest.mark.asyncio
async def test_tool_bad_fields_returns_error():
    ctx = object()
    result = await tool_ask_user_text(
        ctx, prompt="?", fields=[{"label": "no key"}])
    assert result.widget is None
    assert "error" in result.text.lower()


@pytest.mark.asyncio
async def test_tool_default_callback_wired():
    ctx = object()
    result = await tool_ask_user_text(
        ctx, prompt="?", fields=["color"])
    inject = result.widget.on_response({"color": "blue"})
    assert inject == "User responded: blue"
