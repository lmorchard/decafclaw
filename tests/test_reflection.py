"""Tests for the reflection module — judge prompt, tool summary, verdict parsing."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.config import Config
from decafclaw.config_types import AgentConfig, LlmConfig, ReflectionConfig
from decafclaw.reflection import (
    ReflectionResult,
    _parse_verdict,
    _summarize_tool_result,
    build_prior_turn_summary,
    build_tool_summary,
    evaluate_response,
    load_reflection_prompt,
)

# ---------------------------------------------------------------------------
# build_tool_summary
# ---------------------------------------------------------------------------

class TestBuildToolSummary:
    def test_no_tools(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        assert build_tool_summary(history, 0) == ""

    def test_with_tools(self):
        history = [
            {"role": "user", "content": "search for cats"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {
                    "name": "memory_search",
                    "arguments": json.dumps({"query": "cats"}),
                }},
            ]},
            {"role": "tool", "content": "Found: cat facts document"},
            {"role": "assistant", "content": "Here are some cat facts."},
        ]
        result = build_tool_summary(history, 0)
        assert "memory_search" in result
        assert "cats" in result
        assert "cat facts document" in result

    def test_includes_widget_response_user_messages(self):
        """A synthetic user message from a widget submission should
        surface in the tool summary so the judge knows the user
        actually answered the question the agent asked."""
        history = [
            {"role": "user", "content": "ask me a question"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {
                    "name": "ask_user_multiple_choice",
                    "arguments": json.dumps(
                        {"prompt": "Pick one", "options": ["a", "b"]}),
                }},
            ]},
            {"role": "tool",
             "content": "[awaiting user response: Pick one]"},
            {"role": "user", "source": "widget_response",
             "content": "User selected: a"},
            {"role": "assistant", "content": "You picked A."},
        ]
        result = build_tool_summary(history, 0)
        assert "ask_user_multiple_choice" in result
        assert "User responded to widget" in result
        assert "User selected: a" in result

    def test_widget_response_not_treated_as_turn_boundary(self):
        """build_prior_turn_summary must skip synthetic widget
        responses when finding turn starts — they're mid-turn, not
        new turns."""
        from decafclaw.reflection import build_prior_turn_summary
        history = [
            {"role": "user", "content": "first turn"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {
                    "name": "ask_user_multiple_choice",
                    "arguments": json.dumps({"prompt": "x", "options": []}),
                }},
            ]},
            {"role": "tool", "content": "[awaiting]"},
            {"role": "user", "source": "widget_response",
             "content": "User selected: a"},
            {"role": "assistant", "content": "ok"},
            # Current turn starts here:
            {"role": "user", "content": "second turn"},
        ]
        # turn_start_index is the second-turn user (idx 5); prior is
        # first turn + synthetic. Synthetic must NOT be a turn boundary.
        result = build_prior_turn_summary(history, 5, max_turns=3)
        # Should have exactly one prior turn ("first turn"), with
        # the ask_user_multiple_choice call in its tool lines.
        assert "ask_user_multiple_choice" in result
        # The synthetic response is included as a tool-line entry;
        # there's no second "User selected" turn-boundary spawning a
        # second prior turn.
        assert result.count("Tools used in prior turns") == 1

    def test_truncates_long_results(self):
        long_result = "x" * 3000
        history = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {
                    "name": "workspace_read",
                    "arguments": json.dumps({"path": "big.txt"}),
                }},
            ]},
            {"role": "tool", "content": long_result},
        ]
        result = build_tool_summary(history, 0)
        assert len(result) < 3000
        assert "..." in result

    def test_custom_max_result_len(self):
        long_result = "x" * 500
        history = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {
                    "name": "workspace_read",
                    "arguments": json.dumps({"path": "big.txt"}),
                }},
            ]},
            {"role": "tool", "content": long_result},
        ]
        result = build_tool_summary(history, 0, max_result_len=100)
        # The result line should be truncated to ~100 chars + "..."
        assert "..." in result
        # With default (2000), it should NOT be truncated
        result_default = build_tool_summary(history, 0)
        assert "..." not in result_default

    def test_respects_turn_start_index(self):
        """Only includes tools from the current turn."""
        history = [
            # Previous turn
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc0", "function": {
                    "name": "old_tool", "arguments": "{}",
                }},
            ]},
            {"role": "tool", "content": "old result"},
            {"role": "assistant", "content": "old answer"},
            # Current turn starts at index 4
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer"},
        ]
        result = build_tool_summary(history, 4)
        assert result == ""  # no tools in current turn
        result_old = build_tool_summary(history, 0)
        assert "old_tool" in result_old



class TestSummarizeToolResult:
    def test_short_content_unchanged(self):
        content = "Short result"
        assert _summarize_tool_result(content, 2000) == content

    def test_preserves_headings(self):
        content = "# Title\n" + "x" * 5000
        result = _summarize_tool_result(content, 500)
        assert "# Title" in result

    def test_preserves_tldrs(self):
        content = "x" * 3000 + "\n> tl;dr: Important summary.\n" + "y" * 3000
        result = _summarize_tool_result(content, 500)
        assert "Important summary" in result

    def test_preserves_result_separators(self):
        content = (
            "--- Result 1 ---\n# Page A\nfoo\n"
            + "x" * 5000 + "\n"
            "--- Result 2 ---\n# Page B\nbar\n"
            + "y" * 5000
        )
        result = _summarize_tool_result(content, 500)
        assert "Result 1" in result
        assert "Result 2" in result
        assert "Page A" in result
        assert "Page B" in result

    def test_single_long_line_preserves_snippet(self):
        """A single long line (e.g. minified JSON) should still produce a snippet."""
        content = "x" * 10000
        result = _summarize_tool_result(content, 500)
        # Should have some content, not just the omission note
        assert result.startswith("x")
        assert "chars of detail omitted" in result

    def test_result_within_max_len(self):
        """Summary + omission note should fit within max_len."""
        content = "# Title\n" + "x" * 10000
        result = _summarize_tool_result(content, 500)
        assert len(result) <= 600  # max_len + reasonable omission note

    def test_includes_omission_note(self):
        content = "x" * 10000
        result = _summarize_tool_result(content, 500)
        assert "chars of detail omitted" in result

    def test_structural_lines_prioritized_over_body(self):
        """Even with lots of body text, all structural lines should appear."""
        content = ""
        for i in range(10):
            content += f"# Page {i}\n> tl;dr: Summary {i}\n" + "x" * 2000 + "\n"
        result = _summarize_tool_result(content, 2000)
        for i in range(10):
            assert f"Page {i}" in result
            assert f"Summary {i}" in result


class TestBuildToolSummaryVaultSearch:
    """Tests specifically for vault_search tool result summarization."""

    def test_vault_search_preserves_page_titles(self):
        """Regression: vault_search with large results should preserve page
        titles/summaries so the judge can verify the agent's response references
        real content, not hallucinations. Issue #200."""
        # Simulate vault_search returning multiple pages — each page has a title
        # and tl;dr that the agent references in its response. With raw truncation
        # at 2000 chars, only the first result's partial text would be visible.
        result_content = "Found 5 result(s):\n\n"
        for i, title in enumerate([
            "Comparison of Short Stories",
            "There was no minimum safe size",
            "Rays of a Distant Sun",
            "When the Halloween invite",
            "Les Orchard",
        ]):
            result_content += f"--- Result {i+1} ---\n# {title}\n\n> tl;dr: Summary of {title}.\n\n"
            result_content += f"{'x' * 3000}\n\n"  # bulk content that makes it large

        history = [
            {"role": "user", "content": "What do you know about my stories?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {
                    "name": "vault_search",
                    "arguments": json.dumps({"query": "stories"}),
                }},
            ]},
            {"role": "tool", "content": result_content},
            {"role": "assistant", "content": "You have written three stories..."},
        ]
        summary = build_tool_summary(history, 0, max_result_len=2000)
        # All page titles should be visible to the judge, not just the first one
        assert "Comparison of Short Stories" in summary
        assert "There was no minimum safe size" in summary
        assert "Rays of a Distant Sun" in summary
        assert "When the Halloween invite" in summary
        assert "Les Orchard" in summary

    def test_vault_search_preserves_tldrs(self):
        """tl;dr lines from vault_search results should survive summarization."""
        result_content = (
            "Found 2 result(s):\n\n"
            "--- Result 1 ---\n# Page One\n\n> tl;dr: A story about robots.\n\n"
            + "x" * 5000 + "\n\n"
            "--- Result 2 ---\n# Page Two\n\n> tl;dr: A poem about cats.\n\n"
            + "y" * 5000
        )
        history = [
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "function": {
                    "name": "vault_search",
                    "arguments": json.dumps({"query": "writing"}),
                }},
            ]},
            {"role": "tool", "content": result_content},
        ]
        summary = build_tool_summary(history, 0, max_result_len=2000)
        assert "A story about robots" in summary
        assert "A poem about cats" in summary


