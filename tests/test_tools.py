"""Tests for core tools and tool execution."""

import pytest

from decafclaw.tools import execute_tool


@pytest.mark.asyncio
async def test_execute_tool_with_extra_tools(ctx):
    """Skill-provided tools on ctx.tools.extra are callable via execute_tool."""
    def mock_tool(ctx, query: str) -> str:
        return f"mock result: {query}"

    ctx.tools.extra = {"mock_search": mock_tool}
    result = await execute_tool(ctx, "mock_search", {"query": "hello"})
    assert result.text == "mock result: hello"


@pytest.mark.asyncio
async def test_execute_tool_unknown(ctx):
    """Unknown tool returns error message with guidance to use tool_search."""
    result = await execute_tool(ctx, "nonexistent_tool", {})
    assert "unknown tool" in result.text
    assert "nonexistent_tool" in result.text
    assert "tool_search" in result.text


@pytest.mark.asyncio
async def test_execute_tool_unknown_skill_tool_names_owning_skill(ctx, tmp_path):
    """A tool that belongs to a non-activated trusted-tier skill produces
    an error that names the owning skill and suggests activate_skill."""
    from decafclaw.skills import SkillInfo

    skill = SkillInfo(
        name="writing-clearly",
        description="Edit prose.",
        location=tmp_path / "writing-clearly",
        trust_tier="extra",
    )
    skill.location.mkdir()
    ctx.config.discovered_skills = [skill]
    ctx.config.skill_tool_owners = {"edit_with_strunk": "writing-clearly"}

    result = await execute_tool(ctx, "edit_with_strunk", {})
    assert "writing-clearly" in result.text
    assert "activate_skill('writing-clearly')" in result.text
    # The agent shouldn't be told to call tool_search — the recovery
    # is direct.
    assert "tool_search" not in result.text


@pytest.mark.asyncio
async def test_execute_tool_unknown_workspace_skill_tool_unapproved(
    ctx, tmp_path,
):
    """An unapproved workspace-tier skill's tool produces a 'request user
    approval' error."""
    from decafclaw.skills import SkillInfo

    skill = SkillInfo(
        name="ws-skill",
        description="Workspace.",
        location=tmp_path / "ws-skill",
        trust_tier="workspace",
    )
    skill.location.mkdir()
    ctx.config.discovered_skills = [skill]
    ctx.config.skill_tool_owners = {"ws_tool": "ws-skill"}

    result = await execute_tool(ctx, "ws_tool", {})
    assert "workspace skill 'ws-skill'" in result.text
    assert "approval" in result.text
    assert "activate_skill('ws-skill')" in result.text


@pytest.mark.asyncio
async def test_execute_tool_unknown_denied_skill_tool(ctx, tmp_path):
    """A denied skill's tool produces a tool-unavailable error."""
    from decafclaw.skills import SkillInfo
    from decafclaw.tools.skill_tools import _save_permission

    skill = SkillInfo(
        name="denied-skill",
        description="Denied.",
        location=tmp_path / "denied-skill",
        trust_tier="extra",
    )
    skill.location.mkdir()
    ctx.config.discovered_skills = [skill]
    ctx.config.skill_tool_owners = {"denied_tool": "denied-skill"}
    _save_permission(ctx.config, "denied-skill", "deny")

    result = await execute_tool(ctx, "denied_tool", {})
    assert "denied-skill" in result.text
    assert "denied" in result.text.lower()
    assert "unavailable" in result.text


