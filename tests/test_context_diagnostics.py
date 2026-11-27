"""Slot and extension telemetry for the Context Inspector (#1509).

The inspector needs to attribute each hook-driven context contribution to the
lifecycle slot it mounted into and the extension that produced it. These tests
cover the ``SourceEntry`` attribution, sidecar serialization, and the
``last_request.json`` snapshot.
"""

import pytest

from decafclaw.context_composer import ContextComposer
from decafclaw.context_pipeline import (
    compose_pre_compose_hooks,
    mount_context_contribution,
)
from decafclaw.context_sidecars import read_last_request_sidecar
from decafclaw.context_types import ComposedContext, SourceEntry
from decafclaw.events import TurnLifecycle
from decafclaw.extensions import ContextContribution, ContextSlot
from decafclaw.extensions.diagnostics_and_telemetry import DiagnosticsAndTelemetryExtension


def _entry(source: str = "test_source", **kwargs) -> SourceEntry:
    return SourceEntry(source=source, tokens_estimated=10, items_included=1, **kwargs)


def _empty_mounts():
    return [], [], [], [], [], []


class _BoundExtension:
    """Stand-in extension so hooks expose ``__self__.name`` like the real ones."""

    def __init__(self, name: str, result):
        self.name = name
        self._result = result

    async def pre_compose(self, ctx, user_message):
        return self._result


def test_mount_attributes_slot_and_extension_from_the_contribution():
    """A contribution's mount target names its slot; the hook names its extension."""
    entry = _entry()
    contribution = ContextContribution(
        slot=ContextSlot.TAIL,
        messages=[{"role": "user", "content": "TAIL"}],
        source_entry=entry,
    )
    head, delta, tail, epilogue, working_memory, sources = _empty_mounts()

    mount_context_contribution(
        contribution,
        head,
        delta,
        tail,
        epilogue,
        working_memory,
        sources,
        extension="vault_references",
    )

    assert tail == [{"role": "user", "content": "TAIL"}]
    assert sources == [entry]
    assert entry.slot == "tail"
    assert entry.extension == "vault_references"


def test_mount_keeps_an_extension_supplied_extension_name():
    """An entry that already names its origin keeps that attribution."""
    entry = _entry(extension="vault_references")
    contribution = ContextContribution(
        slot=ContextSlot.TAIL,
        messages=[{"role": "user", "content": "TAIL"}],
        source_entry=entry,
    )
    head, delta, tail, epilogue, working_memory, sources = _empty_mounts()

    mount_context_contribution(
        contribution,
        head,
        delta,
        tail,
        epilogue,
        working_memory,
        sources,
        extension="renamed",
    )

    assert entry.extension == "vault_references"
    assert entry.slot == "tail"


@pytest.mark.asyncio
async def test_compose_hooks_attribute_every_slot(ctx, config):
    """HEAD, DELTA, TAIL, and EPILOGUE contributions carry slot + owner name."""
    contributions = [
        ContextContribution(
            slot=ContextSlot.HEAD,
            messages=[{"role": "system", "content": "HEAD"}],
            source_entry=_entry("head_source"),
        ),
        ContextContribution(
            slot=ContextSlot.DELTA,
            messages=[{"role": "user", "content": "DELTA"}],
            source_entry=_entry("delta_source"),
        ),
        ContextContribution(
            slot=ContextSlot.TAIL,
            messages=[{"role": "user", "content": "TAIL"}],
            source_entry=_entry("tail_source"),
        ),
        ContextContribution(
            slot=ContextSlot.EPILOGUE,
            messages=[{"role": "skill_hint", "content": "EPILOGUE"}],
            source_entry=_entry("epilogue_source"),
        ),
    ]
    ctx.add_interceptor(
        TurnLifecycle.PRE_COMPOSE,
        _BoundExtension("vault_retrieval", contributions).pre_compose,
    )

    (
        head,
        delta,
        tail,
        epilogue,
        _working_memory,
        sources,
        _facts,
        _provenance,
    ) = await compose_pre_compose_hooks(ctx, config, "hi", [], budget_consumers=False)

    by_source = {entry.source: entry for entry in sources}
    assert [m["content"] for m in head] == ["HEAD"]
    assert [m["content"] for m in delta] == ["DELTA"]
    assert [m["content"] for m in tail] == ["TAIL"]
    assert [m["content"] for m in epilogue] == ["EPILOGUE"]
    for slot, source in (
        ("head", "head_source"),
        ("delta", "delta_source"),
        ("tail", "tail_source"),
        ("epilogue", "epilogue_source"),
    ):
        assert by_source[source].slot == slot
        assert by_source[source].extension == "vault_retrieval"


@pytest.mark.asyncio
async def test_legacy_fact_text_records_working_memory_slot_and_owner(ctx, config):
    """Legacy str results attribute the working-memory slot to the owning hook."""
    ctx.add_interceptor(
        TurnLifecycle.PRE_COMPOSE,
        _BoundExtension("improvement_scout", ["A durable fact"]).pre_compose,
    )

    (_head, _delta, _tail, _epilogue, working_memory, sources, facts, _prov) = await compose_pre_compose_hooks(
        ctx, config, "hi", [], budget_consumers=False
    )

    entry = next(s for s in sources if s.source == "recalled_facts")
    assert entry.slot == "working_memory"
    assert entry.extension == "improvement_scout"
    assert facts == ["A durable fact"]
    assert working_memory


def test_build_diagnostics_serializes_slot_and_extension(config):
    """The context.json diagnostics sidecar records slot and extension."""
    config.system_prompt = "System."
    composed = ComposedContext(
        messages=[],
        tools=[],
        deferred_tools=[],
        total_tokens_estimated=10,
        sources=[
            SourceEntry(
                source="memory",
                tokens_estimated=10,
                items_included=1,
                slot="working_memory",
                extension="vault_retrieval",
            ),
            SourceEntry(source="history", tokens_estimated=5, items_included=2),
        ],
        memory_results=[],
    )

    diag = ContextComposer().build_diagnostics(config, composed)

    by_source = {entry["source"]: entry for entry in diag["sources"]}
    assert by_source["memory"]["slot"] == "working_memory"
    assert by_source["memory"]["extension"] == "vault_retrieval"
    # Sources without a hook keep the empty defaults rather than omitting keys.
    assert by_source["history"]["slot"] == ""
    assert by_source["history"]["extension"] == ""


@pytest.mark.asyncio
async def test_last_request_sidecar_records_slot_and_extension(ctx):
    """last_request.json exposes the slot + extension attributes for the inspector."""
    ctx.composer.last_sources = [
        SourceEntry(
            source="vault_guide",
            tokens_estimated=20,
            items_included=1,
            slot="head",
            extension="vault_guide",
        )
    ]
    ext = DiagnosticsAndTelemetryExtension({}, ctx.config)
    ext.on_before_llm_call(ctx, [{"role": "system", "content": "sys"}], [])

    await ext.on_turn_settle_hook(ctx)

    data = read_last_request_sidecar(ctx.config, ctx.conv_id)
    assert data is not None
    assert data["sources"] == [
        {
            "source": "vault_guide",
            "tokens_estimated": 20,
            "items_included": 1,
            "items_truncated": 0,
            "details": {},
            "slot": "head",
            "extension": "vault_guide",
        }
    ]
