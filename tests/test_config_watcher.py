"""Tests for the config change watcher (#149)."""

import asyncio
import json
import os

from decafclaw.config import load_config
from decafclaw.config_watcher import (
    ConfigFileClassifier,
    classifier_baseline,
    config_change_watcher,
)

TEST_INTERVAL = 0.05
RESTART_GRACE = 0.05


def _pin_env(monkeypatch, tmp_path) -> None:
    """Pin env so a fresh load_config() is deterministic and file-driven.

    DECACLAW_LOAD_DOTENV=0 keeps the checkout's .env (real credentials,
    HTTP_PORT) out of the watcher's fresh loads; the explicit pins give
    load_config the same agent dir the fixture config points at.
    """
    monkeypatch.setenv("DECACLAW_LOAD_DOTENV", "0")
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_ID", "test-agent")
    monkeypatch.setenv("HTTP_PORT", "18880")  # HttpConfig.port default


def _align_config(ctx, monkeypatch, tmp_path) -> None:
    """Make the live config a real load_config() product, as in production."""
    _pin_env(monkeypatch, tmp_path)
    ctx.config = load_config()


def _write_config(tmp_path, data: dict, filename: str = "config.json"):
    path = tmp_path / "test-agent" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


async def _run_watcher(ctx, **overrides):
    shutdown_event = asyncio.Event()
    kwargs = {"interval": TEST_INTERVAL, "restart_grace_sec": RESTART_GRACE, **overrides}
    task = asyncio.create_task(config_change_watcher(ctx, shutdown_event, **kwargs))
    return task, shutdown_event


async def _wait_for(condition, timeout: float = 3.0) -> None:
    """Poll until condition() holds; fail with a timeout if it never does."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    if not condition():
        raise AssertionError("timed out waiting for condition")


async def _cancel(task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _settle(intervals: float = 3.0):
    """Let the watcher's poll loop run a few no-change passes."""
    await asyncio.sleep(TEST_INTERVAL * intervals)


