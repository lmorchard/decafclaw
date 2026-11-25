"""activate_skill returns an ack, never the SKILL.md body (#1823).

The body is rendered by the context composer as a never-archived
<activated_skills> block, so it must not also land in the archived tool
result.
"""

from pathlib import Path

import pytest

from decafclaw.skills import SkillInfo
from decafclaw.tools.skill_tools import activate_skill_internal


@pytest.mark.asyncio
async def test_activation_ack_omits_the_skill_body(ctx):
    info = SkillInfo(
        name="tabstack",
        description="Tabstack web tools",
        location=Path("/fake/skills/tabstack"),
        body="# Tabstack Skill\nSECRET BODY INSTRUCTIONS",
        trust_tier="bundled",
    )
    ctx.config.discovered_skills = [info]

    result = await activate_skill_internal(ctx, info)

    assert isinstance(result, str)
    assert "Activated skill 'tabstack'." in result
    assert "SECRET BODY INSTRUCTIONS" not in result
    assert "# Tabstack Skill" not in result
    assert "tabstack" in ctx.skills.activated
