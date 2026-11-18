"""Structured PRE_COMPOSE context contributions (#1486).

Verifies the `ContextSlot` ordering invariant, legacy string coercion, source
entry telemetry, and timeout / fail-open isolation.
"""

import asyncio

import pytest

from decafclaw.context_composer import ContextComposer, SourceEntry
from decafclaw.events import TurnLifecycle
from decafclaw.extensions import ContextContribution, ContextSlot


def _compose(ctx, user_message="USER-MARKER", history=None):
    composer = ContextComposer(state=ctx.composer)
    return composer.compose(
        ctx=ctx,
        user_message=user_message,
        history=history if history is not None else [],
    )


def _index(messages, marker):
    for i, message in enumerate(messages):
        if marker in str(message.get("content", "")):
            return i
    raise AssertionError(f"marker {marker!r} not found in {messages!r}")


def _archived_text(history):
    return "\n".join(str(m.get("content", "")) for m in history)


def test_context_slots_and_contribution_contract():
    """ContextContribution and ContextSlot expose the three documented slots."""
    assert [slot.value for slot in ContextSlot] == ["head", "delta", "tail"]

    message = {"role": "user", "content": "structured"}
    contribution = ContextContribution(slot=ContextSlot.DELTA, messages=[message])
    assert contribution.slot is ContextSlot.DELTA
    assert contribution.messages == [message]
    assert contribution.source_entry is None


@pytest.mark.asyncio
async def test_slot_ordering_invariant(ctx, config, no_tail_notes):
    """HEAD precedes HISTORY, DELTA sits before TAIL, and the user message is last."""
    config.system_prompt = "<soul>STATIC PREFIX</soul>"

    async def hook(context, user_msg):
        # Deliberately out of order: assembly, not hook result order, decides placement.
        return [
            ContextContribution(ContextSlot.TAIL, [{"role": "user", "content": "TAIL-MARKER"}]),
            ContextContribution(ContextSlot.HEAD, [{"role": "system", "content": "HEAD-MARKER"}]),
            ContextContribution(ContextSlot.DELTA, [{"role": "user", "content": "DELTA-MARKER"}]),
        ]

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    history = [
        {"role": "user", "content": "HISTORY-MARKER"},
        {"role": "assistant", "content": "HISTORY-REPLY"},
    ]
    composed = await _compose(ctx, history=history)

    messages = composed.messages
    head = _index(messages, "HEAD-MARKER")
    history_idx = _index(messages, "HISTORY-MARKER")
    delta = _index(messages, "DELTA-MARKER")
    tail = _index(messages, "TAIL-MARKER")
    user = _index(messages, "USER-MARKER")

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "<soul>STATIC PREFIX</soul>"
    assert head > 0
    assert head < history_idx < delta < tail < user
    assert messages[-1]["content"] == "USER-MARKER"

    # HEAD is a stable prefix contribution: it is not appended to history.
    archived = _archived_text(history)
    assert "HEAD-MARKER" not in archived
    assert "DELTA-MARKER" in archived
    assert "TAIL-MARKER" in archived


@pytest.mark.asyncio
async def test_head_prefix_stable_across_turns(ctx, config, no_tail_notes):
    """HEAD mounts at the same offset every turn and is never archived.

    Turn 2 composes against the history turn 1 actually produced and archived,
    so the test fails if HEAD lands in history (archived) or gets re-added per
    turn on top of an archived copy.
    """
    config.system_prompt = "<soul>STATIC PREFIX</soul>"

    async def hook(context, user_msg):
        return ContextContribution(ContextSlot.HEAD, [{"role": "system", "content": "HEAD-STATIC"}])

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composer = ContextComposer(state=ctx.composer)

    first = await composer.compose(ctx=ctx, user_message="FIRST", history=[])
    # Turn 2 gets the history turn 1 actually archived — what the caller
    # persists and reloads — so a HEAD message that leaked into the archive
    # would reappear here on top of the freshly mounted contribution.
    history: list[dict] = list(first.messages_to_archive)
    second = await composer.compose(ctx=ctx, user_message="SECOND", history=history)

    for composed in (first, second):
        assert sum(1 for m in composed.messages if "HEAD-STATIC" in str(m.get("content", ""))) == 1
    assert "HEAD-STATIC" not in _archived_text(first.messages_to_archive)
    assert "HEAD-STATIC" not in _archived_text(history)

    first_prefix = first.messages[: _index(first.messages, "HEAD-STATIC") + 1]
    second_prefix = second.messages[: _index(second.messages, "HEAD-STATIC") + 1]
    assert first_prefix == second_prefix


