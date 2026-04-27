"""Tests for sub-agent delegation."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.events import EventBus
from decafclaw.media import ToolResult
from decafclaw.tools.delegate import (
    DEFAULT_CHILD_SYSTEM_PROMPT,
    _run_child_turn,
    tool_delegate_task,
)


def _text(result):
    """Extract text from str or ToolResult."""
    return result.text if isinstance(result, ToolResult) else result


def _mock_llm_response(content="child result"):
    return {
        "content": content,
        "tool_calls": None,
        "role": "assistant",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.fixture
def ctx(config):
    """ctx fixture with a ConversationManager attached (required for delegation)."""
    from decafclaw.context import Context

    bus = EventBus()
    context = Context(config=config, event_bus=bus)
    context.conv_id = "test-conv"
    context.channel_id = "test-channel"
    context.user_id = "testuser"
    context.manager = ConversationManager(config, bus)
    return context


class TestRunChildTurn:
    @pytest.mark.asyncio
    async def test_basic_child_turn(self, ctx):
        """Child agent runs and returns result text."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="child says hello")

            result = await _run_child_turn(ctx, "say hello")

        assert result == "child says hello"
        mock_run.assert_called_once()

        # Check the child context passed to run_agent_turn
        call_args = mock_run.call_args
        child_ctx = call_args[0][0]
        assert child_ctx.config.agent.max_tool_iterations == 10
        assert child_ctx.config.system_prompt == DEFAULT_CHILD_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_delegate_task_excluded_from_child(self, ctx):
        """The delegate_task tool is excluded from child's allowed tools."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert "delegate_task" not in child_ctx.tools.allowed
        assert "activate_skill" not in child_ctx.tools.allowed
        # Core tools should be inherited
        assert "current_time" in child_ctx.tools.allowed

    @pytest.mark.asyncio
    async def test_inherits_parent_skill_data(self, ctx):
        """Child inherits parent's skill_data."""
        ctx.skills.data = {"vault_base_path": "obsidian/main"}

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.skills.data == {"vault_base_path": "obsidian/main"}

    @pytest.mark.asyncio
    async def test_inherits_parent_extra_tools(self, ctx):
        """Child inherits parent's extra_tools from activated skills.
        Uses a non-vault example since vault tools have their own
        allowlist policy (see TestVaultAccessPolicy)."""
        ctx.tools.extra = {"some_skill_tool": lambda ctx, **kw: "data"}

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert "some_skill_tool" in child_ctx.tools.extra
        assert "some_skill_tool" in child_ctx.tools.allowed

    @pytest.mark.asyncio
    async def test_timeout(self, ctx):
        """Child that exceeds timeout returns error."""
        async def slow_turn(*args, **kwargs):
            await asyncio.sleep(10)

        ctx.config.agent.child_timeout_sec = 0.1

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock, side_effect=slow_turn):
            result = await _run_child_turn(ctx, "slow task")

        assert "timed out" in _text(result)

    @pytest.mark.asyncio
    async def test_child_error(self, ctx):
        """Child that raises returns error text containing the exception message."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock,
                   side_effect=Exception("boom")):
            result = await _run_child_turn(ctx, "bad task")

        # _start_turn catches the exception and forwards it as [error: boom]
        assert "boom" in _text(result)

    @pytest.mark.asyncio
    async def test_cancel_propagation(self, ctx):
        """Parent cancel event is propagated to child context."""
        cancel = asyncio.Event()
        ctx.cancelled = cancel

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.cancelled is cancel

    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self, ctx):
        """Missing manager on parent ctx returns an error."""
        ctx.manager = None

        result = await _run_child_turn(ctx, "task")

        assert "error" in _text(result)
        assert "manager" in _text(result).lower()


    @pytest.mark.asyncio
    async def test_threads_parent_user_id(self, ctx):
        """Child turn inherits parent's user_id."""
        ctx.user_id = "alice"

        seen = {}

        async def fake_run_agent_turn(child_ctx, user_message, history, **kwargs):
            seen["user_id"] = child_ctx.user_id
            return ToolResult(text="done")

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock,
                   side_effect=fake_run_agent_turn):
            await _run_child_turn(ctx, "whatever")

        assert seen["user_id"] == "alice"


