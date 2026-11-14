"""Request-level regressions for #1257, using the real core tool catalog."""

from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest

from decafclaw.agent import _maybe_compact
from decafclaw.context_composer import ContextComposer
from decafclaw.llm.providers.openai_compat import OpenAICompatProvider, _clean_messages
from decafclaw.skills import discover_skills
from decafclaw.testing import ScriptedResponse
from decafclaw.tool_definitions import build_tool_list
from decafclaw.tools.search_tools import tool_search
from decafclaw.tools.tool_registry import add_fetched_tools
from tests.harness import AgentHarness


@pytest.fixture
def composer(ctx, config):
    config.agent.auto_refresh_skills = False
    config.system_prompt = "Stable test system prompt"
    config.vault_retrieval.mode = "on_demand"
    config.agent.show_context_status = True
    config.agent.max_active_tools = 25
    return ContextComposer(ctx.composer)


async def test_laptop_question_preserves_real_native_tools(ctx, composer):
    history = []
    first = await composer.compose(ctx, "Hello?", history)
    first_tools = composer.update_iteration_tools(ctx, first.messages)
    history.append({"role": "assistant", "content": "Hello!"})
    second = await composer.compose(ctx, "You are now running with a model on my laptop, how does that feel?", history)
    second_tools = composer.update_iteration_tools(ctx, second.messages)
    assert second_tools == first_tools
    assert [m for m in second.messages if m["role"] == "system"] == [m for m in first.messages if m["role"] == "system"]


async def test_sent_tail_replays_in_live_history_and_archive(ctx, composer):
    history = []
    with patch.object(
        composer,
        "_compose_preempt_skill_matches",
        return_value=(None, "<preempt_skill_hint>sample</preempt_skill_hint>"),
    ):
        first = await composer.compose(ctx, "Hello?", history)
    sent = deepcopy(_clean_messages(first.messages))
    archived = deepcopy(first.messages_to_archive)
    reply = {"role": "assistant", "content": "Hello!"}
    history.append(reply)
    archived.append(reply)
    second = await composer.compose(ctx, "Hello again?", history)
    assert _clean_messages(second.messages)[: len(sent)] == sent
    restored = await ContextComposer(ctx.composer).compose(ctx, "After restart", archived)
    assert _clean_messages(restored.messages)[: len(sent)] == sent


async def test_explicit_fetch_changes_once_then_stabilizes(ctx, composer):
    ctx.config.agent.tool_context_budget_pct = 0.001
    history = []
    first = await composer.compose(ctx, "Hello?", history)
    first_tools = composer.update_iteration_tools(ctx, first.messages)
    assert "wait" not in {t["function"]["name"] for t in first_tools}
    add_fetched_tools(ctx, {"wait"})
    loaded = composer.update_iteration_tools(ctx, first.messages)
    assert "wait" in {t["function"]["name"] for t in loaded}
    history.append({"role": "assistant", "content": "Done"})
    second = await composer.compose(ctx, "Running a model on my laptop", history)
    assert composer.update_iteration_tools(ctx, second.messages) == loaded


