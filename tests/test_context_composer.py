"""Core context composer tests: dataclasses, state, and constructor behavior.

Split from the former monolithic ``test_context_composer.py`` by concern (#1420).
See the sibling ``test_context_composer_{modes,retrieval,token_budget,assembly}.py
files for mode-specific, retrieval, token-budget, and assembly coverage."""

from decafclaw.context_composer import ContextComposer
from decafclaw.context_types import ComposedContext, ComposerState, InjectionCursors, SourceEntry

# -- Dataclass construction


class TestSourceEntry:
    def test_construction(self):
        entry = SourceEntry(source="memory", tokens_estimated=100, items_included=3)
        assert entry.source == "memory"
        assert entry.tokens_estimated == 100
        assert entry.items_included == 3
        assert entry.items_truncated == 0
        assert entry.details == {}

    def test_with_details(self):
        entry = SourceEntry(
            source="tools",
            tokens_estimated=500,
            items_included=10,
            items_truncated=5,
            details={"deferred_mode": True},
        )
        assert entry.items_truncated == 5
        assert entry.details["deferred_mode"] is True


class TestComposedContext:
    def test_construction(self):
        ctx = ComposedContext(
            messages=[{"role": "system", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
            deferred_tools=[],
            total_tokens_estimated=100,
            sources=[],
        )
        assert len(ctx.messages) == 1
        assert len(ctx.tools) == 1
        assert ctx.total_tokens_estimated == 100
        assert ctx.retrieved_context_text == ""

    def test_with_retrieved_context(self):
        ctx = ComposedContext(
            messages=[],
            tools=[],
            deferred_tools=[],
            total_tokens_estimated=0,
            sources=[],
            retrieved_context_text="some memory context",
        )
        assert ctx.retrieved_context_text == "some memory context"


# -- ComposerState


class TestComposerState:
    def test_defaults(self):
        state = ComposerState()
        assert state.last_sources == []
        assert state.last_total_tokens_estimated == 0
        assert state.last_prompt_tokens_actual == 0
        assert state.last_completion_tokens_actual == 0
        assert state.injected_paths == set()
        assert state.injection_cursors == InjectionCursors()


# -- ContextComposer


class TestContextComposer:
    def test_creates_default_state(self):
        composer = ContextComposer()
        assert isinstance(composer.state, ComposerState)

    def test_uses_provided_state(self):
        state = ComposerState(last_total_tokens_estimated=42)
        composer = ContextComposer(state=state)
        assert composer.state.last_total_tokens_estimated == 42

    def test_record_actuals(self):
        composer = ContextComposer()
        composer.record_actuals(prompt_tokens=1500, completion_tokens=300)
        assert composer.state.last_prompt_tokens_actual == 1500
        assert composer.state.last_completion_tokens_actual == 300


class TestContextComposerOnContext:
    def test_ctx_has_composer_state(self, ctx):
        assert isinstance(ctx.composer, ComposerState)

    def test_fork_gets_fresh_composer(self, ctx):
        child = ctx.fork()
        assert child.composer is not ctx.composer

    def test_fork_shares_composer_when_explicit(self, ctx):
        child = ctx.fork(composer=ctx.composer)
        assert child.composer is ctx.composer

    def test_fork_for_tool_call_shares_composer(self, ctx):
        child = ctx.fork_for_tool_call("call-123")
        assert child.composer is ctx.composer
