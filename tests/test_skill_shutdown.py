"""Tests for server-shutdown skill cleanup (#929).

On server shutdown, every loaded skill module instance that exports
shutdown() gets exactly one call. Each activation (and restore) imports a
fresh module object, so the registry is process-level and keyed by
instance: two activations of the same skill produce two instances, and
both must be shut down.
"""

import asyncio
import logging

import pytest

from decafclaw.skills import SkillInfo
from decafclaw.tools.skill_tools import (
    _compute_skill_hash,
    _load_native_tools,
    _shutdownable_modules,
    activate_skill_internal,
    restore_skills,
    shutdown_skills,
)

_SKILL_MD = "---\nname: {name}\ndescription: Fixture skill.\n---\nBody."

# A simulated long-lived resource (a watcher task). It must NOT be started
# by activation or init — only on demand — and shutdown() releases it.
_RESOURCE_TOOLS_PY = """
import asyncio

resource = None
released = False


def init(config):
    pass


async def start_watcher(ctx):
    global resource
    if resource is None:
        resource = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
    return "started"


async def shutdown():
    global resource, released
    if resource is not None:
        resource.cancel()
        try:
            await resource
        except asyncio.CancelledError:
            pass
    resource = None
    released = True


TOOLS = {"start_watcher": start_watcher}
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "start_watcher",
            "description": "Starts the simulated watcher.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]
"""

# A per-instance counter: each module instance has its own shutdown_calls.
_COUNTER_TOOLS_PY = """
shutdown_calls = 0


def init(config):
    pass


def dummy_tool(ctx):
    return "ok"


async def shutdown():
    global shutdown_calls
    shutdown_calls += 1


TOOLS = {"dummy_tool": dummy_tool}
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "dummy_tool",
            "description": "Dummy.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]
"""

_RAISING_TOOLS_PY = """
def init(config):
    pass


def shutdown():
    raise RuntimeError("shutdown boom")


TOOLS = {}
TOOL_DEFINITIONS = []
"""

_SLOW_TOOLS_PY = """
import asyncio


async def init(config):
    pass


async def shutdown():
    await asyncio.sleep(30)


TOOLS = {}
TOOL_DEFINITIONS = []
"""

_NO_SHUTDOWN_TOOLS_PY = """
def init(config):
    pass


TOOLS = {}
TOOL_DEFINITIONS = []
"""


def _make_skill_dir(tmp_path, name, tools_py_source):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_SKILL_MD.format(name=name))
    (skill_dir / "tools.py").write_text(tools_py_source)
    return skill_dir


def _skill_info(skill_dir, name):
    return SkillInfo(
        name=name,
        description=f"{name} description",
        location=skill_dir,
        body="Body.",
        has_native_tools=True,
        trust_tier="bundled",
    )


@pytest.fixture(autouse=True)
def _isolate_shutdown_registry():
    """The registry is process-level; isolate it between tests."""
    _shutdownable_modules.clear()
    yield
    _shutdownable_modules.clear()


@pytest.mark.asyncio
async def test_shutdown_releases_resource_started_after_activation(ctx, tmp_path):
    """Activation registers the instance without starting the resource;
    shutdown_skills() releases it."""
    info = _skill_info(_make_skill_dir(tmp_path, "res-fixture", _RESOURCE_TOOLS_PY), "res-fixture")
    ctx.config.discovered_skills = [info]

    result = await activate_skill_internal(ctx, info)
    assert isinstance(result, str)

    assert len(_shutdownable_modules) == 1
    module = next(iter(_shutdownable_modules.values()))
    # Registration must not have started the simulated resource.
    assert module.resource is None

    await module.TOOLS["start_watcher"](ctx)
    assert module.resource is not None
    assert not module.resource.done()

    try:
        await shutdown_skills()
    finally:
        # Safety net: a failed shutdown must not leak the pending task.
        task = module.resource
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert module.released is True
    assert module.resource is None