@pytest.mark.asyncio
async def test_legacy_string_results_coerce_to_tail_envelope(ctx, config, no_tail_notes):
    """str / list[str] hooks keep the bounded <recalled_facts> TAIL behavior."""

    async def hook(context, user_msg):
        return ["Coffee preference: espresso", "Timezone: Pacific"]

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composed = await _compose(
        ctx,
        history=[{"role": "user", "content": "HISTORY-MARKER"}],
    )

    messages = composed.messages
    recalled = [m for m in messages if "<recalled_facts>" in str(m.get("content", ""))]
    assert len(recalled) == 1
    assert recalled[0]["role"] == "user"
    assert recalled[0]["content"] == (
        "<recalled_facts>\nCoffee preference: espresso\nTimezone: Pacific\n</recalled_facts>"
    )

    history = _index(messages, "HISTORY-MARKER")
    tail = _index(messages, "<recalled_facts>")
    user = _index(messages, "USER-MARKER")
    assert history < tail < user

    entry = next(s for s in composed.sources if s.source == "recalled_facts")
    assert entry.items_included == 2
    assert entry.tokens_estimated > 0


@pytest.mark.asyncio
async def test_contribution_source_entry_reaches_turn_sources(ctx, config, no_tail_notes):
    """A contribution's source_entry is appended next to the other turn sources."""
    entry = SourceEntry(source="vault_retrieval_extension", tokens_estimated=42, items_included=3)

    async def hook(context, user_msg):
        return ContextContribution(
            ContextSlot.TAIL,
            messages=[{"role": "user", "content": "RETRIEVED-MARKER"}],
            source_entry=entry,
        )

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composed = await _compose(ctx)

    assert entry in composed.sources
    assert "vault_retrieval_extension" in {s.source for s in composed.sources}
    assert _index(composed.messages, "RETRIEVED-MARKER") < _index(composed.messages, "USER-MARKER")


@pytest.mark.asyncio
async def test_failing_hook_does_not_block_other_contributions(ctx, config, no_tail_notes):
    """One raising hook is logged; another hook's structured contribution survives."""

    async def failing_hook(context, user_msg):
        raise RuntimeError("search backend down")

    async def good_hook(context, user_msg):
        return ContextContribution(ContextSlot.DELTA, [{"role": "user", "content": "GOOD-MARKER"}])

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, failing_hook)
    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, good_hook)

    composed = await _compose(ctx)
    assert _index(composed.messages, "GOOD-MARKER") < _index(composed.messages, "USER-MARKER")


@pytest.mark.asyncio
async def test_timeout_keeps_fast_contributions(ctx, config, no_tail_notes):
    """The collector keeps contributions finished inside the bound, dropping only slow hooks."""
    config.lifecycle.pre_compose_timeout = 0.05

    async def slow_hook(context, user_msg):
        await asyncio.sleep(0.5)
        return ContextContribution(ContextSlot.TAIL, [{"role": "user", "content": "SLOW-MARKER"}])

    async def good_hook(context, user_msg):
        return ContextContribution(ContextSlot.TAIL, [{"role": "user", "content": "FAST-MARKER"}])

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, slow_hook)
    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, good_hook)

    composed = await _compose(ctx)
    assert composed is not None
    assert "SLOW-MARKER" not in str(composed.messages)
    assert "FAST-MARKER" in str(composed.messages)


@pytest.mark.asyncio
async def test_malformed_returns_fail_open(ctx, config, no_tail_notes):
    """Unsupported values and malformed contributions are warned about, not fatal."""

    async def hook(context, user_msg):
        return [
            42,
            ContextContribution(slot="bogus", messages=[{"role": "user", "content": "BAD-SLOT"}]),
            ContextContribution(slot=ContextSlot.DELTA, messages=["not-a-dict"]),
            ContextContribution(ContextSlot.DELTA, [{"role": "user", "content": "VALID-MARKER"}]),
        ]

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composed = await _compose(ctx)

    assert _index(composed.messages, "VALID-MARKER") < _index(composed.messages, "USER-MARKER")
    assert "BAD-SLOT" not in str(composed.messages)
    assert "not-a-dict" not in str(composed.messages)


@pytest.mark.asyncio
async def test_extension_declared_timeout_overrides_shared_default(ctx, config, no_tail_notes):
    """An extension may declare a larger PRE_COMPOSE bound than the shared default (#1489)."""
    config.lifecycle.pre_compose_timeout = 0.05

    class SlowCollector:
        pre_compose_timeout = 0.5

        async def hook(self, context, user_msg):
            await asyncio.sleep(0.15)
            return ContextContribution(ContextSlot.TAIL, [{"role": "user", "content": "SLOW-BUT-ALLOWED"}])

    collector = SlowCollector()
    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, collector.hook)
    composed = await _compose(ctx)

    assert "SLOW-BUT-ALLOWED" in str(composed.messages)