class TestToolDelegateTask:
    @pytest.mark.asyncio
    async def test_single_task(self, ctx):
        """Single task returns result wrapped in ToolResult."""
        with patch("decafclaw.tools.delegate._run_child_turn",
                   new_callable=AsyncMock, return_value="result one"):
            result = await tool_delegate_task(ctx, "do thing")

        assert isinstance(result, ToolResult)
        assert result.text == "result one"
        assert result.data is None

    @pytest.mark.asyncio
    async def test_empty_task(self, ctx):
        """Empty task returns error."""
        result = await tool_delegate_task(ctx, "")
        assert "error" in _text(result)

    @pytest.mark.asyncio
    async def test_whitespace_task(self, ctx):
        """Whitespace-only task returns error."""
        result = await tool_delegate_task(ctx, "   ")
        assert "error" in _text(result)


# -- Structured return schema (#395) ------------------------------------------


class TestParseStructuredOutput:
    """Unit tests for the JSON-block extraction helper."""

    def test_valid_object_extracts_and_strips(self):
        from decafclaw.tools.delegate import _parse_structured_output

        text = (
            "Found 3 issues in the auth module.\n\n"
            "```json\n"
            "{\"count\": 3, \"severity\": \"medium\"}\n"
            "```\n"
            "Trailing prose."
        )
        parsed, prose = _parse_structured_output(text)
        assert parsed == {"count": 3, "severity": "medium"}
        # Block is stripped from the prose half so the auto-rendered
        # ToolResult.data block doesn't get duplicated by the agent loop.
        assert "```json" not in prose
        assert "Found 3 issues" in prose
        assert "Trailing prose" in prose

    def test_no_json_block_returns_none(self):
        from decafclaw.tools.delegate import _parse_structured_output

        parsed, prose = _parse_structured_output("Just prose, no json.")
        assert parsed is None
        assert prose == "Just prose, no json."

    def test_malformed_json_returns_none(self):
        from decafclaw.tools.delegate import _parse_structured_output

        text = "prose\n```json\n{not valid json,\n```"
        parsed, prose = _parse_structured_output(text)
        assert parsed is None
        # On parse failure, prose is the original text unchanged so the
        # caller can fall back to text-only return cleanly.
        assert prose == text

    def test_list_root_is_accepted(self):
        """Schema is opaque — non-object roots parse fine since we
        don't enforce the shape."""
        from decafclaw.tools.delegate import _parse_structured_output

        text = "ok\n```json\n[1, 2, 3]\n```"
        parsed, prose = _parse_structured_output(text)
        assert parsed == [1, 2, 3]

    def test_empty_input_returns_none(self):
        from decafclaw.tools.delegate import _parse_structured_output

        parsed, prose = _parse_structured_output("")
        assert parsed is None
        assert prose == ""


