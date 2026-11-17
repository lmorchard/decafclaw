"""Tests for the out-of-band improvement scout extension (#1482)."""

import copy
import json

import pytest

from decafclaw.config_types import ExtensionConfig
from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.extensions import clear_extension_types, init_extensions
from decafclaw.extensions import improvement_scout as scout
from decafclaw.improvement_inbox import inbox_path, read_items

_SUGGESTION = {
    "title": "SSH commands suffer from multi-level quoting",
    "observed_friction": "Nested bash quotes inside JSON tool arguments failed.",
    "suggested_improvement": "Pipe scripts through stdin instead of CLI strings.",
    "subsystem": "shell",
    "severity": "high",
}


def _llm_reply(stub_llm, payload=None):
    stub_llm.response = {
        "content": json.dumps(payload if payload is not None else _SUGGESTION),
        "role": "assistant",
        "tool_calls": None,
        "usage": {},
    }


def _extension(config, *, model="test-model", **options):
    options.setdefault("model", model)
    return scout.ImprovementScoutExtension(options, config)


def _tool_error_transcript():
    return [
        {"role": "user", "content": "restart the container"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "content": "[error: docker command failed: quote parsing broke]"},
    ]


def _correction_transcript():
    return [
        {"role": "user", "content": "try the sync again"},
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": "that's wrong, it didn't work"},
    ]


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("[error: boom]", True),
        ("output\n[exit code: 1]", True),
        ("[exit code: 0]", False),
        ("all good", False),
    ],
)
def test_tool_error_detection(content, expected):
    assert scout._is_tool_error(content) is expected


def test_detect_trigger_prefers_tool_errors():
    trigger = scout._detect_trigger(_tool_error_transcript())
    assert trigger is not None
    assert trigger.kind == "tool_error"
    assert "quote parsing broke" in trigger.snippet


@pytest.mark.parametrize(
    "content",
    ["stop doing that", "wait", "that didn't work", "That's wrong", "please try again"],
)
def test_detect_trigger_recognizes_user_corrections(content):
    trigger = scout._detect_trigger([{"role": "user", "content": content}])
    assert trigger is not None
    assert trigger.kind == "user_correction"


@pytest.mark.parametrize(
    "content",
    ["start the job", "waiting for the build", "it worked", "retry the request"],
)
def test_disable_triggers_do_not_match(content):
    assert scout._detect_trigger([{"role": "user", "content": content}]) is None


def test_clean_transcript_costs_zero_llm_calls(config, ctx, stub_llm):
    ext = _extension(config)
    transcript = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]

    import asyncio

    asyncio.run(ext.on_turn_settle_hook(ctx, transcript))

    assert stub_llm == []
    assert not inbox_path(config).exists()


# ---------------------------------------------------------------------------
# Evaluation and append
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_error_turn_appends_pending_item(config, ctx, stub_llm):
    _llm_reply(stub_llm)
    ext = _extension(config, model="evaluator-model")

    await ext.on_turn_settle_hook(ctx, _tool_error_transcript())

    items = read_items(config)
    assert len(items) == 1
    item = items[0]
    assert item["trigger"] == "tool_error"
    assert item["title"] == _SUGGESTION["title"]
    assert item["conversation_id"] == "test-conv"
    assert item["subsystem"] == "shell"
    assert item["severity"] == "high"
    assert item["status"] == "pending"
    assert stub_llm[0]["model_name"] == "evaluator-model"


@pytest.mark.asyncio
async def test_user_correction_turn_appends_item(config, ctx, stub_llm):
    _llm_reply(stub_llm, {**_SUGGESTION, "subsystem": "prompt", "severity": "medium"})
    ext = _extension(config)

    await ext.on_turn_settle_hook(ctx, _correction_transcript())

    items = read_items(config)
    assert len(items) == 1
    assert items[0]["trigger"] == "user_correction"
    assert items[0]["subsystem"] == "prompt"


@pytest.mark.asyncio
async def test_model_skip_and_retry_verdicts(config, ctx, stub_llm):
    ext = _extension(config)

    _llm_reply(stub_llm, {"skip": True})
    await ext.on_turn_settle_hook(ctx, _tool_error_transcript())
    assert read_items(config) == []

    stub_llm.response = {"content": "not json at all", "role": "assistant", "tool_calls": None, "usage": {}}
    transcript = [{"role": "tool", "content": "[error: a different failure]"}]
    await ext.on_turn_settle_hook(ctx, transcript)
    assert read_items(config) == []


@pytest.mark.asyncio
async def test_missing_model_skips_without_llm(config, ctx, stub_llm):
    config.default_model = ""
    ext = _extension(config, model=None)

    await ext.on_turn_settle_hook(ctx, _tool_error_transcript())

    assert stub_llm == []
    assert not inbox_path(config).exists()


