"""Tests for the ask_user core tool."""

import pytest

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.tools.core import (
    _ask_user_default_on_response,
    _normalize_ask_user_options,
    tool_ask_user,
)

# ------------- option normalization -------------


def test_normalize_bare_strings():
    out = _normalize_ask_user_options(["alpha", "beta"])
    assert out == [
        {"value": "alpha", "label": "alpha"},
        {"value": "beta", "label": "beta"},
    ]


def test_normalize_dicts_with_description():
    out = _normalize_ask_user_options([
        {"value": "a", "label": "Alpha", "description": "first"},
        {"value": "b", "label": "Beta"},
    ])
    assert out == [
        {"value": "a", "label": "Alpha", "description": "first"},
        {"value": "b", "label": "Beta"},
    ]


def test_normalize_dict_missing_label_is_rejected():
    """Per the tool-definition schema, dict options require both value
    and label. A dict with only value is invalid input — callers should
    use the bare-string shortcut if they want value=label."""
    assert _normalize_ask_user_options([{"value": "x"}]) is None


def test_normalize_mixed_strings_and_dicts():
    out = _normalize_ask_user_options(
        ["alpha", {"value": "b", "label": "Beta"}])
    assert out[0]["label"] == "alpha"
    assert out[1]["label"] == "Beta"


def test_normalize_empty_returns_none():
    assert _normalize_ask_user_options([]) is None


def test_normalize_bad_entry_returns_none():
    assert _normalize_ask_user_options([123]) is None
    assert _normalize_ask_user_options([{"label": "no value"}]) is None


# ------------- default on_response -------------


def test_default_response_single_uses_label():
    options = [{"value": "a", "label": "Alpha"},
               {"value": "b", "label": "Beta"}]
    cb = _ask_user_default_on_response(options, allow_multiple=False)
    assert cb({"selected": "a"}) == "User selected: Alpha"


def test_default_response_single_unknown_value_falls_back():
    options = [{"value": "a", "label": "Alpha"}]
    cb = _ask_user_default_on_response(options, allow_multiple=False)
    assert cb({"selected": "mystery"}) == "User selected: mystery"


def test_default_response_single_missing_selection():
    options = [{"value": "a", "label": "Alpha"}]
    cb = _ask_user_default_on_response(options, allow_multiple=False)
    assert "did not select" in cb({})


def test_default_response_multi_joins_labels():
    options = [{"value": "a", "label": "Alpha"},
               {"value": "b", "label": "Beta"},
               {"value": "c", "label": "Gamma"}]
    cb = _ask_user_default_on_response(options, allow_multiple=True)
    assert cb({"selected": ["a", "c"]}) == "User selected: Alpha, Gamma"


def test_default_response_multi_empty():
    options = [{"value": "a", "label": "Alpha"}]
    cb = _ask_user_default_on_response(options, allow_multiple=True)
    assert "nothing" in cb({"selected": []})


# ------------- tool_ask_user integration -------------


@pytest.mark.asyncio
async def test_ask_user_happy_path():
    ctx = object()  # unused
    result = await tool_ask_user(
        ctx, prompt="Which deploy target?",
        options=["production", "staging"])
    assert isinstance(result, ToolResult)
    assert result.end_turn is True
    assert isinstance(result.widget, WidgetRequest)
    assert result.widget.widget_type == "multiple_choice"
    assert result.widget.data["prompt"] == "Which deploy target?"
    assert len(result.widget.data["options"]) == 2
    assert result.widget.data["allow_multiple"] is False
    assert result.widget.on_response is not None
    assert "awaiting user response" in result.text


@pytest.mark.asyncio
async def test_ask_user_allow_multiple():
    ctx = object()
    result = await tool_ask_user(
        ctx, prompt="Which?",
        options=["a", "b"], allow_multiple=True)
    assert result.widget.data["allow_multiple"] is True
    # Callback handles a list of selections.
    inject = result.widget.on_response({"selected": ["a", "b"]})
    assert inject == "User selected: a, b"


@pytest.mark.asyncio
async def test_ask_user_empty_options_returns_error():
    ctx = object()
    result = await tool_ask_user(ctx, prompt="?", options=[])
    assert result.widget is None
    assert "error" in result.text.lower()


@pytest.mark.asyncio
async def test_ask_user_blank_prompt_returns_error():
    ctx = object()
    result = await tool_ask_user(ctx, prompt="   ", options=["a"])
    assert result.widget is None
    assert "error" in result.text.lower()


@pytest.mark.asyncio
async def test_ask_user_default_callback_wired_correctly():
    """The default callback formatting matches what tests would
    expect: integrates with the normalized options so label > value."""
    ctx = object()
    result = await tool_ask_user(
        ctx, prompt="?",
        options=[{"value": "v1", "label": "Nice Label"}])
    inject = result.widget.on_response({"selected": "v1"})
    assert inject == "User selected: Nice Label"