@pytest.mark.asyncio
async def test_execute_tool_skill_tool_not_auto_fetched_from_deferred_pool(
    ctx, tmp_path,
):
    """Skill tools in the deferred pool are NOT auto-fetched. The agent
    must go through activate_skill so the skill body lands in context."""
    from decafclaw.skills import SkillInfo

    skill = SkillInfo(
        name="my-skill",
        description="My skill.",
        location=tmp_path / "my-skill",
        trust_tier="extra",
    )
    skill.location.mkdir()
    ctx.config.discovered_skills = [skill]
    ctx.config.skill_tool_owners = {"my_skill_tool": "my-skill"}
    # Put the tool in the deferred pool — pre-Phase 6 this would have
    # triggered auto-fetch and silent execution.
    ctx.tools.deferred_pool = [
        {
            "type": "function",
            "function": {
                "name": "my_skill_tool",
                "description": "A tool from my-skill.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    result = await execute_tool(ctx, "my_skill_tool", {})
    # The error should name the owning skill, not execute the tool.
    assert "my-skill" in result.text
    assert "activate_skill" in result.text
    # The tool should NOT have been added to the active set.
    assert "my_skill_tool" not in ctx.tools.extra


@pytest.mark.asyncio
async def test_execute_tool_unknown_suggests_close_match(ctx):
    """When the unknown name is close to a real tool, the error includes it as a suggestion."""
    # workspace_read is a real core tool; try a typo.
    result = await execute_tool(ctx, "workspce_read", {})
    assert "Did you mean" in result.text
    assert "workspace_read" in result.text


def test_suggest_tool_names_excludes_exact_input():
    """The unknown name must never be suggested back to the caller (#355).

    Regression: when the unknown name is also present in the candidate pool
    (e.g. a deferred/unactivated tool), difflib returned it as the top
    (ratio 1.0) match, so the hint read "Did you mean: project_advance"
    for a call to project_advance.
    """
    from decafclaw.tools import _suggest_tool_names

    candidates = {"project_advance", "project_task_done", "project_note"}
    suggestions = _suggest_tool_names("project_advance", candidates)
    assert "project_advance" not in suggestions


def test_suggest_tool_names_excludes_case_insensitive_input():
    """A candidate differing only in case is the same name — not a suggestion (#355)."""
    from decafclaw.tools import _suggest_tool_names

    candidates = {"Project_Advance", "project_note"}
    suggestions = _suggest_tool_names("project_advance", candidates)
    assert "Project_Advance" not in suggestions


def test_suggest_tool_names_tolerates_none_candidate():
    """A malformed tool def can leave a None in the candidate pool
    (deferred_names uses `.get("name")` with no default); the suggestion
    helper must not crash the unknown-tool error path (#355)."""
    from decafclaw.tools import _suggest_tool_names

    candidates = {None, "workspace_read"}
    # "workspce_read" is an intentional typo of the real tool name.
    suggestions = _suggest_tool_names("workspce_read", candidates)
    assert "workspace_read" in suggestions
    assert None not in suggestions


@pytest.mark.asyncio
async def test_execute_tool_unknown_suffix_match_suggests_mcp(ctx, monkeypatch):
    """Dropped mcp__ prefix should surface the full MCP name as a suggestion."""
    from unittest.mock import MagicMock

    from decafclaw import mcp_client

    mock_registry = MagicMock()
    mock_registry.get_tools.return_value = {
        "mcp__oblique-strategies__get_strategy": MagicMock(),
    }
    monkeypatch.setattr(mcp_client, "_registry", mock_registry)

    # Call with the prefix dropped, as Gemini sometimes does.
    result = await execute_tool(ctx, "strategies__get_strategy", {})
    assert "Did you mean" in result.text
    assert "mcp__oblique-strategies__get_strategy" in result.text


@pytest.mark.asyncio
async def test_execute_tool_mcp_routes_to_registry(ctx, monkeypatch):
    """MCP-namespaced tools route to the MCP registry."""
    from unittest.mock import AsyncMock, MagicMock

    from decafclaw import mcp_client

    mock_fn = AsyncMock(return_value="mcp result")
    mock_registry = MagicMock()
    mock_registry.get_tools.return_value = {"mcp__test__my_tool": mock_fn}

    monkeypatch.setattr(mcp_client, "_registry", mock_registry)
    result = await execute_tool(ctx, "mcp__test__my_tool", {"arg": "val"})
    assert result.text == "mcp result"
    mock_fn.assert_called_once_with({"arg": "val"})


@pytest.mark.asyncio
async def test_execute_tool_mcp_no_registry(ctx, monkeypatch):
    """MCP tool with no registry returns error."""
    from decafclaw import mcp_client
    monkeypatch.setattr(mcp_client, "_registry", None)
    result = await execute_tool(ctx, "mcp__test__my_tool", {})
    assert "[error: MCP tool" in result.text


@pytest.mark.asyncio
async def test_execute_tool_mcp_tool_not_found(ctx, monkeypatch):
    """MCP tool not in registry returns error."""
    from unittest.mock import MagicMock

    from decafclaw import mcp_client

    mock_registry = MagicMock()
    mock_registry.get_tools.return_value = {}

    monkeypatch.setattr(mcp_client, "_registry", mock_registry)
    result = await execute_tool(ctx, "mcp__test__missing", {})
    assert "[error: MCP tool" in result.text


@pytest.mark.asyncio
async def test_execute_tool_returns_tool_result(ctx):
    """execute_tool always returns a ToolResult."""
    from decafclaw.media import ToolResult
    result = await execute_tool(ctx, "current_time", {})
    assert isinstance(result, ToolResult)
    assert result.media == []


def test_context_stats(ctx):
    """context_stats returns a formatted stats report."""
    from decafclaw.tools.core import tool_context_stats
    # Set up minimal context state
    ctx.messages = [
        {"role": "system", "content": "You are a test agent."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    ctx.tokens.total_prompt = 100
    ctx.tokens.total_completion = 20

    result = tool_context_stats(ctx)
    assert "Context Stats" in result
    assert "System prompt" in result
    assert "Tool definitions" in result
    assert "Conversation history" in result
    assert "100" in result  # prompt tokens
    assert "user" in result
    assert "assistant" in result


def test_context_stats_with_none_messages(ctx):
    """context_stats works when ctx.messages is None (before first iteration)."""
    from decafclaw.tools.core import tool_context_stats
    ctx.messages = None
    result = tool_context_stats(ctx)
    assert "Context Stats" in result


def test_context_stats_in_forked_ctx(ctx):
    """context_stats works in a fork_for_tool_call ctx (messages inherited)."""
    from decafclaw.tools.core import tool_context_stats
    ctx.messages = [
        {"role": "system", "content": "You are a test agent."},
        {"role": "user", "content": "Hello"},
    ]
    forked = ctx.fork_for_tool_call("call_123")
    result = tool_context_stats(forked)
    assert "Context Stats" in result
    assert "user" in result
