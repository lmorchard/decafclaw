"""Tests for the llm_query tool (#1258).

Drives the normal LLM path through the in-memory ScriptedProvider
(#923 pattern): the provider records each request, so tests assert on
the exact payload (messages, tools) and script responses for behavior.
"""

import asyncio

import pytest

from decafclaw.config import Config
from decafclaw.config_types import (
    AgentConfig,
    ModelConfig,
    ProviderConfig,
    ReflectionConfig,
    VaultRetrievalConfig,
)
from decafclaw.context import Context
from decafclaw.events import EventBus
from decafclaw.testing import (
    PROVIDER_SCRIPTED,
    ScriptedResponse,
    register_scripted_provider,
    unregister_scripted_provider,
)
from decafclaw.tools.llm_tools import tool_llm_models, tool_llm_query

MODEL_NAME = "test-model"


@pytest.fixture
def provider():
    p = register_scripted_provider()
    yield p
    unregister_scripted_provider()


@pytest.fixture
def config(tmp_data):
    return Config(
        agent=AgentConfig(data_home=str(tmp_data), id="test-agent", user_id="testuser"),
        reflection=ReflectionConfig(enabled=False),
        vault_retrieval=VaultRetrievalConfig(enabled=False),
        providers={PROVIDER_SCRIPTED: ProviderConfig(type=PROVIDER_SCRIPTED)},
        model_configs={
            MODEL_NAME: ModelConfig(
                provider=PROVIDER_SCRIPTED,
                model=MODEL_NAME,
                context_window_size=200_000,
            )
        },
        default_model=MODEL_NAME,
    )


@pytest.fixture
def ctx(config):
    context = Context(config=config, event_bus=EventBus())
    context.conv_id = "test-conv"
    return context


@pytest.mark.scripted_provider
class TestSinglePrompt:
    @pytest.mark.asyncio
    async def test_sends_only_user_message_without_tools(self, ctx, provider):
        """A single-prompt call sends exactly the user message with no
        tools in the request payload."""
        provider.extend(ScriptedResponse.text("done"))

        result = await tool_llm_query(ctx, prompt="Summarize this text.", model=MODEL_NAME)

        assert result.text == "done"
        req = provider.requests[0]
        assert req.model == MODEL_NAME
        assert req.messages == [{"role": "user", "content": "Summarize this text."}]
        assert req.tools is None

    @pytest.mark.asyncio
    async def test_system_message_precedes_user(self, ctx, provider):
        """A non-empty system prompt is the first message; still no tools."""
        provider.extend(ScriptedResponse.text("ok"))

        await tool_llm_query(ctx, prompt="p", model=MODEL_NAME, system="You are terse.")

        req = provider.requests[0]
        assert [m["role"] for m in req.messages] == ["system", "user"]
        assert req.messages[0]["content"] == "You are terse."
        assert req.tools is None

    @pytest.mark.asyncio
    async def test_usage_accumulates_into_turn_counters(self, ctx, provider):
        """Reported usage lands on the per-call and turn token counters;
        main-model fields (last_prompt) stay untouched."""
        provider.extend(
            ScriptedResponse.text("done", usage={"prompt_tokens": 10, "completion_tokens": 5, "cached_tokens": 2})
        )
        fork = ctx.fork_for_tool_call("call_1")

        result = await tool_llm_query(fork, prompt="p", model=MODEL_NAME)

        assert result.text == "done"
        # Per-call counter (the tool's own fork).
        assert fork.tokens.total_prompt == 10
        assert fork.tokens.total_completion == 5
        assert fork.tokens.total_cached_prompt == 2
        # Turn counters (the parent ctx the agent loop reads).
        assert ctx.tokens.total_prompt == 10
        assert ctx.tokens.total_completion == 5
        # Main-model fields untouched: this is a separate request.
        assert ctx.tokens.last_prompt == 0

    @pytest.mark.asyncio
    async def test_unknown_model_errors_without_call(self, ctx, provider):
        """An unknown model name is a hard error listing configured models;
        no provider call is made and nothing falls back."""
        result = await tool_llm_query(ctx, prompt="p", model="nope")

        assert result.text.startswith("[error:")
        assert "unknown model 'nope'" in result.text
        assert MODEL_NAME in result.text
        assert provider.requests == []

    @pytest.mark.asyncio
    async def test_inherits_active_model(self, ctx, provider):
        """model="" inherits the conversation's active model."""
        ctx.active_model = MODEL_NAME
        provider.extend(ScriptedResponse.text("ok"))

        result = await tool_llm_query(ctx, prompt="p")

        assert result.text == "ok"
        assert provider.requests[0].model == MODEL_NAME

    @pytest.mark.asyncio
    async def test_stale_active_model_falls_back_to_default(self, ctx, provider):
        """A stale inherited name warns and falls back to the default model
        (agent-turn pattern); an explicit unknown name would error instead."""
        ctx.active_model = "stale-model"
        provider.extend(ScriptedResponse.text("ok"))

        result = await tool_llm_query(ctx, prompt="p")

        assert result.text == "ok"
        assert provider.requests[0].model == MODEL_NAME

    @pytest.mark.asyncio
    async def test_empty_prompt_rejected(self, ctx, provider):
        result = await tool_llm_query(ctx, prompt="")

        assert "non-empty" in result.text
        assert provider.requests == []


