"""Tests for ReflectionExtension and CANDIDATE_RESPONSE dispatch (#1508)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.agent import run_agent_turn
from decafclaw.candidate_response import CandidateResponse, CandidateVerdict
from decafclaw.config import Config
from decafclaw.config_types import ExtensionConfig, ReflectionConfig
from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.extensions import init_extensions
from decafclaw.extensions.reflection import (
    ReflectionExtension,
    ReflectionState,
    should_reflect,
)
from decafclaw.reflection import ReflectionResult

EVALUATE = "decafclaw.reflection.evaluate_response"


def _candidate(content: str = "Answer", **overrides) -> CandidateResponse:
    data = {
        "content": content,
        "history": [],
        "turn_start_index": 0,
        "accumulated_text_parts": [],
        "user_message": "question",
        "attachments": [],
        "retrieved_context_text": "",
    }
    data.update(overrides)
    return CandidateResponse(**data)


def _reflection_extension(ctx) -> ReflectionExtension:
    ext = ctx.extension_manager.get("reflection")
    assert isinstance(ext, ReflectionExtension)
    return ext


def _collect(ctx, event_type: str) -> list[dict]:
    events: list[dict] = []

    def _on(event):
        if event.get("type") == event_type:
            events.append(event)

    ctx.event_bus.subscribe(_on)
    return events


def _llm_response(content: str) -> dict:
    return {"content": content, "tool_calls": None, "role": "assistant", "usage": {}}


class TestRegistration:
    def test_registered_default_enabled(self):
        from decafclaw.extensions import ensure_builtin_extension_types, get_extension_type_meta

        ensure_builtin_extension_types()
        meta = get_extension_type_meta("reflection")
        assert meta is not None
        assert meta.factory is ReflectionExtension
        assert meta.default_enabled is True

    def test_enable_subscribes_candidate_response(self, ctx):
        ext = _reflection_extension(ctx)
        hooks = ctx.event_bus.get_lifecycle_hooks(TurnLifecycle.CANDIDATE_RESPONSE)
        assert ext.on_candidate_response in hooks

    def test_disabled_config_mounts_no_hook(self, ctx):
        config = replace(
            ctx.config,
            extensions={"reflection": ExtensionConfig(enabled=False, type="reflection", options={})},
        )
        bus = EventBus()
        manager = init_extensions(config, bus)
        assert manager.is_enabled("reflection") is False
        assert bus.get_lifecycle_hooks(TurnLifecycle.CANDIDATE_RESPONSE) == []


class TestJudgeOutcomes:
    @pytest.mark.asyncio
    async def test_pass_delivers_text_and_emits_metrics(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True)
        ext = _reflection_extension(ctx)
        events = _collect(ctx, "reflection_turn")

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = ReflectionResult(passed=True, prompt_tokens=150, completion_tokens=8)
            verdict = await ext.on_candidate_response(ctx, _candidate("Good answer"))

        assert verdict == CandidateVerdict(reject=False, text="Good answer")
        mock_eval.assert_called_once()
        assert len(events) == 1
        assert events[0]["outcome"] == "passed_first"
        assert events[0]["retry_count"] == 0
        assert events[0]["judge_prompt_tokens"] == 150
        assert events[0]["judge_completion_tokens"] == 8

    @pytest.mark.asyncio
    async def test_retry_returns_failed_message_and_critique(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True, max_retries=2)
        ext = _reflection_extension(ctx)
        events = _collect(ctx, "reflection_turn")
        candidate = _candidate("Bad answer")

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = ReflectionResult(passed=False, critique="You missed the point")
            verdict = await ext.on_candidate_response(ctx, candidate)

        assert verdict is not None and verdict.reject is True
        assert verdict.text is None
        # Core appends the rejected candidate itself; the hook supplies the critique.
        assert [m["role"] for m in verdict.retry_messages] == ["user"]
        assert "[reflection]" in verdict.retry_messages[0]["content"]
        assert "You missed the point" in verdict.retry_messages[0]["content"]
        assert verdict.retry_messages[0]["source"] == "reflection_retry"
        state = candidate.state["reflection"]
        assert state.retries == 1
        # Metrics wait for the final delivery, not each rejection.
        assert events == []

    @pytest.mark.asyncio
    async def test_judge_error_fails_open(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True)
        ext = _reflection_extension(ctx)
        events = _collect(ctx, "reflection_turn")

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = ReflectionResult(passed=True, error="connection timeout")
            verdict = await ext.on_candidate_response(ctx, _candidate("response"))

        assert verdict is not None and verdict.reject is False
        assert verdict.text == "response"
        assert len(events) == 1
        assert events[0]["outcome"] == "errored"

    @pytest.mark.asyncio
    async def test_judge_inputs_include_turn_text_and_attachments(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True)
        ext = _reflection_extension(ctx)
        candidate = _candidate(
            "trailer",
            accumulated_text_parts=["report body"],
            attachments=[{"filename": "a.png", "mime_type": "image/png"}],
            retrieved_context_text="context",
        )

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = ReflectionResult(passed=True)
            await ext.on_candidate_response(ctx, candidate)

        args = mock_eval.call_args
        assert args.args[1] == "question\n\n[User attached files: a.png (image/png)]"
        assert args.args[2] == "report body\n\ntrailer"
        assert args.kwargs["retrieved_context"] == "context"


class TestSkipPaths:
    @pytest.mark.asyncio
    async def test_disabled_reflection_yields_unjudged(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=False)
        ext = _reflection_extension(ctx)
        events = _collect(ctx, "reflection_turn")

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            verdict = await ext.on_candidate_response(ctx, _candidate("plain answer"))

        mock_eval.assert_not_called()
        assert verdict == CandidateVerdict(reject=False, text="plain answer")
        assert events == []

    @pytest.mark.asyncio
    async def test_child_agent_skips_judge_and_metrics(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True)
        ctx.skip_reflection = True
        ext = _reflection_extension(ctx)
        events = _collect(ctx, "reflection_turn")

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            verdict = await ext.on_candidate_response(ctx, _candidate("child response"))

        mock_eval.assert_not_called()
        assert verdict == CandidateVerdict(reject=False, text="child response")
        assert events == []

    @pytest.mark.asyncio
    async def test_exhausted_budget_appends_model_picker_nudge(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True, max_retries=1)
        ext = _reflection_extension(ctx)
        events = _collect(ctx, "reflection_turn")
        candidate = _candidate("Mediocre answer")
        candidate.state["reflection"] = ReflectionState(
            retries=1,
            first_response="Mediocre answer",
            last_result=ReflectionResult(passed=False, critique="Still not great", prompt_tokens=180),
        )

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            verdict = await ext.on_candidate_response(ctx, candidate)

        mock_eval.assert_not_called()
        assert verdict is not None and verdict.reject is False
        assert "Mediocre answer" in verdict.text
        assert "model picker" in verdict.text
        assert candidate.state["reflection"].exhausted is True
        assert candidate.state["reflection"].last_result is None
        assert len(events) == 1
        assert events[0]["outcome"] == "loop_exhausted"

    def test_should_reflect_gates(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True)
        assert should_reflect(ctx, ctx.config, "text", 0) is True
        assert should_reflect(ctx, ctx.config, "   ", 0) is False
        assert should_reflect(ctx, ctx.config, "text", ctx.config.reflection.max_retries) is False
        ctx.skip_reflection = True
        assert should_reflect(ctx, ctx.config, "text", 0) is False


class TestVisibilityRecords:
    @pytest.mark.asyncio
    async def test_debug_archives_passing_judgment(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True, visibility="debug")
        ext = _reflection_extension(ctx)

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = ReflectionResult(passed=True, raw_response='{"pass": true}')
            verdict = await ext.on_candidate_response(ctx, _candidate("Good answer"))

        assert verdict is not None
        assert verdict.archive_messages == (
            {"role": "reflection", "tool": "reflection: PASS", "content": '{"pass": true}'},
        )

    @pytest.mark.asyncio
    async def test_visible_archives_only_failed_judgment(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True, visibility="visible")
        ext = _reflection_extension(ctx)
        candidate = _candidate("Better answer")
        candidate.state["reflection"] = ReflectionState(retries=1, last_result=ReflectionResult(passed=True))

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = ReflectionResult(passed=True)
            verdict = await ext.on_candidate_response(ctx, candidate)

        assert verdict is not None
        assert verdict.archive_messages == ()

    @pytest.mark.asyncio
    async def test_hidden_archives_nothing(self, ctx):
        ctx.config.reflection = ReflectionConfig(enabled=True)
        ext = _reflection_extension(ctx)

        with patch(EVALUATE, new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = ReflectionResult(passed=True)
            verdict = await ext.on_candidate_response(ctx, _candidate("Good answer"))

        assert verdict is not None
        assert verdict.archive_messages == ()


class TestTurnLoopDispatch:
    @pytest.mark.asyncio
    async def test_hook_rejection_retries_the_turn(self, ctx):
        """The runner appends retry messages to history/messages and iterates."""
        ctx.config.llm.streaming = False
        ctx.config.system_prompt = "test"
        ctx.config.reflection = ReflectionConfig(enabled=False)
        seen: list[str] = []

        def hook(context, candidate):
            seen.append(candidate.content)
            if candidate.content == "first try":
                return CandidateVerdict(
                    reject=True,
                    retry_messages=({"role": "user", "content": "[critic] try again"},),
                )
            return None

        ctx.add_interceptor(TurnLifecycle.CANDIDATE_RESPONSE, hook)

        with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [_llm_response("first try"), _llm_response("second try")]
            history: list[dict] = []
            result = await run_agent_turn(ctx, "question", history)

        assert result.text == "second try"
        assert seen == ["first try", "second try"]
        # Composer injections (preempt hints, status line) also land in
        # history; check our messages keep their order around them.
        contents = [m["content"] for m in history]
        assert contents.index("first try") < contents.index("[critic] try again") < contents.index("second try")
        assert history[-1] == {"role": "assistant", "content": "second try"}

    @pytest.mark.asyncio
    async def test_hook_text_override_is_delivered(self, ctx):
        ctx.config.llm.streaming = False
        ctx.config.system_prompt = "test"
        ctx.config.reflection = ReflectionConfig(enabled=False)

        def hook(context, candidate):
            return CandidateVerdict(reject=False, text=f"{candidate.content} (checked)")

        ctx.add_interceptor(TurnLifecycle.CANDIDATE_RESPONSE, hook)

        with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _llm_response("draft")
            history: list[dict] = []
            result = await run_agent_turn(ctx, "question", history)

        assert result.text == "draft (checked)"
        assert history[-1]["content"] == "draft (checked)"

    @pytest.mark.asyncio
    async def test_raising_hook_is_fail_open(self, ctx):
        ctx.config.llm.streaming = False
        ctx.config.system_prompt = "test"
        ctx.config.reflection = ReflectionConfig(enabled=False)

        def hook(context, candidate):
            raise RuntimeError("critic exploded")

        ctx.add_interceptor(TurnLifecycle.CANDIDATE_RESPONSE, hook)

        with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _llm_response("delivered")
            result = await run_agent_turn(ctx, "question", [])

        assert result.text == "delivered"


class TestEvalRunnerMount:
    """Eval turns mount the reflection hook that used to live in core."""

    async def _run(self, tmp_path, monkeypatch, cfg):
        from decafclaw.eval import runner as eval_runner

        seen: dict = {}

        async def fake_turn(context, turn_input, history):
            seen["ctx"] = context
            return SimpleNamespace(text="ok")

        monkeypatch.setattr(eval_runner, "run_agent_turn", fake_turn)
        monkeypatch.setattr(eval_runner, "read_context_sidecar", lambda config, conv_id: None)
        cfg = eval_runner._build_test_config(cfg, {"setup": {}}, str(tmp_path))
        await eval_runner.run_test(cfg, {"name": "t", "input": "hi", "expect": {}})
        return seen["ctx"]

    @pytest.mark.asyncio
    async def test_run_test_mounts_reflection_hook(self, tmp_path, monkeypatch):
        ctx = await self._run(tmp_path, monkeypatch, Config())

        assert ctx.extension_manager.is_enabled("reflection") is True
        assert ctx.event_bus.get_lifecycle_hooks(TurnLifecycle.CANDIDATE_RESPONSE)

    @pytest.mark.asyncio
    async def test_run_test_honors_explicit_yank(self, tmp_path, monkeypatch):
        cfg = Config(extensions={"reflection": ExtensionConfig(enabled=False, type="reflection", options={})})
        ctx = await self._run(tmp_path, monkeypatch, cfg)

        assert ctx.extension_manager.is_enabled("reflection") is False
        assert ctx.event_bus.get_lifecycle_hooks(TurnLifecycle.CANDIDATE_RESPONSE) == []
