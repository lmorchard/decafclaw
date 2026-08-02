"""`$SKILL_DIR` must not resolve to an agent-writable skill directory.

`_resolve_skill_dir` read `info.location` straight off `discovered_skills`
with no tier check. `workspace/skills/` is agent-writable AND the
highest-precedence scan entry, so an agent-planted skill repointed
`$SKILL_DIR` — and a **trusted** admin-tier schedule declaring
`shell_patterns: ["$SKILL_DIR/fetch.sh*"]` then installed
`<workspace>/skills/<name>/fetch.sh*` as a *pre-approved* pattern on an
unattended turn. Pre-approval means no confirmation and no allow-pattern
check; the agent writes the executable and it runs (#739).

Four contrib skills ship that exact pattern today (`rss-ingest`,
`mastodon-ingest`, `linkding-ingest`, `meta-ingest`), and the prerequisite is
the documented opt-in: copy the contrib SCHEDULE.md to
`data/{agent_id}/schedules/`, which makes its source `admin`.

Two distinct attack shapes are covered, because they need different fixes:

1. **Shadowing** — a trusted skill of the same name also exists, and the
   workspace copy wins on scan precedence.
2. **No trusted skill at all** — nothing to shadow. This is the shape that
   "stop letting workspace skills shadow trusted ones" would NOT have fixed,
   and it is reachable on the documented path: copying the SCHEDULE.md and
   adding the skill dir to `extra_skill_paths` are two separate steps, and
   only the first is needed for the schedule to fire.
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
allowed-tools: shell($SKILL_DIR/fetch.sh*)
---

Fetch the feeds. Scripts live in $SKILL_DIR.
"""


def _write_skill(skill_dir: Path, name: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill for the $SKILL_DIR tier gate.\n"
        f"---\n\nRun $SKILL_DIR/fetch.sh to fetch.\n"
    )
    (skill_dir / "fetch.sh").write_text("#!/bin/sh\necho fetched\n")


def _write_schedule(path: Path, text: str = SCHEDULE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


async def _run(config, task):
    """Run the task with a stubbed agent turn; return (ctx, prompt)."""
    from decafclaw.conversation_manager import ConversationManager

    manager = ConversationManager(config, EventBus())
    seen = {}

    async def fake_run(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        seen["ctx"] = ctx
        seen["prompt"] = user_message
        return ToolResult(text="Done.")

    with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run), \
            patch("decafclaw.notifications.notify"):
        await run_schedule_task(config, EventBus(), manager, task)
    return seen["ctx"], seen["prompt"]


def _patterns(ctx) -> list[str]:
    return list(ctx.tools.preapproved_shell_patterns or [])


@pytest.mark.asyncio
async def test_admin_schedule_skill_dir_skips_workspace_shadow(config):
    """Shape 1: an admin-tier skill of the same name also exists."""
    ws_skill = config.workspace_path / "skills" / "feeds"
    _write_skill(ws_skill, "feeds")
    _write_skill(config.agent_path / "skills" / "feeds", "feeds")
    _write_schedule(config.agent_path / "schedules" / "feeds.md")
    config.discovered_skills = discover_skills(config)

    task = {t.name: t for t in discover_schedules(config)}["feeds"]
    assert task.source == "admin", "the schedule itself is human-controlled"

    ctx, _prompt = await _run(config, task)

    ws = str(ws_skill.resolve())
    assert not any(ws in p for p in _patterns(ctx)), (
        f"agent-writable dir pre-approved: {_patterns(ctx)}"
    )


@pytest.mark.asyncio
async def test_admin_schedule_skill_dir_skips_workspace_only_skill(config):
    """Shape 2: no trusted skill of that name exists, so nothing is shadowed.

    Non-shadowing would not have closed this. The trusted schedule is real,
    the workspace skill is the only one with that name, and it still must not
    anchor a pre-approved pattern.
    """
    ws_skill = config.workspace_path / "skills" / "feeds"
    _write_skill(ws_skill, "feeds")
    _write_schedule(config.agent_path / "schedules" / "feeds.md")
    config.discovered_skills = discover_skills(config)
    tiers = {s.name: s.trust_tier for s in config.discovered_skills}
    assert tiers["feeds"] == "workspace"
    assert sum(1 for s in config.discovered_skills if s.name == "feeds") == 1

    task = {t.name: t for t in discover_schedules(config)}["feeds"]
    ctx, _prompt = await _run(config, task)

    ws = str(ws_skill.resolve())
    assert not any(ws in p for p in _patterns(ctx)), (
        f"agent-writable dir pre-approved: {_patterns(ctx)}"
    )


@pytest.mark.asyncio
async def test_skill_dir_and_body_stay_in_sync(config):
    """The invariant `_resolve_skill_dir`'s docstring exists for.

    The value substituted into the prompt body must be the same one the
    shell pattern expanded to. If the tier check applied to only one of
    them, the agent would be pointed at a directory whose scripts are not
    pre-approved (or worse, the reverse).
    """
    _write_skill(config.workspace_path / "skills" / "feeds", "feeds")
    _write_schedule(config.agent_path / "schedules" / "feeds.md")
    config.discovered_skills = discover_skills(config)

    task = {t.name: t for t in discover_schedules(config)}["feeds"]
    ctx, prompt = await _run(config, task)

    # The body says "Scripts live in $SKILL_DIR"; whatever it was replaced
    # with must be the prefix of the pre-approved pattern.
    patterns = _patterns(ctx)
    assert patterns, "the admin-tier schedule should still pre-approve something"
    anchor = patterns[0].removesuffix("/fetch.sh*")
    assert f"Scripts live in {anchor}" in prompt


@pytest.mark.asyncio
async def test_admin_tier_skill_dir_unchanged(config):
    """Positive control — a human-placed skill still anchors $SKILL_DIR."""
    admin_skill = config.agent_path / "skills" / "feeds"
    _write_skill(admin_skill, "feeds")
    _write_schedule(config.agent_path / "schedules" / "feeds.md")
    config.discovered_skills = discover_skills(config)

    task = {t.name: t for t in discover_schedules(config)}["feeds"]
    ctx, prompt = await _run(config, task)

    expected = f"{admin_skill.resolve()}/fetch.sh*"
    assert expected in _patterns(ctx)
    assert str(admin_skill.resolve()) in prompt


@pytest.mark.asyncio
async def test_extra_tier_skill_dir_unchanged(config, tmp_path):
    """Positive control for the contrib-overlay case this feature exists for.

    An admin-tier overlay schedule at `data/{agent_id}/schedules/<name>.md`
    does not sit next to the contrib skill's scripts, so `$SKILL_DIR` must
    still resolve through `discovered_skills` to the extra-tier skill dir.
    """
    extra_root = tmp_path / "contrib-skills"
    extra_skill = extra_root / "feeds"
    _write_skill(extra_skill, "feeds")
    config.extra_skill_paths = [str(extra_root)]
    _write_schedule(config.agent_path / "schedules" / "feeds.md")
    config.discovered_skills = discover_skills(config)
    assert {s.name: s.trust_tier for s in config.discovered_skills}["feeds"] == "extra"

    task = {t.name: t for t in discover_schedules(config)}["feeds"]
    ctx, prompt = await _run(config, task)

    expected = f"{extra_skill.resolve()}/fetch.sh*"
    assert expected in _patterns(ctx), (
        f"contrib overlay case broke: {_patterns(ctx)}"
    )
    assert str(extra_skill.resolve()) in prompt