@pytest.mark.scripted_provider
class TestBatch:
    @pytest.mark.asyncio
    async def test_one_failure_does_not_fail_others(self, ctx, provider):
        """Per-item ok flags; summary counts match; usage from the
        successful items accumulates."""
        provider.extend(
            [
                ScriptedResponse.text("one", usage={"prompt_tokens": 1, "completion_tokens": 1}),
                ScriptedResponse(error="boom"),
                ScriptedResponse.text("three", usage={"prompt_tokens": 2, "completion_tokens": 2}),
            ]
        )

        result = await tool_llm_query(ctx, prompt=["a", "b", "c"], model=MODEL_NAME)

        assert result.text == "3 completions: 2 succeeded, 1 failed"
        data = result.data
        assert data["summary"] == {"total": 3, "ok": 2, "failed": 1}
        assert [r["index"] for r in data["results"]] == [0, 1, 2]
        assert data["results"][0]["ok"] is True
        assert data["results"][0]["text"] == "one"
        assert data["results"][1]["ok"] is False
        assert "boom" in data["results"][1]["error"]
        assert data["results"][2]["ok"] is True
        assert data["results"][2]["text"] == "three"
        # Usage from the two successful completions.
        assert ctx.tokens.total_prompt == 3
        assert ctx.tokens.total_completion == 3

    @pytest.mark.asyncio
    async def test_batch_over_cap_errors_without_calls(self, ctx, provider):
        result = await tool_llm_query(ctx, prompt=[f"p{i}" for i in range(26)], model=MODEL_NAME)

        assert result.text.startswith("[error:")
        assert "cap is 25" in result.text
        assert provider.requests == []

    @pytest.mark.asyncio
    async def test_empty_list_rejected(self, ctx, provider):
        result = await tool_llm_query(ctx, prompt=[])

        assert "non-empty" in result.text
        assert provider.requests == []

    @pytest.mark.asyncio
    async def test_non_string_item_rejected(self, ctx, provider):
        result = await tool_llm_query(ctx, prompt=["ok", 42], model=MODEL_NAME)

        assert "prompt[1]" in result.text
        assert provider.requests == []

    @pytest.mark.asyncio
    async def test_batch_publishes_progress_events(self, ctx, provider):
        """Each completed item emits an aggregate tool_status event."""
        events: list[dict] = []
        ctx.event_bus.subscribe(events.append)
        provider.extend([ScriptedResponse.text("one"), ScriptedResponse.text("two"), ScriptedResponse.text("three")])

        await tool_llm_query(ctx, prompt=["a", "b", "c"], model=MODEL_NAME)

        statuses = [e for e in events if e.get("type") == "tool_status"]
        assert {e["message"] for e in statuses} == {"1/3 completions", "2/3 completions", "3/3 completions"}

    @pytest.mark.asyncio
    async def test_per_item_timeout_fails_only_that_item(self, ctx, provider):
        """A held response trips the per-item timeout; other items complete."""
        hold = asyncio.Event()  # never released: the item must time out
        provider.extend([ScriptedResponse.text("fast"), ScriptedResponse(hold=hold)])

        result = await tool_llm_query(ctx, prompt=["a", "b"], model=MODEL_NAME, timeout=1)

        assert result.data["summary"] == {"total": 2, "ok": 1, "failed": 1}
        assert result.data["results"][0]["ok"] is True
        assert result.data["results"][0]["text"] == "fast"
        assert result.data["results"][1]["ok"] is False
        assert "timed out" in result.data["results"][1]["error"]


