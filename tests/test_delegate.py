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
    tool_delegate_tasks,
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


class TestDelegateTasks:
    """Parallel batch dispatch via `delegate_tasks` (#397).

    These tests patch ``_run_child_turn`` so we can control per-task
    return values, simulate failures, and observe concurrency without
    spinning up real child agents.
    """

    @pytest.mark.asyncio
    async def test_happy_path_three_tasks(self, ctx):
        """Three tasks all return prose; result has ok entries in
        input order, summary counts match, and the parent emits one
        progress event per child."""
        published: list[tuple] = []

        async def fake_publish(event_type, payload):
            published.append((event_type, payload))

        ctx.publish = fake_publish

        async def fake_run(parent_ctx, task, **kwargs):
            return f"result for {task}"

        with patch(
            "decafclaw.tools.delegate._run_child_turn", side_effect=fake_run,
        ):
            result = await tool_delegate_tasks(ctx, ["a", "b", "c"])

        assert isinstance(result, ToolResult)
        assert result.data["summary"] == {"total": 3, "ok": 3, "failed": 0}
        results = result.data["results"]
        assert [r["index"] for r in results] == [0, 1, 2]
        assert all(r["ok"] for r in results)
        assert results[0]["text"] == "result for a"
        assert results[1]["text"] == "result for b"
        assert results[2]["text"] == "result for c"
        assert "3 subtasks" in result.text
        # One progress event per completion.
        statuses = [p for p in published if p[0] == "tool_status"]
        assert len(statuses) == 3
        assert all(p[1]["tool"] == "delegate_tasks" for p in statuses)

    @pytest.mark.asyncio
    async def test_mixed_failures(self, ctx):
        """One child returns an error ToolResult, another raises;
        siblings still complete and the result reflects per-task
        status."""
        async def fake_run(parent_ctx, task, **kwargs):
            if task == "boom":
                raise RuntimeError("kaboom")
            if task == "softfail":
                return ToolResult(text="[error: subtask timed out]")
            return f"ok for {task}"

        with patch(
            "decafclaw.tools.delegate._run_child_turn", side_effect=fake_run,
        ):
            result = await tool_delegate_tasks(
                ctx, ["fine", "softfail", "boom", "alsofine"],
            )

        assert result.data["summary"] == {
            "total": 4, "ok": 2, "failed": 2,
        }
        results = result.data["results"]
        assert results[0] == {"index": 0, "ok": True, "text": "ok for fine"}
        assert results[1]["ok"] is False
        assert "[error: subtask timed out]" in results[1]["error"]
        assert results[2]["ok"] is False
        assert "kaboom" in results[2]["error"]
        assert results[3] == {
            "index": 3, "ok": True, "text": "ok for alsofine",
        }

    @pytest.mark.asyncio
    async def test_empty_list_errors(self, ctx):
        result = await tool_delegate_tasks(ctx, [])
        assert isinstance(result, ToolResult)
        assert result.text.startswith("[error:")
        assert "non-empty list" in result.text

    @pytest.mark.asyncio
    async def test_blank_entry_errors(self, ctx):
        result = await tool_delegate_tasks(ctx, ["valid", "  "])
        assert result.text.startswith("[error:")
        assert "tasks[1]" in result.text

    @pytest.mark.asyncio
    async def test_non_string_entry_errors(self, ctx):
        result = await tool_delegate_tasks(ctx, ["valid", 42])  # type: ignore[list-item]
        assert result.text.startswith("[error:")
        assert "tasks[1]" in result.text

    @pytest.mark.asyncio
    async def test_over_cap_errors(self, ctx):
        ctx.config.agent.max_tasks_per_delegate_call = 2
        result = await tool_delegate_tasks(ctx, ["a", "b", "c"])
        assert result.text.startswith("[error:")
        assert "cap is 2" in result.text

    @pytest.mark.asyncio
    async def test_concurrency_cap_honored(self, ctx):
        """With cap=2 and 4 tasks, never more than 2 children are
        in-flight simultaneously."""
        ctx.config.agent.max_parallel_delegates = 2
        ctx.config.agent.max_tasks_per_delegate_call = 10

        state = {"in_flight": 0, "max_observed": 0}
        state_lock = asyncio.Lock()
        cap_full = asyncio.Event()

        async def fake_run(parent_ctx, task, **kwargs):
            async with state_lock:
                state["in_flight"] += 1
                if state["in_flight"] > state["max_observed"]:
                    state["max_observed"] = state["in_flight"]
                if state["in_flight"] >= 2:
                    cap_full.set()
            # Wait until both slots are occupied before any one releases,
            # so a buggy implementation that allowed 3+ would visibly
            # exceed the cap.
            await cap_full.wait()
            async with state_lock:
                state["in_flight"] -= 1
            return f"done {task}"

        with patch(
            "decafclaw.tools.delegate._run_child_turn", side_effect=fake_run,
        ):
            result = await tool_delegate_tasks(
                ctx, ["a", "b", "c", "d"],
            )

        assert state["max_observed"] == 2
        assert result.data["summary"]["ok"] == 4

    @pytest.mark.asyncio
    async def test_structured_return_parses_per_task(self, ctx):
        """When return_schema is supplied, each successful entry's
        data is the parsed JSON and text is the prose."""
        async def fake_run(parent_ctx, task, **kwargs):
            return (
                f"prose for {task}\n\n"
                "```json\n"
                f'{{"task": "{task}", "score": 7}}\n'
                "```\n"
            )

        with patch(
            "decafclaw.tools.delegate._run_child_turn", side_effect=fake_run,
        ):
            result = await tool_delegate_tasks(
                ctx, ["x", "y"], return_schema={"task": "string", "score": 0},
            )

        results = result.data["results"]
        assert results[0]["data"] == {"task": "x", "score": 7}
        assert results[0]["text"] == "prose for x"
        assert results[1]["data"] == {"task": "y", "score": 7}
        assert results[1]["text"] == "prose for y"

    @pytest.mark.asyncio
    async def test_event_override_routed_to_child_id(self, ctx):
        """Each child's _run_child_turn call gets a unique override
        for `event_context_id`, so parent UI doesn't get flooded."""
        seen_overrides: list[str | None] = []

        async def fake_run(parent_ctx, task, **kwargs):
            seen_overrides.append(kwargs.get("event_context_id_override"))
            return f"ok {task}"

        with patch(
            "decafclaw.tools.delegate._run_child_turn", side_effect=fake_run,
        ):
            await tool_delegate_tasks(ctx, ["a", "b", "c"])

        assert all(o is not None for o in seen_overrides)
        assert len(set(seen_overrides)) == 3  # all distinct
        assert all("delegate-tasks-child-" in o for o in seen_overrides)

    @pytest.mark.asyncio
    async def test_run_child_turn_event_override_kwarg(self, ctx):
        """The new `event_context_id_override` kwarg on
        `_run_child_turn` reaches the setup callback that
        `enqueue_turn` invokes — so the singular path stays unaffected
        and the plural override actually lands on the child ctx."""
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(
                ctx, "task",
                event_context_id_override="custom-override-id",
            )

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.event_context_id == "custom-override-id"

    @pytest.mark.asyncio
    async def test_singular_event_routing_unchanged(self, ctx):
        """When the override is omitted (singular case), the child
        still routes events to the parent's subscriber id."""
        ctx.event_context_id = "parent-subscriber-id"
        with patch("decafclaw.agent.run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ToolResult(text="ok")

            await _run_child_turn(ctx, "task")

        child_ctx = mock_run.call_args[0][0]
        assert child_ctx.event_context_id == "parent-subscriber-id"