class TestStructuredReturns:
    """Behaviour of `tool_delegate_task` with `return_schema`."""

    @pytest.mark.asyncio
    async def test_no_schema_text_only(self, ctx):
        """Without schema → text-only ToolResult, no data field
        (preserves byte-for-byte the existing single-task behaviour)."""
        with patch("decafclaw.tools.delegate._run_child_turn",
                   new_callable=AsyncMock, return_value="just prose"):
            result = await tool_delegate_task(ctx, "do thing")
        assert result.text == "just prose"
        assert result.data is None

    @pytest.mark.asyncio
    async def test_schema_with_valid_json_populates_data(self, ctx):
        child_response = (
            "Analyzed the auth module — three issues found.\n\n"
            "```json\n"
            "{\"count\": 3, \"items\": [\"x\", \"y\", \"z\"]}\n"
            "```"
        )
        with patch("decafclaw.tools.delegate._run_child_turn",
                   new_callable=AsyncMock, return_value=child_response):
            result = await tool_delegate_task(
                ctx, "audit auth",
                return_schema={"count": "int", "items": "list[str]"},
            )
        assert result.data == {"count": 3, "items": ["x", "y", "z"]}
        # Prose half is stripped of the JSON block.
        assert "```json" not in result.text
        assert "three issues found" in result.text

    @pytest.mark.asyncio
    async def test_schema_with_no_json_falls_back_to_prose(self, ctx, caplog):
        """Child forgot to emit JSON → silent fallback, debug log."""
        with patch("decafclaw.tools.delegate._run_child_turn",
                   new_callable=AsyncMock, return_value="forgot the json"):
            result = await tool_delegate_task(
                ctx, "audit", return_schema={"count": "int"},
            )
        assert result.text == "forgot the json"
        assert result.data is None

    @pytest.mark.asyncio
    async def test_schema_with_malformed_json_falls_back(self, ctx):
        """Bad JSON → silent fallback, raw text returned as-is."""
        bad = "prose\n```json\n{bad json,\n```"
        with patch("decafclaw.tools.delegate._run_child_turn",
                   new_callable=AsyncMock, return_value=bad):
            result = await tool_delegate_task(
                ctx, "audit", return_schema={"count": "int"},
            )
        assert result.text == bad
        assert result.data is None

    @pytest.mark.asyncio
    async def test_schema_passes_through_to_child_turn(self, ctx):
        """Verify the schema reaches `_run_child_turn` so the addendum
        gets rendered into the child system prompt."""
        seen = {}

        async def fake_run(parent_ctx, task, model="", max_iterations=0, **kwargs):
            seen["schema"] = kwargs.get("return_schema")
            return "ok"

        with patch("decafclaw.tools.delegate._run_child_turn",
                   side_effect=fake_run):
            await tool_delegate_task(
                ctx, "go", return_schema={"foo": "bar"},
            )
        assert seen["schema"] == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_render_schema_addendum_in_child_prompt(self, ctx, monkeypatch):
        """End-to-end: schema arrives, child system prompt contains
        the rendered JSON example."""
        seen_prompts = []

        async def fake_run_agent_turn(child_ctx, user_message, history, **kwargs):
            seen_prompts.append(child_ctx.config.system_prompt)
            return ToolResult(text='ok\n```json\n{"x": 1}\n```')

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        result = await tool_delegate_task(
            ctx, "investigate",
            return_schema={"x": "int", "items": ["a", "b"]},
        )
        assert seen_prompts, "child agent never ran"
        prompt = seen_prompts[0]
        # Addendum text + the rendered schema both present.
        assert "fenced JSON block matching this exact schema" in prompt
        assert "\"x\": \"int\"" in prompt
        # End-to-end the parsed payload landed.
        assert result.data == {"x": 1}


class TestDelegateRoutingThroughManager:
    @pytest.mark.asyncio
    async def test_delegate_routes_through_manager(self, ctx, monkeypatch):
        """delegate_task routes child turns through ConversationManager.enqueue_turn."""
        seen = []
        orig_enqueue = ctx.manager.enqueue_turn

        async def spy_enqueue(conv_id, *, kind, prompt, **kwargs):
            seen.append({"conv_id": conv_id, "kind": kind, "prompt": prompt[:40]})
            return await orig_enqueue(conv_id, kind=kind, prompt=prompt, **kwargs)

        monkeypatch.setattr(ctx.manager, "enqueue_turn", spy_enqueue)

        async def fake_run_agent_turn(child_ctx, user_message, history, **kwargs):
            return ToolResult(text="done")

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        result = await tool_delegate_task(ctx, "do a thing")

        assert len(seen) == 1
        assert seen[0]["kind"] is TurnKind.CHILD_AGENT
        assert "--child-" in seen[0]["conv_id"]
        assert "done" in _text(result)

    @pytest.mark.asyncio
    async def test_child_conv_id_format(self, ctx, monkeypatch):
        """Child conv_id is based on parent conv_id with a unique suffix."""
        seen_conv_ids = []
        orig_enqueue = ctx.manager.enqueue_turn

        async def spy_enqueue(conv_id, *, kind, prompt, **kwargs):
            seen_conv_ids.append(conv_id)
            return await orig_enqueue(conv_id, kind=kind, prompt=prompt, **kwargs)

        monkeypatch.setattr(ctx.manager, "enqueue_turn", spy_enqueue)
        monkeypatch.setattr(
            "decafclaw.agent.run_agent_turn",
            AsyncMock(return_value=ToolResult(text="ok")),
        )

        await tool_delegate_task(ctx, "task one")
        await tool_delegate_task(ctx, "task two")

        assert len(seen_conv_ids) == 2
        # Both should start with parent conv_id
        assert seen_conv_ids[0].startswith("test-conv--child-")
        assert seen_conv_ids[1].startswith("test-conv--child-")
        # They should be different (random suffix)
        assert seen_conv_ids[0] != seen_conv_ids[1]


