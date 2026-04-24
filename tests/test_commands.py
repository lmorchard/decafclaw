"""Tests for user-invokable commands."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.commands import (
    execute_command,
    format_help,
    parse_command_trigger,
    substitute_body,
)
from decafclaw.skills import SkillInfo

# -- parse_command_trigger -----------------------------------------------------


class TestParseCommandTrigger:
    def test_basic_command(self):
        assert parse_command_trigger("!help") == ("help", "")

    def test_command_with_args(self):
        assert parse_command_trigger("!migrate from yesterday") == ("migrate", "from yesterday")

    def test_slash_prefix(self):
        assert parse_command_trigger("/help", prefix="/") == ("help", "")

    def test_not_a_command(self):
        assert parse_command_trigger("hello world") is None

    def test_exclamation_not_command(self):
        """'!!! wow' should not trigger — first char after ! is not a letter."""
        assert parse_command_trigger("!!! wow") is None

    def test_bang_space(self):
        """'! hello' should not trigger."""
        assert parse_command_trigger("! hello") is None

    def test_slash_space(self):
        assert parse_command_trigger("/ path/to/file", prefix="/") is None

    def test_empty_string(self):
        assert parse_command_trigger("") is None

    def test_just_prefix(self):
        assert parse_command_trigger("!") is None


# -- substitute_body -----------------------------------------------------------


class TestSubstituteBody:
    def test_arguments_placeholder(self):
        body = "Do the thing with $ARGUMENTS"
        result = substitute_body(body, "foo bar")
        assert result == "Do the thing with foo bar"

    def test_positional_args(self):
        body = "Move $0 to $1"
        result = substitute_body(body, "today tomorrow")
        assert result == "Move today to tomorrow"

    def test_mixed(self):
        body = "City: $0\nFull: $ARGUMENTS"
        result = substitute_body(body, "Portland OR")
        assert result == "City: Portland\nFull: Portland OR"

    def test_no_placeholder_appends(self):
        body = "Do the thing."
        result = substitute_body(body, "extra context")
        assert "ARGUMENTS: extra context" in result

    def test_no_arguments(self):
        body = "Do the thing. $ARGUMENTS"
        result = substitute_body(body, "")
        assert result == "Do the thing. "  # $ARGUMENTS replaced with empty

    def test_out_of_range_positional(self):
        body = "Use $0 and $5"
        result = substitute_body(body, "only-one")
        assert "only-one" in result
        assert "$5" in result  # unreplaced


# -- format_help ---------------------------------------------------------------


class TestFormatHelp:
    def test_basic(self):
        skills = [
            SkillInfo(name="weather", description="Get weather", location=Path(".")),
            SkillInfo(name="migrate", description="Migrate todos", location=Path(".")),
        ]
        text = format_help(skills, prefix="!")
        assert "!weather" in text
        assert "!migrate" in text
        assert "Get weather" in text

    def test_with_hint(self):
        skills = [
            SkillInfo(
                name="weather", description="Get weather", location=Path("."),
                argument_hint="[city]",
            ),
        ]
        text = format_help(skills, prefix="!")
        assert "!weather [city]" in text

    def test_slash_prefix(self):
        skills = [
            SkillInfo(name="help", description="Help", location=Path(".")),
        ]
        text = format_help(skills, prefix="/")
        assert "/help" in text

    def test_empty(self):
        text = format_help([], prefix="!")
        assert "No commands" in text

    def test_filters_non_invocable(self):
        skills = [
            SkillInfo(name="visible", description="Visible", location=Path("."), user_invocable=True),
            SkillInfo(name="hidden", description="Hidden", location=Path("."), user_invocable=False),
        ]
        text = format_help(skills, prefix="!")
        assert "visible" in text
        assert "hidden" not in text


# -- execute_command -----------------------------------------------------------


class TestExecuteCommand:
    @pytest.mark.asyncio
    async def test_inline_mode(self, ctx):
        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do $ARGUMENTS", context="inline",
            allowed_tools=["vault_read"],
        )
        mode, result = await execute_command(ctx, skill, "the thing")
        assert mode == "inline"
        assert "Do the thing" in result
        assert ctx.tools.preapproved == {"vault_read"}

    @pytest.mark.asyncio
    async def test_fork_mode(self, ctx):
        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do $ARGUMENTS", context="fork",
            allowed_tools=["shell"],
        )
        with patch("decafclaw.tools.delegate._run_child_turn", new_callable=AsyncMock) as mock:
            mock.return_value = "child result"
            mode, result = await execute_command(ctx, skill, "the thing")

        assert mode == "fork"
        assert result == "child result"
        assert ctx.tools.preapproved == {"shell"}

    @pytest.mark.asyncio
    async def test_shell_skill_not_activated(self, ctx):
        """Shell-based skills (no native tools) don't get activated — body IS the prompt."""
        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do stuff", context="inline", has_native_tools=False,
        )
        await execute_command(ctx, skill, "")
        assert "test-cmd" not in ctx.skills.activated

    @pytest.mark.asyncio
    async def test_native_skill_auto_activated(self, ctx):
        """Skills with native tools DO get activated to register callables."""
        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do stuff", context="inline", has_native_tools=True,
        )
        # Mock the activation since there's no actual tools.py
        with patch("decafclaw.tools.skill_tools.activate_skill_internal", new_callable=AsyncMock, return_value="activated"):
            await execute_command(ctx, skill, "")

    @pytest.mark.asyncio
    async def test_already_activated_skips(self, ctx):
        """If skill already activated, don't re-activate."""
        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do stuff", context="inline",
        )
        ctx.skills.activated.add("test-cmd")
        # Should not error even though activation logic isn't called
        mode, result = await execute_command(ctx, skill, "")
        assert mode == "inline"

    @pytest.mark.asyncio
    async def test_fork_required_skills_activated(self, ctx):
        """Fork commands pre-activate required-skills before spawning child."""
        dep_skill = SkillInfo(
            name="tabstack", description="Tabstack", location=Path("."),
            has_native_tools=True,
        )
        ctx.config.discovered_skills = [dep_skill]

        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do stuff", context="fork",
            requires_skills=["tabstack"],
        )
        with patch("decafclaw.tools.skill_tools.activate_skill_internal",
                    new_callable=AsyncMock, return_value="activated") as mock_activate, \
             patch("decafclaw.tools.delegate._run_child_turn",
                    new_callable=AsyncMock, return_value="child result"):
            mode, result = await execute_command(ctx, skill, "")

        assert mode == "fork"
        mock_activate.assert_called_once_with(ctx, dep_skill)

    @pytest.mark.asyncio
    async def test_fork_required_skills_already_active(self, ctx):
        """Already-activated skills are not re-activated."""
        dep_skill = SkillInfo(
            name="tabstack", description="Tabstack", location=Path("."),
        )
        ctx.config.discovered_skills = [dep_skill]
        ctx.skills.activated.add("tabstack")

        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do stuff", context="fork",
            requires_skills=["tabstack"],
        )
        with patch("decafclaw.tools.skill_tools.activate_skill_internal",
                    new_callable=AsyncMock) as mock_activate, \
             patch("decafclaw.tools.delegate._run_child_turn",
                    new_callable=AsyncMock, return_value="done"):
            await execute_command(ctx, skill, "")

        mock_activate.assert_not_called()

    @pytest.mark.asyncio
    async def test_fork_mode_propagates_manager_to_child_turn(self, ctx):
        """The ctx handed to _run_child_turn MUST carry the manager from the
        parent ctx — otherwise delegate.py bails and the fork never runs."""
        sentinel_manager = object()
        ctx.manager = sentinel_manager

        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do $ARGUMENTS", context="fork",
        )
        with patch(
            "decafclaw.tools.delegate._run_child_turn",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = "child result"
            mode, result = await execute_command(ctx, skill, "go")

        assert mode == "fork"
        # First positional arg to _run_child_turn is parent_ctx
        called_ctx = mock.call_args.args[0]
        assert called_ctx.manager is sentinel_manager

    @pytest.mark.asyncio
    async def test_fork_mode_without_manager_surfaces_clear_error(self, ctx):
        """If a future transport forgets to attach the manager, the existing
        error in delegate.py should still fire with a readable message —
        not a silent KeyError or None-dereference."""
        ctx.manager = None  # explicit for the test's intent

        skill = SkillInfo(
            name="test-cmd", description="Test", location=Path("."),
            body="Do $ARGUMENTS", context="fork",
        )
        # Do NOT mock _run_child_turn — let the real function hit its own
        # bail-out so the error text is the one real users would see.
        mode, result = await execute_command(ctx, skill, "go")

        assert mode == "fork"
        assert "ConversationManager" in result
