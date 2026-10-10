"""Unit tests for the bundled dream skill and dream_recent_runs tool."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from decafclaw.archive import append_message
from decafclaw.media import ToolResult
from decafclaw.skills import discover_skills
from decafclaw.skills.dream.tools import (
    SkillConfig,
    dream_recent_runs,
    init,
)
from decafclaw.tools.skill_tools import activate_skill_internal


@pytest.mark.asyncio
async def test_dream_tools_register_via_skill_loader(ctx):
    """Verify dream skill exposes dream_recent_runs via skill loader."""
    skills = discover_skills(ctx.config)
    dream_info = next((s for s in skills if s.name == "dream"), None)
    assert dream_info is not None, "dream skill not found in bundled skills"
    assert dream_info.has_native_tools, "dream skill should have native tools"

    await activate_skill_internal(ctx, dream_info)

    assert "dream_recent_runs" in ctx.tools.extra
    def_names = {d["function"]["name"] for d in ctx.tools.extra_definitions}
    assert "dream_recent_runs" in def_names


def test_dream_skill_config_defaults():
    cfg = SkillConfig()
    assert cfg.window_days == 7


@pytest.mark.asyncio
async def test_dream_recent_runs_filtering(ctx, monkeypatch):
    """dream_recent_runs returns only dream runs within window with correct fields."""
    ref_time = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    # 1. Dream run from 2 days ago (inside 7-day window)
    t_recent = ref_time - timedelta(days=2)
    conv_recent = f"schedule-dream-{t_recent.strftime('%Y%m%d-%H%M%S')}"
    append_message(ctx.config, conv_recent, {"role": "user", "content": "Nightly dream pass"})
    append_message(
        ctx.config,
        conv_recent,
        {
            "role": "assistant",
            "content": "Consolidated recent notes into Architecture page.",
            "tool_calls": [
                {
                    "function": {
                        "name": "vault_write",
                        "arguments": json.dumps({"page": "agent/pages/Architecture", "content": "..."}),
                    }
                }
            ],
        },
    )
    append_message(ctx.config, conv_recent, {"role": "assistant", "content": "HEARTBEAT_OK"})

    # 2. Dream run from 12 days ago (outside 7-day window)
    t_old = ref_time - timedelta(days=12)
    conv_old = f"schedule-dream-{t_old.strftime('%Y%m%d-%H%M%S')}"
    append_message(ctx.config, conv_old, {"role": "user", "content": "Old dream pass"})
    append_message(
        ctx.config,
        conv_old,
        {
            "role": "assistant",
            "content": "Consolidated old notes into Legacy page.",
            "tool_calls": [
                {
                    "function": {
                        "name": "vault_write",
                        "arguments": json.dumps({"page": "agent/pages/Legacy", "content": "..."}),
                    }
                }
            ],
        },
    )

    # 3. Garden run from 1 day ago (different scheduled skill)
    t_garden = ref_time - timedelta(days=1)
    conv_garden = f"schedule-garden-{t_garden.strftime('%Y%m%d-%H%M%S')}"
    append_message(ctx.config, conv_garden, {"role": "user", "content": "Garden pass"})
    append_message(ctx.config, conv_garden, {"role": "assistant", "content": "Pruned 3 dead pages."})

    # Monkeypatch datetime.now in scheduled_activity
    class MockDatetime:
        @classmethod
        def now(cls, tz=None):
            return ref_time

        @classmethod
        def strptime(cls, date_string, format):
            return datetime.strptime(date_string, format)

        @classmethod
        def fromtimestamp(cls, ts, tz=None):
            return datetime.fromtimestamp(ts, tz)

    monkeypatch.setattr("decafclaw.scheduled_activity.datetime", MockDatetime)
    init(ctx.config, SkillConfig(window_days=7))

    # Default call (days=None -> 7 days)
    res = await dream_recent_runs(ctx)
    assert isinstance(res, ToolResult)
    data = res.data
    assert data is not None
    runs = data["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["skill_name"] == "dream"
    assert run["conv_id"] == conv_recent
    assert run["final_message"] == "Consolidated recent notes into Architecture page."
    assert run["vault_pages_touched"] == ["agent/pages/Architecture"]
    assert "Architecture" in res.text

    # Custom window (days=14 -> includes old run)
    res_14 = await dream_recent_runs(ctx, days=14)
    data_14 = res_14.data
    assert data_14 is not None
    assert len(data_14["runs"]) == 2
    conv_ids = [r["conv_id"] for r in data_14["runs"]]
    assert conv_recent in conv_ids
    assert conv_old in conv_ids

    # Empty window (days=1 -> neither dream run is within 1 day)
    res_1 = await dream_recent_runs(ctx, days=1)
    assert res_1.data["runs"] == []
    assert "No dream runs found" in res_1.text
