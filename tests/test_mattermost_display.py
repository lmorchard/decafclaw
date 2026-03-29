"""Tests for ConversationDisplay message sequencing."""

from unittest.mock import AsyncMock

import pytest

from decafclaw.mattermost_display import (
    THINKING_INDICATOR,
    THINKING_SUFFIX,
    ConversationDisplay,
)


def make_mock_client():
    client = AsyncMock()
    client.send = AsyncMock(return_value="post-id-1")
    client.edit_message = AsyncMock()
    client.delete_message = AsyncMock()
    client.send_typing = AsyncMock()
    return client


def make_display(client, initial_post_id=None):
    return ConversationDisplay(
        client=client,
        channel_id="ch1",
        root_id="root1",
        throttle_ms=0,
        initial_post_id=initial_post_id,
    )


@pytest.mark.asyncio
async def test_on_llm_start_edits_placeholder():
    """When initial_post_id is set, first on_llm_start keeps it as thinking placeholder."""
    client = make_mock_client()
    display = make_display(client, initial_post_id="placeholder-id")

    await display.on_llm_start(iteration=1)

    # First iteration reuses the initial post — no send or edit yet
    assert display._current_post_id == "placeholder-id"
    assert display._current_type == "thinking"
    client.send.assert_not_called()


@pytest.mark.asyncio
async def test_on_llm_start_no_placeholder_sends_new():
    """When no initial_post_id and iteration > 1, sends a new thinking post."""
    client = make_mock_client()
    display = make_display(client, initial_post_id=None)

    # Simulate iteration 2 (after tool calls)
    await display.on_llm_start(iteration=2)

    client.send.assert_awaited_once_with(
        "ch1", THINKING_INDICATOR, root_id="root1", attachments=None,
    )
    assert display._current_post_id == "post-id-1"
    assert display._current_type == "thinking"


@pytest.mark.asyncio
async def test_on_text_complete_sends_text():
    """on_text_complete sends text as a new message when no current post."""
    client = make_mock_client()
    display = make_display(client)

    await display.on_text_complete("Hello world")

    client.send.assert_awaited_once_with(
        "ch1", "Hello world", root_id="root1", attachments=None,
    )
    assert display._text_buffer == "Hello world"
    assert display._text_has_content is True


@pytest.mark.asyncio
async def test_on_text_complete_edits_thinking_placeholder():
    """on_text_complete edits existing thinking placeholder instead of sending new."""
    client = make_mock_client()
    display = make_display(client, initial_post_id="placeholder-id")
    await display.on_llm_start(iteration=1)

    await display.on_text_complete("Hello world")

    client.edit_message.assert_awaited_with("placeholder-id", "Hello world")
    client.send.assert_not_called()


@pytest.mark.asyncio
async def test_on_tool_start_sends_tool_message():
    """on_tool_start sends a tool indicator message."""
    client = make_mock_client()
    display = make_display(client)

    await display.on_tool_start("web_search", {"query": "test"}, tool_call_id="tc1")

    client.send.assert_awaited_once_with(
        "ch1", "\U0001f527 web_search...", root_id="root1",
        attachments=None,
    )
    assert "tc1" in display._tool_posts


@pytest.mark.asyncio
async def test_on_tool_start_reuses_thinking_placeholder():
    """First tool_start reuses the thinking placeholder instead of sending new."""
    client = make_mock_client()
    display = make_display(client, initial_post_id="placeholder-id")
    await display.on_llm_start(iteration=1)

    await display.on_tool_start("web_search", {}, tool_call_id="tc1")

    # Should edit the placeholder, not send a new message
    client.edit_message.assert_awaited_with(
        "placeholder-id", "\U0001f527 web_search...",
    )
    assert display._tool_posts["tc1"] == "placeholder-id"
    client.send.assert_not_called()


@pytest.mark.asyncio
async def test_on_tool_end_edits_tool_message():
    """After tool_start, on_tool_end edits the tool message with a checkmark."""
    client = make_mock_client()
    display = make_display(client)

    await display.on_tool_start("web_search", {"q": "test"}, tool_call_id="tc1")
    client.edit_message.reset_mock()

    await display.on_tool_end(
        "web_search", "result text", display_text=None, media=[],
        tool_call_id="tc1",
    )

    client.edit_message.assert_awaited_once_with(
        "post-id-1", "\U0001f527 web_search \u2714\ufe0f",
        props={"attachments": []},
    )
    assert "tc1" not in display._tool_posts


@pytest.mark.asyncio
async def test_on_tool_end_uses_display_text():
    """on_tool_end uses display_text when provided."""
    client = make_mock_client()
    display = make_display(client)

    await display.on_tool_start("web_search", {}, tool_call_id="tc1")
    client.edit_message.reset_mock()

    await display.on_tool_end(
        "web_search", "raw result", display_text="Custom display", media=[],
        tool_call_id="tc1",
    )

    client.edit_message.assert_awaited_once_with(
        "post-id-1", "Custom display", props={"attachments": []},
    )


@pytest.mark.asyncio
async def test_finalize_deletes_empty_placeholder():
    """If only a thinking placeholder exists with no real content, finalize deletes it."""
    client = make_mock_client()
    display = make_display(client, initial_post_id="placeholder-id")
    await display.on_llm_start(iteration=1)

    await display.finalize()

    client.delete_message.assert_awaited_once_with("placeholder-id")


@pytest.mark.asyncio
async def test_finalize_strips_thinking_suffix():
    """If text was posted with thinking suffix, finalize strips it."""
    client = make_mock_client()
    display = make_display(client, initial_post_id="placeholder-id")
    await display.on_llm_start(iteration=1)

    # Simulate streaming text (adds THINKING_SUFFIX via throttled edit)
    await display.on_text_chunk("Hello ")
    await display.on_text_chunk("world")
    client.edit_message.reset_mock()

    await display.finalize()

    # Final edit should have clean text without thinking suffix
    client.edit_message.assert_awaited_with("placeholder-id", "Hello world")


@pytest.mark.asyncio
async def test_finalize_is_idempotent():
    """Calling finalize twice does not double-delete or double-edit."""
    client = make_mock_client()
    display = make_display(client, initial_post_id="placeholder-id")
    await display.on_llm_start(iteration=1)

    await display.finalize()
    client.delete_message.reset_mock()

    await display.finalize()
    client.delete_message.assert_not_called()
