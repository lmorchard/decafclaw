"""Structured PRE_COMPOSE context contributions (#1486).

Verifies the `ContextSlot` ordering invariant, legacy string coercion, source
entry telemetry, and timeout / fail-open isolation.
"""

import asyncio

import pytest

from decafclaw.context_composer import ContextComposer
from decafclaw.context_types import SourceEntry
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
    """ContextContribution and ContextSlot expose the five documented slots."""
    assert [slot.value for slot in ContextSlot] == ["head", "delta", "tail", "epilogue", "working_memory"]

    message = {"role": "user", "content": "structured"}
    contribution = ContextContribution(slot=ContextSlot.DELTA, messages=[message])
    assert contribution.slot is ContextSlot.DELTA
    assert contribution.messages == [message]
    assert contribution.source_entry is None


def test_working_memory_envelope_normalizes_tags():
    """Odd message roles still produce valid, attributing sub-block tags."""
    from decafclaw.context_pipeline import working_memory_envelope

    envelope = working_memory_envelope(
        [
            {"role": "3rd-party source", "content": "A"},
            {"role": "", "content": "B"},
            {"role": "empty", "content": "   "},
        ]
    )
    assert envelope is not None
    assert envelope["content"] == (
        "<working_memory>\n<_3rd-party_source>\nA\n</_3rd-party_source>\n<context>\nB\n</context>\n</working_memory>"
    )
    assert working_memory_envelope([{"role": "user", "content": "  "}]) is None


