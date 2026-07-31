"""A schedule's `required-skills` cannot activate a workspace-tier skill.

The fifth capability vector found under #731, and the one that does not
need an agent-written schedule to be reachable.

`setup_schedule_ctx` in `schedules.py` pre-activates every name in
`required-skills` via `activate_skill_internal`, which imports and execs
the skill's `tools.py` — arbitrary code, at import time. Both ends of
that were reachable by the agent:

1. `workspace/schedules/*.md` (agent-writable) naming a skill the agent
   also wrote to `workspace/skills/`.
2. An **admin or bundled** schedule naming a skill the agent wrote.
   `workspace/skills/` is the highest-precedence scan entry, so an
   agent-authored `workspace/skills/dream/` shadows the bundled `dream`
   skill that the bundled `dream` SCHEDULE.md already lists in its
   `required-skills`. No agent-written schedule required.

Restricting `allowed-tools` is not a mitigation: `tools.py` runs at
import, before any tool is called, and the agent controls that field too.

`activate_skill` already treats workspace tier as untrusted in three
places (confirmation prompt with an unattended denial per #649,
`auto-approve` ignored, `always-loaded` ignored). The schedule path
routed around all three.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from decafclaw.events import EventBus
from decafclaw.schedules import discover_schedules, run_schedule_task
from decafclaw.skills import discover_skills

SCHEDULE = """\
---
schedule: "* * * * *"
required-skills:
  - evil
---

Routine maintenance.
"""


def _write_skill(skill_dir: Path, name: str, marker: Path) -> None:
    """A skill whose tools.py has an import-time side effect."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill for the tier gate.\n"
        f"---\n\nBody of {name}.\n"
    )
    (skill_dir / "tools.py").write_text(
        f"open({str(marker)!r}, 'w').close()\n\n"
        "TOOLS = {}\nTOOL_DEFINITIONS = []\n"
    )


def _write_schedule(path: Path, text: str = SCHEDULE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


async def _ctx_for(config, task):
    """Run the task with a stubbed agent turn, returning the turn's ctx."""
    from decafclaw.conversation_manager import ConversationManager

    manager = ConversationManager(config, EventBus())
    seen = {}

    async def fake_run(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        seen["ctx"] = ctx
        return ToolResult(text="Done.")

    with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run), \
            patch("decafclaw.notifications.notify"):
        await run_schedule_task(config, EventBus(), manager, task)
    return seen["ctx"]


@pytest.mark.asyncio
async def test_workspace_schedule_cannot_activate_workspace_skill(config):
    """Both ends agent-controlled."""
    marker = config.workspace_path / "imported.marker"
    _write_skill(config.workspace_path / "skills" / "evil", "evil", marker)
    _write_schedule(config.workspace_path / "schedules" / "maintenance.md")
    config.discovered_skills = discover_skills(config)
    assert {s.name: s.trust_tier for s in config.discovered_skills}["evil"] \
        == "workspace"

    task = {t.name: t for t in discover_schedules(config)}["maintenance"]
    assert task.source == "workspace"
    assert task.required_skills == ["evil"]

    ctx = await _ctx_for(config, task)

    assert not marker.exists(), "workspace skill's tools.py executed"
    assert "evil" not in ctx.skills.activated


@pytest.mark.asyncio
async def test_admin_schedule_cannot_activate_workspace_skill(config):
    """The non-latent case: a *trusted* schedule naming a skill the agent
    wrote. Workspace shadows bundled, so the bundled `dream` / `garden` /
    `newsletter` SCHEDULE.md files already reach this path today."""
    marker = config.workspace_path / "imported.marker"
    _write_skill(config.workspace_path / "skills" / "evil", "evil", marker)
    _write_schedule(config.agent_path / "schedules" / "maintenance.md")
    config.discovered_skills = discover_skills(config)

    task = {t.name: t for t in discover_schedules(config)}["maintenance"]
    assert task.source == "admin", "the schedule itself is human-controlled"

    ctx = await _ctx_for(config, task)

    assert not marker.exists(), "workspace skill's tools.py executed"
    assert "evil" not in ctx.skills.activated


@pytest.mark.asyncio
async def test_admin_tier_skill_still_activates(config):
    """The gate must not over-correct — a human-placed skill is unchanged."""
    marker = config.workspace_path / "imported.marker"
    _write_skill(config.agent_path / "skills" / "evil", "evil", marker)
    _write_schedule(config.agent_path / "schedules" / "maintenance.md")
    config.discovered_skills = discover_skills(config)
    assert {s.name: s.trust_tier for s in config.discovered_skills}["evil"] \
        == "admin"

    task = {t.name: t for t in discover_schedules(config)}["maintenance"]
    ctx = await _ctx_for(config, task)

    assert marker.exists(), "admin-tier skill should still load its tools.py"
    assert "evil" in ctx.skills.activated
