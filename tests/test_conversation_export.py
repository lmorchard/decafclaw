"""Unit tests for the markdown renderer in conversation_export."""

from __future__ import annotations

import json

from decafclaw.conversation_export import MAX_FENCED_BYTES, render_markdown


def test_empty_archive_emits_header_only():
    out = render_markdown([], "conv-abc")
    assert out.startswith("# Conversation conv-abc\n")
    assert "Exported " in out
    # No body sections should appear after the header.
    assert "## User" not in out
    assert "## Assistant" not in out


def test_user_and_assistant_text():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi back"},
    ]
    out = render_markdown(msgs, "c1")
    assert "## User\n\nhello" in out
    assert "## Assistant\n\nhi back" in out


def test_assistant_tool_calls_emit_tool_call_section():
    msgs = [
        {
            "role": "assistant",
            "content": "let me look",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "vault_read",
                        "arguments": '{"path": "notes/foo.md"}',
                    },
                }
            ],
        },
    ]
    out = render_markdown(msgs, "c1")
    assert "## Assistant\n\nlet me look" in out
    assert "### Tool call: vault_read" in out
    # Arguments should be pretty-printed JSON inside a fenced block.
    assert '"path": "notes/foo.md"' in out
    assert "```json" in out


def test_tool_result_uses_name_from_preceding_tool_call():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_x", "function": {"name": "tabstack_list", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_x", "content": "[tab data]"},
    ]
    out = render_markdown(msgs, "c1")
    assert "### Tool call: tabstack_list" in out
    assert "### Tool result: tabstack_list" in out
    assert "[tab data]" in out


def test_tool_result_falls_back_when_name_missing():
    msgs = [{"role": "tool", "tool_call_id": "orphan", "content": "x"}]
    out = render_markdown(msgs, "c1")
    assert "### Tool result: (unknown)" in out


def test_skipped_roles_do_not_appear():
    msgs = [
        {"role": "system", "content": "system prompt"},
        {"role": "model", "content": "gpt-4o"},
        {"role": "reflection", "content": "self-reflection text"},
        {"role": "confirmation_request", "content": "approve?"},
        {"role": "confirmation_response", "content": "yes"},
        {"role": "cancel_marker", "content": "cancelled"},
        {"role": "wake_trigger", "content": "wakey"},
        {"role": "user", "content": "real user message"},
    ]
    out = render_markdown(msgs, "c1")
    assert "system prompt" not in out
    assert "gpt-4o" not in out
    assert "self-reflection text" not in out
    assert "approve?" not in out
    assert "yes" not in out
    assert "cancelled" not in out
    assert "wakey" not in out
    assert "real user message" in out


def test_background_event_renders_as_blockquote():
    msgs = [
        {
            "role": "background_event",
            "kind": "scheduled_task",
            "content": "ran newsletter task",
        },
        {"role": "user", "content": "ok"},
    ]
    out = render_markdown(msgs, "c1")
    assert "> [background event] ran newsletter task" in out


def test_widget_code_block_renders_as_fenced():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "widget": {
                "widget_type": "code_block",
                "data": {"language": "python", "code": "print('hi')"},
            },
        },
    ]
    out = render_markdown(msgs, "c1")
    assert "```python" in out
    assert "print('hi')" in out


def test_other_widget_types_skipped():
    msgs = [
        {
            "role": "assistant",
            "content": "see the chart",
            "widget": {
                "widget_type": "iframe_sandbox",
                "data": {"html": "<script>evil()</script>"},
            },
        },
    ]
    out = render_markdown(msgs, "c1")
    assert "see the chart" in out
    assert "iframe_sandbox" not in out
    assert "evil()" not in out


def test_attachments_emit_markdown_references():
    msgs = [
        {
            "role": "user",
            "content": "look at this",
            "attachments": [
                {"path": "/tmp/screenshot.png"},
                {"file_id": "file-abc"},
            ],
        }
    ]
    out = render_markdown(msgs, "c1")
    assert "![](/tmp/screenshot.png)" in out
    assert "![](file-abc)" in out


def test_attachment_paths_with_unsafe_chars_are_wrapped():
    """Paths with spaces/parens/brackets must not break markdown parsing
    or open an injection vector — wrap them in CommonMark ``<...>`` form."""
    msgs = [
        {
            "role": "user",
            "content": "files",
            "attachments": [
                {"path": "/tmp/my file (1).png"},
                {"path": "/tmp/<bracketed>.png"},
                {"path": "/tmp/has)paren.png"},
            ],
        }
    ]
    out = render_markdown(msgs, "c1")
    # The dangerous chars are wrapped in <...> form; > inside the path
    # is backslash-escaped so it doesn't terminate the destination.
    assert "![](</tmp/my file (1).png>)" in out
    assert "![](</tmp/\\<bracketed\\>.png>)" in out
    assert "![](</tmp/has)paren.png>)" in out


def test_triple_backtick_body_uses_longer_fence():
    body = "outer\n```\ninner\n```\nafter"
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c", "function": {"name": "shell_exec", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c", "content": body},
    ]
    out = render_markdown(msgs, "c1")
    # Body contains a length-3 run, so the fence must be length-4.
    assert "````" in out
    # And the inner ``` must survive as-is between the longer fences.
    assert "\n```\ninner\n```\n" in out


def test_oversize_body_truncated_with_marker():
    huge = "a" * (MAX_FENCED_BYTES + 1024)
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c", "function": {"name": "x", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c", "content": huge},
    ]
    out = render_markdown(msgs, "c1")
    assert "[truncated, original was" in out
    # The original body should not appear in full.
    assert huge not in out


def test_tool_result_uses_data_when_text_missing():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c", "function": {"name": "foo", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c", "content": "", "data": {"k": "v"}},
    ]
    out = render_markdown(msgs, "c1")
    assert '"k": "v"' in out


def test_arguments_pretty_printed_when_parseable():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c",
                    "function": {
                        "name": "x",
                        "arguments": json.dumps({"a": 1, "b": [2, 3]}),
                    },
                }
            ],
        },
    ]
    out = render_markdown(msgs, "c1")
    # Pretty-printing should indent.
    assert '"a": 1' in out
    assert '"b": [' in out


def test_nameless_tool_calls_skipped():
    """Thinking-placeholder tool calls have no function name."""
    msgs = [
        {
            "role": "assistant",
            "content": "thinking",
            "tool_calls": [
                {"id": "thought-1", "function": {"name": None, "arguments": ""}},
            ],
        },
    ]
    out = render_markdown(msgs, "c1")
    assert "### Tool call:" not in out
    assert "thinking" in out
