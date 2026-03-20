"""Tests for the reflection module — judge prompt, tool summary, verdict parsing."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.config import Config
from decafclaw.config_types import AgentConfig, LlmConfig, ReflectionConfig
from decafclaw.reflection import (
    ReflectionResult,
    _parse_verdict,
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

    def test_truncates_long_results(self):
        long_result = "x" * 1000
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
        assert len(result) < 1000
        assert "..." in result

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
