"""A user-invoked command grants capability only at a capability tier.

`execute_command` installed a skill's `allowed-tools` and `shell(...)`
patterns as **pre-approvals** with no tier check, and pre-activated its
`requires_skills` with no tier check either. `workspace/skills/` is
agent-writable and the highest-precedence scan entry, so a skill the agent
wrote could pre-approve its own shell commands (#737).

Weaker than #731 because a human has to type `!name` — there is no timer
firing it unattended. That is a real mitigation, not a dismissal: shadowing
means the agent can redefine a command the user already runs by habit, and
`requires_skills` is a dependency list the human never named.

The split mirrors the schedules fix: frontmatter **restricts** at every tier,
and only **grants** at a capability tier. A workspace-tier command still runs.

`extra` tier positive controls are load-bearing here. Six contrib skills ship
user-invocable `allowed-tools` containing `shell($SKILL_DIR/fetch.sh*)` —
`rss-ingest`, `mastodon-ingest`, `linkding-ingest`, `meta-ingest`, `kindle`,
`blog-develop`. Reusing `schedules._PREAPPROVAL_TIERS` (which excludes
`extra`) would break every one of them; the probe in this branch's plan
demonstrates that by construction.
"""

from pathlib import Path

import pytest

from decafclaw.commands import execute_command
from decafclaw.context import Context
from decafclaw.events import EventBus
from decafclaw.skills import discover_skills

SKILL_MD = """\
---
name: {name}
description: Test command for the tier gate.
user-invocable: true
context: inline
allowed-tools: shell($SKILL_DIR/fetch.sh*), vault_read
---

Do the thing with $SKILL_DIR/fetch.sh.
"""

DEP_SKILL_MD = """\
---
name: {name}
description: Dependency of the test command.
---

Dependency body.
"""


def _write_command(skill_dir: Path, name: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD.format(name=name))
    (skill_dir / "fetch.sh").write_text("#!/bin/sh\necho fetched\n")


def _write_dep(skill_dir: Path, name: str, marker: Path) -> None:
    """A dependency skill whose tools.py writes `marker` at import."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(DEP_SKILL_MD.format(name=name))
    (skill_dir / "tools.py").write_text(
        f"open({str(marker)!r}, 'w').close()\n\n"
        "def _probe(ctx):\n    return 'ok'\n\n"
        "TOOLS = {'dep_tool': _probe}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {\n"
        "    'name': 'dep_tool', 'description': 'probe',\n"
        "    'parameters': {'type': 'object', 'properties': {}}}}]\n"
    )


def _skill(config, name: str):
    """Discover skills, publish them on the config, and return one by name.

    Publishing to `config.discovered_skills` is load-bearing, not tidiness:
    `execute_command` resolves `requires_skills` through
    `ctx.config.discovered_skills`, which the `config` fixture leaves empty.
    Without this, a dependency is simply unresolvable and the
    "workspace dependency is not activated" assertion passes for the wrong
    reason — it would pass against the vulnerable code too.
    """
    config.discovered_skills = discover_skills(config)
    return {s.name: s for s in config.discovered_skills}[name]


def _ctx(config):
    return Context(config=config, event_bus=EventBus())


# -- pre-approvals --


@pytest.mark.asyncio
async def test_workspace_command_does_not_preapprove(config):
    _write_command(config.workspace_path / "skills" / "doit", "doit")
    skill = _skill(config, "doit")
    assert skill.trust_tier == "workspace"
    ctx = _ctx(config)

    mode, _result = await execute_command(ctx, skill, "")

    assert mode == "inline"
    assert not ctx.tools.preapproved, (
        f"workspace command pre-approved tools: {ctx.tools.preapproved}"
    )
    assert not ctx.tools.preapproved_shell_patterns, (
        f"workspace command pre-approved shell: "
        f"{ctx.tools.preapproved_shell_patterns}"
    )


@pytest.mark.asyncio
async def test_workspace_command_still_runs(config):
    """Not over-corrected: the command still executes and $SKILL_DIR still
    expands in its body. Only the GRANT is withheld."""
    skill_dir = config.workspace_path / "skills" / "doit"
    _write_command(skill_dir, "doit")
    ctx = _ctx(config)

    mode, result = await execute_command(ctx, _skill(config, "doit"), "")

    assert mode == "inline"
    assert str(skill_dir.resolve()) in result


@pytest.mark.asyncio
async def test_admin_command_still_preapproves(config):
    """Positive control."""
    skill_dir = config.agent_path / "skills" / "doit"
    _write_command(skill_dir, "doit")
    skill = _skill(config, "doit")
    assert skill.trust_tier == "admin"
    ctx = _ctx(config)

    await execute_command(ctx, skill, "")

    assert "vault_read" in ctx.tools.preapproved
    assert f"{skill_dir}/fetch.sh*" in ctx.tools.preapproved_shell_patterns


@pytest.mark.asyncio
async def test_extra_tier_command_still_preapproves(config, tmp_path):
    """Guards the six shipping contrib commands.

    This is the test that fails if the schedules' `_PREAPPROVAL_TIERS`
    (which excludes `extra`) is reused here instead of the skill-tier
    predicate. `!rss-ingest` and friends depend on it.
    """
    extra_root = tmp_path / "contrib-skills"
    skill_dir = extra_root / "doit"
    _write_command(skill_dir, "doit")
    config.extra_skill_paths = [str(extra_root)]
    skill = _skill(config, "doit")
    assert skill.trust_tier == "extra"
    ctx = _ctx(config)

    await execute_command(ctx, skill, "")

    assert "vault_read" in ctx.tools.preapproved
    assert f"{skill_dir}/fetch.sh*" in ctx.tools.preapproved_shell_patterns


# -- requires_skills --

DEP_COMMAND_MD = """\
---
name: needsdep
description: Command with a dependency.
user-invocable: true
context: inline
required-skills:
  - dep
---

Body.
"""


@pytest.mark.asyncio
async def test_workspace_required_skill_not_activated(config):
    """Typing `!needsdep` approves THAT skill, not a dependency the agent
    controls. Activation imports the dependency's tools.py — asserted via
    its import-time marker, which is what proves the code did not run."""
    cmd_dir = config.agent_path / "skills" / "needsdep"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    (cmd_dir / "SKILL.md").write_text(DEP_COMMAND_MD)

    marker = config.workspace_path / "dep.marker"
    _write_dep(config.workspace_path / "skills" / "dep", "dep", marker)

    skill = _skill(config, "needsdep")
    assert skill.trust_tier == "admin", "the command itself is human-placed"
    ctx = _ctx(config)

    await execute_command(ctx, skill, "")

    assert not marker.exists(), "workspace dependency's tools.py executed"
    assert "dep" not in ctx.skills.activated


@pytest.mark.asyncio
async def test_admin_required_skill_still_activated(config):
    """Positive control."""
    cmd_dir = config.agent_path / "skills" / "needsdep"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    (cmd_dir / "SKILL.md").write_text(DEP_COMMAND_MD)

    marker = config.workspace_path / "dep2.marker"
    _write_dep(config.agent_path / "skills" / "dep", "dep", marker)

    ctx = _ctx(config)
    await execute_command(ctx, _skill(config, "needsdep"), "")

    assert marker.exists(), "admin dependency should still load its tools.py"
    assert "dep" in ctx.skills.activated