@pytest.mark.asyncio
async def test_parser_accepts_fenced_json(config, ctx, stub_llm):
    stub_llm.response = {
        "content": "```json\n" + json.dumps(_SUGGESTION) + "\n```",
        "role": "assistant",
        "tool_calls": None,
        "usage": {},
    }
    ext = _extension(config)

    await ext.on_turn_settle_hook(ctx, _tool_error_transcript())

    items = read_items(config)
    assert len(items) == 1
    assert items[0]["severity"] == "high"


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_identical_errors_are_deduplicated_with_ttl(config, ctx, stub_llm):
    _llm_reply(stub_llm)
    clock = [0.0]
    ext = scout.ImprovementScoutExtension(
        {"model": "test-model", "dedup_ttl_seconds": 60},
        config,
        clock=lambda: clock[0],
    )

    await ext.on_turn_settle_hook(ctx, _tool_error_transcript())
    await ext.on_turn_settle_hook(ctx, _tool_error_transcript())
    assert len(read_items(config)) == 1
    assert len(stub_llm) == 1

    clock[0] = 61.0
    await ext.on_turn_settle_hook(ctx, _tool_error_transcript())
    assert len(read_items(config)) == 2
    assert len(stub_llm) == 2


# ---------------------------------------------------------------------------
# Fail-open and isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluation_failure_fails_open(config, ctx, stub_llm, monkeypatch, caplog):
    async def _boom(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(scout.llm, "call_llm", _boom)
    ext = _extension(config)

    with caplog.at_level("WARNING", logger="decafclaw.extensions.improvement_scout"):
        await ext.on_turn_settle_hook(ctx, _tool_error_transcript())

    assert not inbox_path(config).exists()
    assert any("provider exploded" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_failed_evaluation_does_not_suppress_identical_trigger(config, ctx, stub_llm, monkeypatch):
    calls: list[str] = []

    async def _flaky(*args, **kwargs):
        calls.append(kwargs.get("model_name", ""))
        if len(calls) == 1:
            raise RuntimeError("transient provider error")
        return {
            "content": json.dumps(_SUGGESTION),
            "role": "assistant",
            "tool_calls": None,
            "usage": {},
        }

    monkeypatch.setattr(scout.llm, "call_llm", _flaky)
    ext = _extension(config, model="test-model", dedup_ttl_seconds=3600)

    await ext.on_turn_settle_hook(ctx, _tool_error_transcript())
    assert read_items(config) == []

    # The failed evaluation must not have recorded the signature, so the same
    # friction reaches the evaluator again.
    await ext.on_turn_settle_hook(ctx, _tool_error_transcript())
    assert len(calls) == 2
    assert len(read_items(config)) == 1


@pytest.mark.asyncio
async def test_hook_never_mutates_transcript_or_publishes(config, ctx, stub_llm, monkeypatch):
    _llm_reply(stub_llm)
    transcript = _tool_error_transcript()
    before = copy.deepcopy(transcript)

    published = []

    async def _record_publish(event):
        published.append(event)

    monkeypatch.setattr(ctx, "publish", _record_publish, raising=False)
    ext = _extension(config)

    await ext.on_turn_settle_hook(ctx, transcript)

    assert transcript == before
    assert published == []
    assert ctx.history in (None, [])


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_enabled_config_registers_listener(config, ctx, stub_llm):
    clear_extension_types()
    config.extensions["improvement_scout"] = ExtensionConfig(
        enabled=True,
        type="improvement_scout",
        options={"model": "test-model"},
    )
    _llm_reply(stub_llm)
    bus = EventBus()

    manager = init_extensions(config, bus)

    assert manager.is_enabled("improvement_scout")
    hooks = bus.get_lifecycle_hooks(TurnLifecycle.ON_TURN_SETTLE)
    assert len(hooks) == 1
    assert hooks[0] == manager.get("improvement_scout").on_turn_settle_hook

    import asyncio

    asyncio.run(hooks[0](ctx, _tool_error_transcript()))
    assert len(read_items(config)) == 1


def test_disable_detaches_listener(config):
    ext = _extension(config)
    bus = EventBus()
    ids = ext.enable(bus)
    assert len(ids) == 1
    assert len(bus.get_lifecycle_hooks(TurnLifecycle.ON_TURN_SETTLE)) == 1

    ext.disable(bus)
    assert bus.get_lifecycle_hooks(TurnLifecycle.ON_TURN_SETTLE) == []


def test_extension_does_not_mutate_config(config):
    before = repr(config)
    ext = _extension(config)
    bus = EventBus()
    ext.enable(bus)
    ext.disable(bus)
    assert repr(config) == before