# ---------------------------------------------------------------------------
# build_prior_turn_summary
# ---------------------------------------------------------------------------

def _make_tool_turn(user_msg: str, tool_name: str, tool_args: str, tool_result: str) -> list:
    """Helper: build a complete turn with user msg, tool call, result, and reply."""
    return [
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "tc", "function": {
                "name": tool_name,
                "arguments": json.dumps({"query": tool_args}),
            }},
        ]},
        {"role": "tool", "content": tool_result},
        {"role": "assistant", "content": f"Answer about {tool_name}"},
    ]


class TestBuildPriorTurnSummary:
    def test_first_turn_empty(self):
        assert build_prior_turn_summary([], 0) == ""

    def test_no_tools_in_prior_turns(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "current question"},
        ]
        result = build_prior_turn_summary(history, 2)
        assert result == ""

    def test_extracts_prior_tools(self):
        turn1 = _make_tool_turn("q1", "wiki_read", "therapy", "Jan 19 notes")
        turn2 = _make_tool_turn("q2", "memory_search", "visits", "Found 3 entries")
        current = [{"role": "user", "content": "summarize"}]
        history = turn1 + turn2 + current
        turn_start = len(turn1) + len(turn2)  # index of current user msg

        result = build_prior_turn_summary(history, turn_start)
        assert "wiki_read" in result
        assert "memory_search" in result
        assert "Jan 19 notes" in result
        assert "Found 3 entries" in result
        assert result.startswith("Tools used in prior turns:")

    def test_respects_max_turns(self):
        turns = []
        for i in range(5):
            turns.extend(_make_tool_turn(f"q{i}", f"tool_{i}", f"arg{i}", f"result_{i}"))
        current = [{"role": "user", "content": "final"}]
        history = turns + current
        turn_start = len(turns)

        result = build_prior_turn_summary(history, turn_start, max_turns=2)
        # Only the last 2 turns (tool_3, tool_4) should appear
        assert "tool_3" in result
        assert "tool_4" in result
        assert "tool_0" not in result
        assert "tool_1" not in result
        assert "tool_2" not in result

    def test_skips_reflection_critiques(self):
        """Reflection retry messages (role=user, content starts with [reflection])
        should not count as turn boundaries."""
        turn1 = _make_tool_turn("q1", "real_tool", "arg1", "result_1")
        # Reflection critique injected as a user message
        reflection = [
            {"role": "user", "content": "[reflection] Your previous response..."},
            {"role": "assistant", "content": "Sorry, let me try again."},
        ]
        current = [{"role": "user", "content": "next"}]
        history = turn1 + reflection + current
        turn_start = len(turn1) + len(reflection)

        result = build_prior_turn_summary(history, turn_start, max_turns=3)
        # real_tool should appear (from the real user turn)
        assert "real_tool" in result
        # The reflection message should NOT create its own turn boundary
        # that pushes real turns out of the window

    def test_truncates_results(self):
        turn = _make_tool_turn("q", "big_tool", "arg", "x" * 500)
        current = [{"role": "user", "content": "next"}]
        history = turn + current
        turn_start = len(turn)

        result = build_prior_turn_summary(history, turn_start, max_result_len=100)
        assert "..." in result


