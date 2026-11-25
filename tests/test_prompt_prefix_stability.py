"""Request-level regressions for #1257, using the real core tool catalog."""

from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest

from decafclaw.agent import _maybe_compact
from decafclaw.config_types import ModelConfig, RetrievalMode
from decafclaw.context_composer import ContextComposer
from decafclaw.llm.providers.openai_compat import OpenAICompatProvider, _clean_messages
from decafclaw.skills import discover_skills
from decafclaw.testing import ScriptedResponse
from decafclaw.tool_definitions import build_tool_list
from decafclaw.tools.search_tools import tool_search
from decafclaw.tools.tool_registry import add_fetched_tools, estimate_tool_tokens
from tests.harness import AgentHarness


@pytest.fixture
def composer(ctx, config):
    config.agent.auto_refresh_skills = False
    config.system_prompt = "Stable test system prompt"
    config.vault_retrieval.mode = RetrievalMode.ON_DEMAND
    config.agent.show_context_status = True
    # Force deferral through the token budget; there is no count cap (#1692).
    config.agent.tool_context_budget_pct = 0.001
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


def _without_working_memory(messages):
    """Drop this turn's transient blocks (#1574, #1823).

    The working-memory bundle and the activated-skills block are deliberately
    outside the replayable prefix: everything before them must remain a
    byte-identical prefix across turns.
    """
    return [m for m in messages if not str(m.get("content", "")).startswith(("<working_memory>", "<activated_skills>"))]


async def test_sent_tail_replays_in_live_history_and_archive(ctx, composer):
    history = []
    with patch(
        "decafclaw.extensions.preempt_hints.compose_preempt_skill_matches",
        return_value=(None, "<preempt_skill_hint>sample</preempt_skill_hint>"),
    ):
        first = await composer.compose(ctx, "Hello?", history)
    sent = _without_working_memory(deepcopy(_clean_messages(first.messages)))
    archived = deepcopy(first.messages_to_archive)
    reply = {"role": "assistant", "content": "Hello!"}
    history.append(reply)
    archived.append(reply)
    second = await composer.compose(ctx, "Hello again?", history)
    assert _without_working_memory(_clean_messages(second.messages))[: len(sent)] == sent
    restored = await ContextComposer(ctx.composer).compose(ctx, "After restart", archived)
    assert _without_working_memory(_clean_messages(restored.messages))[: len(sent)] == sent


async def test_working_memory_is_the_only_turn_varying_tail(ctx, composer):
    """The conversation prefix is stable; only the ephemeral block differs (#1574)."""
    history = []
    first = await composer.compose(ctx, "Hello?", history)
    first_sent = deepcopy(_clean_messages(first.messages))
    history.append({"role": "assistant", "content": "Hello!"})
    second = await composer.compose(ctx, "Hello again?", history)
    second_sent = _clean_messages(second.messages)

    assert str(first_sent[-1]["content"]).startswith("<working_memory>")
    assert str(second_sent[-1]["content"]).startswith("<working_memory>")
    stable = _without_working_memory(first_sent)
    assert _without_working_memory(second_sent)[: len(stable)] == stable
    assert "<working_memory>" not in "\n".join(str(m.get("content", "")) for m in history)


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

    # The archive carries working-memory references as metadata on user rows
    # (#1574); order and content are otherwise identical to live history.
    def content_view(messages):
        return [{k: v for k, v in m.items() if k != "working_memory"} for m in messages]

    assert content_view(archived) == content_view(history)
    assert any(m.get("source") == "system_update" for m in archived)
    assert any(m.get("role") == "user" and m.get("working_memory") for m in archived)
    assert not any("working_memory" in m for m in history)


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
    first_messages = _without_working_memory(first["messages"])
    assert _without_working_memory(second["messages"])[: len(first_messages)] == first_messages


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
        first_messages = _without_working_memory(first.messages)
        assert _without_working_memory(second.messages)[: len(first_messages)] == first_messages
        # The status line is per-turn working memory, not archived history
        # (#1574): it reaches the provider but never replays as a stale row.
        assert any("<context_status>" in str(m.get("content", "")) for m in first.messages)
        assert not any(m.get("role") == "context_status" for m in harness.archive())


def test_skill_restore_order_cannot_change_budget_selection(ctx):
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
    # Room for exactly one tool: the second must be deferred either way.
    ctx.config.compaction.max_tokens = estimate_tool_tokens([definitions[0]]) + 1
    first = build_tool_list(ctx, all_defs=definitions)
    restored = build_tool_list(ctx, all_defs=list(reversed(definitions)))
    assert first == restored


@pytest.mark.scripted_provider
async def test_disk_archive_replays_model_visible_prefix_after_restart(tmp_path):
    requests = []
    for prompt in ("Hello?", "A follow-up after restarting the manager"):
        async with AgentHarness(tmp_path) as harness:
            harness.config.discovered_skills = discover_skills(harness.config)
            harness.config.agent.show_context_status = True
            harness.config.agent.preemptive_search.enabled = True
            harness.script(ScriptedResponse.text("Hello!"))
            await (await harness.enqueue(prompt))
            requests.append(deepcopy(harness.provider.requests[0]))
    first, restored = requests
    assert restored.tools == first.tools
    # JSONL adds archive timestamps; chat templates do not consume that key.
    # The working-memory block is per-turn only (#1574); the archived
    # conversation prefix must still replay byte-identically.
    replay = [{k: v for k, v in m.items() if k != "timestamp"} for m in _without_working_memory(restored.messages)]
    assert replay[: len(_without_working_memory(first.messages))] == _without_working_memory(first.messages)


@pytest.mark.parametrize("named", [True, False])
async def test_model_setting_change_resets_diagnostics_without_alias_change(ctx, composer, caplog, named):
    if named:
        ctx.active_model = "same-alias"
        ctx.config.model_configs["same-alias"] = ModelConfig(model="original-model")
    request = await composer.compose(ctx, "Hello?", [])
    composer.update_iteration_tools(ctx, request.messages)
    if named:
        ctx.config.model_configs["same-alias"].model = "changed-model"
    else:
        ctx.config.llm.model = "changed-model"
    with caplog.at_level("INFO"):
        composer.update_iteration_tools(ctx, request.messages)
    assert any("model changed" in r.message for r in caplog.records if "Prompt prefix reset" in r.message)