@pytest.mark.parametrize("preserve_cache", [True, False])
async def test_cleanup_preserves_sent_history_by_default(ctx, config, preserve_cache):
    if not preserve_cache:
        config.cleanup.preserve_prompt_cache = False
    original = "large old output " * 200
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "tool_calls": [{"id": "old", "function": {"name": "wait", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "old", "content": original},
        {"role": "user", "content": "second"},
        {"role": "user", "content": "third"},
    ]
    await _maybe_compact(ctx, config, history, 1)
    assert (history[2]["content"] == original) is preserve_cache


async def test_deferred_match_is_a_replayable_discovery_hint(ctx, composer):
    ctx.config.agent.tool_context_budget_pct = 0.001
    history = []
    first = await composer.compose(ctx, "Hello?", history)
    second = await composer.compose(ctx, "Use wait to pause for a second", history)
    assert second.tools == first.tools
    hints = [m for m in second.messages_to_archive if "<preempt_tool_hint>" in m.get("content", "")]
    assert len(hints) == 1
    assert "tool_search" in hints[0]["content"]
    assert "- wait" in hints[0]["content"]
    assert hints[0] in history
    result = tool_search(ctx, "select:wait")
    assert "wait" in result.text
    loaded = composer.update_iteration_tools(ctx, second.messages)
    assert "wait" in {t["function"]["name"] for t in loaded}


async def test_revoked_tool_and_hint_disappear(ctx, composer):
    ctx.config.agent.tool_context_budget_pct = 0.001
    add_fetched_tools(ctx, {"wait"})
    first = await composer.compose(ctx, "Use wait", [])
    assert "wait" in {t["function"]["name"] for t in first.tools}
    ctx.tools.disallowed = {"wait"}
    second = await composer.compose(ctx, "Use wait", [])
    assert "wait" not in {t["function"]["name"] for t in second.tools}
    assert "wait" not in {t["function"]["name"] for t in ctx.tools.deferred_pool}
    assert not any("- wait\n" in m["content"] for m in second.messages_to_archive)


async def test_prefix_reset_logged_once_for_explicit_changes(ctx, composer, caplog):
    ctx.config.agent.tool_context_budget_pct = 0.001
    first = await composer.compose(ctx, "Hello?", [])
    composer.update_iteration_tools(ctx, first.messages)
    with caplog.at_level("INFO"):
        add_fetched_tools(ctx, {"wait"})
        composer.update_iteration_tools(ctx, first.messages)
        composer.update_iteration_tools(ctx, first.messages)
    resets = [r.message for r in caplog.records if "Prompt prefix reset" in r.message]
    assert len(resets) == 1
    assert "tools" in resets[0]
    assert "system" in resets[0]
    assert "Stable test system prompt" not in resets[0]
    ctx.composer.reset_for_compaction()
    assert ctx.composer.prefix_fingerprints == {}


async def test_dynamic_tool_revocation_still_reaches_iteration(ctx, composer):
    available = True
    definition = {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "dynamic_probe",
            "description": "A temporary capability",
            "parameters": {"type": "object"},
        },
    }

    def provider(_ctx):
        return ({"dynamic_probe": lambda _ctx: None}, [definition]) if available else ({}, [])

    ctx.tools.dynamic_providers["probe"] = provider
    request = await composer.compose(ctx, "Hello?", [])
    tools = composer.update_iteration_tools(ctx, request.messages)
    assert "dynamic_probe" in {t["function"]["name"] for t in tools}
    available = False
    tools = composer.update_iteration_tools(ctx, request.messages)
    assert "dynamic_probe" not in {t["function"]["name"] for t in tools}


async def test_system_updates_have_identical_live_and_archive_order(ctx, composer):
    history = []
    first = await composer.compose(ctx, "Hello?", history)
    archived = deepcopy(first.messages_to_archive)
    ctx.config.system_prompt = "Updated system context"
    second = await composer.compose(ctx, "Next turn", history)
    archived.extend(second.messages_to_archive)
    assert archived == history
    assert any(m.get("source") == "system_update" for m in archived)


@pytest.mark.live_llm
async def test_provider_receives_stable_tools_and_exact_message_prefix(ctx, composer):
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
        json={
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
        },
    )
    provider = OpenAICompatProvider("https://example.invalid")
    history = []
    with patch("httpx.AsyncClient.post", return_value=response) as post:
        for text in ("Hello?", "You are now running with a model on my laptop, how does that feel?"):
            composed = await composer.compose(ctx, text, history)
            tools = composer.update_iteration_tools(ctx, composed.messages)
            await provider.complete("local-test", composed.messages, tools=tools)
            history.append({"role": "assistant", "content": "Hello"})
    first, second = [call.kwargs["json"] for call in post.call_args_list]
    assert second["tools"] == first["tools"]
    assert second["messages"][: len(first["messages"])] == first["messages"]


@pytest.mark.scripted_provider
async def test_manager_preserves_prefix_across_real_turn_boundaries(tmp_path):
    async with AgentHarness(tmp_path) as harness:
        # Production startup discovers skills before the first turn setup.
        harness.config.discovered_skills = discover_skills(harness.config)
        harness.config.agent.show_context_status = True
        harness.config.agent.preemptive_search.enabled = True
        harness.script(ScriptedResponse.text("Hello!"), ScriptedResponse.text("Still here."))
        await (await harness.enqueue("Hello?"))
        await (await harness.enqueue("You are now running with a model on my laptop, how does that feel?"))
        first, second = harness.provider.requests
        assert second.tools == first.tools, (
            [t["function"]["name"] for t in first.tools],
            [t["function"]["name"] for t in second.tools],
        )
        assert second.messages[: len(first.messages)] == first.messages
        assert any(m["role"] == "context_status" for m in harness.archive())


def test_skill_restore_order_cannot_change_budget_selection(ctx):
    ctx.config.agent.max_active_tools = 1
    ctx.config.agent.tool_context_budget_pct = 1.0
    definitions = [
        {
            "type": "function",
            "priority": "normal",
            "function": {
                "name": name,
                "description": "Restored capability",
                "parameters": {"type": "object"},
            },
        }
        for name in ("restored_z", "restored_a")
    ]
    first = build_tool_list(ctx, all_defs=definitions)
    restored = build_tool_list(ctx, all_defs=list(reversed(definitions)))
    assert first == restored
