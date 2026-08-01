"""A workspace skill's `tools.py` must not be imported before activation.

Importing `tools.py` execs its module-level code. The codebase already
decided that workspace-tier code needs a human first: `tool_activate_skill`
requires a confirmation for `trust_tier == "workspace"` and *denies* on an
unattended turn (#649), `run_schedule_task` skips workspace-tier
`required-skills` entirely (#731 vector 5), `activate_always_loaded` skips the
tier, and `discover_skills` strips `auto-approve` / `always-loaded` from it.

Three callers of `_load_native_tools` front-ran all of that (#744):

1. `build_skill_tool_owners` — at startup, and via the agent-callable
   `refresh_skills` tool.
2. the skill-def preload in `build_tool_list` — every turn's tool assembly.
3. `eval/tool_choice/loadout.py` — `make eval-tools` only, not agent-reachable.

That gate lives in the `tool_activate_skill` *wrapper*, not in
`activate_skill_internal`, whose own docstring records the split: "Shared by
tool_activate_skill (with permission checks) and command execution (without
permission checks)." So callers that skip the wrapper skip the gate.

Every assertion here is on the planted `tools.py`'s **import-time side
effect** — a marker file — not on activation state. The marker is the only
thing that proves the code did not run. Same shape as
`tests/test_schedule_required_skills_tier.py`.
"""

from pathlib import Path

import pytest

from decafclaw.events import EventBus
from decafclaw.skills import build_skill_tool_owners, discover_skills


def _write_skill(skill_dir: Path, name: str, marker: Path) -> None:
    """A skill whose tools.py writes `marker` at import time."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill for the tools.py tier gate.\n"
        f"---\n\nBody of {name}.\n"
    )
    # A valid TOOLS / TOOL_DEFINITIONS pair so `check_tools_contract` passes and
    # the skill is rejected (if at all) for its TIER, not its shape.
    (skill_dir / "tools.py").write_text(
        f"open({str(marker)!r}, 'w').close()\n\n"
        "def _probe(ctx):\n"
        "    return 'ok'\n\n"
        "TOOLS = {'probe_tool': _probe}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {\n"
        "    'name': 'probe_tool', 'description': 'probe',\n"
        "    'parameters': {'type': 'object', 'properties': {}}}}]\n"
    )


def _ctx(config):
    from decafclaw.context import Context

    return Context(config=config, event_bus=EventBus())


# -- build_skill_tool_owners (startup + refresh_skills) --


def test_catalog_build_does_not_exec_workspace_tools_py(config):
    marker = config.workspace_path / "owners.marker"
    _write_skill(config.workspace_path / "skills" / "evil", "evil", marker)
    skills = discover_skills(config)
    assert {s.name: s.trust_tier for s in skills}["evil"] == "workspace"

    owners = build_skill_tool_owners(skills)

    assert not marker.exists(), "workspace skill's tools.py executed"
    assert "probe_tool" not in owners


def test_refresh_skills_does_not_exec_workspace_tools_py(config):
    """The fully agent-reachable path: `refresh_skills` is an ordinary tool,
    and skill-creator's SKILL.md tells the agent to call it."""
    from decafclaw.tools.skill_tools import tool_refresh_skills

    marker = config.workspace_path / "refresh.marker"
    _write_skill(config.workspace_path / "skills" / "evil", "evil", marker)

    tool_refresh_skills(_ctx(config))

    assert not marker.exists(), "workspace skill's tools.py executed"


def test_admin_tier_tools_py_still_indexed(config):
    """Positive control — a human-placed skill is unchanged."""
    marker = config.workspace_path / "admin.marker"
    _write_skill(config.agent_path / "skills" / "good", "good", marker)
    skills = discover_skills(config)
    assert {s.name: s.trust_tier for s in skills}["good"] == "admin"

    owners = build_skill_tool_owners(skills)

    assert marker.exists(), "admin-tier skill should still load its tools.py"
    assert owners.get("probe_tool") == "good"


def test_extra_tier_tools_py_still_indexed(config, tmp_path):
    """Positive control for contrib — `extra` must keep working."""
    marker = config.workspace_path / "extra.marker"
    extra_root = tmp_path / "extra-skills"
    _write_skill(extra_root / "good", "good", marker)
    config.extra_skill_paths = [str(extra_root)]
    skills = discover_skills(config)
    assert {s.name: s.trust_tier for s in skills}["good"] == "extra"

    owners = build_skill_tool_owners(skills)

    assert marker.exists(), "extra-tier skill should still load its tools.py"
    assert owners.get("probe_tool") == "good"


# -- the per-turn tool-list build --


def test_build_tool_list_does_not_exec_workspace_tools_py(config):
    from decafclaw.tool_definitions import _skill_def_cache, build_tool_list

    marker = config.workspace_path / "toollist.marker"
    _write_skill(config.workspace_path / "skills" / "evil", "evil", marker)
    config.discovered_skills = discover_skills(config)
    _skill_def_cache.clear()

    build_tool_list(_ctx(config))

    assert not marker.exists(), "workspace skill's tools.py executed"


def test_collect_all_tool_defs_omits_unactivated_workspace_tool(config):
    """The accepted consequence, pinned where it is actually observable.

    Asserting on `build_tool_list`'s *active* list would be toothless: an
    unactivated skill's tools are deferred, not active, so that assertion
    held before this fix too (verified). `collect_all_tool_defs` is the
    layer the preload feeds, so it is where the tool name appearing or not
    distinguishes fixed from unfixed.
    """
    from decafclaw.tool_definitions import _skill_def_cache, collect_all_tool_defs

    marker = config.workspace_path / "toollist2.marker"
    _write_skill(config.workspace_path / "skills" / "evil", "evil", marker)
    config.discovered_skills = discover_skills(config)
    _skill_def_cache.clear()

    defs = collect_all_tool_defs(_ctx(config))
    names = {d.get("function", {}).get("name")
             for d in defs if isinstance(d, dict)}

    assert "probe_tool" not in names


def test_build_tool_list_still_preloads_admin_tier(config):
    """Positive control — the preload itself still works."""
    from decafclaw.tool_definitions import _skill_def_cache, build_tool_list

    marker = config.workspace_path / "admin2.marker"
    _write_skill(config.agent_path / "skills" / "good", "good", marker)
    config.discovered_skills = discover_skills(config)
    _skill_def_cache.clear()

    build_tool_list(_ctx(config))

    assert marker.exists(), "admin-tier skill should still be preloaded"


# -- the eval loadout --


def test_eval_loadout_does_not_exec_workspace_tools_py(config):
    """Not agent-reachable (`make eval-tools` only), but a developer's real
    config points at a real workspace that may hold agent-authored skills."""
    from decafclaw.eval.tool_choice.loadout import build_full_tool_loadout

    marker = config.workspace_path / "loadout.marker"
    _write_skill(config.workspace_path / "skills" / "evil", "evil", marker)

    build_full_tool_loadout(config, include_mcp=False)

    assert not marker.exists(), "workspace skill's tools.py executed"
