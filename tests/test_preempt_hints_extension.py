"""PreemptHintsExtension: EPILOGUE discovery hints (#1507).

Verifies the extension's slot placement (directly after the user message),
hint formatting, archived replay, disable behavior, and fail-open isolation.
Helper-level matching tests remain in ``test_context_composer.py``.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.context_composer import ContextComposer
from decafclaw.context_types import ComposerMode
from decafclaw.extensions import ContextSlot
from decafclaw.extensions.preempt_hints import PreemptHintsExtension


def _make_tool_def(name: str, description: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": {}}},
    }


def _make_skill_info(name, description, **kwargs):
    from decafclaw.skills import SkillInfo

    return SkillInfo(
        name=name,
        description=description,
        location=Path(f"/fake/skills/{name}"),
        **kwargs,
    )


def _compose(ctx, user_message, history=None):
    composer = ContextComposer(state=ctx.composer)
    return composer.compose(
        ctx=ctx,
        user_message=user_message,
        history=history if history is not None else [],
        mode=ComposerMode.INTERACTIVE,
    )


def _index(messages, predicate):
    for i, message in enumerate(messages):
        if predicate(message):
            return i
    raise AssertionError(f"no matching message in {messages!r}")


def _hint_messages(messages):
    return [m for m in messages if "<preempt" in str(m.get("content", ""))]


async def _hook(ctx, user_message, config):
    return await PreemptHintsExtension({}, config).pre_compose_hook(ctx, user_message, [])


@pytest.mark.asyncio
async def test_hint_mounts_after_user_message_and_before_status(ctx, config):
    """The skill hint sits directly after the user message and before the status line."""
    config.agent.auto_refresh_skills = False
    config.system_prompt = "You are a test agent."
    config.agent.show_context_status = True
    config.discovered_skills = [_make_skill_info("background", "Run things in the background")]

    with (
        patch("decafclaw.extensions.preempt_hints.collect_all_tool_defs", return_value=[]),
        patch("decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]),
    ):
        result = await _compose(ctx, "run my dev server in the background")

    user_idx = _index(result.messages, lambda m: m.get("content") == "run my dev server in the background")
    hint_idx = _index(result.messages, lambda m: "<preempt_skill_hint>" in str(m.get("content", "")))
    assert hint_idx == user_idx + 1
    assert result.messages[hint_idx]["role"] == "user"
    # The status line rides in the ephemeral working-memory block (#1574).
    status = [m for m in result.messages if "<context_status>" in str(m.get("content", ""))]
    assert len(status) == 1
    assert "[Context:" in status[0]["content"]
    assert result.messages[-1] is status[0]
    assert "preempt_skill_matches" in {s.source for s in result.sources}

    archived = [m for m in result.messages_to_archive if "<preempt_skill_hint>" in str(m.get("content", ""))]
    assert len(archived) == 1
    assert archived[0]["role"] == "skill_hint"


@pytest.mark.asyncio
async def test_hint_replays_in_place_on_later_turns(ctx, config):
    """Archived hints replay where they were sent, not at the next boundary."""
    config.agent.auto_refresh_skills = False
    config.system_prompt = "You are a test agent."
    config.discovered_skills = [_make_skill_info("background", "Run things in the background")]

    with (
        patch("decafclaw.extensions.preempt_hints.collect_all_tool_defs", return_value=[]),
        patch("decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]),
    ):
        first = await _compose(ctx, "run my dev server in the background")
        history = list(first.messages_to_archive)
        config.discovered_skills = []
        second = await _compose(ctx, "thanks", history)

    hints = _hint_messages(second.messages)
    assert len(hints) == 1
    hint_idx = _index(second.messages, lambda m: "<preempt_skill_hint>" in str(m.get("content", "")))
    second_user_idx = _index(second.messages, lambda m: m.get("content") == "thanks")
    assert hint_idx < second_user_idx


@pytest.mark.asyncio
async def test_disabled_extension_removes_hints(ctx, config):
    """Yanking preempt_hints removes the hints without runtime errors."""
    config.agent.auto_refresh_skills = False
    config.system_prompt = "You are a test agent."
    config.discovered_skills = [_make_skill_info("background", "Run things in the background")]
    ctx.extension_manager.disable("preempt_hints")
    assert ctx.extension_manager.is_enabled("preempt_hints") is False

    with patch("decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]):
        result = await _compose(ctx, "run my dev server in the background")

    assert _hint_messages(result.messages) == []
    assert "preempt_matches" not in {s.source for s in result.sources}
    assert "preempt_skill_matches" not in {s.source for s in result.sources}
    assert ctx.skills.preempt_matches == set()


@pytest.mark.asyncio
async def test_fail_open_when_matching_raises(ctx, config):
    """A raising matcher logs and yields no hint; the turn still composes."""
    config.agent.auto_refresh_skills = False
    config.system_prompt = "You are a test agent."
    with (
        patch(
            "decafclaw.extensions.preempt_hints.compose_preempt_matches",
            side_effect=RuntimeError("matcher exploded"),
        ),
        patch("decafclaw.extensions.vault_retrieval.retrieve_memory_context", new_callable=AsyncMock, return_value=[]),
    ):
        result = await _compose(ctx, "hello")

    assert result.messages[0]["role"] == "system"
    assert any(m.get("content") == "hello" for m in result.messages)
    assert _hint_messages(result.messages) == []


@pytest.mark.asyncio
async def test_tool_hint_lists_deferred_matches(ctx, config):
    """Deferred matches render as <preempt_tool_hint> pointing at tool_search."""
    tools = [
        _make_tool_def("vault_backlinks", "List vault backlinks"),
        _make_tool_def("unrelated_tool", "Totally unrelated"),
    ]
    ctx.tools.deferred_pool = tools
    with patch("decafclaw.extensions.preempt_hints.collect_all_tool_defs", return_value=tools):
        contributions = await _hook(ctx, "show vault backlinks", config)

    contributions = [c for c in contributions if c.slot is ContextSlot.EPILOGUE]
    hint = next(m["content"] for c in contributions for m in c.messages if "<preempt_tool_hint>" in m["content"])
    assert "- vault_backlinks" in hint
    assert "tool_search" in hint
    assert "unrelated_tool" not in hint

    entries = {c.source_entry.source: c.source_entry for c in contributions}
    assert entries["preempt_matches"].details["suggested"] == ["vault_backlinks"]
    assert entries["preempt_matches"].tokens_estimated > 0


@pytest.mark.asyncio
async def test_skill_hint_formatting_and_source(ctx, config):
    """Skill matches render as bullets with a preempt_skill_matches source entry."""
    config.discovered_skills = [
        _make_skill_info("background", "Search the background vault"),
        _make_skill_info("vault-search", "Search the background vault"),
    ]
    contributions = await _hook(ctx, "search background vault", config)
    hint = next(m["content"] for c in contributions for m in c.messages if "<preempt_skill_hint>" in m["content"])
    assert "- background" in hint
    assert "- vault-search" in hint
    assert "activate_skill" in hint

    entry = next(c.source_entry for c in contributions if c.source_entry.source == "preempt_skill_matches")
    assert entry.items_included == 2
    assert entry.details["hinted_count"] == 2


@pytest.mark.asyncio
async def test_no_matches_yields_no_contribution(ctx, config):
    """A message with no keyword overlap adds no hint and no source."""
    config.discovered_skills = [_make_skill_info("background", "Run things in the background")]
    assert await _hook(ctx, "qwertyuiop", config) is None


@pytest.mark.asyncio
async def test_disabled_search_config_yields_no_contribution(ctx, config):
    """agent.preemptive_search.enabled = false suppresses both matchers."""
    config.agent.preemptive_search.enabled = False
    config.discovered_skills = [_make_skill_info("background", "Run things in the background")]
    assert await _hook(ctx, "run my dev server in the background", config) is None
