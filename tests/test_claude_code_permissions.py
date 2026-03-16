"""Tests for Claude Code permission bridge."""

import asyncio

import pytest

from decafclaw.skills.claude_code.permissions import (
    AUTO_APPROVE_TOOLS,
    load_allowlist,
    make_permission_handler,
    matches_allowlist,
    save_allowlist_entry,
)
from decafclaw.tools.confirmation import request_confirmation

# -- Allowlist tests -----------------------------------------------------------


def test_load_empty_allowlist(config):
    assert load_allowlist(config) == []


def test_save_and_load_allowlist(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    save_allowlist_entry(config, "Edit")
    save_allowlist_entry(config, "Bash")
    patterns = load_allowlist(config)
    assert "Edit" in patterns
    assert "Bash" in patterns


def test_save_no_duplicates(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    save_allowlist_entry(config, "Edit")
    save_allowlist_entry(config, "Edit")
    assert load_allowlist(config).count("Edit") == 1


def test_matches_allowlist():
    assert matches_allowlist("Edit", ["Edit", "Bash"]) is True
    assert matches_allowlist("Read", ["Edit", "Bash"]) is False


def test_matches_allowlist_glob():
    assert matches_allowlist("Bash", ["Bas*"]) is True
    assert matches_allowlist("Edit", ["Bas*"]) is False


# -- Permission handler tests --------------------------------------------------


@pytest.mark.asyncio
async def test_auto_approves_read_only_tools(ctx):
    handler = make_permission_handler(ctx, ctx.config)
    for tool in AUTO_APPROVE_TOOLS:
        result = await handler(tool, {}, None)
        assert result.behavior == "allow", f"{tool} should be auto-approved"


@pytest.mark.asyncio
async def test_auto_approves_allowlisted_tools(ctx):
    ctx.config.agent_path.mkdir(parents=True, exist_ok=True)
    save_allowlist_entry(ctx.config, "Edit")
    handler = make_permission_handler(ctx, ctx.config)
    result = await handler("Edit", {"file_path": "test.py"}, None)
    assert result.behavior == "allow"


@pytest.mark.asyncio
async def test_requests_confirmation_for_unknown_tool(ctx):
    handler = make_permission_handler(ctx, ctx.config)

    async def approve():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "claude_code:Bash",
            "approved": True,
        })

    asyncio.create_task(approve())
    result = await handler("Bash", {"command": "ls"}, None)
    assert result.behavior == "allow"


@pytest.mark.asyncio
async def test_denied_confirmation_blocks_tool(ctx):
    handler = make_permission_handler(ctx, ctx.config)

    async def deny():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "claude_code:Bash",
            "approved": False,
        })

    asyncio.create_task(deny())
    result = await handler("Bash", {"command": "rm -rf /"}, None)
    assert result.behavior == "deny"


@pytest.mark.asyncio
async def test_always_approval_adds_to_allowlist(ctx):
    ctx.config.agent_path.mkdir(parents=True, exist_ok=True)
    handler = make_permission_handler(ctx, ctx.config)

    async def approve_always():
        await asyncio.sleep(0.05)
        await ctx.event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": ctx.context_id,
            "tool": "claude_code:Edit",
            "approved": True,
            "always": True,
        })

    asyncio.create_task(approve_always())
    result = await handler("Edit", {"file_path": "test.py"}, None)
    assert result.behavior == "allow"
    patterns = load_allowlist(ctx.config)
    assert "Edit" in patterns


@pytest.mark.asyncio
async def test_timeout_denies_tool(ctx):
    # Short timeout — no one responds
    result = await request_confirmation(
        ctx, tool_name="claude_code:Write", command="test",
        message="test", timeout=0.1,
    )
    assert result["approved"] is False