async def test_no_change_is_silent(ctx, monkeypatch, tmp_path):
    """No config change → no events, no shutdown, watcher keeps running."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()
        assert not shutdown_event.is_set()
        assert not task.done()
        assert events == []
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_safe_change_applies_and_publishes(ctx, monkeypatch, tmp_path):
    """A safe section change applies in place and announces exactly that section."""
    _align_config(ctx, monkeypatch, tmp_path)
    # Simulate runtime state a real process would hold.
    ctx.config.discovered_skills = ["sentinel-skill"]

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()
        _write_config(tmp_path, {"llm": {"model": "custom-model"}})

        await _wait_for(lambda: ctx.config.llm.model == "custom-model")
        assert not shutdown_event.is_set()
        assert len(events) == 1
        assert events[0]["type"] == "config_changed"
        assert events[0]["requires_restart"] is False
        # model_configs migrates from the llm section, so it may cascade here.
        assert "llm" in events[0]["keys"]
        # Runtime-only state must survive the apply untouched.
        assert ctx.config.discovered_skills == ["sentinel-skill"]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_safe_change_reverts_when_file_reverts(ctx, monkeypatch, tmp_path):
    """Reverting a section in the file must revert the live config (#149 review)."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(tmp_path, {"llm": {"model": "custom-model"}})
        await _wait_for(lambda: ctx.config.llm.model == "custom-model")

        _write_config(tmp_path, {})
        await _wait_for(lambda: ctx.config.llm.model != "custom-model")

        assert not shutdown_event.is_set()
        assert len(events) == 2
        assert events[-1]["requires_restart"] is False
        assert "llm" in events[-1]["keys"]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_safe_sections_beyond_the_original_trio_apply(ctx, monkeypatch, tmp_path):
    """Every non-restart section applies in place, not just llm/agent/compaction."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()
        assert ctx.config.notes.enabled is True

        _write_config(tmp_path, {"notes": {"enabled": False}})
        await _wait_for(lambda: ctx.config.notes.enabled is False)

        assert events == [{"type": "config_changed", "requires_restart": False, "keys": ["notes"]}]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_unsafe_section_signals_restart(ctx, monkeypatch, tmp_path):
    """An http section change publishes requires_restart and stops the watcher."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(tmp_path, {"http": {"enabled": True}})

        await _wait_for(shutdown_event.is_set)
        await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

        assert events == [{"type": "config_changed", "requires_restart": True, "keys": ["http"]}]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        if not task.done():
            await _cancel(task)


async def test_startup_wired_sections_require_restart(ctx, monkeypatch, tmp_path):
    """telemetry/schedules wire event subscribers and timers once at startup;
    they cannot be rewired mid-run, so they are restart-required (#149 review)."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(tmp_path, {"telemetry": {"tool_usage_enabled": False}})
        await _wait_for(shutdown_event.is_set)
        await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

        assert events == [{"type": "config_changed", "requires_restart": True, "keys": ["telemetry"]}]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        if not task.done():
            await _cancel(task)


async def test_env_reload_updates_owned_vars_only(ctx, monkeypatch, tmp_path):
    """env hot-apply must update config-owned vars, set new ones, leave
    real-env overrides alone (#149 review: apply_env never updated anything)."""
    _align_config(ctx, monkeypatch, tmp_path)
    # Simulate the startup result: the config's env section set these.
    ctx.config.env = {"PR1200_KEY": "old", "PR1200_KEPT": "kept"}
    monkeypatch.setenv("PR1200_KEY", "old")
    monkeypatch.setenv("PR1200_KEPT", "kept")
    monkeypatch.setenv("PR1200_FOREIGN", "real")  # real env, config doesn't own it

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(
            tmp_path,
            {"env": {"PR1200_KEY": "new", "PR1200_KEPT": "kept", "PR1200_FOREIGN": "mine", "PR1200_ADDED": "added"}},
        )
        await _wait_for(lambda: ctx.config.env.get("PR1200_KEY") == "new")

        assert not shutdown_event.is_set()
        assert os.environ["PR1200_KEY"] == "new"  # owned → updated
        assert os.environ["PR1200_KEPT"] == "kept"  # unchanged
        assert os.environ["PR1200_FOREIGN"] == "real"  # foreign → untouched
        assert os.environ["PR1200_ADDED"] == "added"  # new + absent → set
        assert events[-1]["requires_restart"] is False
        assert "env" in events[-1]["keys"]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_env_reload_unsets_removed_owned_vars(ctx, monkeypatch, tmp_path):
    """A key dropped from the env section is unset — if the config owned it."""
    _align_config(ctx, monkeypatch, tmp_path)
    ctx.config.env = {"PR1200_KEY": "old", "PR1200_KEPT": "kept"}
    monkeypatch.setenv("PR1200_KEY", "old")
    monkeypatch.setenv("PR1200_KEPT", "kept")
    monkeypatch.setenv("PR1200_OVERRIDE", "real")  # foreign, dropped later too

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(tmp_path, {"env": {"PR1200_KEPT": "kept"}})
        await _wait_for(lambda: "PR1200_KEY" not in ctx.config.env)

        assert "PR1200_KEY" not in os.environ  # owned → unset
        assert os.environ["PR1200_KEPT"] == "kept"
        assert os.environ["PR1200_OVERRIDE"] == "real"  # never ours → untouched
        assert events[-1]["requires_restart"] is False
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_providers_change_rebuilds_registry(ctx, monkeypatch, tmp_path):
    """A providers section change re-runs init_providers (#149 review: the
    registry was built once at startup, so the announce-alone claim lied)."""
    _align_config(ctx, monkeypatch, tmp_path)
    calls: list = []

    def _fake_init(config) -> None:
        calls.append(config)

    monkeypatch.setattr("decafclaw.llm.init_providers", _fake_init)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(
            tmp_path,
            {"providers": {"p1": {"type": "openai-compat", "url": "http://example.test", "api_key": "k"}}},
        )
        await _wait_for(lambda: "p1" in ctx.config.providers)

        assert not shutdown_event.is_set()
        assert calls == [ctx.config]
        assert events[-1]["requires_restart"] is False
        assert "providers" in events[-1]["keys"]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_skill_catalog_change_rescans(ctx, monkeypatch, tmp_path):
    """extra_skill_paths changes re-run the skill discovery mutation path
    (#149 review: runtime-only catalog fields were never refreshed)."""
    _align_config(ctx, monkeypatch, tmp_path)
    calls: list = []

    def _fake_rescan(config) -> list:
        calls.append(config)
        return []

    monkeypatch.setattr("decafclaw.tools.skill_tools.rediscover_skills", _fake_rescan)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        extra = str(tmp_path / "extra-skills")
        _write_config(tmp_path, {"extra_skill_paths": [extra]})
        await _wait_for(lambda: ctx.config.extra_skill_paths == [extra])

        assert not shutdown_event.is_set()
        assert calls == [ctx.config]
        assert events[-1]["requires_restart"] is False
        assert "extra_skill_paths" in events[-1]["keys"]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_model_configs_reload_reapplies_model_catalog(ctx, monkeypatch, tmp_path):
    """A model_configs hot-reload replaces config.model_configs wholesale,
    which strands every catalog-materialized discovered model (the picker
    still lists it, but selection fails with 'Unknown model') until a
    restart. The watcher must re-apply the sidecar catalog so discovered
    models and the default pointer keep working without a restart
    (#1211 review finding)."""
    _write_config(tmp_path, {"providers": {"local": {"type": "openai-compat", "url": "http://x/v1"}}})
    _align_config(ctx, monkeypatch, tmp_path)
    config = ctx.config

    import asyncio

    from decafclaw.config import resolve_model
    from decafclaw.llm.registry import clear_providers, register_provider
    from decafclaw.llm.types import DiscoveredModel
    from decafclaw.model_catalog import (
        effective_default_model,
        picker_models,
        refresh_catalog,
        set_default_model,
    )

    class Gateway:
        async def discover_models(self):
            return [DiscoveredModel(id="m1", chat=True, tools=True, context_window_size=131072)]

    clear_providers()
    register_provider("local", Gateway())
    try:
        # Startup discovery materialized local/m1 and the user made it the
        # global default.
        await refresh_catalog(config)
        await set_default_model(config, "local/m1")
        assert "local/m1" in config.model_configs
        assert picker_models(config) == ["local/m1"]

        task, shutdown_event = await _run_watcher(ctx)
        events: list[dict] = []
        sub_id = ctx.event_bus.subscribe(events.append)
        try:
            await _settle()

            # User edits config.json: adds a hand model. The fresh load
            # replaces model_configs wholesale, dropping local/m1.
            _write_config(
                tmp_path,
                {
                    "providers": {"local": {"type": "openai-compat", "url": "http://x/v1"}},
                    "model_configs": {"hand-model": {"provider": "local", "model": "hand"}},
                },
            )
            await _wait_for(lambda: "hand-model" in config.model_configs and "local/m1" in config.model_configs)

            assert not shutdown_event.is_set()
            # Hand entry and re-materialized discovered entry coexist.
            assert "hand-model" in config.model_configs
            assert "local/m1" in config.catalog_materialized
            assert picker_models(config) == ["hand-model", "local/m1"]
            # The default pointer still resolves (was stranded before).
            assert effective_default_model(config) == "local/m1"
            _, mc = resolve_model(config, "local/m1")
            assert mc.model == "m1"
            assert events[-1]["requires_restart"] is False
        finally:
            ctx.event_bus.unsubscribe(sub_id)
            await _cancel(task)
    finally:
        clear_providers()


def test_classifier_seeds_baseline_from_startup_files(tmp_path):
    """The classifier diffs the first edit against the files as they were at
    dev-server start (#149 review: first-sighting-only caching hid restart-
    required changes from the supervisor)."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"mattermost": {"url": "a"}}))
    clf = ConfigFileClassifier(classifier_baseline([config_path]))

    config_path.write_text(json.dumps({"mattermost": {"url": "b"}}))
    assert clf.is_restart_required(config_path) is True
    assert clf.is_restart_required(config_path) is False  # no further delta

    # A file absent at start compares against empty defaults once it appears.
    created = tmp_path / "mcp_servers.json"
    created.write_text(json.dumps({"mcpServers": {}}))
    assert clf.is_restart_required(created) is True


async def test_mcp_servers_change_requires_restart(ctx, monkeypatch, tmp_path):
    """An mcp_servers.json change is restart-required (#149 review: it was ignored)."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(
            tmp_path,
            {"mcpServers": {"tester": {"command": "echo"}}},
            filename="mcp_servers.json",
        )

        await _wait_for(shutdown_event.is_set)
        await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

        assert events == [{"type": "config_changed", "requires_restart": True, "keys": ["mcp_servers"]}]
        assert ctx.config.llm.model != "custom-model"  # untouched
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        if not task.done():
            await _cancel(task)


async def test_unparseable_config_is_skipped_until_it_parses(ctx, monkeypatch, tmp_path):
    """A torn/partial write must never reach the live config (#149 review)."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        # Establish a non-default live model, as a real session would have.
        _write_config(tmp_path, {"llm": {"model": "custom-a"}})
        await _wait_for(lambda: ctx.config.llm.model == "custom-a")

        # Torn write: readable but invalid JSON.
        config_file = tmp_path / "test-agent" / "config.json"
        config_file.write_text('{"llm": {"mod')
        await _settle(6.0)
        assert len(events) == 1  # the torn write was skipped, not announced
        assert "llm" in events[0]["keys"]  # only the earlier apply so far
        assert ctx.config.llm.model == "custom-a"
        assert not shutdown_event.is_set()

        # The completed write is still seen as a change.
        _write_config(tmp_path, {"llm": {"model": "custom-b"}})
        await _wait_for(lambda: ctx.config.llm.model == "custom-b")
        assert len(events) == 2
        assert events[-1]["requires_restart"] is False
        assert "llm" in events[-1]["keys"]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)