@pytest.mark.scripted_provider
class TestStructuredOutput:
    @pytest.mark.asyncio
    async def test_compliant_block_parses_to_data(self, ctx, provider):
        """A compliant fenced JSON block parses to ToolResult.data; the
        schema instruction rides on the system message."""
        content = 'Here you go:\n```json\n{"title": "T", "score": 7}\n```\nAll done.'
        provider.extend(ScriptedResponse.text(content))

        result = await tool_llm_query(
            ctx, prompt="p", model=MODEL_NAME, return_schema={"title": "string", "score": "number"}
        )

        assert result.data == {"title": "T", "score": 7}
        assert "```" not in result.text
        # Prompt-level structured output: the schema is in the request.
        req = provider.requests[0]
        assert [m["role"] for m in req.messages] == ["system", "user"]
        assert '"title"' in req.messages[0]["content"]
        assert req.tools is None

    @pytest.mark.asyncio
    async def test_noncompliant_strict_errors_with_field_feedback(self, ctx, provider):
        content = '```json\n{"title": "T"}\n```'  # missing score
        provider.extend(ScriptedResponse.text(content))

        result = await tool_llm_query(
            ctx, prompt="p", model=MODEL_NAME, return_schema={"title": "string", "score": "number"}, strict=True
        )

        assert result.text.startswith("[error:")
        assert "missing required field 'score'" in result.text

    @pytest.mark.asyncio
    async def test_noncompliant_permissive_falls_back_to_raw_text(self, ctx, provider):
        content = '```json\n{"title": "T"}\n```'  # missing score
        provider.extend(ScriptedResponse.text(content))

        result = await tool_llm_query(
            ctx, prompt="p", model=MODEL_NAME, return_schema={"title": "string", "score": "number"}
        )

        assert result.text == content
        assert result.data is None


@pytest.mark.scripted_provider
class TestValidation:
    @pytest.mark.asyncio
    async def test_nonpositive_timeout_rejected(self, ctx, provider):
        result = await tool_llm_query(ctx, prompt="p", model=MODEL_NAME, timeout=0)

        assert "timeout" in result.text
        assert provider.requests == []


@pytest.fixture
def models_config(tmp_data):
    """Three models across two providers, with an explicit default."""
    return Config(
        agent=AgentConfig(data_home=str(tmp_data), id="test-agent", user_id="testuser"),
        reflection=ReflectionConfig(enabled=False),
        vault_retrieval=VaultRetrievalConfig(enabled=False),
        providers={
            PROVIDER_SCRIPTED: ProviderConfig(type=PROVIDER_SCRIPTED),
            "otari-local": ProviderConfig(type=PROVIDER_SCRIPTED),
        },
        model_configs={
            "alpha": ModelConfig(provider=PROVIDER_SCRIPTED, model="gemini-3.8-flash"),
            "flint": ModelConfig(provider="otari-local", model="mandelbrot3-flint"),
            "qwen-small": ModelConfig(provider="otari-local", model="qwen3-8b"),
        },
        default_model="alpha",
    )