# -- Vault access policy (#396) -----------------------------------------------


class TestVaultAccessPolicy:
    """Children get NO vault access by default; parent opts in via flags."""

    @pytest.mark.asyncio
    async def test_default_blocks_all_vault_tools(self, ctx):
        """No flags → child can't call any vault tool, read or write."""
        from decafclaw.tools.delegate import (
            _VAULT_READ_TOOLS,
            _VAULT_WRITE_TOOLS,
        )

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        for tool in _VAULT_READ_TOOLS:
            assert tool not in child_ctx.tools.allowed, (
                f"{tool} should be excluded by default"
            )
        for tool in _VAULT_WRITE_TOOLS:
            assert tool not in child_ctx.tools.allowed, (
                f"{tool} should always be excluded for children"
            )
        # Default also disables proactive retrieval.
        assert child_ctx.skip_vault_retrieval is True

    @pytest.mark.asyncio
    async def test_allow_vault_read_lets_in_read_set_only(self, ctx):
        """Opt-in for read tools; writes still excluded."""
        from decafclaw.tools.delegate import (
            _VAULT_READ_TOOLS,
            _VAULT_WRITE_TOOLS,
        )
        # Seed parent with the vault tools as activated-skill tools so
        # the inheritance path actually has something to keep/exclude.
        ctx.tools.extra = {
            tool: (lambda ctx, **kw: "x") for tool in _VAULT_READ_TOOLS | _VAULT_WRITE_TOOLS
        }

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            await _run_child_turn(ctx, "task", allow_vault_read=True)

        child_ctx = mock_run.call_args[0][0]
        for tool in _VAULT_READ_TOOLS:
            assert tool in child_ctx.tools.allowed, (
                f"{tool} should be allowed when allow_vault_read=True"
            )
        for tool in _VAULT_WRITE_TOOLS:
            assert tool not in child_ctx.tools.allowed, (
                f"{tool} should never be allowed for children"
            )

    @pytest.mark.asyncio
    async def test_allow_vault_retrieval_enables_proactive_retrieval(self, ctx):
        """Opt-in for proactive retrieval flips skip_vault_retrieval off."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            await _run_child_turn(ctx, "task", allow_vault_retrieval=True)

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.skip_vault_retrieval is False

    @pytest.mark.asyncio
    async def test_flags_combine(self, ctx):
        """Both flags can be set together."""
        from decafclaw.tools.delegate import (
            _VAULT_READ_TOOLS,
            _VAULT_WRITE_TOOLS,
        )
        ctx.tools.extra = {
            tool: (lambda ctx, **kw: "x") for tool in _VAULT_READ_TOOLS | _VAULT_WRITE_TOOLS
        }

        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")
            await _run_child_turn(
                ctx, "task",
                allow_vault_retrieval=True, allow_vault_read=True,
            )

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.skip_vault_retrieval is False
        for tool in _VAULT_READ_TOOLS:
            assert tool in child_ctx.tools.allowed
        for tool in _VAULT_WRITE_TOOLS:
            assert tool not in child_ctx.tools.allowed

    @pytest.mark.asyncio
    async def test_tool_wrapper_threads_flags_through(self, ctx):
        """`tool_delegate_task` parameters reach `_run_child_turn`."""
        seen = {}

        async def fake_run(parent_ctx, task, model="", max_iterations=0, **kwargs):
            seen["allow_vault_retrieval"] = kwargs.get("allow_vault_retrieval")
            seen["allow_vault_read"] = kwargs.get("allow_vault_read")
            return ToolResult(text="ok")

        with patch("decafclaw.tools.delegate._run_child_turn", side_effect=fake_run):
            await tool_delegate_task(
                ctx, "task",
                allow_vault_retrieval=True,
                allow_vault_read=True,
            )

        assert seen == {
            "allow_vault_retrieval": True,
            "allow_vault_read": True,
        }