@pytest.mark.asyncio
async def test_slot_ordering_invariant(ctx, config, no_tail_notes):
    """HEAD precedes HISTORY, DELTA before TAIL, and working memory mounts last."""
    config.system_prompt = "<soul>STATIC PREFIX</soul>"

    async def hook(context, user_msg):
        # Deliberately out of order: assembly, not hook result order, decides placement.
        return [
            ContextContribution(ContextSlot.TAIL, [{"role": "user", "content": "TAIL-MARKER"}]),
            ContextContribution(ContextSlot.HEAD, [{"role": "system", "content": "HEAD-MARKER"}]),
            ContextContribution(ContextSlot.DELTA, [{"role": "user", "content": "DELTA-MARKER"}]),
            ContextContribution(ContextSlot.WORKING_MEMORY, [{"role": "vault_retrieval", "content": "MEMORY-MARKER"}]),
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
    memory = _index(messages, "MEMORY-MARKER")

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "<soul>STATIC PREFIX</soul>"
    assert head > 0
    assert head < history_idx < delta < tail < user < memory
    assert messages[memory]["role"] == "user"  # internal working_memory role remaps
    assert messages[memory]["content"] == (
        "<working_memory>\n<vault_retrieval>\nMEMORY-MARKER\n</vault_retrieval>\n</working_memory>"
    )

    # HEAD is a stable prefix contribution and working memory is transient;
    # neither is appended to history.
    archived = _archived_text(history)
    assert "HEAD-MARKER" not in archived
    assert "MEMORY-MARKER" not in archived
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
async def test_legacy_string_results_coerce_to_working_memory(ctx, config, no_tail_notes):
    """str / list[str] hooks keep the bounded <recalled_facts> block, unarchived."""

    async def hook(context, user_msg):
        return ["Coffee preference: espresso", "Timezone: Pacific"]

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    history = [{"role": "user", "content": "HISTORY-MARKER"}]
    composed = await _compose(ctx, history=history)

    messages = composed.messages
    recalled = [m for m in messages if "<recalled_facts>" in str(m.get("content", ""))]
    assert len(recalled) == 1
    assert recalled[0]["role"] == "user"
    assert recalled[0]["content"] == (
        "<working_memory>\n"
        "<recalled_facts>\n"
        "Coffee preference: espresso\n"
        "Timezone: Pacific\n"
        "</recalled_facts>\n"
        "</working_memory>"
    )

    history_idx = _index(messages, "HISTORY-MARKER")
    user = _index(messages, "USER-MARKER")
    memory = _index(messages, "<recalled_facts>")
    assert history_idx < user < memory

    # The block is this turn's prompt only; it never reaches the archive.
    assert "<recalled_facts>" not in _archived_text(composed.messages_to_archive)
    assert "<recalled_facts>" not in _archived_text(history)

    entry = next(s for s in composed.sources if s.source == "recalled_facts")
    assert entry.items_included == 2
    assert entry.tokens_estimated > 0

    # The UI discloses the atomic facts, not the rendered envelope (#1514).
    assert composed.recalled_facts == ["Coffee preference: espresso", "Timezone: Pacific"]
    assert composed.provenance == {
        "facts": [
            {"fact": "Coffee preference: espresso"},
            {"fact": "Timezone: Pacific"},
        ]
    }


@pytest.mark.asyncio
async def test_recalled_facts_match_the_trimmed_envelope(ctx, config, no_tail_notes):
    """Over the token ceiling, the exposed facts list drops lines the model never saw (#1514)."""
    config.lifecycle.pre_compose_token_ceiling = 20

    async def hook(context, user_msg):
        return [
            "Fact one fits inside the ceiling",
            "Fact two does not fit inside the ceiling at all",
        ]

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composed = await _compose(ctx)

    recalled = [m for m in composed.messages if m.get("role") == "user" and "<recalled_facts>" in m["content"]]
    assert len(recalled) == 1
    assert "Fact one fits inside the ceiling" in recalled[0]["content"]
    assert "Fact two" not in recalled[0]["content"]

    assert composed.recalled_facts == ["Fact one fits inside the ceiling"]
    assert composed.provenance == {"facts": [{"fact": "Fact one fits inside the ceiling"}]}


@pytest.mark.asyncio
async def test_contribution_provenance_reaches_the_turn(ctx, config, no_tail_notes):
    """Structured contributions merge their provenance items into the turn (#1563)."""
    first = ContextContribution(
        slot=ContextSlot.DELTA,
        messages=[{"role": "conversation_notes", "content": "NOTES-MARKER"}],
        provenance={"scratchpad": [{"text": "Ship capsules."}]},
    )
    second = ContextContribution(
        slot=ContextSlot.TAIL,
        messages=[{"role": "vault_retrieval", "content": "VAULT-MARKER"}],
        provenance={"vault": [{"page": "agent/pages/decafclaw.md", "score": 0.82}]},
    )

    async def hook(context, user_msg):
        return [first, second]

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composed = await _compose(ctx)

    assert composed.provenance == {
        "scratchpad": [{"text": "Ship capsules."}],
        "vault": [{"page": "agent/pages/decafclaw.md", "score": 0.82}],
    }
    assert ctx.composer.last_provenance == composed.provenance
    assert "NOTES-MARKER" in _archived_text(composed.messages_to_archive)
    assert "VAULT-MARKER" in _archived_text(composed.messages_to_archive)


@pytest.mark.asyncio
async def test_malformed_provenance_is_dropped_fail_open(ctx, config, no_tail_notes):
    """A bad provenance payload is logged and skipped without failing the turn (#1563)."""

    async def hook(context, user_msg):
        return [
            ContextContribution(
                ContextSlot.TAIL,
                [{"role": "user", "content": "GOOD-MARKER"}],
                provenance={
                    "facts": [{"fact": "kept"}, "dropped-string"],
                    "bad": "not-a-list",
                    3: [{"nope": True}],
                },
            ),
            ContextContribution(
                ContextSlot.TAIL,
                [{"role": "user", "content": "SECOND-MARKER"}],
                provenance=["not-a-dict"],  # type: ignore[arg-type]
            ),
        ]

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composed = await _compose(ctx)

    assert composed.provenance == {"facts": [{"fact": "kept"}]}
    assert _index(composed.messages, "GOOD-MARKER") < _index(composed.messages, "SECOND-MARKER")


@pytest.mark.asyncio
async def test_provenance_is_refilled_each_turn(ctx, config, no_tail_notes):
    """A later turn without contributions does not replay the prior payload (#1563)."""
    calls = {"n": 0}

    async def hook(context, user_msg):
        calls["n"] += 1
        if calls["n"] > 1:
            return None
        return ContextContribution(
            ContextSlot.DELTA,
            [{"role": "conversation_notes", "content": "ONE"}],
            provenance={"scratchpad": [{"text": "one"}]},
        )

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composer = ContextComposer(state=ctx.composer)

    first = await composer.compose(ctx=ctx, user_message="FIRST", history=[])
    assert first.provenance == {"scratchpad": [{"text": "one"}]}

    second = await composer.compose(ctx=ctx, user_message="SECOND", history=list(first.messages_to_archive))
    assert second.provenance == {}


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
async def test_working_memory_bundles_sources_and_never_replays(ctx, config, no_tail_notes):
    """Transient discovery lands in one block and is absent from later turns (#1574)."""

    calls = {"n": 0}

    async def hook(context, user_msg):
        calls["n"] += 1
        turn = calls["n"]
        return [
            ContextContribution(
                ContextSlot.WORKING_MEMORY,
                [{"role": "vault_retrieval", "content": f"VAULT-{turn}"}],
            ),
            ContextContribution(
                ContextSlot.WORKING_MEMORY,
                [{"role": "conversation_notes", "content": f"NOTES-{turn}"}],
            ),
        ]

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composer = ContextComposer(state=ctx.composer)

    first = await composer.compose(ctx=ctx, user_message="FIRST", history=[])
    memory_msgs = [m for m in first.messages if str(m.get("content", "")).startswith("<working_memory>")]
    assert len(memory_msgs) == 1
    content = memory_msgs[0]["content"]
    assert "<vault_retrieval>\nVAULT-1\n</vault_retrieval>" in content
    assert "<conversation_notes>\nNOTES-1\n</conversation_notes>" in content
    assert content.index("VAULT-1") < content.index("NOTES-1")
    assert first.messages[-1] is memory_msgs[0]

    # Only the user turn is archived; the working-memory block is not.
    archived_text = _archived_text(first.messages_to_archive)
    assert "VAULT-1" not in archived_text
    assert "NOTES-1" not in archived_text

    # Turn 2 gets its own block; turn 1's retrieval does not replay.
    history = list(first.messages_to_archive)
    second = await composer.compose(ctx=ctx, user_message="SECOND", history=history)
    second_text = "\n".join(str(m.get("content", "")) for m in second.messages)
    assert "VAULT-1" not in second_text
    assert "NOTES-1" not in second_text
    assert "<vault_retrieval>\nVAULT-2\n</vault_retrieval>" in second_text
    assert second.messages[-1]["role"] == "user"
    assert str(second.messages[-1]["content"]).startswith("<working_memory>")


@pytest.mark.asyncio
async def test_epilogue_mounts_after_user_message(ctx, config, no_tail_notes):
    """EPILOGUE mounts after the user message, archives, and replays in place (#1507)."""

    calls = {"n": 0}

    async def hook(context, user_msg):
        calls["n"] += 1
        if calls["n"] > 1:
            return None
        return ContextContribution(
            ContextSlot.EPILOGUE,
            [{"role": "skill_hint", "content": "EPILOGUE-MARKER"}],
        )

    ctx.add_interceptor(TurnLifecycle.PRE_COMPOSE, hook)
    composer = ContextComposer(state=ctx.composer)

    first = await composer.compose(ctx=ctx, user_message="FIRST", history=[])
    user = _index(first.messages, "FIRST")
    epilogue = _index(first.messages, "EPILOGUE-MARKER")
    assert epilogue > user
    assert first.messages[epilogue]["role"] == "user"  # internal skill_hint role remaps
    assert "EPILOGUE-MARKER" in _archived_text(first.messages_to_archive)

    # Turn 2 replays the archived hint where turn 1 sent it, not at the new
    # generation boundary.
    history = list(first.messages_to_archive)
    second = await composer.compose(ctx=ctx, user_message="SECOND", history=history)
    assert sum(1 for m in second.messages if "EPILOGUE-MARKER" in str(m.get("content", ""))) == 1
    assert _index(second.messages, "EPILOGUE-MARKER") < _index(second.messages, "SECOND")


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