@pytest.fixture
def models_ctx(models_config):
    context = Context(config=models_config, event_bus=EventBus())
    context.conv_id = "test-conv"
    return context


class TestModelsDiscovery:
    @pytest.mark.asyncio
    async def test_llm_models_lists_all_configured(self, models_ctx):
        models_ctx.active_model = "flint"

        result = await tool_llm_models(models_ctx)

        data = result.data
        assert data["default_model"] == "alpha"
        assert data["active_model"] == "flint"
        assert data["total"] == 3
        assert [m["name"] for m in data["models"]] == ["alpha", "flint", "qwen-small"]
        by_name = {m["name"]: m for m in data["models"]}
        assert by_name["alpha"] == {
            "name": "alpha",
            "provider": PROVIDER_SCRIPTED,
            "model": "gemini-3.8-flash",
            "is_default": True,
            "is_active": False,
        }
        assert by_name["flint"]["is_active"] is True
        assert by_name["flint"]["is_default"] is False
        assert by_name["qwen-small"] == {
            "name": "qwen-small",
            "provider": "otari-local",
            "model": "qwen3-8b",
            "is_default": False,
            "is_active": False,
        }
        assert result.text.startswith("3 configured models (default: alpha; active: flint)")
        assert "- alpha [provider=" in result.text and "(default)" in result.text
        assert "- flint [provider=otari-local, model=mandelbrot3-flint] (active)" in result.text

    @pytest.mark.asyncio
    async def test_llm_models_filter_by_provider(self, models_ctx):
        """Provider matches exactly or as a case-insensitive substring."""
        result = await tool_llm_models(models_ctx, provider="OTARI")

        assert [m["name"] for m in result.data["models"]] == ["flint", "qwen-small"]
        assert result.data["total"] == 2
        assert result.text.startswith("2 of 3 configured models")
        assert "- alpha [" not in result.text

    @pytest.mark.asyncio
    async def test_llm_models_filter_by_query(self, models_ctx):
        """Query matches the config name or the underlying model id."""
        by_name = await tool_llm_models(models_ctx, query="FLINT")
        assert [m["name"] for m in by_name.data["models"]] == ["flint"]

        by_model = await tool_llm_models(models_ctx, query="qwen3")
        assert [m["name"] for m in by_model.data["models"]] == ["qwen-small"]

    @pytest.mark.asyncio
    async def test_llm_models_empty_filter_match(self, models_ctx):
        result = await tool_llm_models(models_ctx, query="nope")

        assert result.data["models"] == []
        assert result.data["total"] == 0
        assert "No configured models match query='nope'" in result.text
        assert "3 configured: alpha, flint, qwen-small" in result.text

    @pytest.mark.asyncio
    async def test_llm_models_stale_active_model_not_reported(self, models_ctx):
        """An active name removed from config is not reported active, matching
        llm_query's fallback behavior."""
        models_ctx.active_model = "removed-model"

        result = await tool_llm_models(models_ctx)

        assert result.data["active_model"] == ""
        assert all(m["is_active"] is False for m in result.data["models"])
        assert "active:" not in result.text

    @pytest.mark.asyncio
    async def test_llm_models_without_configs(self, tmp_data):
        config = Config(
            agent=AgentConfig(data_home=str(tmp_data), id="test-agent", user_id="testuser"),
            reflection=ReflectionConfig(enabled=False),
            vault_retrieval=VaultRetrievalConfig(enabled=False),
            model_configs={},
        )
        context = Context(config=config, event_bus=EventBus())
        context.conv_id = "test-conv"

        result = await tool_llm_models(context)

        assert result.data["models"] == []
        assert result.text == "No models configured in config.model_configs."