# ---------------------------------------------------------------------------
# _parse_verdict
# ---------------------------------------------------------------------------

class TestParseVerdict:
    def test_clean_json(self):
        passed, critique = _parse_verdict('{"pass": true, "critique": ""}')
        assert passed is True
        assert critique == ""

    def test_fail_with_critique(self):
        passed, critique = _parse_verdict(
            '{"pass": false, "critique": "You missed the point"}')
        assert passed is False
        assert critique == "You missed the point"

    def test_json_embedded_in_text(self):
        text = (
            "Let me evaluate this response.\n\n"
            "The user asked about weather but the agent talked about sports.\n\n"
            '{"pass": false, "critique": "Off topic"}'
        )
        passed, critique = _parse_verdict(text)
        assert passed is False
        assert critique == "Off topic"

    def test_unparseable(self):
        passed, critique = _parse_verdict("This is just plain text with no JSON")
        assert passed is None

    def test_empty_string(self):
        passed, critique = _parse_verdict("")
        assert passed is None

    def test_string_false(self):
        """Judge returns "pass": "false" as string — should be treated as False."""
        passed, critique = _parse_verdict('{"pass": "false", "critique": "bad"}')
        assert passed is False
        assert critique == "bad"

    def test_string_true(self):
        passed, _ = _parse_verdict('{"pass": "true", "critique": ""}')
        assert passed is True

    def test_integer_0(self):
        passed, _ = _parse_verdict('{"pass": 0, "critique": "nope"}')
        assert passed is False

    def test_integer_1(self):
        passed, _ = _parse_verdict('{"pass": 1, "critique": ""}')
        assert passed is True


