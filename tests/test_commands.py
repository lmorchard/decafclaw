"""Tests for user-invokable commands."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.commands import (
    apply_command_ctx,
    execute_command,
    format_help,
    parse_command_trigger,
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
                name="weather",
                description="Get weather",
                location=Path("."),
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
    def test_apply_command_ctx_copies_body_marker(self, ctx):
        """The turn context inherits which bodies the command already placed
        in the prompt (#1823)."""
        from decafclaw.context import Context

        cmd_ctx = Context(config=ctx.config, event_bus=ctx.event_bus)
        cmd_ctx.skills.body_in_prompt.add("test-cmd")
        target = Context(config=ctx.config, event_bus=ctx.event_bus)

        apply_command_ctx(target, cmd_ctx)

        assert target.skills.body_in_prompt == {"test-cmd"}

    @pytest.mark.asyncio
    async def test_inline_mode(self, ctx):
        skill = SkillInfo(
            name="test-cmd",
            description="Test",
            location=Path("."),
            body="Do $ARGUMENTS",
            context="inline",
            allowed_tools=["vault_read"],
            # Explicit trusted tier: only capability tiers pre-approve (#737),
            # and `trust_tier` defaults to the untrusted tier. This test's
            # subject is inline-mode substitution, not tier policy.
            trust_tier="bundled",
        )
        result = await execute_command(ctx, skill, "the thing")
        assert result.mode == "inline"
        assert "Do the thing" in result.text
        assert ctx.tools.preapproved == {"vault_read"}
        # The body is this turn's prompt; the composer must not render it
        # again in <activated_skills> (#1823).
        assert ctx.skills.body_in_prompt == {"test-cmd"}

    @pytest.mark.asyncio
    async def test_fork_mode(self, ctx):
        skill = SkillInfo(
            name="test-cmd",
            description="Test",
            location=Path("."),
            body="Do $ARGUMENTS",
            context="fork",
            allowed_tools=["shell"],
            # Explicit trusted tier — see test_inline_mode. Subject here is
            # fork-mode dispatch, not tier policy.
            trust_tier="bundled",
        )
        with patch("decafclaw.tools.delegate.run_child_turn", new_callable=AsyncMock) as mock:
            mock.return_value = ("child result", None)
            result = await execute_command(ctx, skill, "the thing")

        assert result.mode == "fork"
        assert result.text == "child result"
        assert ctx.tools.preapproved == {"shell"}
        # Fork mode delivers the body to the child, not this turn's prompt.
        assert ctx.skills.body_in_prompt == set()

    @pytest.mark.asyncio
    async def test_shell_skill_not_activated(self, ctx):
        """Shell-based skills (no native tools) don't get activated — body IS the prompt."""
        skill = SkillInfo(
            name="test-cmd",
            description="Test",
            location=Path("."),
            body="Do stuff",
            context="inline",
            has_native_tools=False,
        )
        await execute_command(ctx, skill, "")
        assert "test-cmd" not in ctx.skills.activated

    @pytest.mark.asyncio
    async def test_native_skill_auto_activated(self, ctx):
        """Skills with native tools DO get activated to register callables."""
        skill = SkillInfo(
            name="test-cmd",
            description="Test",
            location=Path("."),
            body="Do stuff",
            context="inline",
            has_native_tools=True,
        )
        # Mock the activation since there's no actual tools.py
        with patch(
            "decafclaw.tools.skill_tools.activate_skill_internal", new_callable=AsyncMock, return_value="activated"
        ):
            await execute_command(ctx, skill, "")

    @pytest.mark.asyncio
    async def test_already_activated_skips(self, ctx):
        """If skill already activated, don't re-activate."""
        skill = SkillInfo(
            name="test-cmd",
            description="Test",
            location=Path("."),
            body="Do stuff",
            context="inline",
        )
        ctx.skills.activated["test-cmd"] = ""
        # Should not error even though activation logic isn't called
        result = await execute_command(ctx, skill, "")
        assert result.mode == "inline"

    @pytest.mark.asyncio
    async def test_fork_required_skills_activated(self, ctx):
        """Fork commands pre-activate required-skills before spawning child."""
        dep_skill = SkillInfo(
            name="tabstack",
            description="Tabstack",
            location=Path("."),
            has_native_tools=True,
            # Explicit trusted tier: a workspace-tier dependency is
            # deliberately NOT activated (#737 — the human named the command,
            # not its dependency list). See
            # tests/test_command_tier_trust.py for that case.
            trust_tier="bundled",
        )
        ctx.config.discovered_skills = [dep_skill]

        skill = SkillInfo(
            name="test-cmd",
            description="Test",
            location=Path("."),
            body="Do stuff",
            context="fork",
            requires_skills=["tabstack"],
        )
        with (
            patch(
                "decafclaw.tools.skill_tools.activate_skill_internal", new_callable=AsyncMock, return_value="activated"
            ) as mock_activate,
            patch(
                "decafclaw.tools.delegate.run_child_turn", new_callable=AsyncMock, return_value=("child result", None)
            ),
        ):
            result = await execute_command(ctx, skill, "")

        assert result.mode == "fork"
        mock_activate.assert_called_once_with(ctx, dep_skill)

    @pytest.mark.asyncio
    async def test_fork_required_skills_already_active(self, ctx):
        """Already-activated skills are not re-activated."""
        dep_skill = SkillInfo(
            name="tabstack",
            description="Tabstack",
            location=Path("."),
        )
        ctx.config.discovered_skills = [dep_skill]
        ctx.skills.activated["tabstack"] = ""

        skill = SkillInfo(
            name="test-cmd",
            description="Test",
            location=Path("."),
            body="Do stuff",
            context="fork",
            requires_skills=["tabstack"],
        )
        with (
            patch("decafclaw.tools.skill_tools.activate_skill_internal", new_callable=AsyncMock) as mock_activate,
            patch("decafclaw.tools.delegate.run_child_turn", new_callable=AsyncMock, return_value=("done", None)),
        ):
            await execute_command(ctx, skill, "")

        mock_activate.assert_not_called()

    @pytest.mark.asyncio
    async def test_fork_mode_propagates_manager_to_child_turn(self, ctx):
        """The ctx handed to run_child_turn MUST carry the manager from the
        parent ctx — otherwise delegate.py bails and the fork never runs."""
        sentinel_manager = object()
        ctx.manager = sentinel_manager

        skill = SkillInfo(
            name="test-cmd",
            description="Test",
            location=Path("."),
            body="Do $ARGUMENTS",
            context="fork",
        )
        with patch(
            "decafclaw.tools.delegate.run_child_turn",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = ("child result", None)
            result = await execute_command(ctx, skill, "go")

        assert result.mode == "fork"
        # First positional arg to run_child_turn is parent_ctx
        called_ctx = mock.call_args.args[0]
        assert called_ctx.manager is sentinel_manager

    @pytest.mark.asyncio
    async def test_fork_mode_without_manager_surfaces_clear_error(self, ctx):
        """If a future transport forgets to attach the manager, the existing
        error in delegate.py should still fire with a readable message —
        not a silent KeyError or None-dereference."""
        ctx.manager = None  # explicit for the test's intent

        skill = SkillInfo(
            name="test-cmd",
            description="Test",
            location=Path("."),
            body="Do $ARGUMENTS",
            context="fork",
        )
        # Do NOT mock run_child_turn — let the real function hit its own
        # bail-out so the error text is the one real users would see.
        result = await execute_command(ctx, skill, "go")

        assert result.mode == "fork"
        assert "ConversationManager" in result.text