@pytest.mark.asyncio
async def test_shutdown_covers_every_loaded_instance(ctx, tmp_path):
    """Two activations of the same skill import two module instances;
    shutdown_skills() calls shutdown() on both."""
    info = _skill_info(_make_skill_dir(tmp_path, "two-inst", _COUNTER_TOOLS_PY), "two-inst")
    ctx.config.discovered_skills = [info]

    await activate_skill_internal(ctx, info)
    await activate_skill_internal(ctx, info)  # re-activation re-imports tools.py

    instances = list(_shutdownable_modules.values())
    assert len(instances) == 2
    assert len({id(m) for m in instances}) == 2  # distinct module objects

    await shutdown_skills()

    assert all(m.shutdown_calls == 1 for m in instances)
    assert _shutdownable_modules == {}


@pytest.mark.asyncio
async def test_second_shutdown_skills_call_is_noop(ctx, tmp_path):
    """The registry clears on the first call, so a second call is a no-op."""
    info = _skill_info(_make_skill_dir(tmp_path, "noop-inst", _COUNTER_TOOLS_PY), "noop-inst")
    ctx.config.discovered_skills = [info]
    await activate_skill_internal(ctx, info)
    module = next(iter(_shutdownable_modules.values()))

    await shutdown_skills()
    await shutdown_skills()

    assert module.shutdown_calls == 1


@pytest.mark.asyncio
async def test_failing_and_slow_shutdowns_do_not_block_others(ctx, tmp_path, caplog):
    """A raising shutdown and a shutdown that exceeds the timeout are logged
    with log.warning each; the healthy instance still gets its call, and
    nothing louder than WARNING reaches the log."""
    good = _skill_info(_make_skill_dir(tmp_path, "shutdown-good", _COUNTER_TOOLS_PY), "shutdown-good")
    raiser = _skill_info(_make_skill_dir(tmp_path, "shutdown-raise", _RAISING_TOOLS_PY), "shutdown-raise")
    slow = _skill_info(_make_skill_dir(tmp_path, "shutdown-slow", _SLOW_TOOLS_PY), "shutdown-slow")
    ctx.config.discovered_skills = [good, raiser, slow]

    for skill in (good, raiser, slow):
        await activate_skill_internal(ctx, skill)
    # Identify the healthy instance: it is the one that provides dummy_tool.
    good_module = next(m for m in _shutdownable_modules.values() if "dummy_tool" in m.TOOLS)

    with caplog.at_level(logging.DEBUG, logger="decafclaw.tools.skill_tools"):
        await shutdown_skills(timeout=0.2)

    assert good_module is not None
    assert good_module.shutdown_calls == 1  # healthy instance still got its call

    shutdown_warnings = [
        r for r in caplog.records if r.name == "decafclaw.tools.skill_tools" and r.levelno >= logging.WARNING
    ]
    assert len(shutdown_warnings) == 2  # one per failing instance
    assert all(r.levelno == logging.WARNING for r in shutdown_warnings)


@pytest.mark.asyncio
async def test_module_without_shutdown_export_not_registered(ctx, tmp_path):
    """Only instances that export shutdown() are recorded."""
    info = _skill_info(_make_skill_dir(tmp_path, "no-shutdown", _NO_SHUTDOWN_TOOLS_PY), "no-shutdown")
    ctx.config.discovered_skills = [info]
    await activate_skill_internal(ctx, info)
    assert _shutdownable_modules == {}


def test_import_without_activation_not_registered(tmp_path):
    """_load_native_tools without _call_init (e.g. build_skill_tool_owners)
    must not register the instance for shutdown."""
    info = _skill_info(_make_skill_dir(tmp_path, "no-activation", _COUNTER_TOOLS_PY), "no-activation")
    _load_native_tools(info)
    assert _shutdownable_modules == {}


@pytest.mark.asyncio
async def test_restore_skills_registers_instance(ctx, tmp_path):
    """restore_skills is a post-restart activation path; its module instance
    must be registered for shutdown too."""
    info = _skill_info(_make_skill_dir(tmp_path, "restore-inst", _COUNTER_TOOLS_PY), "restore-inst")
    ctx.config.discovered_skills = [info]
    ctx.skills.activated["restore-inst"] = _compute_skill_hash(info)

    await restore_skills(ctx)

    assert len(_shutdownable_modules) == 1
    module = next(iter(_shutdownable_modules.values()))
    await shutdown_skills()
    assert module.shutdown_calls == 1