# ---------------------------------------------------------------------------
# load_reflection_prompt
# ---------------------------------------------------------------------------

class TestLoadReflectionPrompt:
    def test_default(self, tmp_path):
        config = Config(agent=AgentConfig(data_home=str(tmp_path), id="test"))
        result = load_reflection_prompt(config)
        assert "{user_message}" in result
        assert "{agent_response}" in result

    def test_override_file(self, tmp_path):
        agent_dir = tmp_path / "test"
        agent_dir.mkdir()
        override = agent_dir / "REFLECTION.md"
        override.write_text("Custom prompt: {user_message}")
        config = Config(agent=AgentConfig(data_home=str(tmp_path), id="test"))
        result = load_reflection_prompt(config)
        assert result == "Custom prompt: {user_message}"


# ---------------------------------------------------------------------------
# evaluate_response
# ---------------------------------------------------------------------------

class TestEvaluateResponse:
    @pytest.fixture
    def config(self, tmp_path):
        return Config(
            agent=AgentConfig(data_home=str(tmp_path), id="test"),
            llm=LlmConfig(url="http://test/v1/chat/completions",
                           model="test-model", api_key="test-key"),
            reflection=ReflectionConfig(enabled=True),
        )

    @pytest.mark.asyncio
    async def test_pass(self, config):
        mock_response = {"content": '{"pass": true, "critique": ""}'}
        with patch("decafclaw.reflection.call_llm", new_callable=AsyncMock,
                    return_value=mock_response):
            result = await evaluate_response(config, "hello", "Hi there!", "")
        assert result.passed is True
        assert result.critique == ""
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_fail_with_critique(self, config):
        mock_response = {
            "content": '{"pass": false, "critique": "Did not greet the user"}'
        }
        with patch("decafclaw.reflection.call_llm", new_callable=AsyncMock,
                    return_value=mock_response):
            result = await evaluate_response(config, "hello", "The weather is nice", "")
        assert result.passed is False
        assert result.critique == "Did not greet the user"

    @pytest.mark.asyncio
    async def test_unparseable_treated_as_pass(self, config):
        mock_response = {"content": "I can't decide, this is fine I guess"}
        with patch("decafclaw.reflection.call_llm", new_callable=AsyncMock,
                    return_value=mock_response):
            result = await evaluate_response(config, "hello", "Hi!", "")
        assert result.passed is True
        assert "unparseable" in result.error

    @pytest.mark.asyncio
    async def test_network_error_treated_as_pass(self, config):
        with patch("decafclaw.reflection.call_llm", new_callable=AsyncMock,
                    side_effect=ConnectionError("timeout")):
            result = await evaluate_response(config, "hello", "Hi!", "")
        assert result.passed is True
        assert "timeout" in result.error

    @pytest.mark.asyncio
    async def test_uses_reflection_model(self, config):
        config.reflection.model = "cheap-judge-model"
        mock_response = {"content": '{"pass": true, "critique": ""}'}
        with patch("decafclaw.reflection.call_llm", new_callable=AsyncMock,
                    return_value=mock_response) as mock_call:
            await evaluate_response(config, "hello", "Hi!", "")
        # Check that the judge model was used
        _, kwargs = mock_call.call_args
        assert kwargs["llm_model"] == "cheap-judge-model"

    @pytest.mark.asyncio
    async def test_prior_turn_summary_in_prompt(self, config):
        prior = "Tools used in prior turns:\nTool: wiki_read(page=\"Therapy\")"
        mock_response = {"content": '{"pass": true, "critique": ""}'}
        with patch("decafclaw.reflection.call_llm", new_callable=AsyncMock,
                    return_value=mock_response) as mock_call:
            await evaluate_response(
                config, "summarize therapy", "Here is the summary.", "",
                prior_turn_summary=prior,
            )
        prompt = mock_call.call_args[0][1][0]["content"]
        assert "Tools used in prior turns:" in prompt
        assert "wiki_read" in prompt

    @pytest.mark.asyncio
    async def test_empty_prior_turn_summary(self, config):
        mock_response = {"content": '{"pass": true, "critique": ""}'}
        with patch("decafclaw.reflection.call_llm", new_callable=AsyncMock,
                    return_value=mock_response) as mock_call:
            await evaluate_response(config, "hello", "Hi!", "")
        prompt = mock_call.call_args[0][1][0]["content"]
        assert "Tools used in prior turns:" not in prompt
